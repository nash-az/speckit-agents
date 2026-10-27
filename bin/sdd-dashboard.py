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
    GET /api/runs/<id>/tasks/<task>/log -- live transcript of the task's agent (data-model.md §4.6)
    GET /api/runs/<id>/report     -- run report markdown (id must look like 20261027-1431)
    GET /api/status               -- supervisor status (usage windows)
    GET /api/run/current          -- run state: none | running | paused | stale (data-model.md §4.5)
"""

from __future__ import annotations

import argparse
import http.server
import importlib.util
import json
import os
import re
import time
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


TRANSCRIPT_GONE = ("This run's transcript is gone (Claude Code keeps transcripts for "
                   "`cleanupPeriodDays`, default 30); its report is `.sdd/runs/{id}.md`.")


def list_runs(project):
    """Runs are `.sdd/runs/<id>/` directories holding `workflows.json` (data-model.md §4.4),
    newest id first (ids are timestamps). Flat files such as `.sdd/runs/x.jsonl` are ignored."""
    runs_dir = project / ".sdd" / "runs"
    runs = []
    if not runs_dir.is_dir():
        return runs
    entries = [e for e in runs_dir.iterdir()
               if e.is_dir() and RUN_ID_RE.match(e.name) and (e / "workflows.json").is_file()]
    for entry in sorted(entries, key=lambda p: p.name, reverse=True):
        run_id = entry.name
        events = sdd_tasks.run_events(project, run_id)["events"]
        runs.append({
            "id": run_id,
            "events_count": len(events),
            "approved": sum(1 for e in events if e.get("kind") == "task.approved"),
            "blocked": sum(1 for e in events if e.get("kind") in ("task.blocked", "task.failed")),
            # The journal has no interrupted or run.finished events (§3.9); a run is finished
            # once its report is written.
            "interrupted": 0,
            "finished": (runs_dir / (run_id + ".md")).is_file(),
            "stopped": None,
        })
    return runs


def get_events(project, run_id):
    """`run_events()` from sdd-tasks.py (§3.9), or None for an invalid run id. When every listed
    workflow directory is gone and no events remain, `notice` carries the "transcript is gone" text."""
    if not RUN_ID_RE.match(run_id):
        return None
    result = sdd_tasks.run_events(project, run_id)
    if result["missing"] and not result["events"]:
        result["notice"] = TRANSCRIPT_GONE.format(id=run_id)
    return result


def get_report(project, run_id):
    report_file = project / ".sdd" / "runs" / (run_id + ".md")
    return read_text(report_file)


def get_status(project):
    state_dir = STATE_BASE / slug_for(project)
    status_file = state_dir / "status.json"
    return read_json(status_file)


# A run whose journal or agent transcripts changed within this many seconds is `running` (§4.5).
RUNNING_WINDOW_SECONDS = 30 * 60


def _recently_modified(workflow_dir: Path, now: float) -> bool:
    """True when `journal.jsonl` or any `agent-*.jsonl` in the directory changed in the window."""
    for f in [workflow_dir / "journal.jsonl", *workflow_dir.glob("agent-*.jsonl")]:
        try:
            if now - f.stat().st_mtime < RUNNING_WINDOW_SECONDS:
                return True
        except OSError:
            continue
    return False


def current_run(project, now=None):
    """`GET /api/run/current` (data-model.md §4.5, FR-017): the first rule that matches wins.
    1. no `.sdd/run.json` -> none; 2. `.sdd/runs/<run_id>.md` exists -> paused (interrupted, waiting
    to resume); 3. supervisor `status.json` with a `state` other than "stopped" -> paused;
    4. a valid `workflow_dir` whose journal or an agent file changed in the last 30 minutes ->
    running; 5. otherwise stale."""
    now = time.time() if now is None else now
    run_file = project / ".sdd" / "run.json"
    if not run_file.is_file():
        return {"state": "none", "run_id": None, "workflow_dir": None}
    run = read_json(run_file)
    run = run if isinstance(run, dict) else {}
    run_id = run.get("run_id") if isinstance(run.get("run_id"), str) else None
    workflow_dir = run.get("workflow_dir") if isinstance(run.get("workflow_dir"), str) else None
    result = {"run_id": run_id, "workflow_dir": workflow_dir}
    # The run id names a file, so it must look like one (this also rules out path traversal).
    if run_id and RUN_ID_RE.match(run_id) and (project / ".sdd" / "runs" / (run_id + ".md")).is_file():
        return {"state": "paused", **result}
    status = get_status(project)
    if isinstance(status, dict) and status.get("state") != "stopped":
        return {"state": "paused", **result}
    if (workflow_dir and sdd_tasks.valid_workflow_dir(workflow_dir)
            and _recently_modified(Path(workflow_dir), now)):
        return {"state": "running", **result}
    return {"state": "stale", **result}


# Live output (data-model.md §4.6, FR-021).
TASK_ID_RE = re.compile(r"^T\d+[a-z]?$")
LOG_MAX_LINES = 200
LOG_MAX_LINE_CHARS = 2000


def _unavailable(reason):
    return {"available": False, "reason": reason}


def task_log(project, run_id, task_id):
    """`GET /api/runs/<id>/tasks/<task_id>/log` (data-model.md §4.6, FR-021).

    The task's newest event in the run's last workflow directory names its agent; the transcript
    is `<workflow_dir>/agent-<agent_id>.jsonl` (research.md D7). Anything not found gives
    `{"available": false, "reason"}`; the route always answers 200.
    """
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        return _unavailable("invalid run id")
    if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
        return _unavailable("invalid task id")
    run = read_json(project / ".sdd" / "run.json")
    if not isinstance(run, dict) or run.get("run_id") != run_id:
        return _unavailable("run is not active")
    workflow_dir = run.get("workflow_dir")
    if not isinstance(workflow_dir, str) or not sdd_tasks.valid_workflow_dir(workflow_dir):
        return _unavailable("run has no workflow directory")
    feed = sdd_tasks.run_events(project, run_id)
    last = len(feed["workflow_dirs"]) - 1
    event = next((e for e in reversed(feed["events"])
                  if e.get("workflow") == last and e.get("id") == task_id and e.get("agent_id")), None)
    if event is None:
        return _unavailable("no agent for this task in the current workflow")
    agent_id = str(event["agent_id"])
    if not re.match(r"^[A-Za-z0-9_-]+$", agent_id):  # it names a file
        return _unavailable("invalid agent id")
    transcript = Path(workflow_dir) / f"agent-{agent_id}.jsonl"
    try:
        with transcript.open(encoding="utf-8", errors="replace") as f:
            lines = [line.rstrip("\n") for line in f]
    except OSError:
        return _unavailable("transcript not found")
    return {"available": True, "label": event.get("label"),
            "lines": [line[:LOG_MAX_LINE_CHARS] for line in lines[-LOG_MAX_LINES:]]}


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
    """`GET /api/project/tasks`: {"text", "phases", "max_retries"} with §2 tasks and §1 phase rollups.
    `max_retries` (from the config) lets the page label `retrying` as n/max (data-model.md §4.6)."""
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
    max_retries = config.get("run", {}).get("max_retries", 2)
    if not isinstance(max_retries, int) or isinstance(max_retries, bool):
        max_retries = 2
    return {"text": text, "phases": phases, "max_retries": max_retries}


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
            events = get_events(proj, m.group(1))
            if events is None:
                self._send_json({"error": "not found"}, 404)
                return
            self._send_json(events)
            return
        m = re.match(r"^/api/runs/([^/]+)/tasks/([^/]+)/log$", path)
        if m:
            self._send_json(task_log(proj, m.group(1), m.group(2)))
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
        if path == "/api/run/current":
            self._send_json(current_run(proj))
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
<!-- Document rendering (data-model.md §4.3): pinned browser builds from jsDelivr -->
<link href="https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/styles/github-dark.min.css" rel="stylesheet">
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
  .markdown pre code.hljs { padding: 0; background: transparent; }
  .markdown a { color: var(--accent); }
  .doc-toc { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.75rem 1rem; margin-bottom: 1rem; font-size: 0.875rem; }
  .doc-toc-title { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); font-weight: 600; margin-bottom: 0.375rem; }
  .doc-toc ul { list-style: none; margin: 0; }
  .doc-toc li { margin: 0.125rem 0; }
  .doc-toc li.toc-h3 { padding-left: 1rem; }
  .doc-notice { font-size: 0.8125rem; color: var(--warning); margin-bottom: 0.5rem; }
  .stale-banner { background: rgba(239,68,68,0.15); border: 1px solid var(--danger); color: var(--danger); border-radius: 6px; padding: 0.75rem 1rem; margin-bottom: 1rem; font-size: 0.875rem; }
  .stale-banner code { font-family: 'JetBrains Mono', monospace; }
  .doc-plain { white-space: pre-wrap; font-family: 'JetBrains Mono', monospace; font-size: 0.875rem; color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 1rem; }
  .raw-doc { margin-top: 0.75rem; }

  /* Task board */
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
  .task-done { opacity: 0.6; }
  .task-done .task-text { text-decoration: line-through; }
  .task-head { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; margin-bottom: 0.25rem; }
  .task-head .task-id { margin-bottom: 0; }
  .phase-heading { display: flex; align-items: center; gap: 0.75rem; font-size: 0.8125rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); margin-bottom: 0.5rem; }
  .phase-heading .status { text-transform: none; letter-spacing: normal; }
  .phase-tasks { display: grid; gap: 0.5rem; margin-bottom: 1rem; }
  /* Live output of a running task (data-model.md §4.6) */
  .live-output { margin-top: 0.5rem; border-top: 1px solid var(--border); padding-top: 0.5rem; cursor: default; }
  .live-output-title { font-size: 0.6875rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); font-weight: 600; margin-bottom: 0.25rem; }
  .live-output-empty { font-size: 0.8125rem; color: var(--text-muted); }
  .live-output-lines { max-height: 240px; overflow: auto; background: var(--bg); border: 1px solid var(--border); border-radius: 4px; padding: 0.5rem; font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; white-space: pre-wrap; word-break: break-all; color: var(--text); }

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
  .blocked-phase { font-size: 0.75rem; color: var(--text-muted); margin-bottom: 0.25rem; }
  .blocked-answer { font-size: 0.875rem; color: var(--text); padding: 0.5rem; background: var(--bg); border-radius: 4px; border-left: 3px solid var(--success); margin-top: 0.5rem; white-space: pre-wrap; }
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

  /* Feature picker (data-model.md §4.2): shown when a per-feature request returns 409 */
  .feature-picker { border-color: var(--warning); }
  .feature-picker-hint { font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.75rem; }
  .feature-picker-list { display: flex; flex-wrap: wrap; gap: 0.5rem; }
  .feature-picker-list .btn { font-family: 'JetBrains Mono', monospace; }

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
      <div class="nav-item" data-section="research"><span class="icon">🔎</span>Research</div>
      <div class="nav-item" data-section="data-model"><span class="icon">🧩</span>Data model</div>
      <div class="nav-item" data-section="quickstart"><span class="icon">🚀</span>Quickstart</div>
      <div class="nav-item" data-section="tasks"><span class="icon">✅</span>Tasks</div>
      <div class="nav-item" data-section="blocked"><span class="icon">🚫</span>Blocked</div>
      <div class="nav-item" data-section="decisions"><span class="icon">⚖️</span>Decisions</div>
      <div class="nav-item" data-section="constitution"><span class="icon">📜</span>Constitution</div>
      <div class="nav-item" data-section="learnings"><span class="icon">💡</span>Learnings</div>
      <div class="nav-item" data-section="config"><span class="icon">⚙️</span>Config</div>
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
      <!-- Stale run (data-model.md §4.5): run.json is left behind and nothing is running -->
      <div id="stale-banner" class="stale-banner" role="alert" style="display:none;"></div>

      <!-- Feature picker: no active feature (409 from a per-feature request) -->
      <div id="feature-picker" class="card feature-picker" style="display:none;">
        <div class="card-title">Choose a feature</div>
        <div class="feature-picker-hint">This project has several feature folders and none is marked active. Pick the one to show.</div>
        <div id="feature-picker-list" class="feature-picker-list"></div>
      </div>

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
              <div class="stat-label">Waiting on you</div>
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
            <a href="#" data-file="research" class="btn">View research.md</a>
            <a href="#" data-file="data-model" class="btn">View data-model.md</a>
            <a href="#" data-file="quickstart" class="btn">View quickstart.md</a>
            <a href="#" data-file="tasks" class="btn">View tasks.md</a>
            <a href="#" data-file="blocked" class="btn">View BLOCKED.md</a>
            <a href="#" data-file="decisions" class="btn">View decisions.md</a>
            <a href="#" data-file="constitution" class="btn">View constitution.md</a>
            <a href="#" data-file="learnings" class="btn">View .sdd/learnings.md</a>
            <a href="#" data-file="config" class="btn">View .sdd/config.toml</a>
          </div>
        </div>
      </section>

      <!-- Document viewer, shared by every /api/doc name except tasks and blocked -->
      <section id="section-doc" class="section" style="display:none;">
        <div class="card">
          <div class="card-title" id="doc-title">Document</div>
          <div id="doc-content" class="markdown"></div>
        </div>
      </section>

      <!-- Tasks -->
      <section id="section-tasks" class="section" style="display:none;">
        <div class="card">
          <div class="card-title">Waiting on you <span class="badge" id="waiting-count">0</span></div>
          <div id="waiting-list"></div>
        </div>
        <div class="card">
          <div class="card-title">Tasks by Phase</div>
          <div id="tasks-list"></div>
        </div>
        <div class="card">
          <div class="card-title">Raw document <button class="btn" data-raw="tasks">Show tasks.md</button></div>
          <div id="raw-tasks" class="markdown raw-doc" style="display:none;"></div>
        </div>
      </section>

      <!-- Blocked -->
      <section id="section-blocked" class="section" style="display:none;">
        <div class="card-title">BLOCKED.md entries</div>
        <div id="blocked-list"></div>
        <div class="card">
          <div class="card-title">Raw document <button class="btn" data-raw="blocked">Show BLOCKED.md</button></div>
          <div id="raw-blocked" class="markdown raw-doc" style="display:none;"></div>
        </div>
      </section>

      <!-- Runs -->
      <section id="section-runs" class="section" style="display:none;">
        <div class="card-title">Run History</div>
        <div id="runs-list"></div>
        <div class="card" style="margin-top:1rem;">
          <div class="card-title">Event Feed</div>
          <div id="events-view" class="events-list"></div>
        </div>
        <div class="card" id="report-card" style="margin-top:1rem;display:none;">
          <div class="card-title"><span>Run report <span class="mono" id="report-run"></span></span></div>
          <div id="report-view" class="markdown"></div>
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

<!-- Document rendering (data-model.md §4.3). Exact pinned URLs; if any fails to load (offline), docLibs()
     returns null and documents fall back to the plain <pre> view with a one-line notice. -->
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/highlight.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js"></script>
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
        if (res.status === 409) {
          // No active feature: offer the picker, and treat the response as "no data" like any other error.
          return res.json().then(function(d) {
            var features = pickerFeatures(res.status, d);
            if (features) showFeaturePicker(features);
            return null;
          }).catch(function() { return null; });
        }
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

    // Phase rollup (data-model.md §1, FR-004), most urgent first; same list as ROLLUP_ORDER in Python.
    var ROLLUP_ORDER = ['blocked', 'retrying', 'running', 'held', 'paused', 'queued',
                        'moved', 'wontfix', 'superseded', 'done'];

    // Transient states from the selected run's events (data-model.md §4.6): the newest event per
    // task id decides. Returns {taskId: {key, vars}}; a task whose newest event maps to nothing
    // (task.blocked, task.failed, anything else) is left out, so its parked state or queued applies.
    // runState is /api/run/current's state: while `paused`, every transient state shows `paused`.
    function transientStates(events, maxRetries, runState) {
      var newest = {}, rejects = {};
      (events || []).forEach(function(ev) {
        if (!ev || typeof ev.id !== 'string') return;
        newest[ev.id] = ev;
        if (ev.kind === 'task.verified' && ev.verdict === 'reject') rejects[ev.id] = (rejects[ev.id] || 0) + 1;
      });
      var max = (typeof maxRetries === 'number' ? maxRetries : 2) + 1;
      var out = {};
      Object.keys(newest).forEach(function(id) {
        var ev = newest[id], st = null;
        if (ev.kind === 'task.started') st = { key: 'running', vars: { step: 'Implementing' } };
        else if (ev.kind === 'task.implemented') st = { key: 'running', vars: { step: 'Verifying' } };
        else if (ev.kind === 'task.verified' && ev.verdict === 'reject') st = { key: 'retrying', vars: { n: rejects[id] + 1, max: max } };
        else if (ev.kind === 'task.approved') st = { key: 'running', vars: { step: 'Committing' } };
        if (!st) return;
        if (runState === 'paused') st = { key: 'paused', vars: {} };
        out[id] = st;
      });
      return out;
    }

    // Copy of the API's phases with each task's transient state attached (only where the task has no
    // parked status and is not done) and each phase's rollup recomputed over the merged states; the
    // backend's phase `status` only knows parked and done states.
    function mergeTransient(phases, states) {
      states = states || {};
      return (phases || []).map(function(p) {
        var tasks = (p.tasks || []).map(function(t) {
          var copy = {};
          Object.keys(t).forEach(function(k) { copy[k] = t[k]; });
          copy.transient = (!t.status && !t.done && states[t.id]) ? states[t.id] : null;
          return copy;
        });
        var copy = {};
        Object.keys(p).forEach(function(k) { copy[k] = p[k]; });
        copy.tasks = tasks;
        copy.status = phaseRollup(tasks);
        return copy;
      });
    }

    // The first ROLLUP_ORDER key that any task has; null for a phase with no tasks.
    function phaseRollup(tasks) {
      var present = {};
      (tasks || []).forEach(function(t) { present[taskStatusKey(t)] = true; });
      for (var i = 0; i < ROLLUP_ORDER.length; i++) if (present[ROLLUP_ORDER[i]]) return ROLLUP_ORDER[i];
      return null;
    }

    // A task's status key (data-model.md §2): the API's parked key or "done", else the transient
    // state from the event feed (§4.6), else queued.
    function taskStatusKey(t) {
      return t.status || (t.done ? 'done' : (t.transient ? t.transient.key : 'queued'));
    }

    function taskBadge(t) {
      var key = taskStatusKey(t);
      if (key === 'held') return statusBadge(key, { tag: t.status_tag });
      if (key === 'superseded') return statusBadge(key, { by: t.status_tag });
      if (!t.status && !t.done && t.transient) return statusBadge(key, t.transient.vars);
      return statusBadge(key);
    }

    // "Live output" panel on a running task (data-model.md §4.6, FR-021). log is the last
    // /api/runs/<id>/tasks/<task>/log answer, or undefined before the first one.
    function liveOutputBodyHtml(log) {
      if (!log || !log.available) return '<div class="live-output-empty">Not available yet</div>';
      return '<pre class="live-output-lines">' + escapeHtml((log.lines || []).join('\\n')) + '</pre>';
    }

    // The newest log answer per running task id, so the 3 s re-render of the task list keeps the panel's text.
    var liveLogs = {};

    function liveOutputHtml(t, log) {
      return '<div class="live-output"><div class="live-output-title">Live output</div>' +
        '<div class="live-output-body" data-live-task="' + escapeHtml(t.id) + '">' + liveOutputBodyHtml(log) + '</div></div>';
    }

    // Everything waiting on a person, held and blocked together (FR-005).
    function isWaiting(t) {
      var key = taskStatusKey(t);
      return key === 'held' || key === 'blocked';
    }

    // BLOCKED.md entry badges (data-model.md §1).
    function entryBadge(e) {
      if (e.status === 'open') return statusBadge('blocked');
      if (e.status === 'resolved') return statusBadge('queued', { label: 'Answered — runs next' });
      return statusBadge(e.status);
    }

    // Overview counters, from the same statuses as the task cards.
    function countTasks(phases) {
      var c = { total: 0, done: 0, waiting: 0, blocked: 0 };
      (phases || []).forEach(function(p) {
        (p.tasks || []).forEach(function(t) {
          var key = taskStatusKey(t);
          c.total++;
          if (key === 'done') c.done++;
          if (isWaiting(t)) c.waiting++;
          if (key === 'blocked') c.blocked++;
        });
      });
      return c;
    }

    function tagsHtml(tags) {
      return (tags || []).map(function(tag) {
        return '<span class="task-tag tag-' + escapeHtml(String(tag).toLowerCase()) + '">' + escapeHtml(tag) + '</span>';
      }).join('');
    }

    function taskCardHtml(t) {
      return '<div class="task-card' + (taskStatusKey(t) === 'done' ? ' task-done' : '') + '">' +
        '<div class="task-head"><span class="task-id">' + escapeHtml(t.id) + '</span>' + taskBadge(t) + '</div>' +
        '<div class="task-text">' + escapeHtml(t.text) + '</div>' +
        '<div class="task-tags">' + tagsHtml(t.tags) + '</div>' +
        (taskStatusKey(t) === 'running' ? liveOutputHtml(t, liveLogs[t.id]) : '') + '</div>';
    }

    function phaseHeadingHtml(p) {
      return '<div class="phase-heading"><span>' + escapeHtml(p.title) + '</span>' +
        (p.status ? statusBadge(p.status) : '') + '</div>';
    }

    function waitingItemHtml(t, phaseTitle) {
      var ask = taskStatusKey(t) === 'blocked' ? t.question : null;
      return '<div class="blocked-item">' +
        '<div class="blocked-header"><div class="blocked-id">' + escapeHtml(t.id) + '</div>' + taskBadge(t) + '</div>' +
        (phaseTitle ? '<div class="blocked-phase">' + escapeHtml(phaseTitle) + '</div>' : '') +
        '<div class="task-text">' + escapeHtml(t.text) + '</div>' +
        (t.status_reason ? '<div class="blocked-reason">' + escapeHtml(t.status_reason) + '</div>' : '') +
        (ask ? '<div class="blocked-question">' + escapeHtml(ask) + '</div>' : '') +
        '</div>';
    }

    function entryItemHtml(e) {
      return '<div class="blocked-item">' +
        '<div class="blocked-header"><div class="blocked-id">' + escapeHtml(e.id) + '</div>' + entryBadge(e) + '</div>' +
        (e.reason ? '<div class="blocked-reason">' + escapeHtml(e.reason) + '</div>' : '') +
        (e.question ? '<div class="blocked-question">' + escapeHtml(e.question) + '</div>' : '') +
        (e.answer ? '<div class="blocked-answer">' + escapeHtml(e.answer) + '</div>' : '') +
        '<div class="blocked-tags">' + tagsHtml(e.tag ? [e.tag] : []) + '</div></div>';
    }

    // Documents (data-model.md §4.3): every /api/doc name. tasks and blocked keep their structured views
    // and show the document only under a "Raw document" toggle; the others open in the shared doc view.
    var DOC_TITLES = {
      spec: 'Spec', plan: 'Plan', research: 'Research', 'data-model': 'Data model', quickstart: 'Quickstart',
      decisions: 'Decisions', constitution: 'Constitution', learnings: 'Learnings', config: 'Config',
      tasks: 'tasks.md', blocked: 'BLOCKED.md'
    };
    var RAW_DOCS = { tasks: true, blocked: true };
    var DOC_NOTICE = 'The document renderer did not load (offline?), so this is the plain text.';

    // marked, DOMPurify and highlight.js, or null when any of them failed to load.
    function docLibs() {
      var g = typeof window !== 'undefined' ? window : {};
      return g.marked && g.DOMPurify && g.hljs ? { marked: g.marked, DOMPurify: g.DOMPurify, hljs: g.hljs } : null;
    }

    function slugify(text) {
      return String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    }

    // Table of contents for h2/h3 headings ([{level: 'h2', text}]): null below 4 headings, else
    // {ids, html} with one unique id per heading (prefixed so it cannot clash with the page's own ids).
    function tocFor(headings, prefix) {
      if (headings.length < 4) return null;
      var seen = {};
      var ids = headings.map(function(h) {
        var base = prefix + '-' + (slugify(h.text) || 'section'), id = base, n = 2;
        while (seen[id]) id = base + '-' + (n++);
        seen[id] = true;
        return id;
      });
      var items = headings.map(function(h, i) {
        return '<li class="toc-' + escapeHtml(h.level) + '"><a href="#' + escapeHtml(ids[i]) + '">' + escapeHtml(h.text) + '</a></li>';
      }).join('');
      return { ids: ids, html: '<nav class="doc-toc"><div class="doc-toc-title">Contents</div><ul>' + items + '</ul></nav>' };
    }

    // The old plain view, used when a library is missing; the notice is one line above it.
    function fallbackHtml(text, notice) {
      return (notice ? '<div class="doc-notice">' + escapeHtml(notice) + '</div>' : '') +
        '<pre class="doc-plain">' + escapeHtml(text) + '</pre>';
    }

    function docMessageHtml(msg) {
      return '<div class="empty"><div>' + escapeHtml(msg) + '</div></div>';
    }

    // Render text into el: sanitized markdown (agents write these files, so never unsanitized HTML),
    // highlighted code blocks and a table of contents. kind 'text' (config) is one highlighted code block.
    function renderDoc(el, text, kind, libs) {
      if (libs === undefined) libs = docLibs();
      if (!libs) { el.innerHTML = fallbackHtml(text, DOC_NOTICE); return; }
      try {
        if (kind === 'text') {
          el.innerHTML = '<pre><code class="language-ini">' + escapeHtml(text) + '</code></pre>';
        } else {
          el.innerHTML = libs.DOMPurify.sanitize(libs.marked.parse(text));
        }
        el.querySelectorAll('pre code').forEach(function(code) { libs.hljs.highlightElement(code); });
        var hs = Array.prototype.slice.call(el.querySelectorAll('h2, h3'));
        var toc = tocFor(hs.map(function(h) { return { level: h.tagName.toLowerCase(), text: h.textContent }; }), el.id || 'doc');
        if (toc) {
          hs.forEach(function(h, i) { h.id = toc.ids[i]; });
          el.insertAdjacentHTML('afterbegin', toc.html);
        }
      } catch (e) {
        console.error('renderDoc error', e);
        el.innerHTML = fallbackHtml(text, DOC_NOTICE);
      }
    }

    // GET a document: {text} on success, the JSON body plus its status otherwise
    // ({"missing": true} for a file not written yet, 409 for no active feature, 404 for no such thing).
    function fetchDoc(url) {
      return fetch(url).then(function(res) {
        var type = res.headers.get('Content-Type') || '';
        if (type.indexOf('application/json') === 0) {
          return res.json().then(function(d) { d = d || {}; d.status = res.status; return d; });
        }
        if (!res.ok) return { error: 'HTTP ' + res.status, status: res.status };
        return res.text().then(function(text) { return { text: text }; });
      }).catch(function(e) { return { error: String(e) }; });
    }

    // Show the document at url in el; a slower earlier request never overwrites a newer one.
    function showDoc(el, url, kind) {
      el.setAttribute('data-want', url);
      return fetchDoc(url).then(function(d) {
        if (el.getAttribute('data-want') !== url) return;
        if (d.missing) el.innerHTML = docMessageHtml('Not written yet');
        else if (d.text != null) renderDoc(el, d.text, kind);
        else if (d.status === 409) {
          el.innerHTML = docMessageHtml('Choose a feature first.');
          var features = pickerFeatures(d.status, d);
          if (features) showFeaturePicker(features);
        }
        else if (d.status === 404) el.innerHTML = docMessageHtml('Not found.');
        else el.innerHTML = docMessageHtml('Could not load: ' + (d.error || 'unknown error'));
      });
    }

    function docUrl(name) {
      return '/api/doc?name=' + encodeURIComponent(name);
    }

    function docKind(name) {
      return name === 'config' ? 'text' : 'markdown';
    }

    // Feature picker (data-model.md §4.2). A 409 body is {"error": "choose a feature", "features": [...]};
    // pickerFeatures() returns that list for a 409, else null.
    function pickerFeatures(status, body) {
      if (status !== 409 || !body || !Array.isArray(body.features)) return null;
      return body.features.map(String);
    }

    function featurePickerHtml(features) {
      if (!features.length) return '<div class="empty"><div>No feature folders under specs/.</div></div>';
      return features.map(function(name) {
        return '<button class="btn" data-feature="' + escapeHtml(name) + '">' + escapeHtml(name) + '</button>';
      }).join('');
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
        hideFeaturePicker();
        document.getElementById('page-title').textContent = path.split('/').pop();
        toast('Project loaded: ' + path.split('/').pop());
        refreshAll();
      }).catch(function(e) { console.error('selectProjectPath error', e); toast('Error selecting project'); });
    }

    // Show the picker with these feature names. The 3 s refresh calls this again on every 409,
    // so the buttons are rebuilt only when the list changes.
    function showFeaturePicker(features) {
      var box = document.getElementById('feature-picker');
      var list = document.getElementById('feature-picker-list');
      if (!box || !list) return;
      var key = JSON.stringify(features);
      if (list.getAttribute('data-features') !== key) {
        list.setAttribute('data-features', key);
        list.innerHTML = featurePickerHtml(features);
        list.querySelectorAll('button[data-feature]').forEach(function(btn) {
          btn.addEventListener('click', function() { chooseFeature(btn.getAttribute('data-feature')); });
        });
      }
      box.style.display = 'block';
    }

    function hideFeaturePicker() {
      var box = document.getElementById('feature-picker');
      var list = document.getElementById('feature-picker-list');
      if (box) box.style.display = 'none';
      if (list) list.removeAttribute('data-features');
    }

    // Choose a feature for this dashboard session (memory only, FR-027), then reload the views.
    function chooseFeature(name) {
      fetchJSON('/api/project/select', 'POST', { feature: name }).then(function(result) {
        if (!result || !result.ok) { toast('Failed to choose feature ' + name); return; }
        hideFeaturePicker();
        toast('Feature: ' + name);
        reloadViews();
      }).catch(function(e) { console.error('chooseFeature error', e); toast('Error choosing feature'); });
    }

    // Reload the current section, the overview counters and any open raw document.
    function reloadViews() {
      showSection(selectedSection);
      if (selectedSection !== 'overview') loadOverview();
      Object.keys(RAW_DOCS).forEach(function(name) {
        var el = document.getElementById('raw-' + name);
        if (el && el.style.display !== 'none') showDoc(el, docUrl(name), 'markdown');
      });
      try { document.getElementById('refresh-time').textContent = new Date().toLocaleTimeString(); } catch(e) {}
    }

    function showSection(name) {
      try {
        document.querySelectorAll('.nav-item').forEach(function(el) { el.classList.remove('active'); });
        var target = document.querySelector('.nav-item[data-section="' + name + '"]');
        if (target) target.classList.add('active');
        document.querySelectorAll('section').forEach(function(el) { el.style.display = 'none'; });
        var isDoc = DOC_TITLES[name] && !RAW_DOCS[name];
        var sec = document.getElementById(isDoc ? 'section-doc' : 'section-' + name);
        if (sec) sec.style.display = 'block';
        document.getElementById('page-title').textContent = isDoc ? DOC_TITLES[name] : name.charAt(0).toUpperCase() + name.slice(1);
        selectedSection = name;
        if (name === 'overview') loadOverview();
        if (name === 'tasks') loadTasks();
        if (name === 'blocked') loadBlocked();
        if (name === 'runs') loadRuns();
        if (name === 'status') loadStatus();
        if (isDoc) loadDoc(name);
      } catch(e) { console.error('showSection error', e); }
    }

    function loadDoc(name) {
      document.getElementById('doc-title').textContent = DOC_TITLES[name];
      var el = document.getElementById('doc-content');
      if (el.getAttribute('data-want') !== docUrl(name)) el.innerHTML = '';
      showDoc(el, docUrl(name), docKind(name));
    }

    // "Raw document" toggle under the Tasks and Blocked views.
    function setRawDoc(name, open) {
      var el = document.getElementById('raw-' + name);
      var btn = document.querySelector('button[data-raw="' + name + '"]');
      if (!el) return;
      el.style.display = open ? 'block' : 'none';
      if (btn) btn.textContent = (open ? 'Hide ' : 'Show ') + DOC_TITLES[name];
      if (open) showDoc(el, docUrl(name), 'markdown');
    }

    function loadOverview() {
      loadTasksData();
      fetchJSON('/api/runs', 'GET').then(function(runs) {
        runs = runs || [];
        document.getElementById('stat-runs').textContent = runs.length;
      });
    }

    function loadTasksData() {
      fetchJSON('/api/project/tasks', 'GET').then(function(data) {
        data = data || {};
        var c = countTasks(data.phases);
        document.getElementById('stat-tasks').textContent = c.total;
        document.getElementById('stat-done').textContent = c.done;
        document.getElementById('stat-held').textContent = c.waiting;
        document.getElementById('stat-blocked').textContent = c.blocked;
      }).catch(function(e) { console.error('loadTasksData error', e); });
    }

    // The run whose events give the transient states: the run picked in the Runs view, else the
    // run in .sdd/run.json. `paused` (§4.5) applies only when that run is the current one.
    function loadTasks() {
      Promise.all([fetchJSON('/api/project/tasks', 'GET'), fetchJSON('/api/run/current', 'GET')]).then(function(r) {
        var data = r[0] || {}, run = r[1] || {};
        var runId = selectedRunId || (run.state && run.state !== 'none' ? run.run_id : null);
        var feed = runId ? fetchJSON('/api/runs/' + encodeURIComponent(runId) + '/events', 'GET') : Promise.resolve(null);
        return feed.then(function(f) {
          var states = transientStates(f && f.events, data.max_retries, runId === run.run_id ? run.state : null);
          renderTasks(mergeTransient(data.phases, states), runId);
        });
      }).catch(function(e) { console.error('loadTasks error', e); });
    }

    function renderTasks(phases, runId) {
      var waiting = '', count = 0, list = '', running = [];
      phases.forEach(function(p) {
        (p.tasks || []).forEach(function(t) {
          if (isWaiting(t)) { waiting += waitingItemHtml(t, p.title); count++; }
          if (taskStatusKey(t) === 'running') running.push(t.id);
        });
        list += phaseHeadingHtml(p) + '<div class="phase-tasks">' + (p.tasks || []).map(function(t) { return taskCardHtml(t); }).join('') + '</div>';
      });
      document.getElementById('waiting-count').textContent = count;
      document.getElementById('waiting-list').innerHTML = waiting ||
        '<div class="empty"><div>Nothing is waiting on you.</div></div>';
      document.getElementById('tasks-list').innerHTML = list ||
        '<div class="empty"><div>No tasks.</div></div>';
      pollLiveOutputs(runId, running);
    }

    // Fetch the live output of each running task; loadTasks runs every 3 s (refreshAll), so this
    // polls /api/runs/<id>/tasks/<task>/log every 3 s while the Tasks view is open.
    function pollLiveOutputs(runId, ids) {
      Object.keys(liveLogs).forEach(function(id) { if (ids.indexOf(id) < 0) delete liveLogs[id]; });
      if (!runId) return;
      ids.forEach(function(id) {
        fetchJSON('/api/runs/' + encodeURIComponent(runId) + '/tasks/' + encodeURIComponent(id) + '/log', 'GET').then(function(log) {
          liveLogs[id] = log || { available: false, reason: 'request failed' };
          var el = document.querySelector('.live-output-body[data-live-task="' + id + '"]');
          if (!el) return;
          el.innerHTML = liveOutputBodyHtml(liveLogs[id]);
          var pre = el.querySelector('pre');
          if (pre) pre.scrollTop = pre.scrollHeight;
        });
      });
    }

    function loadBlocked() {
      fetchJSON('/api/project/blocked', 'GET').then(function(data) {
        data = data || {};
        var entries = data.entries || [];
        var el = document.getElementById('blocked-list');
        if (entries.length === 0) {
          el.innerHTML = '<div class="empty"><div class="empty-icon">✅</div><div>No BLOCKED.md entries.</div></div>';
          return;
        }
        el.innerHTML = entries.map(entryItemHtml).join('');
      }).catch(function(e) { console.error('loadBlocked error', e); });
    }

    function viewFile(kind) {
      if (!DOC_TITLES[kind]) return;
      showSection(kind);
      if (RAW_DOCS[kind]) setRawDoc(kind, true);
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
      fetchJSON('/api/runs/' + encodeURIComponent(selectedRunId) + '/events', 'GET').then(function(data) {
        var events = (data && data.events) || [];
        var el = document.getElementById('events-view');
        el.innerHTML = '';
        if (data && data.notice) {
          el.innerHTML = '<div class="empty"><div class="empty-icon">📭</div><div class="transcript-gone">' + escapeHtml(data.notice) + '</div></div>';
          return;
        }
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

    // Run reports render like every other document (data-model.md §4.3).
    function viewReport(runId) {
      document.getElementById('report-card').style.display = 'block';
      document.getElementById('report-run').textContent = '#' + runId;
      var el = document.getElementById('report-view');
      el.innerHTML = '';
      showDoc(el, '/api/runs/' + encodeURIComponent(runId) + '/report', 'markdown');
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

    // Run state (data-model.md §4.5): `stale` shows the red banner on every page.
    function staleBannerHtml(runId) {
      return 'Run ' + escapeHtml(runId || '(unknown)') + ' left <code>.sdd/run.json</code> behind and is not running. ' +
        'Start <code>/sdd-run</code> to resume or clean it up.';
    }

    function loadRunState() {
      fetchJSON('/api/run/current', 'GET').then(function(run) {
        var el = document.getElementById('stale-banner');
        if (run && run.state === 'stale') {
          el.innerHTML = staleBannerHtml(run.run_id);
          el.style.display = '';
        } else {
          el.innerHTML = '';
          el.style.display = 'none';
        }
      }).catch(function(e) { console.error('loadRunState error', e); });
    }

    function refreshAll() {
      loadRunState();
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

      document.querySelectorAll('button[data-raw]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          var name = btn.getAttribute('data-raw');
          var el = document.getElementById('raw-' + name);
          setRawDoc(name, !el || el.style.display === 'none');
        });
      });

      loadProjects();
      loadRunState();
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
