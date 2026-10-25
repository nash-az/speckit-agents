#!/usr/bin/env python3
"""speckit-agents Dashboard — a commercial-grade visualizer for Speckit projects.

A read-only Python stdlib HTTP server that visualizes everything Speckit knows
about a project: specs, plans, tasks, BLOCKED.md, decisions, runs, events,
and supervisor status.

Usage:
    bin/sdd-dashboard.py [--project DIR] [--port PORT]

Endpoints:
    GET /                         -- single-page HTML dashboard
    GET /api/projects             -- discover Speckit projects
    POST /api/project/select      -- { "path": "..." } select and validate a project
    GET /api/project/spec         -- read spec.md
    GET /api/project/plan         -- read plan.md
    GET /api/project/tasks        -- read tasks.md (parsed)
    GET /api/project/blocked      -- read BLOCKED.md
    GET /api/project/decisions    -- read decisions.md
    GET /api/project/config       -- read .sdd/config.toml
    GET /api/runs                 -- list runs with status and counts
    GET /api/runs/<id>/events     -- event feed for one run
    GET /api/runs/<id>/report     -- run report markdown
    GET /api/status               -- supervisor status (usage windows)
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import subprocess
from pathlib import Path

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


def discover_projects():
    """Find Speckit projects in common locations."""
    projects = []
    roots = [Path.home() / "private", Path.home() / "projects", Path.home()]
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for entry in root.iterdir():
                if not entry.is_dir():
                    continue
                rp = str(entry.resolve())
                if rp in seen:
                    continue
                if is_speckit_project(entry):
                    seen.add(rp)
                    projects.append({
                        "path": str(entry),
                        "name": entry.name,
                    })
        except OSError:
            continue
    return projects


def find_file(project, names):
    """Look for one of several candidate files, in common layouts."""
    candidates = []
    for name in names:
        candidates.append(project / name)
        candidates.append(project / "specs" / project.name / name)
        specs_dir = project / "specs"
        if specs_dir.is_dir():
            try:
                for sub in specs_dir.iterdir():
                    if sub.is_dir():
                        candidates.append(sub / name)
            except OSError:
                pass
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
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


def parse_tasks(text):
    """Parse tasks.md into phases with tasks, tags, and held status."""
    if not text:
        return None
    phases = []
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = {"title": line[2:].strip(), "tasks": []}
            phases.append(current)
        elif line.startswith("- [") and current is not None:
            tags = re.findall(r"\[([^\]]+)\]", line)
            m = re.match(r"- \[(.)\] (T\d+[a-z]?)", line)
            task = {
                "id": m.group(2) if m else "?",
                "done": m.group(1) == "x" if m else False,
                "text": line,
                "tags": tags,
                "parallel": "P" in tags,
            }
            current["tasks"].append(task)
    return phases


def parse_blocked(text):
    """Parse BLOCKED.md into entries."""
    if not text:
        return []
    entries = []
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = {"id": line[2:].strip(), "reason": "", "question": "", "status": "open", "answer": "", "tags": []}
            entries.append(current)
        elif current and line.startswith("- status:"):
            current["status"] = line.split(":", 1)[1].strip()
        elif current and line.startswith("- answer:"):
            current["answer"] = line.split(":", 1)[1].strip()
        elif current and line.startswith("- tag:"):
            current["tags"].append(line.split(":", 1)[1].strip())
    return entries


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    project = None
    html = ""

    def log_message(self, format, *args):  # noqa: ANN001, ARG002
        pass

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

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/":
            self._send_html()
            return
        if path == "/api/projects":
            self._send_json(discover_projects())
            return
        proj = DashboardHandler.project
        if path == "/api/project/spec":
            f = find_file(proj, ["spec.md"])
            self._send_text(read_text(f) if f else "", content_type="text/markdown")
            return
        if path == "/api/project/plan":
            f = find_file(proj, ["plan.md"])
            self._send_text(read_text(f) if f else "", content_type="text/markdown")
            return
        if path == "/api/project/tasks":
            f = find_file(proj, ["tasks.md"])
            text = read_text(f) if f else ""
            self._send_json({"text": text, "phases": parse_tasks(text)})
            return
        if path == "/api/project/blocked":
            f = find_file(proj, ["BLOCKED.md"])
            text = read_text(f) if f else ""
            self._send_json({"text": text, "entries": parse_blocked(text)})
            return
        if path == "/api/project/decisions":
            f = find_file(proj, ["decisions.md"])
            self._send_text(read_text(f) if f else "", content_type="text/markdown")
            return
        if path == "/api/project/config":
            f = find_file(proj, [".sdd/config.toml"])
            self._send_text(read_text(f) if f else "", content_type="text/plain")
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
        path = self.path.split("?")[0]
        if path == "/api/project/select":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(body)
            except ValueError:
                data = {}
            new_path = data.get("path")
            if not new_path:
                self._send_json({"error": "path required"}, 400)
                return
            p = Path(new_path).expanduser().resolve()
            if not p.is_dir():
                self._send_json({"error": "not a directory"}, 400)
                return
            if not is_speckit_project(p):
                self._send_json({"error": "not a speckit project (missing .sdd/config.toml or .specify/)"}, 400)
                return
            DashboardHandler.project = p
            self._send_json({"ok": True, "path": str(p)})
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
          var tagsHtml = (e.tags||[]).map(function(tag) { return '<span class="task-tag tag-' + tag.toLowerCase() + '">' + escapeHtml(tag) + '</span>'; }).join('');
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

    DashboardHandler.project = args.project
    DashboardHandler.html = build_html()

    server = http.server.ThreadingHTTPServer(('127.0.0.1', args.port), DashboardHandler)
    print('speckit-agents dashboard on http://127.0.0.1:%s (project: %s)' % (args.port, args.project), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\\nstopping dashboard', flush=True)
        server.shutdown()


if __name__ == '__main__':
    main()
