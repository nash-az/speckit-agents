#!/usr/bin/env python3
"""speckit-agents Dashboard — a commercial-grade visualizer for Speckit projects.

A read-only Python stdlib HTTP server that visualizes everything Speckit knows
about a project: specs, plans, tasks, BLOCKED.md, decisions, runs, events,
and supervisor status.

Usage:
    bin/sdd-dashboard.py [--project DIR] [--port PORT]

Endpoints:
    GET /                         -- single-page HTML dashboard
    GET /api/projects             -- the started project only (no directory scan)
    POST /api/project/select      -- { "path"?, "feature"? } select a project and/or feature (memory only)
    GET /api/doc?name=<name>      -- one document from a fixed name set (missing -> {"missing": true})
    GET /api/project/spec         -- alias of /api/doc?name=spec
    GET /api/project/plan         -- alias of /api/doc?name=plan
    GET /api/project/tasks        -- read tasks.md (parsed)
    GET /api/project/blocked      -- read BLOCKED.md
    GET /api/project/decisions    -- alias of /api/doc?name=decisions
    GET /api/project/config       -- alias of /api/doc?name=config
    GET /api/runs                 -- list runs with status and counts
    GET /api/runs/<id>/events     -- event feed for one run
    GET /api/runs/<id>/report     -- run report markdown (id must look like 20261027-1431)
    GET /api/status               -- supervisor status (usage windows)
"""

from __future__ import annotations

import argparse
import http.server
import importlib.util
import json
import os
import re
import urllib.parse
from pathlib import Path

# All task state comes from bin/sdd-tasks.py, so the CLI and the dashboard cannot disagree
# (data-model.md §4.1). The hyphen in the file name prevents a plain import.
_spec = importlib.util.spec_from_file_location("sdd_tasks", Path(__file__).with_name("sdd-tasks.py"))
sdd_tasks = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_tasks)

DEFAULT_PORT = 8765
STATE_BASE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "speckit-agents"


def slug_for(project: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", str(project.resolve())).strip("-")


def read_text(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_lines(path):
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def is_speckit_project(project):
    """A project is a Speckit project if it has .sdd/config.toml or .specify/."""
    try:
        return (project / ".sdd" / "config.toml").is_file() or (project / ".specify").is_dir()
    except OSError:
        return False


def discover_projects(project: Path):
    """`GET /api/projects`: only the project the dashboard was started for (data-model.md §4.7).

    No directory scan: the dashboard never lists other folders on the machine.
    """
    return [{"path": str(project), "name": project.name}]


# Hosts a request may name (data-model.md §4.7, FR-022). Any other Host header, e.g. one a
# DNS-rebinding page sends, gets 421.
ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def host_without_port(host):
    """The Host header without its port: `[::1]:8765` → `[::1]`, `localhost:8765` → `localhost`."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[:end + 1] if end != -1 else host
    return host.split(":", 1)[0]


def feature_names(project: Path) -> list[str]:
    """Names of the subdirectories of `specs/`, sorted."""
    try:
        return sorted(p.name for p in (project / "specs").iterdir() if p.is_dir())
    except OSError:
        return []


def active_feature(project: Path) -> Path | None:
    """The feature folder every per-feature file is read from (data-model.md §4.2, FR-027).

    Order: `run.feature` from `.sdd/config.toml` if non-empty → `feature_directory` from
    `.specify/feature.json` → the only subdirectory of `specs/` → None (the page asks the person).
    """
    try:
        feature = sdd_tasks.load_config(project / ".sdd" / "config.toml").get("run", {}).get("feature")
    except (OSError, ValueError):  # unreadable or invalid TOML: no configured feature
        feature = None
    if isinstance(feature, str) and feature.strip():
        return project / feature.strip()
    data = read_json(project / ".specify" / "feature.json")
    if isinstance(data, dict):
        directory = data.get("feature_directory")
        if isinstance(directory, str) and directory.strip():
            return project / directory.strip()
    names = feature_names(project)
    if len(names) == 1:
        return project / "specs" / names[0]
    return None


def list_runs(project):
    runs_dir = project / ".sdd" / "runs"
    runs = []
    if not runs_dir.is_dir():
        return runs
    for entry in sorted(runs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if entry.is_file() and entry.suffix == ".jsonl":
            run_id = entry.stem
            events = []
            for line in read_lines(entry):
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
            approved = sum(1 for e in events if e.get("kind") == "task.approved")
            blocked = sum(1 for e in events if e.get("kind") in ("task.blocked", "task.failed"))
            interrupted = sum(1 for e in events if e.get("kind") == "task.interrupted")
            finished = any(e.get("kind") == "run.finished" for e in events)
            stopped = None
            for e in reversed(events):
                if e.get("kind") == "run.finished":
                    stopped = e.get("stopped")
                    break
            runs.append({
                "id": run_id,
                "events_count": len(events),
                "approved": approved,
                "blocked": blocked,
                "interrupted": interrupted,
                "finished": finished,
                "stopped": stopped,
            })
    return runs


def get_events(project, run_id):
    events_file = project / ".sdd" / "runs" / (run_id + ".jsonl")
    events = []
    for line in read_lines(events_file):
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def get_report(project, run_id):
    report_file = project / ".sdd" / "runs" / (run_id + ".md")
    return read_text(report_file)


def get_status(project):
    state_dir = STATE_BASE / slug_for(project)
    status_file = state_dir / "status.json"
    return read_json(status_file)


# Phase rollup (data-model.md §1, FR-004): most urgent first. A phase's status is the first key
# that any of its tasks has.
ROLLUP_ORDER = ("blocked", "retrying", "running", "held", "paused", "queued",
                "moved", "wontfix", "superseded", "done")

# sdd-tasks.py hold tags that become the parked status `held` (data-model.md §2).
PERSON_HOLD_TAGS = ("manual", "decision", "dep", "download")


def task_status(task, holds, entries):
    """The §2 status fields for one parsed task.

    `holds` is propagate_holds() output ({id: (tag, reason)}), `entries` is
    parse_blocked_entries() output. An open task without a hold gets `status: None`;
    the page fills in the transient state or `queued`.
    """
    entry = entries.get(task["id"])
    out = {"status": None, "status_tag": None, "status_reason": None, "question": None, "answer": None}
    if task["done"]:
        out["status"] = "done"
        return out
    hold = holds.get(task["id"])
    if hold is not None:
        tag, reason = hold
        out["status_reason"] = reason
        if tag in PERSON_HOLD_TAGS:
            out["status"], out["status_tag"] = "held", tag
        elif tag == "held":  # propagated from a held dependency
            out["status"], out["status_tag"] = "held", "dependency"
        elif tag == "blocked":
            out["status"] = "blocked"
            out["status_tag"] = entry["status"] if entry else "open"
            out["question"] = entry["question"] if entry else None
        elif tag in ("wontfix", "moved"):
            out["status"] = tag
            out["status_tag"] = entry["status"] if entry else tag
        elif tag == "superseded":
            out["status"], out["status_tag"] = "superseded", reason  # successor id(s) or "Phase N"
        else:  # an unknown hold kind still parks the task
            out["status"], out["status_tag"] = "held", tag
    if entry and entry.get("status") == "resolved":
        out["answer"] = entry.get("answer") or None
    return out


def rollup(statuses):
    """The first key of ROLLUP_ORDER present in `statuses`; None for a phase with no tasks.

    A task with no parked status (None) counts as `queued`.
    """
    present = {s or "queued" for s in statuses}
    return next((key for key in ROLLUP_ORDER if key in present), None)


def project_tasks(tasks_path, blocked_path, config_path):
    """`GET /api/project/tasks`: {"text", "phases"} with §2 tasks and §1 phase rollups."""
    text = read_text(tasks_path) if tasks_path else None
    if text is None:
        return {"text": "", "phases": []}
    parsed = sdd_tasks.parse_tasks(tasks_path)
    entries = sdd_tasks.parse_blocked_entries(blocked_path)
    try:
        config = sdd_tasks.load_config(config_path)
    except (OSError, ValueError):  # unreadable or invalid TOML: fall back to the defaults
        config = sdd_tasks.load_config(None)
    statuses = {i: e["status"] for i, e in entries.items()}
    holds = sdd_tasks.propagate_holds(
        parsed, sdd_tasks.direct_holds(parsed, statuses, config.get("allow", {})))
    phases = []
    for phase in parsed["phases"]:
        tasks = [dict(t, **task_status(t, holds, entries)) for t in phase["tasks"]]
        phases.append(dict(phase, tasks=tasks, status=rollup(t["status"] for t in tasks)))
    return {"text": text, "phases": phases}


def project_blocked(blocked_path):
    """`GET /api/project/blocked`: {"text", "entries": [{"id", **entry}]}, last entry per id."""
    text = (read_text(blocked_path) if blocked_path else None) or ""
    entries = sdd_tasks.parse_blocked_entries(blocked_path)
    return {"text": text, "entries": [{"id": i, **e} for i, e in entries.items()]}


# Structured routes that read a file from the active feature folder (data-model.md §4.2), with that
# file's name. The plain document routes are DOC_ALIASES below.
PER_FEATURE_ROUTES = {
    "/api/project/tasks": "tasks.md",
    "/api/project/blocked": "BLOCKED.md",
}

# `GET /api/doc?name=<name>` (data-model.md §4.3, FR-001): the only documents served. A name maps to
# ("feature", file in the active feature folder) or ("project", path under the project root).
# Any other name is a 404, so a request can never choose its own path.
DOC_NAMES = {
    "spec": ("feature", "spec.md"),
    "plan": ("feature", "plan.md"),
    "research": ("feature", "research.md"),
    "data-model": ("feature", "data-model.md"),
    "quickstart": ("feature", "quickstart.md"),
    "tasks": ("feature", "tasks.md"),
    "blocked": ("feature", "BLOCKED.md"),
    "decisions": ("feature", "decisions.md"),
    "constitution": ("project", ".specify/memory/constitution.md"),
    "learnings": ("project", ".sdd/learnings.md"),
    "config": ("project", ".sdd/config.toml"),
}

# The old document routes, kept as aliases of `/api/doc` with that name.
DOC_ALIASES = {
    "/api/project/spec": "spec",
    "/api/project/plan": "plan",
    "/api/project/decisions": "decisions",
    "/api/project/config": "config",
}

# Run ids are timestamps (`20261027-1431`, optionally suffixed); anything else is a 404, which
# also rules out path traversal through `/api/runs/<id>/…`.
RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{4}[a-z0-9-]*$")


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    project = None
    started_project = None  # the --project the server was started with; /api/projects lists only it
    feature = None  # a `specs/` folder name chosen in the page; memory only, never written to disk
    html = ""

    def log_message(self, format, *args):  # noqa: ANN001, ARG002
        pass

    def _host_allowed(self):
        """False (after sending 421) when the Host header is not a localhost name (§4.7)."""
        if host_without_port(self.headers.get("Host")) in ALLOWED_HOSTS:
            return True
        self._send_json({"error": "misdirected request"}, 421)
        return False

    def _feature_dir(self):
        """The person's in-memory choice if one was made, else active_feature()."""
        proj = DashboardHandler.project
        if DashboardHandler.feature:
            return proj / "specs" / DashboardHandler.feature
        return active_feature(proj)

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, status=200, content_type="text/plain"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self):
        body = self.html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_doc(self, name):
        """`GET /api/doc?name=<name>` (data-model.md §4.3): raw text, `200 {"missing": true}` for a
        missing file, 404 for a name outside DOC_NAMES, 409 for a feature document without a feature."""
        if name not in DOC_NAMES:
            self._send_json({"error": "not found"}, 404)
            return
        scope, rel = DOC_NAMES[name]
        proj = DashboardHandler.project
        if scope == "feature":
            feature = self._feature_dir()
            if feature is None:
                self._send_json({"error": "choose a feature", "features": feature_names(proj)}, 409)
                return
            path = feature / rel
        else:
            path = proj / rel
        text = read_text(path)
        if text is None:
            self._send_json({"missing": True})
            return
        self._send_text(text, content_type="text/plain" if name == "config" else "text/markdown")

    def do_GET(self):  # noqa: N802
        if not self._host_allowed():
            return
        path, _, query = self.path.partition("?")
        if path == "/":
            self._send_html()
            return
        if path == "/api/projects":
            self._send_json(discover_projects(DashboardHandler.started_project or DashboardHandler.project))
            return
        if path == "/api/doc":
            names = urllib.parse.parse_qs(query).get("name", [])
            self._send_doc(names[0] if len(names) == 1 else None)
            return
        if path in DOC_ALIASES:
            self._send_doc(DOC_ALIASES[path])
            return
        proj = DashboardHandler.project
        if path in PER_FEATURE_ROUTES:
            feature = self._feature_dir()
            if feature is None:
                self._send_json({"error": "choose a feature", "features": feature_names(proj)}, 409)
                return
            if path == "/api/project/tasks":
                self._send_json(project_tasks(feature / "tasks.md", feature / "BLOCKED.md",
                                              proj / ".sdd" / "config.toml"))
            else:
                self._send_json(project_blocked(feature / "BLOCKED.md"))
            return
        if path == "/api/runs":
            self._send_json(list_runs(proj))
            return
        m = re.match(r"^/api/runs/([^/]+)/events$", path)
        if m:
            self._send_json(get_events(proj, m.group(1)))
            return
        m = re.match(r"^/api/runs/([^/]+)/report$", path)
        if m:
            if not RUN_ID_RE.match(m.group(1)):
                self._send_json({"error": "not found"}, 404)
                return
            report = get_report(proj, m.group(1))
            if report is None:
                self._send_json({"error": "not found"}, 404)
                return
            self._send_text(report, content_type="text/markdown")
            return
        if path == "/api/status":
            self._send_json(get_status(proj))
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):  # noqa: N802
        if not self._host_allowed():
            return
        path = self.path.split("?")[0]
        if path == "/api/project/select":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(body)
            except ValueError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            new_path, new_feature = data.get("path"), data.get("feature")
            if not new_path and not new_feature:
                self._send_json({"error": "path or feature required"}, 400)
                return
            p = DashboardHandler.project
            if new_path:
                p = Path(new_path).expanduser().resolve()
                if not p.is_dir():
                    self._send_json({"error": "not a directory"}, 400)
                    return
                if not is_speckit_project(p):
                    self._send_json({"error": "not a speckit project (missing .sdd/config.toml or .specify/)"}, 400)
                    return
            if new_feature and new_feature not in feature_names(p):
                self._send_json({"error": "unknown feature", "features": feature_names(p)}, 400)
                return
            # Memory only (FR-027): a new project drops an earlier feature choice unless one is given.
            if new_path and p != DashboardHandler.project:
                DashboardHandler.feature = None
            DashboardHandler.project = p
            if new_feature:
                DashboardHandler.feature = new_feature
            self._send_json({"ok": True, "path": str(p), "feature": DashboardHandler.feature})
            return
        self.send_response(404)
        self.end_headers()


def build_html():
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>speckit-agents Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0f1115;
    --surface: #161921;
    --surface-hover: #1c212a;
    --border: #232934;
    --text: #e8eaed;
    --text-muted: #8b93a1;
    --accent: #3b82f6;
    --accent-hover: #2563eb;
    --success: #22c55e;
    --warning: #eab308;
    --danger: #ef4444;
    --info: #06b6d4;
    /* Status taxonomy (data-model.md §1): one color per status key */
    --st-queued: #8b93a1;
    --st-running: #3b82f6;
    --st-retrying: #eab308;
    --st-paused: #64748b;
    --st-done: #22c55e;
    --st-held: #f59e0b;
    --st-blocked: #ef4444;
    --st-wontfix: #6b7280;
    --st-moved: #a78bfa;
    --st-superseded: #94a3b8;
  }
  body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); height: 100vh; overflow: hidden; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .mono { font-family: 'JetBrains Mono', 'SF Mono', Monaco, monospace; }

  /* Layout */
  .app { display: flex; height: 100vh; }
  .sidebar { width: 260px; background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; z-index: 10; }
  .main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .header { height: 56px; background: var(--surface); border-bottom: 1px solid var(--border); display: flex; align-items: center; padding: 0 1.25rem; flex-shrink: 0; }
  .brand { font-weight: 700; font-size: 1.125rem; color: var(--text); display: flex; align-items: center; gap: 0.5rem; }
  .brand-dot { width: 8px; height: 8px; background: var(--success); border-radius: 50%; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
  .content { flex: 1; overflow-y: auto; padding: 1.5rem; background: var(--bg); }

  /* Project switcher */
  .project-bar { padding: 0.75rem 1.25rem; border-bottom: 1px solid var(--border); background: var(--surface); flex-shrink: 0; display: flex; gap: 0.5rem; align-items: center; }
  .project-select { flex: 1; padding: 0.5rem 0.75rem; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 0.9375rem; }
  .project-select:focus { outline: none; border-color: var(--accent); }

  /* Nav */
  .nav { flex: 1; overflow-y: auto; padding: 1rem 0; }
  .nav-item { display: flex; align-items: center; gap: 0.75rem; padding: 0.625rem 1.25rem; color: var(--text-muted); font-size: 0.9375rem; cursor: pointer; border-left: 3px solid transparent; transition: all 0.15s; }
  .nav-item:hover { background: var(--surface-hover); color: var(--text); }
  .nav-item.active { background: rgba(59,130,246,0.1); color: var(--text); border-left-color: var(--accent); }
  .nav-item .icon { width: 18px; text-align: center; }

  /* Section header */
  .section { margin-bottom: 1.5rem; }
  .section-title { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); margin-bottom: 0.75rem; font-weight: 600; }

  /* Cards */
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; margin-bottom: 1rem; }
  .card-title { font-size: 0.9375rem; font-weight: 600; color: var(--text); margin-bottom: 0.75rem; display: flex; justify-content: space-between; align-items: center; }
  .card-title .badge { font-size: 0.75rem; padding: 0.1875rem 0.5rem; border-radius: 4px; background: var(--border); color: var(--text-muted); font-weight: 500; }

  /* Grid */
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1rem; }
  @media (max-width: 1024px) { .grid-3 { grid-template-columns: 1fr 1fr; } }
  @media (max-width: 768px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } .app { flex-direction: column; } .sidebar { width: 100%; height: auto; } .main { height: 100vh; } }

  /* Stat */
  .stat { display: flex; flex-direction: column; gap: 0.25rem; }
  .stat-value { font-size: 1.875rem; font-weight: 700; font-family: 'JetBrains Mono', monospace; color: var(--text); }
  .stat-label { font-size: 0.8125rem; color: var(--text-muted); }

  /* Status pill */
  .pill { display: inline-flex; align-items: center; gap: 0.5rem; padding: 0.25rem 0.625rem; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }
  .pill-dot { width: 6px; height: 6px; border-radius: 50%; }
  .pill-running { background: rgba(34,197,94,0.15); color: var(--success); }
  .pill-running .pill-dot { background: var(--success); box-shadow: 0 0 8px var(--success); }
  .pill-paused { background: rgba(234,179,8,0.15); color: var(--warning); }
  .pill-paused .pill-dot { background: var(--warning); }
  .pill-done { background: rgba(59,130,246,0.15); color: var(--accent); }
  .pill-done .pill-dot { background: var(--accent); }
  .pill-stopped { background: rgba(239,68,68,0.15); color: var(--danger); }
  .pill-stopped .pill-dot { background: var(--danger); }

  /* Run list */
  .run-item { display: flex; align-items: center; justify-content: space-between; padding: 0.75rem; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 0.5rem; cursor: pointer; transition: all 0.15s; background: var(--surface); }
  .run-item:hover { border-color: var(--accent); background: var(--surface-hover); }
  .run-item.active { border-color: var(--accent); background: rgba(59,130,246,0.05); }
  .run-info { flex: 1; }
  .run-id { font-family: 'JetBrains Mono', monospace; font-size: 0.875rem; font-weight: 600; color: var(--text); }
  .run-meta { font-size: 0.8125rem; color: var(--text-muted); margin-top: 0.25rem; }
  .run-actions { display: flex; gap: 0.5rem; opacity: 0; transition: opacity 0.15s; }
  .run-item:hover .run-actions { opacity: 1; }
  .btn-icon { width: 28px; height: 28px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 0.875rem; }
  .btn-icon:hover { border-color: var(--accent); color: var(--accent); }

  /* Empty state */
  .empty { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 200px; color: var(--text-muted); }
  .empty-icon { font-size: 2rem; margin-bottom: 0.75rem; opacity: 0.5; }

  /* Spec / content viewer */
  .markdown { font-size: 0.9375rem; line-height: 1.7; color: var(--text); }
  .markdown h1, .markdown h2, .markdown h3 { color: var(--text); margin: 1.25rem 0 0.5rem; font-weight: 600; }
  .markdown h1 { font-size: 1.375rem; border-bottom: 1px solid var(--border); padding-bottom: 0.5rem; }
  .markdown h2 { font-size: 1.125rem; }
  .markdown h3 { font-size: 1rem; color: var(--text-muted); }
  .markdown p { margin: 0.5rem 0; }
  .markdown code { background: var(--bg); padding: 0.125rem 0.375rem; border-radius: 4px; font-family: 'JetBrains Mono', monospace; font-size: 0.875rem; color: var(--info); }
  .markdown pre { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 1rem; overflow-x: auto; margin: 0.75rem 0; }
  .markdown pre code { padding: 0; color: var(--text); background: transparent; }
  .markdown ul, .markdown ol { margin: 0.5rem 0 0.5rem 1.5rem; }
  .markdown li { margin: 0.25rem 0; }
  .markdown blockquote { border-left: 3px solid var(--accent); padding-left: 1rem; margin: 0.75rem 0; color: var(--text-muted); font-style: italic; }
  .markdown table { border-collapse: collapse; width: 100%; margin: 0.75rem 0; }
  .markdown th, .markdown td { border: 1px solid var(--border); padding: 0.5rem; text-align: left; font-size: 0.875rem; }
  .markdown th { background: var(--surface); color: var(--text-muted); }

  /* Task board */
  .task-board { display: flex; gap: 1rem; overflow-x: auto; padding-bottom: 1rem; scrollbar-width: none; }
  .task-board::-webkit-scrollbar { display: none; }
  .task-column { flex: 0 0 auto; width: 260px; display: flex; flex-direction: column; gap: 0.5rem; }
  .task-column-title { font-size: 0.8125rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); padding-bottom: 0.5rem; border-bottom: 1px solid var(--border); margin-bottom: 0.5rem; }
  .task-card { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 0.75rem; cursor: pointer; transition: all 0.15s; }
  .task-card:hover { border-color: var(--accent); background: var(--surface-hover); }
  .task-id { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; font-weight: 700; color: var(--accent); margin-bottom: 0.25rem; display: block; }
  .task-text { font-size: 0.875rem; color: var(--text); margin-bottom: 0.5rem; line-height: 1.4; }
  .task-tags { display: flex; gap: 0.25rem; flex-wrap: wrap; }
  .task-tag { font-size: 0.6875rem; padding: 0.125rem 0.5rem; border-radius: 4px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
  .tag-P { background: rgba(59,130,246,0.15); color: var(--accent); }
  .tag-manual { background: rgba(127,29,29,0.15); color: var(--danger); }
  .tag-decision { background: rgba(120,53,15,0.15); color: var(--warning); }
  .tag-dep { background: rgba(30,58,138,0.15); color: #bfdbfe; }
  .tag-download { background: rgba(88,28,135,0.15); color: #e9d5ff; }
  .tag-eval { background: rgba(6,182,212,0.15); color: var(--info); }
  .tag-ui { background: rgba(139,92,246,0.15); color: #c4b5fd; }
  .task-done { opacity: 0.5; text-decoration: line-through; }

  /* Usage windows */
  .usage-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  @media (max-width: 640px) { .usage-grid { grid-template-columns: 1fr; } }
  .usage-card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; }
  .usage-name { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); margin-bottom: 0.5rem; }
  .usage-bar { height: 6px; background: var(--bg); border-radius: 3px; overflow: hidden; margin-bottom: 0.5rem; }
  .usage-fill { height: 100%; border-radius: 3px; transition: width 0.5s; }
  .usage-fill-low { background: var(--success); }
  .usage-fill-med { background: var(--warning); }
  .usage-fill-high { background: var(--danger); }
  .usage-val { font-family: 'JetBrains Mono', monospace; font-size: 1.125rem; font-weight: 700; color: var(--text); }
  .usage-reset { font-size: 0.8125rem; color: var(--text-muted); }

  /* Blocked / decisions */
  .blocked-item { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 0.75rem; margin-bottom: 0.5rem; }
  .blocked-header { display: flex; justify-content: space-between; align-items: start; margin-bottom: 0.5rem; }
  .blocked-id { font-family: 'JetBrains Mono', monospace; font-size: 0.875rem; font-weight: 700; color: var(--text); }
  .blocked-status { font-size: 0.75rem; font-weight: 600; text-transform: uppercase; }
  .status-open { color: var(--warning); }
  .status-resolved { color: var(--success); }
  .status-wontfix { color: var(--text-muted); }
  .blocked-reason { font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem; }
  .blocked-question { font-size: 0.875rem; color: var(--text); font-style: italic; padding: 0.5rem; background: var(--bg); border-radius: 4px; border-left: 3px solid var(--accent); }
  .blocked-tags { display: flex; gap: 0.25rem; margin-top: 0.5rem; }

  /* Events */
  .events-list { max-height: 400px; overflow-y: auto; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); }
  .event-row { display: flex; padding: 0.625rem 0.875rem; border-bottom: 1px solid var(--border); font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; }
  .event-row:last-child { border-bottom: none; }
  .event-ts { width: 120px; color: var(--text-muted); }
  .event-kind { width: 160px; font-weight: 600; color: var(--accent); }
  .event-payload { color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; padding-left: 0.75rem; }

  /* Buttons */
  .btn { display: inline-flex; align-items: center; gap: 0.5rem; padding: 0.5rem 1rem; border-radius: 6px; border: 1px solid var(--border); background: var(--surface); color: var(--text); font-size: 0.875rem; cursor: pointer; transition: all 0.15s; font-family: inherit; }
  .btn:hover { border-color: var(--accent); background: var(--surface-hover); }
  .btn-primary { background: var(--accent); border-color: var(--accent); color: white; }
  .btn-primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); }

  /* Toast */
  .toast-container { position: fixed; bottom: 1.5rem; right: 1.5rem; z-index: 100; display: flex; flex-direction: column; gap: 0.5rem; pointer-events: none; }
  .toast { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 0.75rem 1rem; color: var(--text); font-size: 0.875rem; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.3); pointer-events: auto; animation: slideIn 0.2s ease-out; }
  @keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }

  /* Status badge (data-model.md §1): glyph + color + label, always all three (FR-003) */
  .status { display: inline-flex; align-items: center; gap: 0.3125rem; font-size: 0.75rem; font-weight: 600; white-space: nowrap; }
  .status svg { width: 14px; height: 14px; flex-shrink: 0; }
  .status-queued { color: var(--st-queued); }
  .status-running { color: var(--st-running); }
  .status-retrying { color: var(--st-retrying); }
  .status-paused { color: var(--st-paused); }
  .status-done { color: var(--st-done); }
  .status-held { color: var(--st-held); }
  .status-blocked { color: var(--st-blocked); }
  .status-wontfix { color: var(--st-wontfix); }
  .status-moved { color: var(--st-moved); }
  .status-superseded { color: var(--st-superseded); }
  .status svg.glyph-spinner { animation: status-spin 1s linear infinite; }
  @keyframes status-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

  .refresh { font-size: 0.75rem; color: var(--text-muted); margin-top: 1rem; text-align: center; }
</style>
</head>
<body>
<div class="app">
  <div class="sidebar">
    <div class="brand">
      <div class="brand-dot"></div>
      speckit-agents
    </div>
    <div class="project-bar">
      <select id="project-select" class="project-select"></select>
      <button id="btn-load-project" class="btn btn-primary">Load Project</button>
    </div>
    <div class="nav">
      <div class="nav-item active" data-section="overview"><span class="icon">📊</span>Overview</div>
      <div class="nav-item" data-section="spec"><span class="icon">📄</span>Spec</div>
      <div class="nav-item" data-section="plan"><span class="icon">🗺️</span>Plan</div>
      <div class="nav-item" data-section="tasks"><span class="icon">✅</span>Tasks</div>
      <div class="nav-item" data-section="blocked"><span class="icon">🚫</span>Blocked</div>
      <div class="nav-item" data-section="decisions"><span class="icon">⚖️</span>Decisions</div>
      <div class="nav-item" data-section="runs"><span class="icon">🔄</span>Runs</div>
      <div class="nav-item" data-section="status"><span class="icon">⚡</span>Status</div>
    </div>
  </div>
  <div class="main">
    <div class="header">
      <div class="brand">
        <div class="brand-dot"></div>
        <span id="page-title">Overview</span>
      </div>
    </div>
    <div class="content">
      <!-- Overview -->
      <section id="section-overview" class="section">
        <div class="grid-3">
          <div class="card">
            <div class="stat">
              <div class="stat-value" id="stat-tasks">-</div>
              <div class="stat-label">Tasks</div>
            </div>
          </div>
          <div class="card">
            <div class="stat">
              <div class="stat-value" id="stat-done">-</div>
              <div class="stat-label">Done</div>
            </div>
          </div>
          <div class="card">
            <div class="stat">
              <div class="stat-value" id="stat-held">-</div>
              <div class="stat-label">Held / Waiting</div>
            </div>
          </div>
          <div class="card">
            <div class="stat">
              <div class="stat-value" id="stat-runs">-</div>
              <div class="stat-label">Runs</div>
            </div>
          </div>
          <div class="card">
            <div class="stat">
              <div class="stat-value" id="stat-blocked">-</div>
              <div class="stat-label">Blocked</div>
            </div>
          </div>
          <div class="card">
            <div class="stat">
              <div class="stat-value" id="stat-decisions">-</div>
              <div class="stat-label">Decisions</div>
            </div>
          </div>
        </div>
        <div class="card">
          <div class="card-title">Project Files</div>
          <div style="display: grid; gap: 0.5rem;">
            <a href="#" data-file="spec" class="btn">View spec.md</a>
            <a href="#" data-file="plan" class="btn">View plan.md</a>
            <a href="#" data-file="tasks" class="btn">View tasks.md</a>
            <a href="#" data-file="blocked" class="btn">View BLOCKED.md</a>
            <a href="#" data-file="decisions" class="btn">View decisions.md</a>
            <a href="#" data-file="config" class="btn">View .sdd/config.toml</a>
          </div>
        </div>
      </section>

      <!-- Spec / Plan viewer (shared) -->
      <section id="section-spec" class="section" style="display:none;">
        <div class="card">
          <div class="card-title">Feature Specification</div>
          <div id="spec-content" class="markdown"></div>
        </div>
      </section>
      <section id="section-plan" class="section" style="display:none;">
        <div class="card">
          <div class="card-title">Implementation Plan</div>
          <div id="plan-content" class="markdown"></div>
        </div>
      </section>

      <!-- Tasks -->
      <section id="section-tasks" class="section" style="display:none;">
        <div class="card-title">Tasks by Phase</div>
        <div id="tasks-board" class="task-board"></div>
        <div class="card" style="margin-top:1rem;">
          <div class="card-title">All Tasks</div>
          <div id="tasks-list"></div>
        </div>
      </section>

      <!-- Blocked -->
      <section id="section-blocked" class="section" style="display:none;">
        <div class="card-title">Blocked Tasks &amp; Waiting on You</div>
        <div id="blocked-list"></div>
      </section>

      <!-- Decisions -->
      <section id="section-decisions" class="section" style="display:none;">
        <div class="card-title">Decisions Register</div>
        <div id="decisions-content" class="markdown"></div>
      </section>

      <!-- Runs -->
      <section id="section-runs" class="section" style="display:none;">
        <div class="card-title">Run History</div>
        <div id="runs-list"></div>
        <div class="card" style="margin-top:1rem;">
          <div class="card-title">Event Feed</div>
          <div id="events-view" class="events-list"></div>
        </div>
      </section>

      <!-- Status -->
      <section id="section-status" class="section" style="display:none;">
        <div class="card-title">Supervisor &amp; Usage</div>
        <div id="status-content"></div>
      </section>

      <div class="refresh" id="refresh-text">Refreshed <span id="refresh-time">-</span></div>
    </div>
  </div>
</div>

<div class="toast-container" id="toasts"></div>

<script>
  (function() {
    var currentProject = null;
    var selectedRunId = null;
    var selectedSection = 'overview';

    function toast(msg) {
      try {
        var c = document.getElementById('toasts');
        var t = document.createElement('div');
        t.className = 'toast';
        t.textContent = msg;
        c.appendChild(t);
        setTimeout(function() { t.remove(); }, 3000);
      } catch(e) { console.error('toast error', e); }
    }

    function fetchJSON(url, method, body) {
      var opts = { method: method || 'GET', headers: {} };
      if (body) opts.headers['Content-Type'] = 'application/json';
      if (body) opts.body = JSON.stringify(body);
      return fetch(url, opts).then(function(res) {
        if (!res.ok) return null;
        return res.json();
      }).catch(function(e) { console.error('fetchJSON error', url, e); return null; });
    }

    function escapeHtml(s) {
      if (s == null) return '';
      return String(s).replace(/[&<>"']/g, function(c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
      });
    }

    // Status system (data-model.md §1). Keys match ROLLUP_ORDER in the Python code byte for byte.
    // Inline SVGs only: no icon font, no CDN.
    var SVG_OPEN = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
    function glyph(name, body) { return SVG_OPEN + ' class="glyph-' + name + '">' + body + '</svg>'; }
    var GLYPHS = {
      'circle-outline': glyph('circle-outline', '<circle cx="12" cy="12" r="9"/>'),
      'spinner': glyph('spinner', '<path d="M21 12a9 9 0 1 1-6.22-8.56"/>'),
      'rotate-arrow': glyph('rotate-arrow', '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><polyline points="21 3 21 9 15 9"/>'),
      'pause-bars': glyph('pause-bars', '<line x1="9" y1="5" x2="9" y2="19"/><line x1="15" y1="5" x2="15" y2="19"/>'),
      'check': glyph('check', '<polyline points="20 6 9 17 4 12"/>'),
      'clock': glyph('clock', '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/>'),
      'alert-triangle': glyph('alert-triangle', '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>'),
      'slash-circle': glyph('slash-circle', '<circle cx="12" cy="12" r="9"/><line x1="5.64" y1="5.64" x2="18.36" y2="18.36"/>'),
      'arrow-up-right': glyph('arrow-up-right', '<line x1="7" y1="17" x2="17" y2="7"/><polyline points="7 7 17 7 17 17"/>'),
      'arrow-right': glyph('arrow-right', '<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>')
    };
    var STATUS = {
      queued: { glyph: 'circle-outline', label: 'Queued' },
      running: { glyph: 'spinner', label: 'Running: {step}' },
      retrying: { glyph: 'rotate-arrow', label: 'Retry {n}/{max}' },
      paused: { glyph: 'pause-bars', label: 'Paused' },
      done: { glyph: 'check', label: 'Done' },
      held: { glyph: 'clock', label: 'Held: {tag}' },
      blocked: { glyph: 'alert-triangle', label: 'Blocked' },
      wontfix: { glyph: 'slash-circle', label: "Won't fix" },
      moved: { glyph: 'arrow-up-right', label: 'Moved' },
      superseded: { glyph: 'arrow-right', label: 'Superseded by {by}' }
    };

    // statusBadge('held', {tag: 'manual'}) -> glyph + color + label (FR-003).
    // vars fill the label template; vars.label replaces the template (e.g. "Answered — runs next").
    // A placeholder with no value is dropped together with a trailing ": ".
    function statusBadge(key, vars) {
      vars = vars || {};
      var st = STATUS[key] || { glyph: 'circle-outline', label: String(key) };
      var label = vars.label != null ? String(vars.label) : st.label.replace(/[{]([a-z]+)[}]/g, function(m, name) {
        return vars[name] != null ? String(vars[name]) : '';
      }).replace(/[:][ ]*$/, '');
      var safe = escapeHtml(label);
      return '<span class="status status-' + escapeHtml(key) + '" title="' + safe + '">' + GLYPHS[st.glyph] +
        '<span class="status-label">' + safe + '</span></span>';
    }

    function loadProjects() {
      fetchJSON('/api/projects', 'GET').then(function(projects) {
        projects = projects || [];
        var sel = document.getElementById('project-select');
        sel.innerHTML = '<option value="">Select a project...</option>';
        projects.forEach(function(p) {
          var o = document.createElement('option');
          o.value = p.path;
          o.textContent = p.name + ' (' + p.path + ')';
          sel.appendChild(o);
        });
        if (projects.length === 1) {
          sel.value = projects[0].path;
          selectProjectPath(projects[0].path);
        }
      }).catch(function(e) { console.error('loadProjects error', e); toast('Failed to load projects'); });
    }

    function selectProjectPath(path) {
      fetchJSON('/api/project/select', 'POST', { path: path }).then(function(result) {
        if (!result || !result.ok) {
          toast(result && result.error ? result.error : 'Failed to select project');
          return;
        }
        currentProject = path;
        document.getElementById('page-title').textContent = path.split('/').pop();
        toast('Project loaded: ' + path.split('/').pop());
        refreshAll();
      }).catch(function(e) { console.error('selectProjectPath error', e); toast('Error selecting project'); });
    }

    function showSection(name) {
      try {
        document.querySelectorAll('.nav-item').forEach(function(el) { el.classList.remove('active'); });
        var target = document.querySelector('.nav-item[data-section="' + name + '"]');
        if (target) target.classList.add('active');
        document.querySelectorAll('section').forEach(function(el) { el.style.display = 'none'; });
        var sec = document.getElementById('section-' + name);
        if (sec) sec.style.display = 'block';
        document.getElementById('page-title').textContent = name.charAt(0).toUpperCase() + name.slice(1);
        selectedSection = name;
        if (name === 'overview') loadOverview();
        if (name === 'tasks') loadTasks();
        if (name === 'blocked') loadBlocked();
        if (name === 'runs') loadRuns();
        if (name === 'status') loadStatus();
        if (name === 'spec') loadFile('spec');
        if (name === 'plan') loadFile('plan');
        if (name === 'decisions') loadFile('decisions');
      } catch(e) { console.error('showSection error', e); }
    }

    function loadOverview() {
      loadTasksData();
      loadBlockedData();
      fetchJSON('/api/runs', 'GET').then(function(runs) {
        runs = runs || [];
        document.getElementById('stat-runs').textContent = runs.length;
      });
    }

    function loadTasksData() {
      fetchJSON('/api/project/tasks', 'GET').then(function(data) {
        data = data || {};
        var phases = data.phases || [];
        var total = 0, done = 0, held = 0;
        phases.forEach(function(p) {
          (p.tasks || []).forEach(function(t) {
            total++;
            if (t.done) done++;
          });
        });
        phases.forEach(function(p) {
          (p.tasks || []).forEach(function(t) {
            if (!t.done && (t.tags.indexOf('manual') >= 0 || t.tags.indexOf('decision') >= 0 || t.tags.indexOf('dep') >= 0 || t.tags.indexOf('download') >= 0)) held++;
          });
        });
        document.getElementById('stat-tasks').textContent = total;
        document.getElementById('stat-done').textContent = done;
        document.getElementById('stat-held').textContent = held;
      }).catch(function(e) { console.error('loadTasksData error', e); });
    }

    function loadTasks() {
      fetchJSON('/api/project/tasks', 'GET').then(function(data) {
        data = data || {};
        var phases = data.phases || [];
        var board = document.getElementById('tasks-board');
        board.innerHTML = '';
        var cols = { todo: [], inProgress: [], done: [], held: [] };
        phases.forEach(function(p) {
          (p.tasks || []).forEach(function(t) {
            if (t.done) cols.done.push(t);
            else if (t.tags.indexOf('manual') >= 0 || t.tags.indexOf('decision') >= 0 || t.tags.indexOf('dep') >= 0 || t.tags.indexOf('download') >= 0) cols.held.push(t);
            else cols.todo.push(t);
          });
        });
        function renderCol(title, items, emptyMsg) {
          var col = document.createElement('div');
          col.className = 'task-column';
          col.innerHTML = '<div class="task-column-title">' + escapeHtml(title) + ' <span style="font-weight:400;font-size:0.75rem;margin-left:0.25rem;opacity:0.7">(' + items.length + ')</span></div>';
          items.forEach(function(t) {
            var card = document.createElement('div');
            card.className = 'task-card' + (t.done ? ' task-done' : '');
            var tagsHtml = t.tags.map(function(tag) { return '<span class="task-tag tag-' + tag.toLowerCase() + '">' + escapeHtml(tag) + '</span>'; }).join('');
            card.innerHTML = '<span class="task-id">' + escapeHtml(t.id) + '</span><div class="task-text">' + escapeHtml(t.text) + '</div><div class="task-tags">' + tagsHtml + '</div>';
            col.appendChild(card);
          });
          if (items.length === 0) col.innerHTML += '<div style="font-size:0.8125rem;color:var(--text-muted);font-style:italic;padding-top:0.5rem;">' + emptyMsg + '</div>';
          board.appendChild(col);
        }
        renderCol('To Do', cols.todo, 'No open tasks');
        renderCol('In Progress', cols.inProgress, 'None');
        renderCol('Done', cols.done, 'None');
        renderCol('Held / Waiting', cols.held, 'None');
        var list = document.getElementById('tasks-list');
        list.innerHTML = '';
        phases.forEach(function(p) {
          var ph = document.createElement('div');
          ph.style.marginBottom = '0.75rem';
          ph.innerHTML = '<div style="font-size:0.8125rem;font-weight:600;text-transform:uppercase;letter-spacing:0.08em;color:var(--text-muted);margin-bottom:0.5rem;">' + escapeHtml(p.title) + '</div>';
          (p.tasks || []).forEach(function(t) {
            var item = document.createElement('div');
            item.className = 'task-card' + (t.done ? ' task-done' : '');
            var tagsHtml = t.tags.map(function(tag) { return '<span class="task-tag tag-' + tag.toLowerCase() + '">' + escapeHtml(tag) + '</span>'; }).join('');
            item.innerHTML = '<span class="task-id">' + escapeHtml(t.id) + '</span><div class="task-text" style="margin-bottom:0.25rem;">' + escapeHtml(t.text) + '</div><div class="task-tags">' + tagsHtml + '</div>';
            ph.appendChild(item);
          });
          list.appendChild(ph);
        });
      }).catch(function(e) { console.error('loadTasks error', e); });
    }

    function loadBlocked() {
      fetchJSON('/api/project/blocked', 'GET').then(function(data) {
        data = data || {};
        var entries = data.entries || [];
        var el = document.getElementById('blocked-list');
        el.innerHTML = '';
        if (entries.length === 0) {
          el.innerHTML = '<div class="empty"><div class="empty-icon">✅</div><div>No blocked tasks.</div></div>';
          return;
        }
        entries.forEach(function(e) {
          var item = document.createElement('div');
          item.className = 'blocked-item';
          var statusCls = e.status === 'open' ? 'status-open' : (e.status === 'resolved' ? 'status-resolved' : 'status-wontfix');
          var tagsHtml = (e.tag ? [e.tag] : []).map(function(tag) { return '<span class="task-tag tag-' + tag.toLowerCase() + '">' + escapeHtml(tag) + '</span>'; }).join('');
          item.innerHTML = '<div class="blocked-header"><div class="blocked-id">' + escapeHtml(e.id) + '</div><div class="blocked-status ' + statusCls + '">' + escapeHtml(e.status) + '</div></div>' +
            (e.reason ? '<div class="blocked-reason">' + escapeHtml(e.reason) + '</div>' : '') +
            (e.question ? '<div class="blocked-question">❓ ' + escapeHtml(e.question) + '</div>' : '') +
            '<div class="blocked-tags">' + tagsHtml + '</div>';
          el.appendChild(item);
        });
        document.getElementById('stat-blocked').textContent = entries.length;
      }).catch(function(e) { console.error('loadBlocked error', e); });
    }

    function loadFile(kind) {
      fetch('/api/project/' + kind).then(function(res) {
        if (!res.ok) return;
        if (kind === 'tasks') return res.json().then(function(d) { return d.text || ''; });
        return res.text();
      }).then(function(text) {
        if (text == null) return;
        var contentEl = document.getElementById(kind + '-content');
        if (contentEl) contentEl.innerHTML = '<pre style="white-space:pre-wrap;font-family:JetBrains Mono,monospace;font-size:0.875rem;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:1rem;">' + escapeHtml(text) + '</pre>';
      }).catch(function(e) { console.error('loadFile error', kind, e); });
    }

    function viewFile(kind) {
      var map = { spec: 'spec', plan: 'plan', tasks: 'tasks', blocked: 'blocked', decisions: 'decisions', config: 'spec' };
      var section = map[kind];
      showSection(section);
      loadFile(kind);
    }

    function loadRuns() {
      fetchJSON('/api/runs', 'GET').then(function(runs) {
        runs = runs || [];
        var el = document.getElementById('runs-list');
        el.innerHTML = '';
        if (runs.length === 0) {
          el.innerHTML = '<div class="empty"><div class="empty-icon">🔄</div><div>No runs found.</div></div>';
          return;
        }
        runs.forEach(function(run) {
          var div = document.createElement('div');
          div.className = 'run-item' + (selectedRunId === run.id ? ' active' : '');
          (function(runId) {
            div.addEventListener('click', function() {
              selectedRunId = runId;
              loadRuns();
              loadEvents();
            });
          })(run.id);
          var statusCls = run.finished ? (run.stopped ? 'status-stopped' : 'status-done') : (run.interrupted > 0 ? 'status-paused' : 'status-running');
          var finishedText = run.finished ? (run.stopped ? 'stopped' : 'done') : 'running';
          div.innerHTML = '<div class="run-info"><div class="run-id">#' + escapeHtml(run.id) + '</div><div class="run-meta"><span class="pill ' + statusCls + '"><span class="pill-dot"></span>' + finishedText + '</span> ' + run.approved + ' approved · ' + run.blocked + ' blocked · ' + run.interrupted + ' interrupted · ' + run.events_count + ' events</div></div><div class="run-actions"><button class="btn-icon" title="View events">📋</button><button class="btn-icon" title="View report">📄</button></div></div>';
          var actions = div.querySelectorAll('.run-actions .btn-icon');
          if (actions.length >= 2) {
            actions[0].addEventListener('click', function(ev) { ev.stopPropagation(); selectedRunId = run.id; loadEvents(); });
            actions[1].addEventListener('click', function(ev) { ev.stopPropagation(); viewReport(run.id); });
          }
          el.appendChild(div);
        });
        document.getElementById('stat-runs').textContent = runs.length;
      }).catch(function(e) { console.error('loadRuns error', e); });
    }

    function loadEvents() {
      if (!selectedRunId) {
        document.getElementById('events-view').innerHTML = '<div class="empty"><div class="empty-icon">📋</div><div>Select a run to see its events.</div></div>';
        return;
      }
      fetchJSON('/api/runs/' + encodeURIComponent(selectedRunId) + '/events', 'GET').then(function(events) {
        events = events || [];
        var el = document.getElementById('events-view');
        el.innerHTML = '';
        if (events.length === 0) {
          el.innerHTML = '<div class="empty"><div class="empty-icon">📭</div><div>No events.</div></div>';
          return;
        }
        events.slice().reverse().forEach(function(ev) {
          var row = document.createElement('div');
          row.className = 'event-row';
          var ts = ev.ts ? new Date(ev.ts).toLocaleTimeString() : '-';
          var payloadShort = JSON.stringify(ev).slice(0, 120) + (JSON.stringify(ev).length > 120 ? '…' : '');
          row.innerHTML = '<div class="event-ts">[' + ts + ']</div><div class="event-kind">' + escapeHtml(ev.kind) + '</div><div class="event-payload" title="' + escapeHtml(payloadShort) + '">' + escapeHtml(payloadShort) + '</div>';
          el.appendChild(row);
        });
        el.scrollTop = el.scrollHeight;
      }).catch(function(e) { console.error('loadEvents error', e); });
    }

    function viewReport(runId) {
      fetch('/api/runs/' + encodeURIComponent(runId) + '/report').then(function(res) {
        if (!res.ok) {
          document.getElementById('events-view').innerHTML = '<div style="padding:1rem;color:var(--danger);">Report not found.</div>';
          return;
        }
        return res.text();
      }).then(function(text) {
        document.getElementById('events-view').innerHTML = '<div style="padding:1rem;white-space:pre-wrap;font-family:JetBrains Mono,monospace;font-size:0.875rem;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:6px;">' + escapeHtml(text) + '</div>';
      }).catch(function(e) { console.error('viewReport error', e); });
    }

    function loadStatus() {
      fetchJSON('/api/status', 'GET').then(function(status) {
        status = status || {};
        var el = document.getElementById('status-content');
        if (!status || Object.keys(status).length === 0) {
          el.innerHTML = '<div class="empty"><div class="empty-icon">⚡</div><div>No supervisor status available.</div></div>';
          return;
        }
        var html = '<div class="usage-grid">';
        html += '<div class="usage-card"><div class="usage-name">Supervisor State</div><div class="usage-val" style="font-size:1.125rem;">' + escapeHtml(status.state || 'unknown') + '</div>' + (status.cycle !== undefined ? '<div class="usage-reset">Cycle ' + status.cycle + '</div>' : '') + '</div>';
        if (status.usage) {
          Object.entries(status.usage).forEach(function(entry) {
            var name = entry[0], win = entry[1];
            var u = win.utilization || 0;
            var fillClass = 'usage-fill-low';
            if (u >= 0.9) fillClass = 'usage-fill-high';
            else if (u >= 0.75) fillClass = 'usage-fill-med';
            var resets = win.resets_at ? new Date(win.resets_at * 1000).toLocaleString() : 'unknown';
            html += '<div class="usage-card"><div class="usage-name">' + escapeHtml(name) + '</div><div class="usage-bar"><div class="usage-fill ' + fillClass + '" style="width:' + (u * 100) + '%"></div></div><div class="usage-val">' + (u * 100).toFixed(1) + '%</div><div class="usage-reset">resets ' + escapeHtml(resets) + '</div></div>';
          });
        }
        if (status.resets_at) html += '<div class="usage-card"><div class="usage-name">Next Reset</div><div class="usage-val">' + new Date(status.resets_at * 1000).toLocaleString() + '</div></div>';
        html += '</div>';
        el.innerHTML = html;
      }).catch(function(e) { console.error('loadStatus error', e); });
    }

    function refreshAll() {
      loadOverview();
      if (selectedSection === 'tasks') loadTasks();
      if (selectedSection === 'blocked') loadBlocked();
      if (selectedSection === 'runs') { loadRuns(); loadEvents(); }
      if (selectedSection === 'status') loadStatus();
      try { document.getElementById('refresh-time').textContent = new Date().toLocaleTimeString(); } catch(e) {}
    }

    document.addEventListener('DOMContentLoaded', function() {
      var btn = document.getElementById('btn-load-project');
      if (btn) btn.addEventListener('click', function() {
        var sel = document.getElementById('project-select');
        if (!sel.value) { toast('Please select a project from the dropdown'); return; }
        selectProjectPath(sel.value);
      });

      document.querySelectorAll('.nav-item').forEach(function(item) {
        item.addEventListener('click', function() {
          var section = item.getAttribute('data-section');
          if (section) showSection(section);
        });
      });

      document.querySelectorAll('a[data-file]').forEach(function(link) {
        link.addEventListener('click', function(ev) {
          ev.preventDefault();
          var kind = link.getAttribute('data-file');
          viewFile(kind);
        });
      });

      loadProjects();
      setInterval(refreshAll, 3000);
    });
  })();
</script>
</body>
</html>"""


def main(argv=None):
    parser = argparse.ArgumentParser(description='speckit-agents project dashboard')
    parser.add_argument('--project', type=Path, default=Path.cwd(), help='project directory (default: cwd)')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help=f'port to listen on (default: {DEFAULT_PORT})')
    args = parser.parse_args(argv)
    args.project = args.project.resolve()

    DashboardHandler.project = DashboardHandler.started_project = args.project
    DashboardHandler.html = build_html()

    # Security boundary: localhost only, no auth (FR-022). Do not add a --host option.
    server = http.server.ThreadingHTTPServer(('127.0.0.1', args.port), DashboardHandler)
    print('speckit-agents dashboard on http://127.0.0.1:%s (project: %s)' % (args.port, args.project), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\\nstopping dashboard', flush=True)
        server.shutdown()


if __name__ == '__main__':
    main()
