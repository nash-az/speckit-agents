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
    GET /api/doc?name=<name>      -- one document from a fixed name set (missing -> {"missing": true});
                                     &feature=<folder under specs/> reads a feature document from that feature
    GET /api/project/spec         -- alias of /api/doc?name=spec
    GET /api/project/plan         -- alias of /api/doc?name=plan
    GET /api/project/tasks        -- read tasks.md (parsed)
    GET /api/project/blocked      -- read BLOCKED.md
    GET /api/project/decisions    -- alias of /api/doc?name=decisions
    GET /api/project/config       -- alias of /api/doc?name=config
    GET /api/runs                 -- list runs with status, counts and the tasks each touched
    GET /api/runs/tasks           -- {task id: [{run, outcome, commits, job, title}]}, newest run first
    GET /api/runs/<id>/events     -- event feed for one run
    GET /api/runs/<id>/tasks/<task>/log -- live transcript of the task's agent (data-model.md §4.6)
    GET /api/runs/<id>/report     -- run report markdown (id must look like 20261027-1431)
    GET /api/status               -- supervisor status (usage windows)
    GET /api/run/current          -- run state: none | running | paused | stale, plus `controls`
                                     (data-model.md §4.5, §12.3) and, with an open Job, `job` as
                                     /api/jobs gives it (005 FR-012: the banner's words)
    POST /api/run/stop            -- { "mode"?: "after-task" | "now" } ask the supervisor to stop
    POST /api/run/resume          -- ask a held supervisor to start the next cycle
    POST /api/run/hold            -- while waiting for a usage window: stay paused after the reset
    POST /api/run/release         -- undo hold
    GET /api/decisions            -- open [decision] tasks, with answers already given (FR-033)
    POST /api/decisions/answer    -- { "task", "answer" } save a decision answer and apply it (sdd-decide.py)
    POST /api/decisions/apply     -- { "task" } try again to apply a saved answer
    POST /api/manual/done|skip    -- { "task", "note" } settle an open [manual] task (sdd-manual.py, FR-036)
    POST /api/blocked/done|answer|drop -- { "task", "text" } settle an open BLOCKED.md entry (sdd-unblock.py, FR-037)
    GET /api/run/plan             -- what a run would do and whether it can start now (FR-034)
    POST /api/run/start           -- { "phases"?, "no_converge"?, "max_tasks"? } start a supervised run
    GET /api/features             -- the feature folders under specs/ and the active one
    GET /api/doc/state?name=      -- spec | constitution: its hash, whether it has uncommitted edits, why editing is off
    POST /api/doc/save            -- { "name", "text", "base" } replace spec.md or the constitution (sdd-edit.py, FR-035)
    POST /api/doc/commit          -- { "name", "message" } commit just that file
    GET /api/jobs                 -- {"jobs": [record + shown + progress + in_flight + tasks_done, ...]},
                                     open runs first, then newest (009; the route keeps 005's name)
    GET /api/jobs/<id>            -- one run plus its sessions as /api/runs gives them (404 for a bad or unknown id)
    GET /api/jobs/plan            -- Start run preview: {phases, runnable, held, can_start, reason}
    POST /api/jobs/start          -- { "phases"?, "tasks"?, "max_tasks"?, "until"?, "max_runs"?, "title"? }
                                     start a run (sdd-launch.py launch_job)
    POST /api/jobs/<id>/continue  -- { "max_tasks"?, "until"?, "max_runs"? } (number or null) continue a suspended run
    POST /api/jobs/<id>/merge     -- {} merge a 005 Job's branch into its base (sdd-job.py merge)
    POST /api/jobs/<id>/land      -- {} land an ended run on its base (sdd-job.py land; Merge and Land in the page)
    POST /api/jobs/<id>/drop      -- {} drop a suspended run (sdd-job.py drop_suspended), else a 005 Job (drop)
Every POST needs an allowed Origin and `Content-Type: application/json` (data-model.md §4.9).
The control routes write one request to `control.json`, and the answer route one file per answer in
`answers/` of the supervisor's state directory, both outside the project. Applying an answer calls
`sdd-decide.py`, which commits the decision files (FR-033), starting a run calls `sdd-launch.py`
(FR-034), and the job routes call `sdd-launch.py` and `sdd-job.py` (005; a refusal is
`409 {"error"}`); nothing else is ever written (§4.8).
"""

from __future__ import annotations

import argparse
import http.server
import importlib.util
import json
import os
import re
import threading
import time
import urllib.parse
from pathlib import Path

# The read models live in bin/sdd-views.py (008 data-model §14). All task state comes from
# bin/sdd-tasks.py, so the CLI and the dashboard cannot disagree (data-model.md §4.1); the dashboard
# uses the very module objects sdd-views loaded, so both see the same state (and the same patches in
# tests). The hyphen in the file name prevents a plain import.
_spec = importlib.util.spec_from_file_location("sdd_views", Path(__file__).with_name("sdd-views.py"))
sdd_views = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_views)
sdd_tasks = sdd_views.sdd_tasks
sdd_decide = sdd_views.sdd_decide
sdd_launch = sdd_views.sdd_launch
sdd_job = sdd_views.sdd_job
_spec = importlib.util.spec_from_file_location("sdd_manual", Path(__file__).with_name("sdd-manual.py"))
sdd_manual = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_manual)
_spec = importlib.util.spec_from_file_location("sdd_unblock", Path(__file__).with_name("sdd-unblock.py"))
sdd_unblock = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_unblock)
_spec = importlib.util.spec_from_file_location("sdd_edit", Path(__file__).with_name("sdd-edit.py"))
sdd_edit = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_edit)
# Run controls and the one writer of control.json (008 data-model §5).
_spec = importlib.util.spec_from_file_location("sdd_control", Path(__file__).with_name("sdd-control.py"))
sdd_control = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_control)

DEFAULT_PORT = 8765


def logs_dir(project: Path) -> Path:
    """The supervisor's state directory for the project (outside the project)."""
    return sdd_tasks.state_dir(project)


# The read models (008 data-model §14), moved unchanged to bin/sdd-views.py; the handler and the
# tests use them under their old names.
RUN_ID_RE = sdd_views.RUN_ID_RE
TASK_ID_RE = sdd_views.TASK_ID_RE
read_text = sdd_views.read_text
read_json = sdd_views.read_json
read_lines = sdd_views.read_lines
feature_names = sdd_views.feature_names
active_feature = sdd_views.active_feature
TRANSCRIPT_GONE = sdd_views.TRANSCRIPT_GONE
run_tasks = sdd_views.run_tasks
CREATED_RE = sdd_views.CREATED_RE
run_feature = sdd_views.run_feature
runs_by_task = sdd_views.runs_by_task
list_runs = sdd_views.list_runs
job_view = sdd_views.job_view
list_job_views = sdd_views.list_job_views
get_job = sdd_views.get_job
job_plan = sdd_views.job_plan
open_job_view = sdd_views.open_job_view
last_job_view = sdd_views.last_job_view
get_events = sdd_views.get_events
get_report = sdd_views.get_report
ANSWER_MAX_CHARS = sdd_views.ANSWER_MAX_CHARS
answers_dir = sdd_views.answers_dir
read_answers = sdd_views.read_answers
open_decisions = sdd_views.open_decisions
write_answer = sdd_views.write_answer
try_apply = sdd_views.try_apply
LOG_MAX_LINES = sdd_views.LOG_MAX_LINES
LOG_MAX_LINE_CHARS = sdd_views.LOG_MAX_LINE_CHARS
_unavailable = sdd_views._unavailable
task_log = sdd_views.task_log
_newest_agent = sdd_views._newest_agent
TRANSCRIPT_NOISE = sdd_views.TRANSCRIPT_NOISE
TOOL_ARG_KEYS = sdd_views.TOOL_ARG_KEYS
_block_text = sdd_views._block_text
_readable_line = sdd_views._readable_line
ROLLUP_ORDER = sdd_views.ROLLUP_ORDER
PERSON_HOLD_TAGS = sdd_views.PERSON_HOLD_TAGS
task_status = sdd_views.task_status
rollup = sdd_views.rollup
project_tasks = sdd_views.project_tasks
project_blocked = sdd_views.project_blocked
JOB_LIMITS = sdd_views.JOB_LIMITS
_whole_or_none = sdd_views._whole_or_none
_number_or_none = sdd_views._number_or_none
_id_list = sdd_views._id_list
job_limits_from = sdd_views.job_limits_from


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


# The supervisor's status and its liveness probe live in sdd-tasks.py (008 data-model §3).
get_status = sdd_tasks.get_status
supervisor_alive = sdd_tasks.supervisor_alive


STOP_MODES = sdd_control.STOP_MODES


def run_controls(project) -> dict:
    """`sdd-control.py` `controls()` (008 data-model §5) without `adopt` and `confirm_closed`: this
    dashboard has no take-over route, so its `controls` keep the 003 §12.3 keys."""
    controls = sdd_control.controls(project)
    return {key: controls[key] for key in ("stop", "resume", "hold", "release", "reason")}


def run_state(project, now=None):
    """`GET /api/run/current`: current_run() plus `controls`, `by` (held) and `stopping`."""
    result = current_run(project, now)
    status = get_status(project)
    result["controls"] = run_controls(project)
    state = status.get("state") if isinstance(status, dict) else None
    if state == "held":
        result["by"] = "operator"
    if state == "stopping":
        result["stopping"] = True
    job = open_job_view(project)
    if job is None and result.get("state") == "none":
        job = last_job_view(project)  # nothing going: the banner says how the newest run ended (009 FR-022)
    if job is not None:  # only with a run record, so the 003 shape stays as it was without one
        result["job"] = job
    return result


def write_control(logs: Path, command: str, mode: str | None = None):
    """One request for the supervisor in `<logs>/control.json` (data-model.md §4.8, §12.3), written by
    `sdd-control.py` `write_control()` under its lock (008 data-model §5). Returns the request id, or
    False when a request is already pending."""
    return sdd_control.write_control(logs, command, mode)


def current_run(project, now=None):
    """`GET /api/run/current` (003 data-model.md §4.5, FR-017): `sdd-tasks.py` `current_state()`
    (008 data-model §4), the one rule for everyone, without its new keys, so the 003 shape stays."""
    state = sdd_tasks.current_state(project, now)
    return {key: state[key] for key in ("state", "run_id", "workflow_dir")}


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
    "manual": ("feature", "manual.md"),
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

# Run control routes (data-model.md §12.3): path -> the command written to control.json.
CONTROL_ROUTES = {"/api/run/stop": "stop", "/api/run/resume": "resume",
                  "/api/run/hold": "hold", "/api/run/release": "release"}

# `POST /api/jobs/<id>/<action>` (005 contracts/jobs-api.md).
JOB_ACTION_RE = re.compile(r"^/api/jobs/([^/]+)/(continue|merge|land|drop)$")


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    project = None
    started_project = None  # the --project the server was started with; /api/projects lists only it
    feature = None  # a `specs/` folder name chosen in the page; memory only, never written to disk
    html = ""
    port = DEFAULT_PORT  # the dashboard's own port, for the allowed Origins (§4.9)
    allow_ports = ()  # --allow-port: other localhost ports whose pages may POST (the UI server's)

    def log_message(self, format, *args):  # noqa: ANN001, ARG002
        pass

    def _host_allowed(self):
        """False (after sending 421) when the Host header is not a localhost name (§4.7)."""
        if host_without_port(self.headers.get("Host")) in ALLOWED_HOSTS:
            return True
        self._send_json({"error": "misdirected request"}, 421)
        return False

    @classmethod
    def allowed_origins(cls):
        """`http://<localhost name>:<port>` for the dashboard's port and each --allow-port (§4.9)."""
        return {f"http://{name}:{p}" for p in (cls.port, *cls.allow_ports) for name in ALLOWED_HOSTS}

    def _origin(self):
        """The request's Origin when it is allowed, else None."""
        origin = (self.headers.get("Origin") or "").strip()
        return origin if origin in self.allowed_origins() else None

    def _post_allowed(self):
        """§4.9, for every POST after the Host check: an allowed Origin (else 403) and a JSON
        Content-Type, parameters ignored (else 415). False after sending the error."""
        if self._origin() is None:
            self._send_json({"error": "origin not allowed"}, 403)
            return False
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json({"error": "Content-Type must be application/json"}, 415)
            return False
        return True

    def _read_json_body(self):
        """The request body as JSON; an empty body is `{}`. Raises ValueError when it does not parse."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        return json.loads(body) if body.strip() else {}

    def do_OPTIONS(self):  # noqa: N802
        """CORS preflight for the control routes only, and only for an allowed Origin (§4.9)."""
        if not self._host_allowed():
            return
        path = self.path.split("?")[0]
        if path not in CONTROL_ROUTES:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        origin = self._origin()
        if origin is None:
            self._send_json({"error": "origin not allowed"}, 403)
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Vary", "Origin")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _control(self, command):
        """`POST /api/run/<command>` (data-model.md §12.3), after the §4.9 checks: 400 for a bad body
        or `mode`, 409 when the command is not available now or a request is pending (nothing is
        written), else one `control.json` and `202 {"ok": true, "id"}`."""
        cors = {"Access-Control-Allow-Origin": self._origin(), "Vary": "Origin"}
        try:
            data = self._read_json_body()
        except ValueError:
            self._send_json({"error": "body must be JSON"}, 400, cors)
            return
        if not isinstance(data, dict):
            self._send_json({"error": "body must be a JSON object"}, 400, cors)
            return
        mode = None
        if command == "stop":
            mode = data.get("mode", "after-task")
            if mode not in STOP_MODES:
                self._send_json({"error": "unknown mode", "modes": list(STOP_MODES)}, 400, cors)
                return
        # The checks and the write are sdd-control.py's `request()`, as its command line makes them (§5).
        result = sdd_control.request(DashboardHandler.project, command, mode)
        if not result.get("ok"):
            self._send_json({"error": result["error"]}, 409, cors)
            return
        self._send_json({"ok": True, "id": result["id"]}, 202, cors)

    def _answer(self):
        """`POST /api/decisions/answer` `{"task": "T062", "answer": "…"}` (FR-033), after the §4.9
        checks: 400 for a bad body, task id or answer, 404 when the task is not an open decision,
        else one answer file in the state directory and `202 {"ok": true}`. Nothing in the project
        changes; `/sdd-unblock` applies the answer."""
        cors = {"Access-Control-Allow-Origin": self._origin(), "Vary": "Origin"}
        try:
            data = self._read_json_body()
        except ValueError:
            self._send_json({"error": "body must be JSON"}, 400, cors)
            return
        task_id = data.get("task") if isinstance(data, dict) else None
        answer = data.get("answer") if isinstance(data, dict) else None
        if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
            self._send_json({"error": "task must be a task id such as T062"}, 400, cors)
            return
        if not isinstance(answer, str) or not answer.strip() or len(answer) > ANSWER_MAX_CHARS:
            self._send_json({"error": f"answer must be 1 to {ANSWER_MAX_CHARS} characters"}, 400, cors)
            return
        proj = DashboardHandler.project
        feature = self._feature_dir()
        if feature is None:
            self._send_json({"error": "choose a feature", "features": feature_names(proj)}, 409, cors)
            return
        tasks_data = project_tasks(feature / "tasks.md", feature / "BLOCKED.md",
                                   proj / ".sdd" / "config.toml")
        if task_id not in {d["id"] for d in open_decisions(tasks_data, proj)}:
            self._send_json({"error": "not an open decision"}, 404, cors)
            return
        write_answer(proj, task_id, answer.strip())
        outcome = try_apply(proj, feature, task_id, answer.strip())
        self._send_json({"ok": True, **outcome}, 200 if outcome["applied"] else 202, cors)

    def _apply_saved(self):
        """`POST /api/decisions/apply` `{"task": "T062"}`: try again to apply the answer saved for that task."""
        cors = {"Access-Control-Allow-Origin": self._origin(), "Vary": "Origin"}
        try:
            data = self._read_json_body()
        except ValueError:
            data = None
        task_id = data.get("task") if isinstance(data, dict) else None
        if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
            self._send_json({"error": "task must be a task id such as T062"}, 400, cors)
            return
        proj = DashboardHandler.project
        feature = self._feature_dir()
        if feature is None:
            self._send_json({"error": "choose a feature", "features": feature_names(proj)}, 409, cors)
            return
        saved = read_answers(proj).get(task_id)
        if not saved:
            self._send_json({"error": "no saved answer for that task"}, 404, cors)
            return
        outcome = try_apply(proj, feature, task_id, saved["answer"])
        self._send_json({"ok": True, **outcome}, 200 if outcome["applied"] else 202, cors)

    def _start_run(self):
        """`POST /api/run/start` (FR-034), after the §4.9 checks: 400 for a bad body, 409 when the run
        cannot start (the reason is in `error`), else a detached supervised run and `202 {pid, run_args}`."""
        cors = {"Access-Control-Allow-Origin": self._origin(), "Vary": "Origin"}
        try:
            data = self._read_json_body()
        except ValueError:
            self._send_json({"error": "body must be JSON"}, 400, cors)
            return
        if not isinstance(data, dict):
            self._send_json({"error": "body must be a JSON object"}, 400, cors)
            return
        phases, no_converge, max_tasks = data.get("phases", []), data.get("no_converge", True), data.get("max_tasks")
        if (not isinstance(phases, list) or not all(isinstance(p, (str, int)) and not isinstance(p, bool) for p in phases)
                or not isinstance(no_converge, bool)
                or (max_tasks is not None and (not isinstance(max_tasks, int) or isinstance(max_tasks, bool)))):
            self._send_json({"error": "phases must be a list, no_converge a boolean, max_tasks a whole number"}, 400, cors)
            return
        proj = DashboardHandler.project
        feature = self._feature_dir()
        if feature is None:
            self._send_json({"error": "choose a feature", "features": feature_names(proj)}, 409, cors)
            return
        try:
            result = sdd_launch.launch(proj, feature, [str(p) for p in phases], no_converge, max_tasks)
        except sdd_launch.Refused as e:
            self._send_json({"error": str(e)}, 409, cors)
            return
        self._send_json({"ok": True, "pid": result["pid"], "run_args": result["run_args"],
                         "resumes": result["resumes"]}, 202, cors)

    def _job_body(self, cors):
        """The POST body as a JSON object, or None after sending 400."""
        try:
            data = self._read_json_body()
        except ValueError:
            self._send_json({"error": "body must be JSON"}, 400, cors)
            return None
        if not isinstance(data, dict):
            self._send_json({"error": "body must be a JSON object"}, 400, cors)
            return None
        return data

    def _job_start(self):
        """`POST /api/jobs/start` (005 contracts/jobs-api.md), after the §4.9 checks: 400 for a bad body, 409 when
        the launcher refuses (its reason in `error`; nothing changed), else `202 {"ok": true, "job"}`."""
        cors = {"Access-Control-Allow-Origin": self._origin(), "Vary": "Origin"}
        data = self._job_body(cors)
        if data is None:
            return
        phases, tasks, title = data.get("phases", []), data.get("tasks", []), data.get("title")
        limits = job_limits_from(data)
        if (not _id_list(phases) or not _id_list(tasks) or (title is not None and not isinstance(title, str))
                or limits is None):
            self._send_json({"error": "phases and tasks must be lists, title a string, max_tasks and max_runs "
                                      "whole numbers or null, until epoch seconds or null"}, 400, cors)
            return
        proj = DashboardHandler.project
        feature = self._feature_dir()
        if feature is None:
            self._send_json({"error": "choose a feature", "features": feature_names(proj)}, 409, cors)
            return
        scope = {"phases": [str(p) for p in phases], "tasks": [str(t) for t in tasks]}
        try:
            result = sdd_launch.launch_job(proj, feature, scope, limits, title)
        except sdd_launch.Refused as e:
            self._send_json({"error": str(e)}, 409, cors)
            return
        self._send_json({"ok": True, "job": result["job"]}, 202, cors)

    def _job_action(self, job_id, action):
        """`POST /api/jobs/<id>/continue|merge|land|drop` (005 contracts/jobs-api.md, 009 plan "Engine changes"
        6), after the §4.9 checks: 404 for an id not in the run id form or unknown, 400 for a bad body, 409 when
        refused (the reason in `error`; nothing changed, except the `needs-merge` landing a conflict stores).
        Continue answers `202 {"ok": true}`, Merge `200 {"ok": true, "commit"}`, Land `200 {"ok": true,
        "landed", "commit", "fast_forward", "landing"}`, Drop `200 {"ok": true}` (plus `tasks` and `stash`
        for a suspended run, which `drop_suspended` drops)."""
        cors = {"Access-Control-Allow-Origin": self._origin(), "Vary": "Origin"}
        proj = DashboardHandler.project
        if not RUN_ID_RE.match(job_id) or sdd_job.load(proj, job_id) is None:
            self._send_json({"error": "not found"}, 404, cors)
            return
        data = self._job_body(cors)
        if data is None:
            return
        if action == "continue":
            limits = job_limits_from(data)
            if limits is None:
                self._send_json({"error": "max_tasks and max_runs must be whole numbers or null, "
                                          "until epoch seconds or null"}, 400, cors)
                return
            try:
                sdd_launch.continue_job(proj, job_id, limits)
            except sdd_launch.Refused as e:
                self._send_json({"error": str(e)}, 409, cors)
                return
            self._send_json({"ok": True}, 202, cors)
            return
        try:
            if action == "merge":
                result = sdd_job.merge(proj, job_id)
                answer = {"ok": True, "commit": result["commit"]}
            elif action == "land":
                result = sdd_job.land(proj, job_id)
                answer = {"ok": True, **{k: result.get(k) for k in ("landed", "commit", "fast_forward", "landing")}}
            elif sdd_job.shown_state(proj, sdd_job.load(proj, job_id) or {})["state"] == "suspended":
                result = sdd_job.drop_suspended(proj, job_id)
                answer = {"ok": True, "tasks": result.get("tasks"), "stash": result.get("stash")}
            else:
                sdd_job.drop(proj, job_id)
                answer = {"ok": True}
        except sdd_job.Refused as e:
            self._send_json({"error": str(e)}, 409, cors)
            return
        self._send_json(answer, 200, cors)

    def _doc_edit(self, action):
        """`POST /api/doc/save` `{name, text, base}` and `POST /api/doc/commit` `{name, message}` (FR-035), after
        the §4.9 checks: 400 for a bad body, 409 when the edit is refused (the reason is in `error`; nothing
        changed), else `200` with the file's new state. The helper `sdd-edit.py` does the writing."""
        cors = {"Access-Control-Allow-Origin": self._origin(), "Vary": "Origin"}
        try:
            data = self._read_json_body()
        except ValueError:
            data = None
        if not isinstance(data, dict) or data.get("name") not in sdd_edit.EDITABLE:
            self._send_json({"error": "body must be a JSON object naming spec or constitution"}, 400, cors)
            return
        name, proj, feature = data["name"], DashboardHandler.project, self._feature_dir()
        try:
            if action == "save":
                if not isinstance(data.get("text"), str) or not isinstance(data.get("base"), str):
                    self._send_json({"error": "text and base must be strings"}, 400, cors)
                    return
                result = sdd_edit.save(proj, feature, name, data["text"], data["base"])
            else:
                if not isinstance(data.get("message"), str):
                    self._send_json({"error": "message must be a string"}, 400, cors)
                    return
                sdd_edit.commit(proj, feature, name, data["message"])
                result = sdd_edit.state(proj, feature, name)
        except sdd_edit.Refused as e:
            self._send_json({"error": str(e)}, 409, cors)
            return
        self._send_json({"ok": True, **result}, 200, cors)

    def _manual(self, action):
        """`POST /api/manual/done` and `/api/manual/skip` `{task, note}` (FR-036), after the §4.9 checks: 400 for
        a bad body, 409 when the task cannot be settled now (the reason is in `error`; nothing changed), else
        `200 {ok, commit, files}`. The helper `sdd-manual.py` does the writing."""
        cors = {"Access-Control-Allow-Origin": self._origin(), "Vary": "Origin"}
        try:
            data = self._read_json_body()
        except ValueError:
            data = None
        task_id = data.get("task") if isinstance(data, dict) else None
        note = data.get("note", "") if isinstance(data, dict) else None
        if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id) or not isinstance(note, str) \
                or len(note) > ANSWER_MAX_CHARS:
            self._send_json({"error": "task must be a task id such as T067 and note a string"}, 400, cors)
            return
        feature = self._feature_dir()
        if feature is None:
            self._send_json({"error": "choose a feature", "features": feature_names(DashboardHandler.project)}, 409, cors)
            return
        try:
            result = sdd_manual.settle(DashboardHandler.project, feature, task_id, action, note)
        except sdd_manual.Refused as e:
            self._send_json({"error": str(e)}, 409, cors)
            return
        self._send_json({"ok": True, "commit": result["commit"], "files": result["files"]}, 200, cors)

    def _unblock(self, action):
        """`POST /api/blocked/done|answer|drop` `{task, text}` (FR-037), after the §4.9 checks: 400 for a bad body,
        409 when the entry cannot be settled now (the reason is in `error`; nothing changed), else
        `200 {ok, commit, files}`. The helper `sdd-unblock.py` does the writing."""
        cors = {"Access-Control-Allow-Origin": self._origin(), "Vary": "Origin"}
        try:
            data = self._read_json_body()
        except ValueError:
            data = None
        task_id = data.get("task") if isinstance(data, dict) else None
        text = data.get("text", "") if isinstance(data, dict) else None
        if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id) or not isinstance(text, str) \
                or len(text) > ANSWER_MAX_CHARS:
            self._send_json({"error": "task must be a task id such as T053 and text a string"}, 400, cors)
            return
        feature = self._feature_dir()
        if feature is None:
            self._send_json({"error": "choose a feature", "features": feature_names(DashboardHandler.project)}, 409, cors)
            return
        try:
            result = sdd_unblock.settle(DashboardHandler.project, feature, task_id, action, text)
        except sdd_unblock.Refused as e:
            self._send_json({"error": str(e)}, 409, cors)
            return
        self._send_json({"ok": True, "commit": result["commit"], "files": result["files"]}, 200, cors)

    def _feature_dir(self):
        """The person's in-memory choice if one was made, else active_feature()."""
        proj = DashboardHandler.project
        if DashboardHandler.feature:
            return proj / "specs" / DashboardHandler.feature
        return active_feature(proj)

    def _send_json(self, obj, status=200, headers=None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
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

    def _send_doc(self, name, feature_name=None):
        """`GET /api/doc?name=<name>` (data-model.md §4.3): raw text, `200 {"missing": true}` for a
        missing file, 404 for a name outside DOC_NAMES, 409 for a feature document without a feature.
        `feature_name` (a document link in task text) reads that feature's copy instead of the active
        feature's; it must be one of feature_names() and a feature document, else 404."""
        if name not in DOC_NAMES:
            self._send_json({"error": "not found"}, 404)
            return
        scope, rel = DOC_NAMES[name]
        proj = DashboardHandler.project
        if feature_name is not None:
            if scope != "feature" or feature_name not in feature_names(proj):
                self._send_json({"error": "not found"}, 404)
                return
            path = proj / "specs" / feature_name / rel
        elif scope == "feature":
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
            params = urllib.parse.parse_qs(query)
            names, features = params.get("name", []), params.get("feature", [])
            if len(features) > 1:
                self._send_json({"error": "not found"}, 404)
                return
            self._send_doc(names[0] if len(names) == 1 else None, features[0] if features else None)
            return
        if path in DOC_ALIASES:
            self._send_doc(DOC_ALIASES[path])
            return
        if path == "/api/features":
            # active: what this dashboard shows (the person's choice, else what runs use); runs: what
            # `/sdd-run` and the launcher use (`.sdd/config.toml` run.feature, else `.specify/feature.json`).
            feature, runs = self._feature_dir(), active_feature(DashboardHandler.project)
            self._send_json({"features": feature_names(DashboardHandler.project),
                             "active": feature.name if feature else None,
                             "runs": runs.name if runs else None})
            return
        if path == "/api/doc/state":
            names = urllib.parse.parse_qs(query).get("name", [])
            if len(names) != 1 or names[0] not in sdd_edit.EDITABLE:
                self._send_json({"error": "not found"}, 404)
                return
            try:
                self._send_json(sdd_edit.state(DashboardHandler.project, self._feature_dir(), names[0]))
            except sdd_edit.Refused as e:
                self._send_json({"error": str(e)}, 409)
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
        if path == "/api/decisions":
            feature = self._feature_dir()
            if feature is None:
                self._send_json({"error": "choose a feature", "features": feature_names(proj)}, 409)
                return
            tasks_data = project_tasks(feature / "tasks.md", feature / "BLOCKED.md",
                                       proj / ".sdd" / "config.toml")
            self._send_json({"open": open_decisions(tasks_data, proj),
                             "recorded": (feature / "decisions.md").is_file()})
            return
        if path == "/api/runs":
            self._send_json(list_runs(proj))
            return
        if path == "/api/jobs":
            self._send_json({"jobs": list_job_views(proj)})
            return
        if path == "/api/jobs/plan":
            feature = self._feature_dir()
            if feature is None:
                self._send_json({"error": "choose a feature", "features": feature_names(proj)}, 409)
                return
            self._send_json(job_plan(proj, feature))
            return
        m = re.match(r"^/api/jobs/([^/]+)$", path)
        if m:
            job = get_job(proj, m.group(1))
            if job is None:
                self._send_json({"error": "not found"}, 404)
                return
            self._send_json(job)
            return
        if path == "/api/runs/tasks":
            feature = self._feature_dir()
            self._send_json(runs_by_task(proj, feature.name if feature else None))
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
            self._send_json(run_state(proj))
            return
        if path == "/api/run/plan":
            feature = self._feature_dir()
            if feature is None:
                self._send_json({"error": "choose a feature", "features": feature_names(proj)}, 409)
                return
            self._send_json(sdd_launch.preview(proj, feature))
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):  # noqa: N802
        if not self._host_allowed():
            return
        if not self._post_allowed():
            return
        path = self.path.split("?")[0]
        if path in CONTROL_ROUTES:
            self._control(CONTROL_ROUTES[path])
            return
        if path == "/api/decisions/answer":
            self._answer()
            return
        if path == "/api/decisions/apply":
            self._apply_saved()
            return
        if path in ("/api/doc/save", "/api/doc/commit"):
            self._doc_edit(path.rsplit("/", 1)[1])
            return
        if path in ("/api/blocked/done", "/api/blocked/answer", "/api/blocked/drop"):
            self._unblock(path.rsplit("/", 1)[1])
            return
        if path in ("/api/manual/done", "/api/manual/skip"):
            self._manual(path.rsplit("/", 1)[1])
            return
        if path == "/api/run/start":
            self._start_run()
            return
        if path == "/api/jobs/start":
            self._job_start()
            return
        m = JOB_ACTION_RE.match(path)
        if m:
            self._job_action(m.group(1), m.group(2))
            return
        if path == "/api/project/select":
            try:
                data = self._read_json_body()
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
<!-- No web fonts: 'Inter' and 'JetBrains Mono' are used when installed, else the system fallbacks in the CSS. -->
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
  .sidebar { width: 260px; min-width: 0; overflow: hidden; background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; z-index: 10; }
  .main { flex: 1; min-width: 0; display: flex; flex-direction: column; overflow: hidden; }
  .header { height: 56px; background: var(--surface); border-bottom: 1px solid var(--border); display: flex; align-items: center; padding: 0 1.25rem; flex-shrink: 0; }
  .brand { font-weight: 700; font-size: 1.125rem; color: var(--text); display: flex; align-items: center; gap: 0.5rem; }
  .sidebar .brand { height: 56px; padding: 0 1.25rem; border-bottom: 1px solid var(--border); flex-shrink: 0; }
  .brand-dot { width: 8px; height: 8px; background: var(--success); border-radius: 50%; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
  .doc-tabs { display: flex; flex-wrap: wrap; gap: 0.375rem; margin-bottom: 1rem; }
  .doc-tabs:empty { display: none; }
  .doc-tab { padding: 0.375rem 0.875rem; border-radius: 6px; border: 1px solid var(--border); background: var(--bg); color: var(--text-muted); font-size: 0.875rem; cursor: pointer; }
  .doc-tab:hover { color: var(--text); }
  .doc-tab.active { background: rgba(59,130,246,0.1); border-color: var(--accent); color: var(--text); }
  .doc-edit-bar { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; margin-bottom: 0.75rem; }
  .doc-edit-bar:empty { display: none; }
  .header-feature { margin-left: auto; display: flex; align-items: center; gap: 0.5rem; min-width: 0; font-size: 0.8125rem; color: var(--text-muted); }
  .header-feature select { max-width: 16rem; padding: 0.3rem 0.5rem; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; }
  .header-feature-note { color: var(--warning); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  @media (max-width: 768px) { .header-feature label { display: none; } .header-feature select { max-width: 10rem; } }
  .doc-edit-bar select, .doc-edit-bar input[type=text] { padding: 0.375rem 0.625rem; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 0.875rem; }
  .doc-edit-bar input[type=text] { flex: 1; min-width: 14rem; }
  .doc-edit-note { color: var(--text-muted); font-size: 0.8125rem; flex-basis: 100%; }
  .doc-edit-error { color: var(--danger); font-size: 0.8125rem; flex-basis: 100%; }
  .doc-editor { width: 100%; min-height: 60vh; resize: vertical; padding: 0.75rem; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-family: 'JetBrains Mono', 'SF Mono', Monaco, monospace; font-size: 0.8125rem; line-height: 1.5; }
  .doc-editor:focus { outline: none; border-color: var(--accent); }
  .content { flex: 1; overflow-y: auto; padding: 1.5rem; background: var(--bg); overflow-wrap: anywhere; }

  /* The project this dashboard was started in */
  .project-bar { padding: 0.625rem 1.25rem; border-bottom: 1px solid var(--border); flex-shrink: 0; min-width: 0; }
  .project-name { display: block; font-size: 0.875rem; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

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
  .grid-2 { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 1rem; }
  .grid-3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem; }
  @media (max-width: 1024px) { .grid-3 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  @media (max-width: 768px) {
    .grid-2, .grid-3 { grid-template-columns: minmax(0, 1fr); }
    /* Phone and narrow windows: the sidebar becomes a top bar with the nav as one scrolling row. */
    .app { flex-direction: column; }
    .sidebar { width: 100%; height: auto; flex-direction: row; align-items: center; border-right: none; border-bottom: 1px solid var(--border); }
    .sidebar .brand { height: 48px; padding: 0 0.75rem 0 1rem; border-bottom: none; font-size: 1rem; }
    .project-bar { display: none; }
    .nav { display: flex; flex: 1; min-width: 0; overflow-x: auto; overflow-y: hidden; padding: 0; }
    .nav-item { flex-shrink: 0; gap: 0.375rem; padding: 0 0.75rem; height: 48px; font-size: 0.875rem; border-left: none; border-bottom: 3px solid transparent; }
    .nav-item.active { border-bottom-color: var(--accent); }
    .main { flex: 1; min-height: 0; }
    .header { height: 44px; padding: 0 1rem; }
    .content { padding: 1rem; }
  }

  /* Stat */
  .stat { display: flex; flex-direction: column; gap: 0.25rem; }
  /* Overview */
  .ov-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; flex-wrap: wrap; }
  .ov-feature { font-size: 0.8125rem; color: var(--text-muted); }
  .ov-big { font-size: 2.25rem; font-weight: 700; font-family: 'JetBrains Mono', monospace; line-height: 1.1; }
  .ov-big small { font-size: 1rem; color: var(--text-muted); font-weight: 500; }
  .ov-run { display: inline-flex; align-items: center; gap: 0.5rem; padding: 0.375rem 0.75rem; border: 1px solid var(--border); border-radius: 999px; font-size: 0.8125rem; color: var(--text-muted); background: var(--bg); }
  .ov-run .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--st-queued); }
  .ov-run.running .dot { background: var(--st-running); animation: pulse 2s infinite; }
  .ov-run.paused .dot { background: var(--st-held); }
  .ov-run.stale .dot { background: var(--st-blocked); }
  .ov-bar { display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: var(--bg); margin: 1rem 0 0.625rem; }
  .ov-bar > span { display: block; height: 100%; }
  .ov-legend { display: flex; flex-wrap: wrap; gap: 0.375rem 1.25rem; font-size: 0.8125rem; color: var(--text-muted); }
  .ov-legend b { color: var(--text); font-family: 'JetBrains Mono', monospace; font-weight: 600; }
  .ov-legend i { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 0.375rem; }
  .ov-row { display: flex; align-items: center; gap: 0.75rem; padding: 0.5rem 0.625rem; border-radius: 6px; cursor: pointer; font-size: 0.875rem; }
  .ov-row:hover { background: var(--surface-hover); }
  .ov-row .id { font-family: 'JetBrains Mono', monospace; color: var(--text-muted); font-size: 0.8125rem; flex-shrink: 0; min-width: 3.25rem; }
  .ov-row .txt { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ov-row .meta { color: var(--text-muted); font-size: 0.8125rem; flex-shrink: 0; }
  .ov-mini { width: 120px; height: 6px; border-radius: 3px; background: var(--bg); overflow: hidden; flex-shrink: 0; }
  .ov-mini > span { display: block; height: 100%; background: var(--st-done); }
  .ov-ok { color: var(--text-muted); font-size: 0.875rem; padding: 0.5rem 0.625rem; }
  .ov-more { color: var(--text-muted); font-size: 0.8125rem; padding: 0.375rem 0.625rem; }
  .ov-more[data-go] { cursor: pointer; }
  .ov-more[data-go]:hover { color: var(--accent); }
  .ov-group + .ov-group { margin-top: 0.75rem; }
  .ov-group-title { font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); padding: 0 0.625rem 0.25rem; cursor: pointer; }
  .ov-group-title:hover { color: var(--accent); }
  .ov-run-task { border-radius: 6px; cursor: pointer; }
  .ov-run-task:hover { background: var(--surface-hover); }
  .ov-run-task .ov-row:hover { background: none; }
  .ov-run-line { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: var(--text-muted); padding: 0 0.625rem 0.5rem 4.6rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
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
  .run-detail { margin: -0.25rem 0 0.75rem; padding: 0.75rem; border: 1px solid var(--border); border-top: 0; border-radius: 0 0 6px 6px; background: var(--surface); }
  .run-detail > .card-title { margin-bottom: 0.5rem; }
  .run-item:hover { border-color: var(--accent); background: var(--surface-hover); }
  .run-item.active { border-color: var(--accent); background: rgba(59,130,246,0.05); }
  .run-info { flex: 1; }
  .run-id { font-family: 'JetBrains Mono', monospace; font-size: 0.875rem; font-weight: 600; color: var(--text); }
  .run-meta { font-size: 0.8125rem; color: var(--text-muted); margin-top: 0.25rem; }

  /* Empty state */
  .empty { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 200px; color: var(--text-muted); }
  .empty-icon { font-size: 2rem; margin-bottom: 0.75rem; opacity: 0.5; }

  /* Spec / content viewer */
  .markdown { font-size: 0.9375rem; line-height: 1.7; color: var(--text); }
  .markdown h1, .markdown h2, .markdown h3 { color: var(--text); margin: 1.25rem 0 0.5rem; font-weight: 600; }
  .markdown h1 { font-size: 1.375rem; border-bottom: 1px solid var(--border); padding-bottom: 0.5rem; }
  .markdown h2 { font-size: 1.125rem; }
  .markdown .doc-target { outline: 2px solid var(--accent); outline-offset: 4px; border-radius: 4px; }
  a.doc-ref { text-decoration: underline dotted; }
  .manual-steps-fold { margin: 0.5rem 0; border: 1px solid var(--border); border-radius: 6px; padding: 0.5rem 0.75rem; background: var(--bg); }
  .manual-steps-fold > summary { cursor: pointer; font-weight: 600; }
  .manual-steps-fold .markdown ol { padding-left: 1.5rem; }
  .manual-steps-fold .markdown li { margin: 0.25rem 0; }
  .manual-record { margin: 0.5rem 0; font-size: 0.875rem; }
  .manual-steps-none { margin: 0.5rem 0; font-size: 0.8125rem; color: var(--text-muted, #8b93a1); }
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
  /* Run banner (data-model.md §12.4): stop and resume the supervisor */
  .run-banner { display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 0.75rem 1rem; margin-bottom: 1rem; font-size: 0.875rem; }
  .run-banner-text { font-weight: 500; }
  .run-banner-actions { display: flex; gap: 0.5rem; }
  .run-banner .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .run-banner-keep { display: inline-flex; align-items: center; gap: 0.375rem; cursor: pointer; }
  .run-banner-reason, .run-banner-error { color: var(--text-muted); }
  .run-banner-error { color: var(--danger); }
  .run-banner code { font-family: 'JetBrains Mono', monospace; }
  .doc-plain { white-space: pre-wrap; font-family: 'JetBrains Mono', monospace; font-size: 0.875rem; color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 1rem; }
  .raw-doc { margin-top: 0.75rem; }

  /* Task board */
  .task-card { min-width: 0; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 0.75rem; cursor: pointer; transition: all 0.15s; }
  .task-card.log-open { border-color: var(--accent); }
  .task-card:hover { border-color: var(--accent); background: var(--surface-hover); }
  .task-id { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; font-weight: 700; color: var(--accent); margin-bottom: 0.25rem; display: block; }
  .task-not-yet { font-size: 0.8125rem; color: var(--text-muted); margin-bottom: 0.5rem; }
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
  .task-done { opacity: 0.75; }
  .done-fold > summary { cursor: pointer; list-style: none; padding: 0.375rem 0.625rem; border-radius: 6px; color: var(--text-muted); font-size: 0.8125rem; user-select: none; }
  .done-fold > summary::-webkit-details-marker { display: none; }
  .done-fold > summary::before { content: '▸'; display: inline-block; width: 1rem; transition: transform 0.15s; }
  .done-fold[open] > summary::before { transform: rotate(90deg); }
  .done-fold > summary:hover { background: var(--surface-hover); color: var(--text); }
  .done-fold > .done-list { display: grid; grid-template-columns: minmax(0, 1fr); gap: 0.5rem; margin-top: 0.5rem; }
  .task-head { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; margin-bottom: 0.25rem; }
  .task-head .task-id { margin-bottom: 0; }
  .phase-heading { display: flex; align-items: center; gap: 0.75rem; font-size: 0.8125rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); margin-bottom: 0.5rem; }
  .phase-heading .status { text-transform: none; letter-spacing: normal; }
  .phase-tasks { display: grid; grid-template-columns: minmax(0, 1fr); gap: 0.5rem; margin-bottom: 1rem; }
  /* Live output of a running task (data-model.md §4.6) */
  .live-output { margin-top: 0.5rem; border-top: 1px solid var(--border); padding-top: 0.5rem; cursor: default; }
  .live-output-title { font-size: 0.6875rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); font-weight: 600; margin-bottom: 0.25rem; }
  .live-output-empty { font-size: 0.8125rem; color: var(--text-muted); }
  .job-live .live-output-lines { max-height: 320px; }
  .job-live-label { text-transform: none; letter-spacing: 0; font-weight: 400; }
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
  .blocked-question { font-size: 0.875rem; line-height: 1.55; color: var(--text); padding: 0.5rem 0.75rem; background: var(--bg); border-radius: 4px; border-left: 3px solid var(--accent); }
  .blocked-question p { margin: 0 0 0.4rem; }
  .blocked-question p:last-child { margin-bottom: 0; }
  .blocked-question .decision-choices { margin: 0; }
  .blocked-question .decision-choices li { background: var(--surface); display: flex; gap: 0.5rem; }
  .blocked-question code { font-family: 'JetBrains Mono', monospace; font-size: 0.85em; padding: 0.05rem 0.3rem; border-radius: 4px; background: var(--surface); border: 1px solid var(--border); overflow-wrap: anywhere; }
  .blocked-tags { display: flex; gap: 0.25rem; margin-top: 0.5rem; }

  /* Events */
  .events-list { max-height: 400px; overflow-y: auto; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); }
  .event-row { display: flex; padding: 0.625rem 0.875rem; border-bottom: 1px solid var(--border); font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; }
  .event-row:last-child { border-bottom: none; }
  .event-ts { width: 120px; color: var(--text-muted); }
  .event-kind { width: 120px; font-weight: 600; color: var(--accent); }
  .event-payload { color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0; padding-left: 0.75rem; font-family: 'Inter', sans-serif; }
  .event-ts { width: 64px; }
  .run-tasks { display: flex; flex-direction: column; gap: 0.25rem; }
  .run-task { display: flex; align-items: center; gap: 0.6rem; padding: 0.4rem 0.6rem; border: 1px solid var(--border); border-radius: 6px; cursor: pointer; min-width: 0; font-size: 0.8125rem; }
  .run-task:hover { border-color: var(--accent); }
  .run-task.other { cursor: default; }
  .run-task .id { font-family: 'JetBrains Mono', monospace; font-weight: 600; }
  .run-task .txt { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-muted); }
  .run-task .meta { color: var(--text-muted); white-space: nowrap; }
  .run-outcome { font-size: 0.6875rem; font-weight: 600; padding: 0.05rem 0.4rem; border-radius: 999px; border: 1px solid currentColor; white-space: nowrap; }
  .o-committed { color: var(--st-done); } .o-approved { color: var(--st-running); } .o-blocked { color: var(--st-blocked); }
  .o-rejected { color: var(--st-retrying); } .o-interrupted { color: var(--st-paused); } .o-running { color: var(--st-running); }
  .run-chip { display: inline-flex; align-items: center; gap: 0.3rem; margin-left: 0.4rem; font-size: 0.75rem; color: var(--text-muted); cursor: pointer; }
  .run-chip:hover { color: var(--accent); }
  .task-card.flash { box-shadow: 0 0 0 2px var(--accent); }

  /* Buttons */
  .decision-text { font-size: 0.95rem; line-height: 1.55; color: var(--text); margin-bottom: 0.5rem; }
  .decision-text p { margin: 0 0 0.6rem; }
  .decision-text code { font-family: 'JetBrains Mono', monospace; font-size: 0.85em; padding: 0.1rem 0.3rem; border-radius: 4px; background: var(--bg); border: 1px solid var(--border); }
  .decision-choices { list-style: none; margin: 0 0 0.6rem; padding: 0; display: grid; gap: 0.4rem; }
  .decision-choices li { padding: 0.5rem 0.75rem; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); }
  .decision-choices li.recommended { border-color: var(--accent); }
  .choice-key { font-family: 'JetBrains Mono', monospace; font-weight: 700; margin-right: 0.35rem; }
  .choice-rec { margin-left: 0.4rem; font-size: 0.75rem; font-weight: 600; color: var(--accent); }
  .decision-after { color: var(--text-muted); font-size: 0.85rem; }
  .start-summary { margin-bottom: 0.5rem; color: var(--text-muted); font-size: 0.875rem; }
  .start-reason { padding: 0.5rem 0.75rem; border-left: 3px solid var(--st-held, orange); background: var(--bg); border-radius: 4px; font-size: 0.875rem; }
  .start-phase, .start-option { display: block; margin: 0.35rem 0; font-size: 0.9rem; }
  .start-count { color: var(--text-muted); font-size: 0.8rem; margin-left: 0.4rem; }
  #start-job-btn { margin-top: 0.5rem; }
  .start-job-body input[type=number], .start-job-body input[type=text], .start-job-body input[type=time] { padding: 0.25rem 0.4rem; border-radius: 6px; border: 1px solid var(--border); background: var(--bg, var(--surface)); color: var(--text); font-family: inherit; max-width: 100%; }
  .start-scope { margin: 0.25rem 0 0.5rem 0.4rem; }
  .start-phase summary { cursor: pointer; margin: 0.35rem 0; font-size: 0.9rem; }
  .start-tasks { margin: 0.1rem 0 0.4rem 2.6rem; }
  .start-task { display: block; margin: 0.2rem 0; font-size: 0.85rem; }
  .start-limits { display: flex; flex-wrap: wrap; gap: 0.25rem 1.25rem; }

  /* Runs view (009 FR-021; 005 FR-011) */
  .job-card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 0.875rem 1rem; margin-bottom: 0.75rem; }
  .job-card.open-job { border-color: var(--accent); }
  .job-head { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.75rem; cursor: pointer; }
  .job-title { font-weight: 600; font-size: 0.9375rem; flex: 1; min-width: 10rem; }
  .job-state { font-size: 0.75rem; font-weight: 600; padding: 0.15rem 0.55rem; border-radius: 999px; border: 1px solid currentColor; white-space: nowrap; }
  .job-working, .job-paused { color: var(--st-running); } .job-waiting, .job-not-landed { color: var(--st-held); }
  .job-suspended, .job-needs-merge, .job-failed { color: var(--st-blocked); } .job-stopped, .job-limit { color: var(--st-retrying); }
  .job-done, .job-ended { color: var(--st-done); } .job-dropped { color: var(--st-wontfix); }
  .job-branch { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: var(--text-muted); }
  .job-reason { font-size: 0.8125rem; color: var(--text-muted); margin-top: 0.375rem; }
  .job-card .ov-bar { margin: 0.625rem 0 0.375rem; }
  .job-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.625rem; }
  .job-actions .btn { padding: 0.375rem 0.75rem; }
  .job-actions .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .job-detail { margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid var(--border); }
  .job-detail > .card-title { margin-bottom: 0.5rem; font-size: 0.8125rem; }
  .job-runs { margin-top: 0.75rem; }
  .manual-actions { margin-top: 0.75rem; }
  .manual-note { width: 100%; margin: 0 0 0.5rem; padding: 0.5rem; border-radius: 6px; border: 1px solid var(--border); background: var(--bg, var(--surface)); color: var(--text); font-family: inherit; font-size: 0.875rem; box-sizing: border-box; }
  .decision-answer { width: 100%; margin: 0.5rem 0; padding: 0.5rem; border-radius: 6px; border: 1px solid var(--border); background: var(--bg, var(--surface)); color: var(--text); font-family: inherit; font-size: 0.875rem; box-sizing: border-box; }
  .decision-options { margin-top: 0.5rem; display: flex; gap: 0.5rem; flex-wrap: wrap; }
  .decision-saved { margin-top: 0.5rem; font-size: 0.8rem; color: var(--text-muted, inherit); }
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
      <span id="project-name" class="project-name"></span>
    </div>
    <div class="nav">
      <div class="nav-item active" data-section="overview"><span class="icon">📊</span>Overview</div>
      <div class="nav-item" data-section="docs"><span class="icon">📚</span>Docs</div>
      <div class="nav-item" data-section="tasks"><span class="icon">✅</span>Tasks</div>
      <div class="nav-item" data-section="blocked"><span class="icon">🚫</span>Blocked</div>
      <div class="nav-item" data-section="decisions"><span class="icon">⚖️</span>Decisions</div>
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
      <!-- The feature every view shows (memory only, FR-027); the note says when runs use another one -->
      <div class="header-feature" id="header-feature" style="display:none;">
        <label for="feature-select">Feature</label>
        <select id="feature-select" title="The feature every view shows"></select>
        <span id="feature-runs-note" class="header-feature-note"></span>
      </div>
    </div>
    <div class="content">
      <!-- Stale run (data-model.md §4.5): run.json is left behind and nothing is running -->
      <div id="stale-banner" class="stale-banner" role="alert" style="display:none;"></div>
      <!-- Run controls (data-model.md §12.4): stop, resume, keep paused, let it resume -->
      <div id="run-banner" class="run-banner" role="status" style="display:none;"></div>

      <!-- Feature picker: no active feature (409 from a per-feature request) -->
      <div id="feature-picker" class="card feature-picker" style="display:none;">
        <div class="card-title">Choose a feature</div>
        <div class="feature-picker-hint">This project has several feature folders and none is marked active. Pick the one to show.</div>
        <div id="feature-picker-list" class="feature-picker-list"></div>
      </div>

      <!-- Overview -->
      <section id="section-overview" class="section">
        <div class="card" id="ov-progress"></div>
        <div class="card" id="ov-running-card" style="display:none;"><div class="card-title">Running now <span class="badge" id="ov-running-count">0</span></div><div id="ov-running"></div></div>
        <div class="grid-2">
          <div class="card"><div class="card-title">Needs you <span class="badge" id="ov-needs-count">0</span></div><div id="ov-needs"></div></div>
          <div class="card"><div class="card-title">Next up</div><div id="ov-next"></div></div>
        </div>
        <div class="card"><div class="card-title">Phases</div><div id="ov-phases"></div></div>
        <div class="card"><div class="card-title">Recent runs</div><div id="ov-runs"></div></div>
      </section>

      <!-- Document viewer, shared by every /api/doc name except tasks and blocked -->
      <section id="section-doc" class="section" style="display:none;">
        <div class="card">
          <div id="doc-tabs" class="doc-tabs"></div>
          <div class="card-title" id="doc-title">Document</div>
          <div id="doc-edit-bar" class="doc-edit-bar"></div>
          <textarea id="doc-editor" class="doc-editor" spellcheck="false" style="display:none;"></textarea>
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

      <!-- Decisions -->
      <section id="section-decisions" class="section" style="display:none;">
        <div class="card-title">Decisions waiting on you</div>
        <div id="decisions-list"></div>
        <div class="card">
          <div class="card-title">Recorded decisions <button class="btn" data-raw="decisions">Show decisions.md</button></div>
          <div id="raw-decisions" class="markdown raw-doc" style="display:none;"></div>
        </div>
      </section>

      <!-- Runs (009 FR-021; 005 FR-011): one card per run, open first; sessions without a run under "Earlier runs" -->
      <section id="section-runs" class="section" style="display:none;">
        <div class="card" id="start-job-card">
          <div class="card-title">Start run</div>
          <div id="start-job-body"></div>
        </div>
        <div class="section-title">Runs</div>
        <div id="jobs-list"></div>
        <div class="section-title" style="margin-top:1.25rem">Earlier runs</div>
        <div id="earlier-runs"></div>
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
    var selectedRunId = null;
    // The run whose events and report are open right under it (clicked again, it closes), and the run whose report is shown.
    var expandedRun = null, reportRun = null;
    var runDetailEl = document.createElement('div');
    runDetailEl.className = 'run-detail';
    runDetailEl.innerHTML = '<div class="card-title">Tasks in this run</div><div id="run-tasks" class="run-tasks"></div>' +
      '<div class="card-title" style="margin-top:0.75rem">Event feed</div><div id="events-view" class="events-list"></div>' +
      '<div id="report-card" style="margin-top:1rem;display:none;"><div class="card-title"><span>Run report <span class="mono" id="report-run"></span></span></div>' +
      '<div id="report-view" class="markdown"></div></div>';
    function rd(id) { return runDetailEl.querySelector('#' + id); }
    var selectedSection = 'overview';
    var sectionShown = false;
    // The design documents share one sidebar entry, "Docs", with a tab per document.
    var DOC_GROUP = ['spec', 'plan', 'research', 'data-model', 'quickstart', 'manual', 'constitution', 'learnings'];
    var lastDoc = 'spec';
    // File names a document link may point at, for links such as [spec.md](./spec.md).
    var DOC_BY_FILE = { spec: 'spec', plan: 'plan', research: 'research', 'data-model': 'data-model', quickstart: 'quickstart',
      tasks: 'tasks', blocked: 'blocked', decisions: 'decisions', constitution: 'constitution', learnings: 'learnings' };

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

    // Replace an element's content only when it changed, and keep the page's scroll position and the
    // element's own: the 3 s refresh must not move what the person is reading.
    function setHtml(el, html) {
      if (el._html === html) return false;
      var content = document.querySelector('.content');
      var top = content ? content.scrollTop : 0, own = el.scrollTop;
      el.innerHTML = html;
      el._html = html;
      if (content) content.scrollTop = top;
      el.scrollTop = own;
      return true;
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

    // `code` spans in task and decision text.
    function inlineCode(text) {
      return linkDocs(escapeHtml(text).replace(/`([^`]+)`/g, '<code>$1</code>'));
    }

    // Document references in task, question and decision text become links that open the document view,
    // at the cited section when there is one: `specs/<feature>/<doc>.md` or a bare `<doc>.md` (the shown
    // feature), either one followed by "§N"; "<NNN> §N" (feature NNN's data model); and a bare "§N" (the
    // shown feature's data model, as tasks.md files cite it). html is already escaped; a path to a feature
    // that does not exist stays plain text.
    var knownFeatures = [], shownFeature = null;
    var DOC_REF = /(?:specs\\/([\\w.-]+)\\/)?\\b(spec|plan|research|data-model|quickstart|decisions|manual)\\.md(?:(<\\/code>)?(\\s+)§(\\d+(?:\\.\\d+)*))?|\\b(\\d{3})\\s+§(\\d+(?:\\.\\d+)*)|§(\\d+(?:\\.\\d+)*)/g;

    function docRefHtml(doc, feature, sec, text) {
      var where = DOC_TITLES[doc] + (sec ? ' §' + sec : '') + (feature ? ' (' + feature + ')' : '');
      return '<a href="#' + doc + '" class="doc-ref" data-doc="' + doc + '"' +
        (feature ? ' data-feature="' + escapeHtml(feature) + '"' : '') + (sec ? ' data-sec="' + sec + '"' : '') +
        ' title="Open ' + escapeHtml(where) + '">' + text + '</a>';
    }

    function linkDocs(html) {
      return String(html).replace(DOC_REF, function(m, folder, doc, close, gap, sec, num, numSec, bareSec) {
        if (doc) {
          if (folder && knownFeatures.length && knownFeatures.indexOf(folder) < 0) return m;  // before the list loads, link
          var name = m.slice(0, m.indexOf('.md') + 3);
          if (!sec) return docRefHtml(doc, folder, null, name);
          if (close) return docRefHtml(doc, folder, sec, name) + close + gap + docRefHtml(doc, folder, sec, '§' + sec);
          return docRefHtml(doc, folder, sec, m);
        }
        if (num) {
          var feature = knownFeatures.filter(function(f) { return f.indexOf(num + '-') === 0; })[0];
          return feature ? docRefHtml('data-model', feature, numSec, m) : m;
        }
        return docRefHtml('data-model', null, bareSec, m);
      });
    }

    // A BLOCKED.md question: `code` and bare file paths styled, one sentence per line, and an
    // "Either <a>. Or <b>." question shown as two numbered ways to settle the task.
    function questionText(text) {
      var path = /(^|[\\s(])(~?\\/[\\w.\\/-]*[\\w\\/]|[\\w.-]+\\/[\\w.\\/-]*\\.\\w+)/g;
      var html = String(text).split(/(`[^`]+`)/).map(function(part, i) {
        return i % 2 ? '<code>' + escapeHtml(part.slice(1, -1)) + '</code>' : escapeHtml(part).replace(path, '$1<code>$2</code>');
      }).join('');
      return linkDocs(html);
    }

    function questionSentences(text) {
      return String(text).split(/(?<=\\.)\\s+(?=[A-Z])(?=(?:[^`]*`[^`]*`)*[^`]*$)/).map(function(s) {
        return '<p>' + questionText(s) + '</p>';
      }).join('');
    }

    function blockedQuestionHtml(text) {
      text = String(text || '').trim();
      var m = text.match(/^Either\\s+([\\s\\S]+?\\.)\\s+Or\\s+([\\s\\S]+)$/);
      if (!m) return '<div class="blocked-question">' + questionSentences(text) + '</div>';
      var cap = function(s) { return s.charAt(0).toUpperCase() + s.slice(1); };
      return '<div class="blocked-question"><p>Two ways to settle it:</p><ul class="decision-choices">' +
        [m[1], m[2]].map(function(o, i) {
          return '<li><span class="choice-key">' + (i + 1) + '</span><div>' + questionSentences(cap(o)) + '</div></li>';
        }).join('') + '</ul></div>';
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
    // Dropped statuses rank after done (T062), so done plus dropped tasks reads done.
    var ROLLUP_ORDER = ['blocked', 'retrying', 'running', 'held', 'paused', 'queued',
                        'done', 'moved', 'wontfix', 'superseded'];

    // Transient states from the selected run's events (data-model.md §4.6): the newest event per
    // task id decides. Returns {taskId: {key, vars}}; a task whose newest event maps to nothing
    // (task.failed, anything else) is left out, so its parked state or queued applies. task.blocked shows
    // as blocked until BLOCKED.md has the entry (the parked status then wins), never as queued.
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
        else if (ev.kind === 'task.blocked') st = { key: 'blocked', vars: { label: 'Blocked (not recorded yet)' } };
        if (!st) return;
        if (runState === 'paused' && st.key !== 'blocked') st = { key: 'paused', vars: {} };
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
      if (!log || !log.available) return '<div class="live-output-empty">' + (log && log.reason ? 'Not available: ' + escapeHtml(log.reason) : 'Not available yet') + '</div>';
      return '<pre class="live-output-lines">' + escapeHtml((log.lines || []).join('\\n')) + '</pre>';
    }

    // The newest log answer per running task id, so the 3 s re-render of the task list keeps the panel's text.
    var liveLogs = {};

    function liveOutputHtml(t, log) {
      return '<div class="live-output"><div class="live-output-title">' + (taskStatusKey(t) === 'running' ? 'Live output' : 'Log') + '</div>' +
        '<div class="live-output-body" data-live-task="' + escapeHtml(t.id) + '">' + liveOutputBodyHtml(log) + '</div></div>';
    }

    // Everything waiting on a person, held and blocked together (FR-005). A task held only because
    // a dependency is held waits on that task, not on the person, so it is not counted here. Neither is
    // a [manual] step that is not doable yet (markNotYet): only what the person can act on now counts.
    function isWaiting(t) {
      var key = taskStatusKey(t);
      return ((key === 'held' && t.status_tag !== 'dependency') || key === 'blocked') && !t.notYet;
    }

    // A held [manual] step is doable once every earlier phase is finished (each task done, moved,
    // wontfix or superseded, not counting other held [manual] steps); tasks in its own phase do not
    // count, so a setup step in the middle of a phase stays doable. Until then `t.notYet` is the
    // number of open tasks in earlier phases, and the step shows in its phase as "Not yet" instead of
    // in Waiting on you.
    var FINISHED_KEYS = ['done', 'moved', 'wontfix', 'superseded'];
    function markNotYet(phases) {
      var open = 0;
      (phases || []).forEach(function(p) {
        var here = 0;
        (p.tasks || []).forEach(function(t) {
          var key = taskStatusKey(t), manual = key === 'held' && t.status_tag === 'manual';
          t.notYet = manual ? open : 0;
          if (!manual && FINISHED_KEYS.indexOf(key) < 0) here++;
        });
        open += here;
      });
      return phases;
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
      markNotYet(phases).forEach(function(p) {
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

    // A card opens its log right under itself when clicked; a running task always shows it.
    var openLogs = {};
    // {task id: [{run, outcome, commits, job, title}]} for the selected feature, newest run first
    // (/api/runs/tasks), and the task a "Tasks in this run" row asked to show.
    var taskRuns = {}, pendingTask = null;

    var OUTCOME_LABELS = { committed: 'committed', approved: 'approved', blocked: 'blocked', rejected: 'rejected',
                           interrupted: 'interrupted', running: 'running' };

    function outcomeHtml(outcome) {
      return '<span class="run-outcome o-' + escapeHtml(outcome) + '">' + escapeHtml(OUTCOME_LABELS[outcome] || outcome) + '</span>';
    }

    // The newest run that touched the task, as a chip that opens that run; the title lists every run.
    // A session that belonged to a run record names the run instead ("done in run <title>") and opens that
    // run's card; the session is one click deeper, inside it (005 FR-013, 009 FR-022).
    function taskRunChipHtml(id) {
      var runs = taskRuns[id];
      if (!runs || !runs.length) return '';
      var r = runs[0];
      var title = runs.map(function(x) { return '#' + x.run + ': ' + x.outcome + (x.commits.length ? ' (' + x.commits.join(', ') + ')' : ''); }).join('\\n');
      if (r.job) {
        var word = r.outcome === 'committed' ? 'done' : (OUTCOME_LABELS[r.outcome] || r.outcome);
        return '<span class="run-chip" data-open-job="' + escapeHtml(r.job) + '" title="' + escapeHtml(title) + '">' +
          '<span class="run-outcome o-' + escapeHtml(r.outcome) + '">' + escapeHtml(word) + '</span> in run ' +
          escapeHtml(r.title || r.job) + (r.commits.length ? ' · <span class="mono">' + escapeHtml(r.commits[0]) + '</span>' : '') +
          (runs.length > 1 ? ' · +' + (runs.length - 1) + ' more' : '') + '</span>';
      }
      return '<span class="run-chip" data-run="' + escapeHtml(r.run) + '" title="' + escapeHtml(title) + '">' + outcomeHtml(r.outcome) +
        ' in run #' + escapeHtml(r.run) + (r.commits.length ? ' · <span class="mono">' + escapeHtml(r.commits[0]) + '</span>' : '') +
        (runs.length > 1 ? ' · +' + (runs.length - 1) + ' more' : '') + '</span>';
    }

    function showsLog(t) { return taskStatusKey(t) === 'running' || !!openLogs[t.id]; }

    function taskCardHtml(t) {
      return '<div class="task-card' + (taskStatusKey(t) === 'done' ? ' task-done' : '') + (openLogs[t.id] ? ' log-open' : '') +
        '" data-task-card="' + escapeHtml(t.id) + '">' +
        '<div class="task-head"><span class="task-id">' + escapeHtml(t.id) + '</span>' + taskBadge(t) + '</div>' +
        '<div class="task-text">' + inlineCode(t.text) + '</div>' +
        (t.notYet ? '<div class="task-not-yet">Not yet: you can do this once the earlier phases are finished (' + t.notYet +
          (t.notYet === 1 ? ' task' : ' tasks') + ' left).</div>' : '') +
        '<div class="task-tags">' + tagsHtml(t.tags) + taskRunChipHtml(t.id) + '</div>' +
        (showsLog(t) ? liveOutputHtml(t, liveLogs[t.id]) : '') + '</div>';
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
        '<div class="task-text">' + inlineCode(t.text) + '</div>' +
        (t.status_reason ? '<div class="blocked-reason">' + escapeHtml(t.status_reason) + '</div>' : '') +
        (ask ? blockedQuestionHtml(ask) : '') +
        (taskStatusKey(t) === 'held' && t.status_tag === 'manual' ? '<div class="manual-steps" data-steps-task="' + escapeHtml(t.id) + '"></div>' + manualActionsHtml(t) : '') +
        (t.status === 'blocked' && t.status_tag === 'open' ? unblockActionsHtml(t.id) : '') +
        '</div>';
    }

    // The step-by-step instructions of a [manual] task: the section of the feature's manual.md whose heading
    // starts with the task id ("## T022 Real Job test"), down to the next heading of the same or a higher level.
    // null when manual.md has no such section.
    function manualSection(text, id) {
      var lines = String(text || '').split('\\n'), start = -1, level = 0;
      for (var i = 0; i < lines.length; i++) {
        var h = /^(#{1,6})\\s+(.*)$/.exec(lines[i]);
        if (!h) continue;
        if (start >= 0 && h[1].length <= level) return lines.slice(start, i).join('\\n');
        if (start < 0 && (h[2] + ' ').indexOf(id + ' ') === 0) { start = i; level = h[1].length; }
      }
      return start >= 0 ? lines.slice(start).join('\\n') : null;
    }

    // Fill each empty `.manual-steps` box under el from manual.md, open, so the person sees what to do where
    // they act. A task without a section says so, and how to add one.
    function fillManualSteps(el) {
      var boxes = Array.prototype.slice.call(el.querySelectorAll('.manual-steps[data-steps-task]:not([data-filled])'));
      if (!boxes.length) return Promise.resolve();
      return fetchDoc(docUrl('manual')).then(function(d) {
        boxes.forEach(function(box) {
          var id = box.getAttribute('data-steps-task');
          var section = d.text != null ? manualSection(d.text, id) : null;
          box.setAttribute('data-filled', '');
          if (section == null) {
            box.innerHTML = '<div class="manual-steps-none">No step-by-step instructions yet: add a <code>## ' + escapeHtml(id) +
              '</code> section to the feature&#39;s <code>manual.md</code>.</div>';
            return;
          }
          // "Record: research.md ## Validation": the note is saved there by I did it (sdd-manual.py).
          var rec = /^Record:\\s*`?([\\w.-]+\\.md)`?\\s+`?(#{2,4} [^`\\n]+?)`?\\s*$/m.exec(section);
          var steps = section.replace(/^#{1,6}[^\\n]*\\n?/, '').replace(/^Record:.*$/m, '');
          box.innerHTML = '<details class="manual-steps-fold" open><summary>Steps</summary><div class="markdown"></div></details>' +
            (rec ? '<div class="manual-record">Your note below is saved under <code>' + escapeHtml(rec[2]) + '</code> in ' +
              docRefHtml(rec[1].replace(/\\.md$/, ''), null, null, '<code>' + escapeHtml(rec[1]) + '</code>') +
              ' when you press <b>I did it</b>, in the same commit as the tick.</div>' : '');
          if (rec && !DOC_TITLES[rec[1].replace(/\\.md$/, '')]) box.querySelector('.manual-record a').outerHTML = '<code>' + escapeHtml(rec[1]) + '</code>';
          renderDoc(box.querySelector('.markdown'), steps, 'markdown');
          linkDocRefsIn(box);
          var input = document.getElementById('manual-note-' + id);
          if (rec && input) {
            input.placeholder = 'Paste what you found or the printed output. It is saved under ' + rec[2] + ' in ' + rec[1] + '.';
            input.rows = 5;
          }
        });
      });
    }

    // Doc names in rendered markdown become document links too: inline code outside links, and paragraphs
    // and list items that are plain text (so an attribute is never rewritten).
    function linkDocRefsIn(el) {
      el.querySelectorAll('code, p, li').forEach(function(n) {
        if (n.tagName === 'CODE' ? n.closest('a, pre') : n.children.length) return;
        var html = linkDocs(n.innerHTML);
        if (html !== n.innerHTML) n.innerHTML = html;
      });
    }

    // A held [manual] task (FR-036): the person says they did it, or skips it. Each button is one commit.
    function manualActionsHtml(t) {
      var id = escapeHtml(t.id);
      return '<div class="manual-actions">' +
        '<textarea class="manual-note" id="manual-note-' + id + '" rows="2" placeholder="What you found or did (optional for I did it; required to skip)"></textarea>' +
        '<button class="btn manual-done" data-task="' + id + '">I did it</button> ' +
        '<button class="btn manual-skip" data-task="' + id + '">Skip</button></div>';
    }

    // An open BLOCKED.md entry (FR-037): the person did the step, answers the question so the next run retries
    // the task with it, or drops the task. Each button is one commit; all refuse while a run is active.
    function unblockActionsHtml(id) {
      id = escapeHtml(id);
      return '<div class="manual-actions">' +
        '<textarea class="manual-note" id="unblock-text-' + id + '" rows="2" placeholder="Your answer, what you did, or why you drop it"></textarea>' +
        '<button class="btn unblock-btn" data-task="' + id + '" data-action="done" title="You did the step by hand: the task is ticked">I did it</button> ' +
        '<button class="btn unblock-btn" data-task="' + id + '" data-action="answer" title="The next run retries the task with your answer">Answer and retry</button> ' +
        '<button class="btn unblock-btn" data-task="' + id + '" data-action="drop" title="Drop the task">Won&#39;t fix</button></div>';
    }

    function settleBlocked(task, action) {
      var boxes = document.querySelectorAll('[id="unblock-text-' + task + '"]'), text = '';
      boxes.forEach(function(b) { if (b.value.trim()) text = b.value.trim(); });
      if (action !== 'done' && !text) { toast(action === 'answer' ? 'Write your answer first' : 'Say why you drop it'); if (boxes[0]) boxes[0].focus(); return; }
      postEdit('/api/blocked/' + action, { task: task, text: text }).then(function(res) {
        if (!res.ok) { toast('Not saved: ' + (res.error || 'unknown error')); return; }
        toast(task + { done: ' marked done', answer: ' answered: the next run retries it', drop: ' dropped' }[action] + ' (commit ' + res.commit + ')');
        refreshAll();
      }).catch(function(e) { console.error('settleBlocked error', e); toast('Could not save'); });
    }

    function settleManual(task, action) {
      var box = document.getElementById('manual-note-' + task);
      var note = box ? box.value.trim() : '';
      if (action === 'skip' && !note) { toast('Say why you are skipping it'); if (box) box.focus(); return; }
      postEdit('/api/manual/' + action, { task: task, note: note }).then(function(res) {
        if (!res.ok) { toast('Not saved: ' + (res.error || 'unknown error')); return; }
        toast(task + (action === 'done' ? ' marked done' : ' skipped') + ' (commit ' + res.commit + ')');
        refreshAll();
      }).catch(function(e) { console.error('settleManual error', e); toast('Could not save'); });
    }

    function entryItemHtml(e) {
      return '<div class="blocked-item">' +
        '<div class="blocked-header"><div class="blocked-id">' + escapeHtml(e.id) + '</div>' + entryBadge(e) + '</div>' +
        (e.reason ? '<div class="blocked-reason">' + escapeHtml(e.reason) + '</div>' : '') +
        (e.question ? blockedQuestionHtml(e.question) : '') +
        (e.answer ? '<div class="blocked-answer">' + escapeHtml(e.answer) + '</div>' : '') +
        '<div class="blocked-tags">' + tagsHtml(e.tag ? [e.tag] : []) + '</div>' +
        (e.status === 'open' ? unblockActionsHtml(e.id) : '') + '</div>';
    }

    // Documents (data-model.md §4.3): every /api/doc name. tasks and blocked keep their structured views
    // and show the document only under a "Raw document" toggle; the others open in the shared doc view.
    var DOC_TITLES = {
      spec: 'Spec', plan: 'Plan', research: 'Research', 'data-model': 'Data model', quickstart: 'Quickstart', manual: 'Manual steps',
      decisions: 'Decisions', constitution: 'Constitution', learnings: 'Learnings', config: 'Config',
      tasks: 'tasks.md', blocked: 'BLOCKED.md'
    };
    var RAW_DOCS = { tasks: true, blocked: true, decisions: true };
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

    function docUrl(name, feature) {
      return '/api/doc?name=' + encodeURIComponent(name) + (feature ? '&feature=' + encodeURIComponent(feature) : '');
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

    // The dashboard serves the project it was started in; show its name.
    function loadProject() {
      fetchJSON('/api/projects', 'GET').then(function(projects) {
        var p = (projects || [])[0];
        if (!p) return;
        var el = document.getElementById('project-name');
        el.textContent = p.name;
        el.title = p.path;
        document.title = p.name + ' - speckit-agents';
        refreshAll();
      }).catch(function(e) { console.error('loadProject error', e); });
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

    // The header's feature picker: every view shows the chosen feature. Redrawn only when the list,
    // the choice or the runs' feature changes, so an open dropdown is not reset by the 3 s refresh.
    function loadFeatureSelect() {
      fetchJSON('/api/features', 'GET').then(function(f) {
        var box = document.getElementById('header-feature');
        knownFeatures = (f && f.features) || [];
        shownFeature = (f && f.active) || null;
        if (selectedSection === 'tasks' && JSON.stringify(knownFeatures) !== box._features) { box._features = JSON.stringify(knownFeatures); loadTasks(); }
        if (!f || !f.features || !f.features.length) { box.style.display = 'none'; return; }
        var key = JSON.stringify(f);
        if (box._key === key) return;
        box._key = key;
        var sel = document.getElementById('feature-select');
        sel.innerHTML = (f.active ? '' : '<option value="" selected>Choose…</option>') + f.features.map(function(name) {
          return '<option value="' + escapeHtml(name) + '"' + (name === f.active ? ' selected' : '') + '>' + escapeHtml(name) + '</option>';
        }).join('');
        var note = document.getElementById('feature-runs-note');
        var differs = f.runs && f.active && f.runs !== f.active;
        note.textContent = differs ? 'runs use ' + f.runs : '';
        note.title = differs ? 'You are looking at ' + f.active + '; /sdd-run and Start run use ' + f.runs + ' (.specify/feature.json).' : '';
        box.style.display = '';
      }).catch(function(e) { console.error('loadFeatureSelect error', e); });
    }

    // Choose a feature for this dashboard session (memory only, FR-027), then reload the views.
    function chooseFeature(name) {
      fetchJSON('/api/project/select', 'POST', { feature: name }).then(function(result) {
        if (!result || !result.ok) { toast('Failed to choose feature ' + name); return; }
        hideFeaturePicker();
        docFeature = null;
        toast('Feature: ' + name);
        editNote = ''; editError = '';
        loadFeatureSelect();
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

    // The section is kept in the URL (#tasks), so a refresh or a shared link opens the same view.
    function sectionFromHash() {
      var name = decodeURIComponent(location.hash.replace(/^#\\/?/, ''));
      if (name === 'docs') return lastDoc;
      if (name === 'jobs') { history.replaceState(null, '', '#runs'); return 'runs'; }  // the Jobs view became Runs (009 FR-022)
      var known = DOC_GROUP.indexOf(name) >= 0 || (/^[a-z-]+$/.test(name) && document.querySelector('.nav-item[data-section="' + name + '"]'));
      return known ? name : 'overview';
    }

    function showSection(name) {
      try {
        if (editorChanged() && name !== docShown && !confirm('Discard your unsaved edit?')) {
          history.replaceState(null, '', '#' + docShown);
          return;
        }
        if (decodeURIComponent(location.hash.replace(/^#\\/?/, '')) !== name) {
          history[sectionShown ? 'pushState' : 'replaceState'](null, '', '#' + name);
        }
        sectionShown = true;
        document.querySelectorAll('.nav-item').forEach(function(el) { el.classList.remove('active'); });
        var target = document.querySelector('.nav-item[data-section="' + (DOC_GROUP.indexOf(name) >= 0 ? 'docs' : name) + '"]');
        if (target) target.classList.add('active');
        // On a phone the nav is one scrolling row: keep the current section's tab in view.
        if (target && target.scrollIntoView) target.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        document.querySelectorAll('section').forEach(function(el) { el.style.display = 'none'; });
        var isDoc = DOC_TITLES[name] && !RAW_DOCS[name];
        if (!isDoc) { docFeature = null; docSection = null; }
        var sec = document.getElementById(isDoc ? 'section-doc' : 'section-' + name);
        if (sec) sec.style.display = 'block';
        document.getElementById('page-title').textContent = isDoc ? docTitle(name) : name.charAt(0).toUpperCase() + name.slice(1);
        selectedSection = name;
        if (name === 'overview') loadOverview();
        if (name === 'tasks') loadTasks();
        if (name === 'blocked') loadBlocked();
        if (name === 'decisions') loadDecisions();
        if (name === 'runs') { loadJobs(); loadStartJob(); }
        if (name === 'status') loadStatus();
        if (isDoc) loadDoc(name);
      } catch(e) { console.error('showSection error', e); }
    }

    // Editing (FR-035): only the spec of the chosen feature and the constitution. The editor saves through
    // /api/doc/save with the hash of the text it opened, and a Commit button commits just that file.
    var EDITABLE_DOCS = ['spec', 'constitution'];
    var editing = false, editBase = null, docShown = null, editNote = '', editError = '';

    function editorChanged() {
      var ed = document.getElementById('doc-editor');
      return editing && ed.value !== ed.getAttribute('data-original');
    }

    function leaveEditor() {
      editing = false;
      editBase = null;
      document.getElementById('doc-editor').style.display = 'none';
      document.getElementById('doc-content').style.display = '';
    }

    function defaultCommitMessage(name, feature) {
      return name === 'spec' ? 'docs(spec): update ' + (feature || 'feature') + ' spec' : 'docs(constitution): update constitution';
    }

    // The bar above an editable document: Edit, and the Commit row while the file has edits.
    function renderEditBar(name) {
      var bar = document.getElementById('doc-edit-bar');
      if (EDITABLE_DOCS.indexOf(name) < 0 || docFeature) { bar.innerHTML = ''; return Promise.resolve(); }
      return Promise.all([fetchJSON('/api/doc/state?name=' + name, 'GET'), name === 'spec' ? fetchJSON('/api/features', 'GET') : Promise.resolve(null)]).then(function(r) {
        if (docShown !== name) return;
        var st = r[0], feats = r[1], html = '';
        if (!st || st.error) { bar.innerHTML = ''; return; }
        if (editing) {
          html = '<button type="button" class="btn btn-primary" data-edit="save">Save</button>' +
            '<button type="button" class="btn" data-edit="cancel">Cancel</button>' +
            '<span class="doc-edit-note">Editing ' + escapeHtml(st.path) + '</span>';
        } else {
          html += '<button type="button" class="btn" data-edit="open"' + (st.locked ? ' disabled title="' + escapeHtml(st.locked) + '"' : '') + '>Edit</button>';
          if (st.locked) html += '<span class="doc-edit-note">' + escapeHtml(st.locked) + '</span>';
          if (st.dirty) {
            html += '<input type="text" id="commit-message" maxlength="200" value="' + escapeHtml(defaultCommitMessage(name, feats && feats.active)) + '" aria-label="Commit message">' +
              '<button type="button" class="btn btn-primary" data-edit="commit"' + (st.locked ? ' disabled' : '') + '>Commit</button>' +
              '<span class="doc-edit-note">' + escapeHtml(st.path) + ' has uncommitted edits.' +
              (name === 'spec' ? ' The plan and tasks may need regenerating.' : '') + '</span>';
          }
        }
        if (editNote) html += '<span class="doc-edit-note">' + escapeHtml(editNote) + '</span>';
        if (editError) html += '<span class="doc-edit-error">' + escapeHtml(editError) + '</span>';
        bar.innerHTML = html;
        bar.querySelectorAll('[data-edit]').forEach(function(b) {
          b.addEventListener('click', function() { editAction(name, b.getAttribute('data-edit'), st); });
        });
      });
    }

    // POST JSON and return the body whatever the status, so a refusal's `error` reaches the person.
    function postEdit(url, body) {
      return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
        .then(function(res) { return res.json().catch(function() { return { error: 'HTTP ' + res.status }; }); })
        .catch(function(e) { return { error: String(e) }; });
    }

    function editAction(name, action, st) {
      var ed = document.getElementById('doc-editor');
      editNote = ''; editError = '';
      if (action === 'open') {
        fetchDoc(docUrl(name)).then(function(d) {
          if (d.text == null && !d.missing) { editError = 'Could not load the document: ' + (d.error || 'unknown error'); renderEditBar(name); return; }
          ed.value = d.text || '';
          ed.setAttribute('data-original', ed.value);
          editBase = st.hash;
          editing = true;
          ed.style.display = 'block';
          document.getElementById('doc-content').style.display = 'none';
          renderEditBar(name).then(function() { ed.focus(); });
        });
      } else if (action === 'cancel') {
        if (editorChanged() && !confirm('Discard your unsaved edit?')) return;
        leaveEditor();
        renderEditBar(name);
      } else if (action === 'save') {
        postEdit('/api/doc/save', { name: name, text: ed.value, base: editBase }).then(function(res) {
          if (!res || res.error) { editError = (res && res.error) || 'Save failed'; renderEditBar(name); return; }
          leaveEditor();
          editNote = 'Saved. Commit it when you are happy with it.';
          showDoc(document.getElementById('doc-content'), docUrl(name), docKind(name));
          renderEditBar(name);
        });
      } else if (action === 'commit') {
        var msg = document.getElementById('commit-message').value;
        postEdit('/api/doc/commit', { name: name, message: msg }).then(function(res) {
          if (!res || res.error) { editError = (res && res.error) || 'Commit failed'; }
          else { editNote = 'Committed.'; toast('Committed ' + st.path); }
          renderEditBar(name);
        });
      }
    }

    function loadDoc(name) {
      if (docShown !== name) { leaveEditor(); editNote = ''; editError = ''; }
      docShown = name;
      renderEditBar(name);
      document.getElementById('doc-title').textContent = docTitle(name);
      var tabs = document.getElementById('doc-tabs');
      if (DOC_GROUP.indexOf(name) >= 0) {
        lastDoc = name;
        tabs.innerHTML = DOC_GROUP.map(function(d) {
          return '<button type="button" class="doc-tab' + (d === name ? ' active' : '') + '" data-doc="' + d + '">' + escapeHtml(DOC_TITLES[d]) + '</button>';
        }).join('');
        tabs.querySelectorAll('.doc-tab').forEach(function(b) {
          b.addEventListener('click', function() { showSection(b.getAttribute('data-doc')); });
        });
      } else {
        tabs.innerHTML = '';
      }
      var el = document.getElementById('doc-content'), url = docUrl(name, docFeature);
      if (el.getAttribute('data-want') !== url) el.innerHTML = '';
      return showDoc(el, url, docKind(name)).then(function() {
        if (docSection && el.getAttribute('data-want') === url) { scrollToSection(el, docSection); docSection = null; }
      });
    }

    // A document link (linkDocs): show that document, from its own feature when that is not the one shown
    // (the title says which, and editing is off), and scroll to the cited section once it has loaded.
    var docFeature = null, docSection = null;

    function openDocRef(name, feature, sec) {
      docFeature = feature && feature !== shownFeature ? feature : null;
      docSection = sec || null;
      if (selectedSection === name) loadDoc(name); else showSection(name);
    }

    function docTitle(name) {
      return DOC_TITLES[name] + (docFeature ? ' · ' + docFeature : '');
    }

    // The heading numbered sec ("## 6. Real run", "### 4.3 Names", "## §2 …"), scrolled to and briefly marked.
    function scrollToSection(el, sec) {
      var hs = el.querySelectorAll('h1, h2, h3, h4');
      for (var i = 0; i < hs.length; i++) {
        var t = hs[i].textContent.replace(/^[\\s§]+/, '');
        if (t.indexOf(sec) !== 0 || /^\\.?\\d/.test(t.slice(sec.length))) continue;
        var h = hs[i];
        if (h.scrollIntoView) h.scrollIntoView({ block: 'start' });
        h.classList.add('doc-target');
        setTimeout(function() { h.classList.remove('doc-target'); }, 2500);
        return true;
      }
      return false;
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

    // Overview: where the work stands, what needs the person, what comes next, and how the last runs went.
    var OV_BAR = [['done', 'Done'], ['running', 'Running'], ['held', 'Held'], ['blocked', 'Blocked'], ['queued', 'To do']];

    function shortText(text, n) {
      text = String(text || '').replace(/`/g, '');
      return text.length > n ? text.slice(0, n - 1).trimEnd() + '…' : text;
    }

    function overviewCounts(phases) {
      var c = { total: 0, done: 0, running: 0, held: 0, blocked: 0, queued: 0 };
      (phases || []).forEach(function(p) {
        (p.tasks || []).forEach(function(t) {
          var key = taskStatusKey(t);
          c.total++;
          if (key === 'done') c.done++;
          else if (key === 'running' || key === 'retrying') c.running++;
          else if (key === 'held' || key === 'paused') c.held++;
          else if (key === 'blocked') c.blocked++;
          else c.queued++;  // queued, plus wontfix, moved and superseded, which are not work left
        });
      });
      return c;
    }

    function runPillHtml(run) {
      // With a run record the pill says what the banner says (009 FR-022); never a run id.
      if (run && run.job) {
        return '<span class="ov-run ' + escapeHtml(jobPillClass(run.job)) + '"><span class="dot"></span>' +
          escapeHtml(jobBannerText(run.job, bannerActivity, bannerKeep(run.job))) + '</span>';
      }
      var state = (run && run.state) || 'none';
      // Words for the person, never the run id (that lives in the Runs view).
      var label = { running: 'Working', paused: 'Paused', stale: 'Stopped unexpectedly', none: 'No run going' }[state] || 'No run going';
      return '<span class="ov-run ' + escapeHtml(state) + '"><span class="dot"></span>' + escapeHtml(label) + '</span>';
    }

    function overviewProgressHtml(c, run, feature) {
      var pct = c.total ? Math.round(c.done * 100 / c.total) : 0;
      var bar = OV_BAR.map(function(b) {
        var n = c[b[0]];
        return n ? '<span style="width:' + (n * 100 / c.total) + '%;background:var(--st-' + b[0] + ')" title="' + b[1] + ': ' + n + '"></span>' : '';
      }).join('');
      var legend = OV_BAR.map(function(b) {
        return '<span><i style="background:var(--st-' + b[0] + ')"></i>' + b[1] + ' <b>' + c[b[0]] + '</b></span>';
      }).join('');
      return '<div class="ov-head"><div><div class="ov-big">' + c.done + '<small> / ' + c.total + ' tasks done · ' + pct + '%</small></div>' +
        (feature ? '<div class="ov-feature">' + escapeHtml(feature) + '</div>' : '') + '</div>' + runPillHtml(run) + '</div>' +
        '<div class="ov-bar">' + bar + '</div><div class="ov-legend">' + legend + '</div>';
    }

    // What waits on the person, in labelled groups, most pressing first: tasks a run blocked, open decisions,
    // manual tasks, then other holds (a dependency or download to approve). Each row opens its view.
    var NEEDS_GROUPS = [
      { key: 'blocked', title: 'Blocked by a run', icon: '🚫', go: 'blocked' },
      { key: 'decision', title: 'Decisions', icon: '⚖️', go: 'decisions' },
      { key: 'manual', title: 'Manual: needs you to do it', icon: '✋', go: 'tasks' },
      { key: 'other', title: 'Approve to continue', icon: '⏳', go: 'tasks' }
    ];
    var NEEDS_PER_GROUP = 5;

    function needsGroups(phases, decisions) {
      var groups = { blocked: [], decision: [], manual: [], other: [] }, seen = {};
      markNotYet(phases);
      (decisions || []).forEach(function(d) { seen[d.id] = true; groups.decision.push({ id: d.id, text: d.question || d.text, meta: 'decision' }); });
      (phases || []).forEach(function(p) {
        (p.tasks || []).forEach(function(t) {
          if (!isWaiting(t) || seen[t.id]) return;
          var key = taskStatusKey(t) === 'blocked' ? 'blocked' : t.status_tag === 'decision' ? 'decision' : t.status_tag === 'manual' ? 'manual' : 'other';
          groups[key].push({ id: t.id, text: t.question || t.text, meta: key === 'other' ? (t.status_tag || 'held') : '' });
        });
      });
      return groups;
    }

    function overviewNeedsHtml(phases, decisions) {
      var groups = needsGroups(phases, decisions), count = 0, html = '';
      NEEDS_GROUPS.forEach(function(g) {
        var rows = groups[g.key];
        if (!rows.length) return;
        count += rows.length;
        html += '<div class="ov-group"><div class="ov-group-title" data-go="' + g.go + '">' + g.icon + ' ' + escapeHtml(g.title) +
          ' <span class="badge">' + rows.length + '</span></div>' +
          rows.slice(0, NEEDS_PER_GROUP).map(function(r) {
            return '<div class="ov-row" ' + (g.go === 'tasks' ? 'data-goto-task="' + escapeHtml(r.id) + '"' : 'data-go="' + g.go + '"') + '><span class="id">' + escapeHtml(r.id) + '</span>' +
              '<span class="txt">' + escapeHtml(shortText(r.text, 110)) + '</span>' + (r.meta ? '<span class="meta">' + escapeHtml(r.meta) + '</span>' : '') + '</div>';
          }).join('') +
          (rows.length > NEEDS_PER_GROUP ? '<div class="ov-more" data-go="' + g.go + '">and ' + (rows.length - NEEDS_PER_GROUP) + ' more</div>' : '') + '</div>';
      });
      return { count: count, html: html || '<div class="ov-ok">✓ Nothing is waiting on you.</div>' };
    }

    // Tasks a run is working on now: step, attempt, time since the attempt started, and the newest log line.
    // starts is {task id: ts of its newest task.started}; lines is {task id: last log line}.
    function runningRows(phases) {
      var rows = [];
      (phases || []).forEach(function(p) {
        (p.tasks || []).forEach(function(t) {
          var key = taskStatusKey(t);
          if (key === 'running' || key === 'retrying' || (key === 'paused' && t.transient)) rows.push({ t: t, phase: p.title });
        });
      });
      return rows;
    }

    function sinceText(ts, now) {
      var ms = (now || Date.now()) - Date.parse(ts);
      if (!isFinite(ms) || ms < 0) return '';
      var m = Math.floor(ms / 60000);
      return m < 1 ? 'just started' : m < 60 ? m + ' min' : Math.floor(m / 60) + ' h ' + (m % 60) + ' min';
    }

    function overviewRunningHtml(rows, starts, lines, now) {
      return rows.map(function(r) {
        var t = r.t, line = lines[t.id];
        return '<div class="ov-run-task" data-goto-task="' + escapeHtml(t.id) + '"><div class="ov-row"><span class="id">' + escapeHtml(t.id) + '</span>' +
          taskBadge(t) + '<span class="txt">' + escapeHtml(shortText(t.text, 100)) + '</span>' +
          '<span class="meta">' + escapeHtml(starts[t.id] ? sinceText(starts[t.id], now) : '') + '</span></div>' +
          (line ? '<div class="ov-run-line">' + escapeHtml(shortText(line, 160)) + '</div>' : '') + '</div>';
      }).join('');
    }

    // The first open tasks nothing is holding, in file order.
    function overviewNextHtml(phases) {
      var next = [];
      (phases || []).forEach(function(p) {
        (p.tasks || []).forEach(function(t) { if (taskStatusKey(t) === 'queued' && !t.status) next.push({ t: t, phase: p.title }); });
      });
      if (!next.length) return '<div class="ov-ok">Nothing left to run.</div>';
      return next.slice(0, 5).map(function(n) {
        return '<div class="ov-row" data-go="tasks"><span class="id">' + escapeHtml(n.t.id) + '</span><span class="txt">' +
          escapeHtml(shortText(n.t.text, 110)) + '</span></div>';
      }).join('') + (next.length > 5 ? '<div class="ov-more">and ' + (next.length - 5) + ' more</div>' : '');
    }

    // Unfinished phases with their own bar; finished ones fold into one line.
    function overviewPhasesHtml(phases) {
      var open = [], complete = 0;
      (phases || []).forEach(function(p) {
        var tasks = p.tasks || [], done = tasks.filter(function(t) { return taskStatusKey(t) === 'done'; }).length;
        if (tasks.length && done === tasks.length) { complete++; return; }
        open.push('<div class="ov-row" data-go="tasks"><span class="txt">' + escapeHtml(shortText(p.title, 70)) + '</span>' +
          (['blocked', 'retrying', 'running', 'held', 'paused'].indexOf(p.status) >= 0 ? statusBadge(p.status) : '') +
          '<span class="ov-mini"><span style="width:' + (tasks.length ? done * 100 / tasks.length : 0) + '%"></span></span>' +
          '<span class="meta">' + done + '/' + tasks.length + '</span></div>');
      });
      if (complete) open.push('<div class="ov-more">✓ ' + complete + ' complete phase' + (complete === 1 ? '' : 's') + '</div>');
      return open.join('') || '<div class="ov-ok">No phases.</div>';
    }

    function overviewRunsHtml(runs) {
      if (!runs.length) return '<div class="ov-ok">No runs yet.</div>';
      return runs.slice(0, 5).map(function(r) {
        var state = r.finished ? (r.stopped ? 'stopped' : 'done') : r.stopped ? 'stopped' : (r.interrupted > 0 ? 'paused' : 'running');
        return '<div class="ov-row" data-run="' + escapeHtml(r.id) + '"><span class="id">#' + escapeHtml(r.id) + '</span>' +
          '<span class="txt">' + r.approved + ' approved · ' + r.blocked + ' blocked' + (r.interrupted ? ' · ' + r.interrupted + ' interrupted' : '') + '</span>' +
          '<span class="meta">' + state + '</span></div>';
      }).join('') + (runs.length > 5 ? '<div class="ov-more" data-go="runs">' + (runs.length - 5) + ' older in Runs</div>' : '');
    }

    // The tasks with the selected run's transient states merged in (as the Tasks view shows them).
    function loadMergedTasks() {
      return Promise.all([fetchJSON('/api/project/tasks', 'GET'), fetchJSON('/api/run/current', 'GET'),
                          fetchJSON('/api/runs/tasks', 'GET')]).then(function(r) {
        var data = r[0] || {}, run = r[1] || {};
        taskRuns = r[2] || {};
        var runId = selectedRunId || (run.state && run.state !== 'none' ? run.run_id : null);
        var feed = runId ? fetchJSON('/api/runs/' + encodeURIComponent(runId) + '/events', 'GET') : Promise.resolve(null);
        return feed.then(function(f) {
          var states = transientStates(f && f.events, data.max_retries, runId === run.run_id ? run.state : null);
          return { phases: mergeTransient(data.phases, states), run: run, runId: runId, events: (f && f.events) || [] };
        });
      });
    }

    // The "Running now" card, shown only while a task is running; the newest log line per task is fetched here.
    var ovLines = {};

    function loadOverviewRunning(m) {
      var rows = runningRows(m.phases), card = document.getElementById('ov-running-card');
      card.style.display = rows.length ? '' : 'none';
      document.getElementById('ov-running-count').textContent = rows.length;
      var starts = {};
      (m.events || []).forEach(function(ev) { if (ev.kind === 'task.started' && ev.id) starts[ev.id] = ev.ts; });
      var draw = function() { setHtml(document.getElementById('ov-running'), overviewRunningHtml(rows, starts, ovLines)); };
      draw();
      if (!m.runId) return;
      rows.slice(0, 5).forEach(function(r) {
        fetchJSON('/api/runs/' + encodeURIComponent(m.runId) + '/tasks/' + encodeURIComponent(r.t.id) + '/log', 'GET').then(function(log) {
          var lines = (log && log.available && log.lines) || [];
          for (var i = lines.length - 1; i >= 0; i--) if (String(lines[i]).trim()) { ovLines[r.t.id] = String(lines[i]).trim(); break; }
          draw();
        });
      });
    }

    function loadOverview() {
      Promise.all([loadMergedTasks(), fetchJSON('/api/decisions', 'GET'), fetchJSON('/api/runs', 'GET'), fetchJSON('/api/features', 'GET')]).then(function(r) {
        var m = r[0], decisions = (r[1] && r[1].open) || [], runs = r[2] || [];
        setHtml(document.getElementById('ov-progress'), overviewProgressHtml(overviewCounts(m.phases), m.run, r[3] && r[3].active));
        var needs = overviewNeedsHtml(m.phases, decisions);
        document.getElementById('ov-needs-count').textContent = needs.count;
        setHtml(document.getElementById('ov-needs'), needs.html);
        setHtml(document.getElementById('ov-next'), overviewNextHtml(m.phases));
        loadOverviewRunning(m);
        setHtml(document.getElementById('ov-phases'), overviewPhasesHtml(m.phases));
        setHtml(document.getElementById('ov-runs'), overviewRunsHtml(runs));
      }).catch(function(e) { console.error('loadOverview error', e); });
    }

    // The run whose events give the transient states: the run picked in the Jobs view, else the
    // run in .sdd/run.json. `paused` (§4.5) applies only when that run is the current one.
    function loadTasks() {
      loadMergedTasks().then(function(m) { renderTasks(m.phases, m.runId); })
        .catch(function(e) { console.error('loadTasks error', e); });
    }

    // Finished tasks fold into one line per phase (closed unless the person opened it; remembered across refreshes).
    var openDone = {};

    function phaseTasksHtml(p) {
      var tasks = p.tasks || [], done = [], rest = [];
      tasks.forEach(function(t) { (taskStatusKey(t) === 'done' ? done : rest).push(t); });
      var html = '';
      if (done.length) {
        html += '<details class="done-fold" data-phase="' + escapeHtml(p.title) + '"' + (openDone[p.title] ? ' open' : '') + '><summary>✓ ' +
          (rest.length ? done.length + ' done' : 'All ' + done.length + ' done') + '</summary><div class="done-list">' +
          done.map(taskCardHtml).join('') + '</div></details>';
      }
      return rest.map(taskCardHtml).join('') + html;
    }

    // Phases with work left first, finished phases last; each keeps its file order.
    function phasesOpenFirst(phases) {
      function finished(p) {
        var tasks = p.tasks || [];
        return tasks.length > 0 && tasks.every(function(t) { return taskStatusKey(t) === 'done'; });
      }
      return phases.filter(function(p) { return !finished(p); }).concat(phases.filter(finished));
    }

    function renderTasks(phases, runId) {
      var waiting = '', count = 0, list = '', running = [];
      markNotYet(phases);
      if (pendingTask) (phases || []).forEach(function(p) {
        if ((p.tasks || []).some(function(t) { return t.id === pendingTask; })) openDone[p.title] = true;
      });
      phasesOpenFirst(phases).forEach(function(p) {
        (p.tasks || []).forEach(function(t) {
          if (isWaiting(t)) { waiting += waitingItemHtml(t, p.title); count++; }
          if (showsLog(t)) running.push(t.id);
        });
        list += phaseHeadingHtml(p) + '<div class="phase-tasks">' + phaseTasksHtml(p) + '</div>';
      });
      document.getElementById('waiting-count').textContent = count;
      setHtml(document.getElementById('waiting-list'), waiting ||
        '<div class="empty"><div>Nothing is waiting on you.</div></div>');
      fillManualSteps(document.getElementById('waiting-list'));
      var tasksEl = document.getElementById('tasks-list');
      var live = {};
      tasksEl.querySelectorAll('.live-output-body').forEach(function(b) { live[b.getAttribute('data-live-task')] = liveScrollState(b); });
      if (setHtml(tasksEl, list || '<div class="empty"><div>No tasks.</div></div>')) {
        tasksEl.querySelectorAll('.live-output-body').forEach(function(b) {
          var id = b.getAttribute('data-live-task');
          b._html = liveOutputBodyHtml(liveLogs[id]);
          if (live[id]) restoreLiveScroll(b, live[id]);
        });
      }
      pollLiveOutputs(runId, running);
      if (pendingTask) {
        var target = tasksEl.querySelector('[data-task-card="' + pendingTask + '"]');
        pendingTask = null;
        if (target) { target.scrollIntoView({ block: 'center' }); target.classList.add('flash'); setTimeout(function() { target.classList.remove('flash'); }, 1500); }
      }
    }

    // Fetch the live output of each running task; loadTasks runs every 3 s (refreshAll), so this
    // polls /api/runs/<id>/tasks/<task>/log every 3 s while the Tasks view is open.
    // A live panel follows the newest line only while it is scrolled to the bottom; scrolled up, it stays put.
    function liveScrollState(el) {
      var pre = el.querySelector('pre');
      if (!pre) return { follow: true, top: 0 };
      return { follow: pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8, top: pre.scrollTop };
    }

    function restoreLiveScroll(el, state) {
      var pre = el.querySelector('pre');
      if (pre) pre.scrollTop = state.follow ? pre.scrollHeight : state.top;
    }

    function pollLiveOutputs(runId, ids) {
      Object.keys(liveLogs).forEach(function(id) { if (ids.indexOf(id) < 0) delete liveLogs[id]; });
      if (!runId) {
        ids.forEach(function(id) {
          liveLogs[id] = { available: false, reason: 'no run to read a log from' };
          var el = document.querySelector('.live-output-body[data-live-task="' + id + '"]');
          if (el) { el.innerHTML = liveOutputBodyHtml(liveLogs[id]); el._html = el.innerHTML; }
        });
        return;
      }
      ids.forEach(function(id) {
        fetchJSON('/api/runs/' + encodeURIComponent(runId) + '/tasks/' + encodeURIComponent(id) + '/log', 'GET').then(function(log) {
          liveLogs[id] = log || { available: false, reason: 'request failed' };
          var el = document.querySelector('.live-output-body[data-live-task="' + id + '"]');
          if (!el) return;
          var html = liveOutputBodyHtml(liveLogs[id]);
          if (el._html === html) return;
          var state = liveScrollState(el);
          el.innerHTML = html;
          el._html = html;
          restoreLiveScroll(el, state);
        });
      });
    }

    // Decisions (FR-033): the open [decision] tasks, each with a box for the person's answer. An answer is
    // saved outside the project; /sdd-unblock applies it. The list is not redrawn while a box has focus.
    function decisionOptions(text) {
      var seen = {}, out = [], re = /\\(([a-z])\\)/g, m;
      while ((m = re.exec(text || ''))) { if (!seen[m[1]]) { seen[m[1]] = true; out.push(m[1]); } }
      return out;
    }

    // A decision task's text is one paragraph: "<question> Options: (a) …; (b) …; (c) …. Recommend (b). <what to record>".
    // Show it as the question, a list of options (the recommended one marked) and the rest, with `code` styled.
    function decisionTextHtml(text) {
      text = String(text || '');
      var m = text.match(/^([\\s\\S]*?)\\s*Options?:\\s*([\\s\\S]*)$/);
      if (!m) return '<div class="decision-text">' + inlineCode(text) + '</div>';
      var question = m[1], rest = m[2], after = '', rec = null;
      var r = rest.match(/^([\\s\\S]*?)\\s*Recommend(?:ed)?:?\\s*\\(([a-z])\\)[^.]*\\.?\\s*([\\s\\S]*)$/);
      if (r) { rest = r[1]; rec = r[2]; after = r[3]; }
      var parts = rest.split(/(?:^|[;.]?\\s+)\\(([a-z])\\)\\s+/).filter(function(x) { return x !== ''; });
      var items = [];
      for (var i = 0; i + 1 < parts.length; i += 2) items.push({ key: parts[i], text: parts[i + 1].replace(/[;.\\s]+$/, '') });
      if (!items.length) return '<div class="decision-text">' + inlineCode(text) + '</div>';
      return '<div class="decision-text"><p>' + inlineCode(question) + '</p>' +
        '<ul class="decision-choices">' + items.map(function(o) {
          return '<li' + (o.key === rec ? ' class="recommended"' : '') + '><span class="choice-key">(' + escapeHtml(o.key) + ')</span> ' +
            inlineCode(o.text) + (o.key === rec ? ' <span class="choice-rec">recommended</span>' : '') + '</li>';
        }).join('') + '</ul>' +
        (after ? '<p class="decision-after">' + inlineCode(after) + '</p>' : '') + '</div>';
    }

    function decisionHtml(d) {
      var opts = decisionOptions(d.text).map(function(o) {
        return '<button class="btn decision-option" data-task="' + escapeHtml(d.id) + '" data-option="' + escapeHtml(o) + '">Choose (' + escapeHtml(o) + ')</button>';
      }).join(' ');
      var pending = d.pending
        ? '<div class="decision-saved">Your answer is saved: <em>' + escapeHtml(d.pending.answer) + '</em>. ' +
          'It is not applied yet. <button class="btn decision-apply" data-task="' + escapeHtml(d.id) + '">Apply now</button> ' +
          'You can change the answer until it is applied.</div>'
        : '';
      return '<div class="blocked-item decision-item" data-task="' + escapeHtml(d.id) + '">' +
        '<div class="blocked-header"><div class="blocked-id">' + escapeHtml(d.id) + '</div>' + statusBadge('held', { tag: 'decision' }) + '</div>' +
        decisionTextHtml(d.text) +
        (opts ? '<div class="decision-options">' + opts + '</div>' : '') +
        '<textarea class="decision-answer" id="answer-' + escapeHtml(d.id) + '" rows="3" placeholder="Your decision, and why"></textarea>' +
        '<button class="btn decision-submit" data-task="' + escapeHtml(d.id) + '">Save decision</button>' + pending + '</div>';
    }

    function loadDecisions() {
      fetchJSON('/api/decisions', 'GET').then(function(data) {
        data = data || {};
        var el = document.getElementById('decisions-list');
        var open = data.open || [];
        if (el.contains(document.activeElement) && document.activeElement.tagName === 'TEXTAREA') return;
        if (open.length === 0) {
          el.innerHTML = '<div class="empty"><div class="empty-icon">✅</div><div>No decisions are waiting on you.' +
            (data.recorded ? '' : ' None have been recorded yet (no decisions.md).') + '</div></div>';
          return;
        }
        el.innerHTML = open.map(decisionHtml).join('');
        open.forEach(function(d) {
          var box = document.getElementById('answer-' + d.id);
          if (box && d.pending) box.value = d.pending.answer;
        });
      }).catch(function(e) { console.error('loadDecisions error', e); });
    }

    // Runs view (009 FR-021, FR-022). A run's record keeps 005's internal names (`job`, /api/jobs), but
    // every word the person reads says "run". The helpers from here to "End of the Runs view helpers" read
    // nothing but their arguments, so tests/test_sdd_dashboard.py runs them in node.
    var JOB_STATE_WORDS = { working: 'Working', paused: 'Paused: usage limit', suspended: 'Suspended', ended: 'Ended' };
    // How an ended run ended (009 FR-002).
    var JOB_OUTCOME_WORDS = { done: 'Done', stopped: 'Stopped', limit: 'Limit reached', waiting: 'Waiting on you',
      failed: 'Failed', dropped: 'Dropped' };
    var JOB_LIMIT_NAMES = ['max_tasks', 'until', 'max_runs'];
    var JOB_OPEN_LANDINGS = ['needs-merge', 'pending'];

    function hhmm(epoch) {
      var d = new Date(epoch * 1000);
      return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
    }

    // The shown state (sdd-job.py shown_state): working, paused, suspended or ended.
    function jobShown(job) { return (job && job.shown && job.shown.state) || (job && job.state) || 'unknown'; }

    // An ended run's landing ({state, commit, files}) or {} (sdd-job.py shown_state).
    function jobLanding(job) { return (job && job.shown && job.shown.landing) || {}; }

    // The reason in words: the shown one (sdd-job.py shown_state may give one of its own), else the record's.
    function jobReason(job) { return (job && job.shown && job.shown.reason) || (job && job.reason) || null; }

    // A run blocks a new one while working, paused or suspended, or ended with its landing still to do
    // (sdd-job.py is_open).
    function jobIsOpen(job) {
      var s = jobShown(job);
      return ['working', 'paused', 'suspended'].indexOf(s) >= 0 ||
        (s === 'ended' && JOB_OPEN_LANDINGS.indexOf(jobLanding(job).state) >= 0);
    }

    // The CSS state class of the card's state chip (.job-<key>).
    function jobStateKey(job) {
      var s = jobShown(job), landing = jobLanding(job).state;
      if (s !== 'ended') return s;
      if (landing === 'needs-merge') return 'needs-merge';
      if (landing === 'pending') return 'not-landed';
      return JOB_OUTCOME_WORDS[job.shown.outcome] ? job.shown.outcome : 'ended';
    }

    function jobStateText(job) {
      var s = jobShown(job);
      if (s === 'paused' && job.shown.resets_at) return 'Paused until ' + hhmm(job.shown.resets_at) + ': usage limit';
      if (s === 'ended') {
        var landing = jobLanding(job).state, how = JOB_OUTCOME_WORDS[job.shown.outcome] || 'Ended';
        if (landing === 'needs-merge') return 'Needs merge';
        if (landing === 'pending') return how + ', not landed';
        return how;
      }
      return JOB_STATE_WORDS[s] || s;
    }

    function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }

    // The banner and Overview headline for the run (009 FR-022), never a run id. `activity` is what the
    // agents are doing ({id, step, since}) or null; `keepPaused` overrides shown.keep_paused while a flip of
    // the usage checkbox waits for the supervisor; `now` is in ms.
    function jobBannerText(job, activity, keepPaused, now) {
      if (!job) return 'No run going';
      var s = jobShown(job), title = job.title || 'the run', p = job.progress || {};
      var base = (job.base && job.base.branch) || 'the base branch';
      if (s === 'working') {
        if (!activity) return 'Working on ' + title;
        var mins = Math.floor(((now || Date.now()) - Date.parse(activity.since)) / 60000);
        return 'Working on ' + title + ': ' + activity.id + ', ' + activity.step +
          (isFinite(mins) && mins >= 0 ? ' (' + (mins < 1 ? 'just started' : mins + ' min') + ')' : '');
      }
      if (s === 'paused') {
        var keep = keepPaused === undefined || keepPaused === null ? !!job.shown.keep_paused : keepPaused;
        return 'Paused until ' + (job.shown.resets_at ? hhmm(job.shown.resets_at) : '?') + ': usage limit; ' +
          (keep ? 'stays paused' : 'continues by itself');
      }
      if (s === 'suspended') {
        var tasks = job.in_flight || [];
        return 'Suspended during ' + (tasks.length ? tasks.join(', ') : 'its last task') + ': ' +
          (jobReason(job) || 'the run was cut off') + '. Continue or Drop';
      }
      if (s === 'ended') {
        var landing = jobLanding(job), n = typeof job.tasks_done === 'number' ? job.tasks_done : (p.done || 0);
        if (landing.state === 'landed') return title + ' ended: ' + plural(n, 'task') + ' landed on ' + base;
        if (landing.state === 'needs-merge') return title + ' ended: needs merge (conflicts in ' + ((landing.files || []).join(', ') || 'some files') + ')';
        if (landing.state === 'pending') return title + ' ended: ' + plural(n, 'task') + ' not landed on ' + base + ' yet';
        return title + ' ended: nothing landed';
      }
      return title + ' ' + s;
    }

    // The usage checkbox's label while the run is paused (research R11).
    function jobKeepLabel(job) {
      return 'Continue when the limit resets (' + (job && job.shown && job.shown.resets_at ? hhmm(job.shown.resets_at) : '?') + ')';
    }

    // The Overview pill's dot class (.ov-run.<class>) for a run's shown state.
    function jobPillClass(job) {
      var s = jobShown(job), landing = jobLanding(job).state;
      if (s === 'working') return 'running';
      if (s === 'paused' || landing === 'pending') return 'paused';
      return s === 'suspended' || landing === 'needs-merge' ? 'stale' : 'none';
    }

    // The limit named in a suspended run's reason ("max_tasks 10 reached"), as sdd-launch.py reads it; else null.
    function limitInReason(job) {
      var reason = jobReason(job);
      if (!job || jobShown(job) !== 'suspended' || typeof reason !== 'string' || !/ reached$/.test(reason)) return null;
      var name = reason.split(' ')[0];
      return JOB_LIMIT_NAMES.indexOf(name) >= 0 ? name : null;
    }

    // Which buttons a run offers (009 plan "Engine changes" 6): Continue and Drop while suspended, Merge when
    // its landing needs a merge, Land when its landing is still to do, nothing otherwise.
    function jobActions(job) {
      var s = jobShown(job), landing = jobLanding(job).state;
      return { 'continue': s === 'suspended', drop: s === 'suspended',
               merge: s === 'ended' && landing === 'needs-merge', land: s === 'ended' && landing === 'pending' };
    }

    // The offered buttons, in the order Continue, Drop, Merge, Land.
    var JOB_BUTTONS = [['continue', 'Continue'], ['drop', 'Drop'], ['merge', 'Merge'], ['land', 'Land']];
    function jobButtonsHtml(job) {
      var a = jobActions(job);
      return JOB_BUTTONS.filter(function(b) { return a[b[0]]; })
        .map(function(b) { return jobButtonHtml(job, b[0], b[1], true); }).join('');
    }

    function jobButtonHtml(job, action, label, enabled, why) {
      return '<button type="button" class="btn job-btn" data-job-action="' + action + '" data-job="' + escapeHtml(job.id) + '"' +
        (enabled ? '' : ' disabled title="' + escapeHtml(why) + '"') + '>' + label + '</button>';
    }

    // What the landing says under the card: the files of a conflict, or why a land could not run.
    function jobLandingHtml(job) {
      var landing = jobLanding(job), base = (job.base && job.base.branch) || 'the base branch';
      if (jobShown(job) !== 'ended') return '';
      if (landing.state === 'needs-merge') {
        return '<div class="job-reason job-landing">Needs merge: the merge into ' + escapeHtml(base) + ' conflicts in ' +
          escapeHtml((landing.files || []).join(', ') || 'some files') + '. Resolve it, then press Merge.</div>';
      }
      if (landing.state === 'pending') {
        return '<div class="job-reason job-landing">Not landed on ' + escapeHtml(base) + ' yet' +
          (landing.reason ? ': ' + escapeHtml(landing.reason) : '') + '. Land brings its tasks in.</div>';
      }
      return '';
    }

    // Progress over the run's scope (sdd-job.py progress): a bar of done, waiting on you and left, and the counts.
    function jobProgressHtml(p) {
      if (!p) return '<div class="job-reason">Progress is not available: the tasks file cannot be read.</div>';
      var total = p.total || 0, pct = function(n) { return total ? (n * 100 / total) : 0; };
      return '<div class="ov-bar job-bar">' +
        '<span style="width:' + pct(p.done) + '%;background:var(--st-done)"></span>' +
        '<span style="width:' + pct(p.waiting_on_you) + '%;background:var(--st-held)"></span>' +
        '<span style="width:' + pct(p.left) + '%;background:var(--st-queued)"></span></div>' +
        '<div class="ov-legend job-counts"><span><b>' + p.done + '</b> of ' + total + ' done</span>' +
        '<span><b>' + p.waiting_on_you + '</b> waiting on you</span><span><b>' + p.left + '</b> left</span></div>';
    }

    // One run card: title, shown state in words, branch, the reason, the landing, progress and the buttons
    // its state offers (none for a run that ended and landed).
    function jobCardHtml(job) {
      var buttons = jobButtonsHtml(job), landing = jobLanding(job), reason = jobReason(job);
      var base = job.base && job.base.branch ? ' from ' + escapeHtml(job.base.branch) : '';
      var landed = landing.state === 'landed' && landing.commit ? ' · landed as <span class="mono">' + escapeHtml(String(landing.commit).slice(0, 7)) + '</span>' : '';
      return '<div class="job-head" data-job-toggle="' + escapeHtml(job.id) + '">' +
        '<span class="job-title">' + escapeHtml(job.title || job.id) + '</span>' +
        '<span class="job-state job-' + escapeHtml(jobStateKey(job)) + '">' + escapeHtml(jobStateText(job)) + '</span>' +
        '<span class="job-branch">' + escapeHtml(job.branch || '') + base + landed + '</span></div>' +
        (reason ? '<div class="job-reason">' + escapeHtml(reason) + '</div>' : '') + jobLandingHtml(job) +
        jobProgressHtml(job.progress) + (buttons ? '<div class="job-actions">' + buttons + '</div>' : '');
    }

    // The sessions of a run, as GET /api/jobs/<id> picks them: `job` in workflows.json, or listed in the record.
    function jobRuns(job, runs) {
      var listed = (job.runs || []).map(String);
      return (runs || []).filter(function(r) { return r.job === job.id || listed.indexOf(r.id) >= 0; });
    }

    // Runs that belong to no Job ("Earlier runs", FR-015).
    function earlierRuns(jobs, runs) {
      return (runs || []).filter(function(r) {
        return !(jobs || []).some(function(j) { return jobRuns(j, [r]).length; });
      });
    }

    // The tasks a Job covered, over its runs (newest first, as /api/runs gives them): the newest outcome
    // per task and every commit, in the order the tasks were first worked on.
    function jobTasks(runs) {
      var order = [], byId = {};
      (runs || []).slice().reverse().forEach(function(run) {
        (run.tasks || []).forEach(function(t) {
          if (!byId[t.id]) { byId[t.id] = { id: t.id, outcome: t.outcome, commits: [], runs: [] }; order.push(t.id); }
          var e = byId[t.id];
          e.outcome = t.outcome;
          (t.commits || []).forEach(function(c) { if (e.commits.indexOf(c) < 0) e.commits.push(c); });
          e.runs.push(run.id);
        });
      });
      return order.map(function(id) { return byId[id]; });
    }

    // Rows of jobTasks(); a row opens the task in Tasks when the Job's feature is the one shown.
    function jobTasksHtml(tasks, here, texts) {
      if (!tasks.length) return '<div class="live-output-empty">No tasks worked on yet.</div>';
      return tasks.map(function(t) {
        return '<div class="run-task' + (here ? '' : ' other') + '"' + (here ? ' data-goto-task="' + escapeHtml(t.id) + '"' : '') + '>' +
          '<span class="id">' + escapeHtml(t.id) + '</span>' + outcomeHtml(t.outcome) +
          '<span class="txt">' + escapeHtml(here ? shortText((texts || {})[t.id] || '', 110) : '') + '</span>' +
          (t.commits.length ? '<span class="mono meta">' + escapeHtml(t.commits.join(', ')) + '</span>' : '') + '</div>';
      }).join('');
    }

    // "3, 4, 5, 7" -> "3–5, 7" (sdd-job.py _ranges), for the default title.
    function idRanges(ids) {
      if (!ids.every(function(i) { return /^[0-9]+$/.test(i); })) return ids.join(', ');
      var nums = ids.map(Number).filter(function(n, i, a) { return a.indexOf(n) === i; }).sort(function(a, b) { return a - b; });
      var parts = [], start = nums[0], prev = nums[0];
      nums.slice(1).concat([null]).forEach(function(n) {
        if (n !== null && n === prev + 1) { prev = n; return; }
        parts.push(start === prev ? String(start) : start + '–' + prev);
        if (n !== null) start = prev = n;
      });
      return parts.join(', ');
    }

    // The default title (FR-014, sdd-job.py default_title): "Phase 11", "Phases 3–5", "T061, T063", "All open tasks".
    function jobDefaultTitle(scope) {
      var phases = (scope && scope.phases) || [], tasks = (scope && scope.tasks) || [];
      if (phases.length) return phases.filter(function(p, i, a) { return a.indexOf(p) === i; }).length === 1 ? 'Phase ' + phases[0] : 'Phases ' + idRanges(phases);
      if (tasks.length) return tasks.join(', ');
      return 'All open tasks';
    }

    // "T061, t063 T64" -> {ids: ['T061', 'T063', 'T64'], bad: []}.
    function parseTaskIds(text) {
      var ids = [], bad = [];
      String(text || '').split(/[\\s,]+/).filter(Boolean).forEach(function(w) {
        var id = w.toUpperCase();
        if (/^T[0-9]+$/.test(id)) { if (ids.indexOf(id) < 0) ids.push(id); } else bad.push(w);
      });
      return { ids: ids, bad: bad };
    }

    // "18:30" -> epoch seconds of the next 18:30 after `now` (ms); null for anything else.
    function untilEpoch(text, now) {
      var m = /^([0-9]{1,2}):([0-9]{2})$/.exec(String(text || '').trim());
      if (!m || +m[1] > 23 || +m[2] > 59) return null;
      var d = new Date(now);
      d.setHours(+m[1], +m[2], 0, 0);
      if (d.getTime() <= now) d.setDate(d.getDate() + 1);
      return Math.floor(d.getTime() / 1000);
    }

    // A limit typed by the person: "none" -> null; max_tasks and max_runs a whole number >= 1; until a time HH:MM.
    // Returns {value} or {error}.
    function parseLimit(name, text, now) {
      var t = String(text == null ? '' : text).trim().toLowerCase();
      if (t === 'none') return { value: null };
      if (name === 'until') {
        var at = untilEpoch(t, now);
        return at === null ? { error: 'give a time as HH:MM, or none' } : { value: at };
      }
      return /^[0-9]+$/.test(t) && +t >= 1 ? { value: +t } : { error: 'give a whole number of 1 or more, or none' };
    }

    // The question Continue asks for a suspended run cut off by a limit (FR-009).
    function limitPrompt(job, name) {
      var what = { max_tasks: 'a new task cap (tasks done in the whole run)', until: 'a new stop-by time (HH:MM)',
                   max_runs: 'a new maximum number of sessions' }[name];
      return job.title + ' stopped: ' + jobReason(job) + '. Give ' + what + ', or none for no limit.';
    }

    // Start run (009 FR-022; 005 FR-003, FR-014): the scope (phases or tasks), the limits and a title.
    function startJobHtml(plan) {
      if (!plan) return '<div class="empty"><div>Could not read the run plan.</div></div>';
      var head = '<div class="start-summary">' + plan.runnable + ' runnable, ' + plan.held + ' held.</div>';
      if (!plan.can_start) return head + '<div class="start-reason">Cannot start: ' + escapeHtml(plan.reason) + '</div>';
      var phases = (plan.phases || []).map(function(p) {
        var tasks = (p.task_list || []).map(function(t) {
          var text = String(t.text || '');
          if (text.length > 110) text = text.slice(0, 107) + '...';
          return '<label class="start-task"><input type="checkbox" class="start-task-box" value="' + escapeHtml(t.id) + '" checked> ' +
            '<b>' + escapeHtml(t.id) + '</b> ' + escapeHtml(text) + '</label>';
        }).join('');
        return '<details class="start-phase" data-phase="' + escapeHtml(p.id) + '"><summary>' +
          '<input type="checkbox" class="start-phase-box" value="' + escapeHtml(p.id) + '" checked> ' +
          'Phase ' + escapeHtml(p.id) + ': ' + escapeHtml(p.title) + ' <span class="start-count">' + p.runnable + ' task' + (p.runnable === 1 ? '' : 's') + '</span></summary>' +
          '<div class="start-tasks">' + tasks + '</div></details>';
      }).join('');
      return head +
        '<div class="start-option">Phases <button class="btn" type="button" id="start-all-btn">Select all</button> ' +
        '<button class="btn" type="button" id="start-none-btn">Select none</button></div>' +
        '<div class="start-scope">' + (phases || '<div class="start-reason">No phase has a runnable task.</div>') + '</div>' +
        '<div class="start-limits">' +
        '<label class="start-option">Task cap <input type="number" id="start-max-tasks" min="1" max="500" placeholder="none" style="width:5rem"></label>' +
        '<label class="start-option">Stop by <input type="time" id="start-until"></label>' +
        '<label class="start-option">Max runs <input type="number" id="start-max-runs" min="1" max="500" placeholder="20" style="width:5rem"></label></div>' +
        '<label class="start-option">Title <input type="text" id="start-title" placeholder="the scope in words" style="width:16rem"></label>' +
        '<div><button class="btn" id="start-job-btn">Start run</button></div>';
    }

    // The POST /api/jobs/start body from the form's values ({scope, phases, tasks, max_tasks, until, max_runs,
    // title}, all text but `phases`), or {error}. `count` is the number of tasks the scope names.
    function startJobBody(v, now) {
      var body = {}, count = 0;
      if (v.scope === 'tasks') {
        var parsed = parseTaskIds(v.tasks);
        if (parsed.bad.length) return { error: 'not a task id: ' + parsed.bad.join(', ') };
        if (!parsed.ids.length) return { error: 'tick at least one task' };
        body.tasks = parsed.ids;
        count = parsed.ids.length;
      } else {
        if (!(v.phases || []).length) return { error: 'tick at least one task' };
        body.phases = v.phases.map(function(p) { return p.id; });
        v.phases.forEach(function(p) { count += p.runnable || 0; });
      }
      var limits = { max_tasks: v.max_tasks, until: v.until, max_runs: v.max_runs };
      for (var i = 0; i < JOB_LIMIT_NAMES.length; i++) {
        var name = JOB_LIMIT_NAMES[i], text = String(limits[name] || '').trim();
        if (!text) continue;
        var got = parseLimit(name, text, now);
        if (got.error) return { error: name.replace('_', ' ') + ': ' + got.error };
        body[name] = got.value;
      }
      var title = String(v.title || '').trim();
      if (title) body.title = title;
      return { body: body, count: count, title: title || jobDefaultTitle(body) };
    }

    // The question Drop asks before dropping a suspended run (009 FR-010).
    function jobDropQuestion(job) {
      var base = (job.base && job.base.branch) || 'the base branch';
      return 'Drop ' + (job.title || 'this run') + '? The half-done work is stashed and saved in the task\\'s history; ' +
        'finished tasks land on ' + base + '.';
    }

    // The toast after a card button: `r` is postJSON's answer ({ok, status, body}).
    function jobDoneText(job, action, r) {
      var base = (job.base && job.base.branch) || 'the base branch', b = (r && r.body) || {};
      if (!r || !r.ok) return 'Could not ' + action + ' the run: ' + (b.error || 'request failed (' + (r ? r.status : '?') + ')');
      var sha = b.commit ? ' (' + String(b.commit).slice(0, 7) + ')' : '';
      if (action === 'continue') return 'Run continues';
      if (action === 'merge') return 'Merged into ' + base + sha;
      if (action === 'land') return b.landing && b.landing.state === 'nothing' ? 'Nothing to land on ' + base : 'Landed on ' + base + sha;
      if (action === 'drop') return 'Run dropped' + (b.stash ? '; the half-done work is in the stash' : '');
      return 'Done';
    }
    // End of the Runs view helpers.

    function loadStartJob() {
      var el = document.getElementById('start-job-body');
      if (!el || (el.contains(document.activeElement) && document.activeElement.tagName === 'INPUT')) return;
      fetchJSON('/api/jobs/plan', 'GET').then(function(plan) {
        var key = JSON.stringify(plan);
        if (el._key === key) return;  // keep what the person ticked or typed while nothing changed
        el._key = key;
        el._plan = plan;
        el.innerHTML = startJobHtml(plan);
      });
    }

    // The Start run tick boxes: a phase box is ticked when all its tasks are, empty when none, mixed otherwise.
    function syncStartBoxes() {
      var el = document.getElementById('start-job-body');
      if (!el) return;
      [].slice.call(el.querySelectorAll('.start-phase')).forEach(function(d) {
        var tasks = [].slice.call(d.querySelectorAll('.start-task-box'));
        var on = tasks.filter(function(b) { return b.checked; }).length;
        var box = d.querySelector('.start-phase-box');
        box.checked = tasks.length > 0 && on === tasks.length;
        box.indeterminate = on > 0 && on < tasks.length;
      });
    }
    function setAllStartBoxes(on) {
      var el = document.getElementById('start-job-body');
      if (!el) return;
      [].slice.call(el.querySelectorAll('.start-task-box, .start-phase-box')).forEach(function(b) { b.checked = on; });
      syncStartBoxes();
    }
    document.addEventListener('change', function(ev) {
      var t = ev.target;
      if (!t || !t.classList) return;
      if (t.classList.contains('start-phase-box')) {
        [].slice.call(t.closest('.start-phase').querySelectorAll('.start-task-box')).forEach(function(b) { b.checked = t.checked; });
      }
      if (t.classList.contains('start-phase-box') || t.classList.contains('start-task-box')) syncStartBoxes();
    });

    // What the boxes name: whole phases when every task of each ticked phase is ticked, else the ticked task ids.
    function startScopeFromBoxes(plan, el) {
      var picked = [], whole = true, phases = [];
      (plan.phases || []).forEach(function(p) {
        var d = el.querySelector('.start-phase[data-phase="' + p.id + '"]');
        if (!d) return;
        var boxes = [].slice.call(d.querySelectorAll('.start-task-box'));
        var ids = boxes.filter(function(b) { return b.checked; }).map(function(b) { return b.value; });
        if (!ids.length) return;
        if (ids.length < boxes.length) whole = false;
        picked = picked.concat(ids);
        phases.push(p);
      });
      return whole ? { scope: 'phases', phases: phases, tasks: '' } : { scope: 'tasks', phases: [], tasks: picked.join(' ') };
    }

    function startJob() {
      var el = document.getElementById('start-job-body'), plan = el._plan || {};
      var chosen = startScopeFromBoxes(plan, el);
      var made = startJobBody({
        scope: chosen.scope,
        phases: chosen.phases,
        tasks: chosen.tasks,
        max_tasks: document.getElementById('start-max-tasks').value,
        until: document.getElementById('start-until').value,
        max_runs: document.getElementById('start-max-runs').value,
        title: document.getElementById('start-title').value
      }, Date.now());
      if (made.error) { toast('Cannot start: ' + made.error); return; }
      if (!window.confirm('Start the run "' + made.title + '" (' + made.count + ' task' + (made.count === 1 ? '' : 's') + ')? ' +
          'It runs unattended until its scope is done, waits on you, or reaches a limit; it makes commits on its own auto/ branch, ' +
          'lands them on the base branch when it ends, and uses your usage.')) return;
      postJSON('/api/jobs/start', made.body).then(function(r) {
        toast(r.ok ? 'Run started: ' + made.title : 'Could not start: ' + (r.body.error || 'unknown error'));
        el._key = null;
        if (r.ok) expandedJob = r.body.job || null;
        loadStartJob(); loadJobs(); loadRunState();
      }).catch(function(e) { console.error('startJob error', e); toast('Could not start the run'); });
    }

    // POST a JSON body; resolves to {ok, status, body} (a 409's reason is in body.error).
    function postJSON(url, body) {
      return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
        .then(function(res) {
          return res.json().catch(function() { return {}; }).then(function(d) { return { ok: res.ok, status: res.status, body: d || {} }; });
        });
    }

    // The card buttons (009 plan "Engine changes" 6). Continue asks for a new value first when a limit cut the run
    // off; Drop asks for confirmation; Merge and Land post at once (a conflict's files come back in the 409).
    // Merge retries a landing that conflicted, so it posts to /land as Land does (sdd-job.py land).
    function jobAction(id, action) {
      var job = jobsById[id];
      if (!job) return;
      var url = '/api/jobs/' + encodeURIComponent(id) + '/' + (action === 'merge' ? 'land' : action), body = {};
      if (action === 'stop') {
        url = '/api/run/stop';
        body = { mode: 'after-task' };
      } else if (action === 'continue') {
        var name = limitInReason(job);
        if (name) {
          var text = window.prompt(limitPrompt(job, name), '');
          if (text === null) return;
          var got = parseLimit(name, text, Date.now());
          if (got.error) { toast('Not continued: ' + got.error); return; }
          body[name] = got.value;
        }
      } else if (action === 'drop') {
        if (!window.confirm(jobDropQuestion(job))) return;
      }
      postJSON(url, body).then(function(r) {
        toast(action === 'stop' && r.ok ? 'Stopping after the current task' : jobDoneText(job, action, r));
        loadJobs(); loadStartJob(); loadRunState();
      }).catch(function(e) { console.error('jobAction error', e); toast('Could not ' + action + ' the run'); });
    }

    function applyDecision(task) {
      fetch('/api/decisions/apply', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ task: task })
      }).then(function(res) {
        return res.json().then(function(d) { return { ok: res.ok, status: res.status, body: d || {} }; });
      }).then(function(r) {
        if (!r.ok) { toast('Could not apply the decision (' + r.status + ')'); return; }
        toast(r.body.applied ? 'Decision applied and committed (' + r.body.commit + ')' : 'Not applied: ' + r.body.reason);
        loadDecisions();
      }).catch(function(e) { console.error('applyDecision error', e); toast('Could not apply the decision'); });
    }

    function saveDecision(task) {
      var box = document.getElementById('answer-' + task);
      var answer = box ? box.value.trim() : '';
      if (!answer) { toast('Write your decision first'); return; }
      fetch('/api/decisions/answer', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task: task, answer: answer })
      }).then(function(res) {
        return res.json().then(function(d) { return { ok: res.ok, status: res.status, body: d || {} }; });
      }).then(function(r) {
        if (!r.ok) { toast('Could not save the decision (' + r.status + ')'); return; }
        toast(r.body.applied ? 'Decision applied and committed (' + r.body.commit + ')' : 'Saved, not applied: ' + r.body.reason);
        if (box) box.blur();
        loadDecisions();
      }).catch(function(e) { console.error('saveDecision error', e); toast('Could not save the decision'); });
    }

    document.addEventListener('click', function(ev) {
      var t = ev.target;
      if (!t || !t.classList) return;
      if (t.classList.contains('decision-submit')) saveDecision(t.getAttribute('data-task'));
      if (t.classList.contains('decision-apply')) applyDecision(t.getAttribute('data-task'));
      var card = t.closest ? t.closest('[data-task-card]') : null;
      if (card && !t.closest('.live-output, button, a, textarea, [data-run], [data-open-job]') &&!(window.getSelection && String(window.getSelection()))) {
        var tid = card.getAttribute('data-task-card');
        if (openLogs[tid]) delete openLogs[tid]; else openLogs[tid] = true;
        loadTasks();
      }
      var goto = t.closest ? t.closest('[data-goto-task]') : null;
      if (goto) {
        pendingTask = goto.getAttribute('data-goto-task');
        openLogs[pendingTask] = true;
        showSection('tasks');
        loadTasks();
        return;
      }
      if (t.classList.contains('unblock-btn')) settleBlocked(t.getAttribute('data-task'), t.getAttribute('data-action'));
      if (t.classList.contains('manual-done')) settleManual(t.getAttribute('data-task'), 'done');
      if (t.classList.contains('manual-skip')) settleManual(t.getAttribute('data-task'), 'skip');
      if (t.id === 'start-job-btn') startJob();
      if (t.id === 'start-all-btn') setAllStartBoxes(true);
      if (t.id === 'start-none-btn') setAllStartBoxes(false);
      if (t.hasAttribute('data-job-action') && !t.disabled) { jobAction(t.getAttribute('data-job'), t.getAttribute('data-job-action')); return; }
      var head = t.closest ? t.closest('[data-job-toggle]') : null;
      if (head) {
        var jid = head.getAttribute('data-job-toggle');
        expandedJob = expandedJob === jid ? null : jid;
        loadJobs();
      }
      if (t.classList.contains('decision-option')) {
        var box = document.getElementById('answer-' + t.getAttribute('data-task'));
        if (box) { box.value = '(' + t.getAttribute('data-option') + ') ' + box.value.replace(/^\\([a-z]\\)\\s*/, ''); box.focus(); }
      }
    });

    function loadBlocked() {
      fetchJSON('/api/project/blocked', 'GET').then(function(data) {
        data = data || {};
        var entries = data.entries || [];
        var el = document.getElementById('blocked-list');
        if (entries.length === 0) {
          setHtml(el, '<div class="empty"><div class="empty-icon">✅</div><div>No BLOCKED.md entries.</div></div>');
          return;
        }
        setHtml(el, entries.map(entryItemHtml).join(''));  // redraws only on change, so a typed answer stays
      }).catch(function(e) { console.error('loadBlocked error', e); });
    }

    function viewFile(kind) {
      if (!DOC_TITLES[kind]) return;
      showSection(kind);
      if (RAW_DOCS[kind]) setRawDoc(kind, true);
    }

    var runTaskText = {}, activeFeature = null;

    function minutesBetween(a, b) {
      var ms = Date.parse(b) - Date.parse(a);
      return isFinite(ms) && ms >= 0 ? Math.max(1, Math.round(ms / 60000)) + ' min' : '';
    }

    // One row per task the run touched, newest first so the running task is on top; a row opens the task in Tasks
    // when the run was for the selected feature.
    function runTasksHtml(run) {
      var tasks = (run.tasks || []).map(function(t, i) { return { t: t, i: i }; }).sort(function(a, b) {
        var x = String(a.t.started || ''), y = String(b.t.started || '');
        return x < y ? 1 : x > y ? -1 : b.i - a.i;
      }).map(function(x) { return x.t; });
      if (!tasks.length) return '<div class="live-output-empty">No tasks recorded for this run.</div>';
      var here = run.feature && run.feature === activeFeature;
      return (here ? '' : '<div class="live-output-empty">This run was for ' + escapeHtml(run.feature || 'an unknown feature') +
          '; choose that feature in Docs → Spec to open its tasks.</div>') +
        tasks.map(function(t) {
          return '<div class="run-task' + (here ? '' : ' other') + '"' + (here ? ' data-goto-task="' + escapeHtml(t.id) + '"' : '') + '>' +
            '<span class="id">' + escapeHtml(t.id) + '</span>' + outcomeHtml(t.outcome) +
            '<span class="txt">' + escapeHtml(here ? shortText(runTaskText[t.id] || '', 110) : '') + '</span>' +
            (t.commits.length ? '<span class="mono meta">' + escapeHtml(t.commits.join(', ')) + '</span>' : '') +
            '<span class="meta">' + escapeHtml((t.attempts > 1 ? t.attempts + ' attempts · ' : '') + minutesBetween(t.started, t.ended)) + '</span></div>';
        }).join('');
    }

    // One run as a row; a click opens its tasks, events and report right under it (the run detail).
    function runItemEl(run) {
      var div = document.createElement('div');
      div.className = 'run-item' + (selectedRunId === run.id ? ' active' : '');
      div.addEventListener('click', function() {
        selectedRunId = run.id;
        if (expandedRun === run.id) { expandedRun = null; reportRun = null; }
        else { expandedRun = run.id; reportRun = null; }
        rd('report-card').style.display = 'none';
        loadJobs();
        loadEvents();
        loadReport();
      });
      var statusCls = run.finished ? (run.stopped ? 'status-stopped' : 'status-done') : run.stopped ? 'status-stopped' : (run.interrupted > 0 ? 'status-paused' : 'status-running');
      var finishedText = run.finished ? (run.stopped ? 'stopped' : 'done') : run.stopped ? 'stopped before its report' : 'running';
      div.innerHTML = '<div class="run-info"><div class="run-id">#' + escapeHtml(run.id) + '</div><div class="run-meta"><span class="pill ' + statusCls + '"><span class="pill-dot"></span>' + finishedText + '</span> ' + run.approved + ' approved · ' + run.blocked + ' blocked · ' + run.interrupted + ' interrupted · ' + run.events_count + ' events</div></div>';
      var block = document.createElement('div');
      block.className = 'run-block';
      block.appendChild(div);
      if (expandedRun === run.id) block.appendChild(runDetailEl);
      return block;
    }

    // The Job whose card is open, the run an Overview row or task chip asked to show, and the Jobs by id.
    var expandedJob = null, revealRun = null, jobsById = {};

    // Jobs view (005 FR-011): Job cards from /api/jobs (open first, then newest); an open card lists the tasks
    // and runs of its Job; runs that belong to no Job are under "Earlier runs".
    function loadJobs() {
      Promise.all([fetchJSON('/api/jobs', 'GET'), fetchJSON('/api/runs', 'GET'), fetchJSON('/api/project/tasks', 'GET'),
                   fetchJSON('/api/features', 'GET')]).then(function(r) {
        var jobs = (r[0] && r[0].jobs) || [], runs = r[1] || [];
        activeFeature = (r[3] && r[3].active) || null;
        runTaskText = {};
        ((r[2] && r[2].phases) || []).forEach(function(p) { (p.tasks || []).forEach(function(t) { runTaskText[t.id] = t.text; }); });
        jobsById = {};
        jobs.forEach(function(j) { jobsById[j.id] = j; });
        if (revealRun) {
          var owner = jobs.filter(function(j) { return jobRuns(j, runs).some(function(x) { return x.id === revealRun; }); })[0];
          expandedJob = owner ? owner.id : null;
          revealRun = null;
        }
        var open = runs.filter(function(x) { return x.id === expandedRun; })[0];
        if (open) setHtml(rd('run-tasks'), runTasksHtml(open));
        var list = document.getElementById('jobs-list'), earlier = document.getElementById('earlier-runs');
        var signature = JSON.stringify([jobs, runs.map(function(x) { return [x.id, x.job, x.events_count, x.finished, x.tasks]; }),
                                        selectedRunId, expandedRun, expandedJob, activeFeature]);
        if (list._signature === signature) { pollJobLive(); return; }
        list._signature = signature;
        var content = document.querySelector('.content'), top = content ? content.scrollTop : 0;
        if (runDetailEl.parentNode) runDetailEl.parentNode.removeChild(runDetailEl);  // keep it (and its scroll) across the rebuild
        list.innerHTML = '';
        earlier.innerHTML = '';
        if (!jobs.length) list.innerHTML = '<div class="ov-ok">No runs yet. Start one above.</div>';
        jobs.forEach(function(job) {
          var card = document.createElement('div');
          card.className = 'job-card' + (jobIsOpen(job) ? ' open-job' : '');
          card.setAttribute('data-job', job.id);
          card.innerHTML = jobCardHtml(job) + jobLiveHtml(job, runs);
          if (expandedJob === job.id) {
            var mine = jobRuns(job, runs);
            var here = !!job.feature && job.feature === activeFeature;
            var detail = document.createElement('div');
            detail.className = 'job-detail';
            detail.innerHTML = '<div class="card-title">Tasks in this run</div>' +
              (here ? '' : '<div class="live-output-empty">This run is for ' + escapeHtml(job.feature || 'an unknown feature') +
                '; choose that feature in the header to open its tasks.</div>') +
              '<div class="run-tasks job-tasks">' + jobTasksHtml(jobTasks(mine), here, runTaskText) + '</div>' +
              '<div class="card-title job-runs">Sessions <span class="badge">' + mine.length + '</span></div>' +
              (mine.length ? '' : '<div class="live-output-empty">No sessions yet.</div>');
            mine.forEach(function(run) { detail.appendChild(runItemEl(run)); });
            card.appendChild(detail);
          }
          list.appendChild(card);
        });
        var early = earlierRuns(jobs, runs);
        if (!early.length) earlier.innerHTML = '<div class="ov-ok">No earlier runs.</div>';
        early.forEach(function(run) { earlier.appendChild(runItemEl(run)); });
        if (content) content.scrollTop = top;
        pollJobLive();
      }).catch(function(e) { console.error('loadJobs error', e); });
    }

    // The technical log of a working run, right on its card: the transcript of the agent working on the running
    // task (implementer, verifier or commit), the same feed as Live output in Tasks. Filled by pollJobLive().
    function jobLiveHtml(job, runs) {
      if (jobShown(job) === 'ended') return '';
      var mine = jobRuns(job, runs), run = mine.filter(function(x) { return !x.finished && !x.stopped; })[0];
      if (!run) return '';
      var task = jobTasks([run]).filter(function(t) { return t.outcome === 'running'; })[0];
      return '<div class="live-output job-live" data-live-run="' + escapeHtml(run.id) + '" data-live-task-id="' + escapeHtml(task ? task.id : '') + '">' +
        '<div class="live-output-title">' + (task ? 'Live output · ' + escapeHtml(task.id) + ' <span class="job-live-label"></span>' : 'Live output') + '</div>' +
        '<div class="live-output-body">' + (task ? liveOutputBodyHtml(jobLiveLogs[run.id + ' ' + task.id]) :
          '<div class="live-output-empty">Preparing the run: the project checks run before the first task starts. ' +
          'Output appears here as soon as the first agent starts.</div>') + '</div></div>';
    }

    var jobLiveLogs = {};

    function pollJobLive() {
      document.querySelectorAll('.job-live').forEach(function(box) {
        var runId = box.getAttribute("data-live-run"), id = box.getAttribute("data-live-task-id");
        if (!id) return;
        fetchJSON('/api/runs/' + encodeURIComponent(runId) + '/tasks/' + encodeURIComponent(id) + '/log', 'GET').then(function(log) {
          jobLiveLogs[runId + ' ' + id] = log;
          var body = box.querySelector('.live-output-body'), label = box.querySelector('.job-live-label');
          if (label) label.textContent = log && log.label ? '(' + log.label + ')' : '';
          var html = liveOutputBodyHtml(log);
          if (!body || body._html === html) return;
          var state = liveScrollState(body);
          body.innerHTML = html;
          body._html = html;
          restoreLiveScroll(body, state);
        });
      });
    }

    // One event as a short sentence; the full record is the row's tooltip.
    function eventText(ev) {
      var k = ev.kind;
      if (k === 'task.started') return 'implementation started' + (ev.attempt > 1 ? ' (attempt ' + ev.attempt + ')' : '');
      if (k === 'task.implemented') return 'implemented, sent to the verifier';
      if (k === 'task.verified') return ev.verdict === 'approve' ? 'verifier approved' :
        'verifier rejected' + (ev.problems && ev.problems.length ? ': ' + String(ev.problems[0]) : '');
      if (k === 'task.approved') return 'approved';
      if (k === 'task.blocked') return 'blocked' + (ev.reason ? ': ' + ev.reason : '') + (ev.tag ? ' [' + ev.tag + ']' : '');
      if (k === 'task.failed') return 'agent failed' + (ev.error ? ': ' + ev.error : '');
      if (k === 'group.repair') return 'repairing the checks';
      if (k === 'group.finished') return (ev.checks_passed ? 'checks passed, committed' : 'finished, checks failed') +
        (ev.commits && ev.commits.length ? ' ' + ev.commits.join(', ') : '');
      return k;
    }

    function loadEvents() {
      if (!selectedRunId || expandedRun !== selectedRunId) return;
      fetchJSON('/api/runs/' + encodeURIComponent(selectedRunId) + '/events', 'GET').then(function(data) {
        var events = (data && data.events) || [];
        var el = rd('events-view');
        if (data && data.notice) {
          setHtml(el, '<div class="empty"><div class="empty-icon">📭</div><div class="transcript-gone">' + escapeHtml(data.notice) + '</div></div>');
          return;
        }
        if (events.length === 0) {
          setHtml(el, '<div class="empty"><div class="empty-icon">📭</div><div>No events.</div></div>');
          return;
        }
        // Newest first, so the latest event is at the top and a refresh never has to scroll.
        setHtml(el, events.slice().reverse().map(function(ev) {
          var ts = ev.ts ? new Date(ev.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '-';
          var ids = ev.id ? [ev.id] : (ev.ids || []);
          return '<div class="event-row" title="' + escapeHtml(JSON.stringify(ev, null, 1)) + '"><div class="event-ts">' + escapeHtml(ts) + '</div>' +
            '<div class="event-kind">' + escapeHtml(ids.join(', ')) + '</div><div class="event-payload">' + escapeHtml(eventText(ev)) + '</div></div>';
        }).join(''));
      }).catch(function(e) { console.error('loadEvents error', e); });
    }

    // The open run's report renders like every other document (data-model.md §4.3); a run without one shows no report.
    function loadReport() {
      var runId = expandedRun;
      if (!runId) return;
      fetchDoc('/api/runs/' + encodeURIComponent(runId) + '/report').then(function(d) {
        if (expandedRun !== runId) return;
        var card = rd('report-card');
        if (d.text == null) { card.style.display = 'none'; return; }
        reportRun = runId;
        card.style.display = 'block';
        rd('report-run').textContent = '#' + runId;
        renderDoc(rd('report-view'), d.text, 'markdown');
      });
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
        // With an open Job the run banner says it in words ("Stopped unexpectedly"), never the run id (005 SC-003).
        if (run && run.state === 'stale' && !run.job) {
          el.innerHTML = staleBannerHtml(run.run_id);
          el.style.display = '';
        } else {
          el.innerHTML = '';
          el.style.display = 'none';
        }
        renderRunBanner(run);
      }).catch(function(e) { console.error('loadRunState error', e); });
    }

    // Run banner (data-model.md §12.4, FR-029, FR-030): what the supervisor accepts now comes from
    // /api/run/current's `controls` (§12.3); the banner never decides that itself.
    var runBannerError = '';
    var stoppingTask = null;
    var lastBannerState = null;

    // The supervisor's state, read back from `controls` (one row of the §12.3 table each).
    function supervisorState(run) {
      var c = (run && run.controls) || {};
      if (run && run.stopping) return 'stopping';
      if (c.stop) return 'running';
      if (c.resume) return 'held';
      if (c.hold) return 'waiting';
      if (c.release) return 'waiting-kept';
      return 'none';
    }

    // HH:MM of the usage reset, as written in `controls.reason` for the waiting rows.
    function resetTimeFromReason(reason) {
      var m = /([0-9]{2}:[0-9]{2}|[?])$/.exec(reason || '');
      return m ? m[1] : '?';
    }

    function controlButton(label, command, mode, enabled, reason, confirmText) {
      var attrs = ' class="btn" data-control="' + command + '"';
      if (mode) attrs += ' data-mode="' + mode + '"';
      if (confirmText) attrs += ' data-confirm="' + escapeHtml(confirmText) + '"';
      if (!enabled) attrs += ' disabled title="' + escapeHtml(reason || 'not available now') + '"';
      return '<button type="button"' + attrs + '>' + escapeHtml(label) + '</button>';
    }

    function reasonHtml(reason) {
      // Reasons may name a command in backticks (`python3 bin/sdd-supervise.py`).
      return escapeHtml(reason).replace(/`([^`]+)`/g, '<code>$1</code>');
    }

    // What the agents are doing, from the run's newest task event: {id, step, since}, or null before the first one.
    var bannerActivity = null;

    function activityFrom(events) {
      var last = null, starts = {};
      (events || []).forEach(function(ev) {
        if (!ev || typeof ev.id !== 'string') return;
        if (ev.kind === 'task.started') starts[ev.id] = ev.ts;
        last = ev;
      });
      if (!last) return null;
      var step = { 'task.started': 'implementing', 'task.implemented': 'verifying', 'task.approved': 'committing',
                   'task.blocked': 'recording a block', 'task.failed': 'recovering' }[last.kind] ||
                 (last.kind === 'task.verified' ? (last.verdict === 'reject' ? 'retrying after review' : 'committing') : 'working');
      return { id: last.id, step: step, since: starts[last.id] || last.ts };
    }

    function activityText(prefix) {
      if (!bannerActivity) return prefix;
      var mins = Math.floor((Date.now() - Date.parse(bannerActivity.since)) / 60000);
      return prefix + ' ' + bannerActivity.id + ', ' + bannerActivity.step + (isFinite(mins) ? ' (' + (mins < 1 ? 'just started' : mins + ' min') + ')' : '');
    }

    function loadBannerActivity(runId) {
      fetchJSON('/api/runs/' + encodeURIComponent(runId) + '/events', 'GET').then(function(data) {
        bannerActivity = activityFrom(data && data.events);
        var el = document.querySelector('#run-banner .run-banner-text');
        if (el && el.hasAttribute('data-job-activity') && bannerJob) el.textContent = jobBannerText(bannerJob, bannerActivity);
        else if (el && el.getAttribute('data-activity')) el.textContent = activityText(el.getAttribute('data-activity'));
      });
    }

    // The open Job the banner shows (005 FR-012), and the usage checkbox's last flip while the supervisor has
    // not taken it up yet ({value: keep_paused wanted, at: ms}), so a refresh in between does not flip it back.
    var bannerJob = null;
    var keepWanted = null;

    function bannerKeep(job) {
      if (!keepWanted) return null;
      var real = !!(job.shown && job.shown.keep_paused);
      if (real === keepWanted.value || Date.now() - keepWanted.at > 15000) { keepWanted = null; return null; }
      return keepWanted.value;
    }

    // The banner for the open Job, in the words of data-model.md §9: Stop buttons while working, the usage
    // checkbox while paused (research R11), and the Job's own Continue, Merge and Drop otherwise.
    function renderJobBanner(el, run) {
      var job = run.job, c = run.controls || {}, s = jobShown(job), sup = supervisorState(run);
      bannerJob = job;
      jobsById[job.id] = job;
      var key = 'job:' + s + ':' + sup;
      if (key !== lastBannerState) runBannerError = '';
      lastBannerState = key;
      var reason = c.reason || null, buttons = [], text, attrs = '', box = '';
      var stopQuestion = 'Stop the run now? The current agent is stopped and its task is resumed from the journal on the next start.';
      if (s === 'working' && sup === 'stopping') {
        text = 'Stopping after ' + (stoppingTask || 'the current task');
        buttons.push(controlButton('Stop now', 'stop', 'now', !!c.stop, reason, stopQuestion));
      } else if (s === 'working') {
        attrs = ' data-job-activity';
        text = jobBannerText(job, bannerActivity);
        buttons.push(controlButton('Stop after current task', 'stop', 'after-task', !!c.stop, reason));
        buttons.push(controlButton('Stop now', 'stop', 'now', !!c.stop, reason, stopQuestion));
      } else if (s === 'paused') {
        var wanted = bannerKeep(job), keep = wanted === null ? !!job.shown.keep_paused : wanted;
        text = jobBannerText(job, null, keep);
        var can = !!(c.hold || c.release) || wanted !== null;
        box = '<label class="run-banner-keep"><input type="checkbox" id="run-banner-continue"' + (keep ? '' : ' checked') +
          (can ? '' : ' disabled title="' + escapeHtml(reason || 'not available now') + '"') + '> ' +
          escapeHtml(jobKeepLabel(job)) + '</label>';
      } else {
        // Suspended: Continue and Drop; ended: Merge or Land while its landing is to do, else Start run.
        text = jobBannerText(job);
        var offered = jobButtonsHtml(job);
        if (offered) buttons.push(offered);
        else if (s === 'ended') buttons.push('<button type="button" class="btn" data-go="runs">Start run</button>');
      }
      var anyDisabled = buttons.some(function(b) { return b.indexOf(' disabled ') !== -1; });
      var html = '<span class="run-banner-text"' + attrs + '>' + escapeHtml(text) + '</span>' + box +
        '<span class="run-banner-actions">' + buttons.join('') + '</span>';
      if (anyDisabled && reason) html += '<span class="run-banner-reason">' + reasonHtml(reason) + '</span>';
      if (runBannerError) html += '<span class="run-banner-error" role="alert">' + escapeHtml(runBannerError) + '</span>';
      el.innerHTML = html;
      el.style.display = '';
      bindControlButtons(el);
      var check = el.querySelector('#run-banner-continue');
      if (check) check.addEventListener('change', function() {
        // Checked: continue at the reset (release); unchecked: stay paused (hold).
        keepWanted = { value: !check.checked, at: Date.now() };
        postControl(check.checked ? 'release' : 'hold');
      });
      if (sup === 'stopping' && run.run_id) loadStoppingTask(run.run_id);
      else stoppingTask = null;
      if (s === 'working' && sup !== 'stopping' && run.run_id) loadBannerActivity(run.run_id);
      else bannerActivity = null;
    }

    function bindControlButtons(el) {
      el.querySelectorAll('button[data-control]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          var question = btn.getAttribute('data-confirm');
          if (question && !window.confirm(question)) return;
          postControl(btn.getAttribute('data-control'), btn.getAttribute('data-mode'));
        });
      });
    }

    function renderRunBanner(run) {
      var el = document.getElementById('run-banner');
      if (!el) return;
      var c = (run && run.controls) || {};
      var state = supervisorState(run);
      var hasRun = run && run.state && run.state !== 'none';
      if (run && run.job) { renderJobBanner(el, run); return; }
      bannerJob = null;
      keepWanted = null;
      if (state === 'none' && !hasRun) {
        // No run record and nothing running (009 FR-022): say so and offer Start run (in the Runs view).
        lastBannerState = 'none';
        el.innerHTML = '<span class="run-banner-text">' + escapeHtml(jobBannerText(null)) + '</span>' +
          '<span class="run-banner-actions"><button type="button" class="btn" data-go="runs">Start run</button></span>';
        el.style.display = '';
        stoppingTask = null;
        bannerActivity = null;
        return;
      }
      if (state !== lastBannerState) runBannerError = '';  // a 409 text belongs to the state it was shown in
      lastBannerState = state;
      var reason = c.reason || null;
      var text = '', buttons = [];
      var activity = null;  // the banner text's prefix when it ends with what the agents are doing
      if (state === 'running') {
        activity = 'Working on';
        text = escapeHtml(activityText(activity));
        buttons.push(controlButton('Stop after current task', 'stop', 'after-task', true, reason));
        buttons.push(controlButton('Stop now', 'stop', 'now', true, reason,
          'Stop the run now? The current agent is stopped and its task is resumed from the journal on the next start.'));
      } else if (state === 'stopping') {
        text = 'Stopping after ' + escapeHtml(stoppingTask || 'the current task');
        buttons.push(controlButton('Stop now', 'stop', 'now', !!c.stop, reason,
          'Stop the run now? The current agent is stopped and its task is resumed from the journal on the next start.'));
      } else if (state === 'held') {
        text = 'Stopped by you';
        buttons.push(controlButton('Resume', 'resume', null, !!c.resume, reason));
      } else if (state === 'waiting') {
        text = 'Waiting for credits until ' + escapeHtml(resetTimeFromReason(reason));
        buttons.push(controlButton('Keep paused', 'hold', null, !!c.hold, reason));
        buttons.push(controlButton('Stop after current task', 'stop', 'after-task', false, reason));
      } else if (state === 'waiting-kept') {
        text = 'Will stay paused after the reset at ' + escapeHtml(resetTimeFromReason(reason));
        buttons.push(controlButton('Let it resume', 'release', null, !!c.release, reason));
        buttons.push(controlButton('Stop after current task', 'stop', 'after-task', false, reason));
      } else if (run.state === 'running') {
        // Started from a Claude session: the dashboard cannot stop it; say what it is doing and where to stop it.
        activity = 'Working (started from a Claude session; stop it there) on';
        text = escapeHtml(activityText(activity));
        buttons.push(controlButton('Stop after current task', 'stop', 'after-task', false, reason));
        buttons.push(controlButton('Stop now', 'stop', 'now', false, reason));
      } else {
        text = run.state === 'paused' ? 'Paused: the run waits to be resumed' : 'A run stopped without finishing';
        buttons.push(controlButton('Stop after current task', 'stop', 'after-task', false, reason));
        buttons.push(controlButton('Stop now', 'stop', 'now', false, reason));
        buttons.push(controlButton('Resume', 'resume', null, false, reason));
      }
      var anyDisabled = buttons.some(function(b) { return b.indexOf(' disabled ') !== -1; });
      var html = '<span class="run-banner-text"' + (activity ? ' data-activity="' + escapeHtml(activity) + '"' : '') + '>' + text + '</span>' +
        '<span class="run-banner-actions">' + buttons.join('') + '</span>';
      if (anyDisabled && reason) html += '<span class="run-banner-reason">' + reasonHtml(reason) + '</span>';
      if (runBannerError) html += '<span class="run-banner-error" role="alert">' + escapeHtml(runBannerError) + '</span>';
      el.innerHTML = html;
      el.style.display = '';
      bindControlButtons(el);
      if (state === 'stopping' && run.run_id) loadStoppingTask(run.run_id);
      else stoppingTask = null;
      if (activity && run.run_id) loadBannerActivity(run.run_id);
      else bannerActivity = null;
    }

    // The task the run stops after: the newest task event of the run.
    function loadStoppingTask(runId) {
      fetchJSON('/api/runs/' + encodeURIComponent(runId) + '/events', 'GET').then(function(data) {
        var events = (data && data.events) || [];
        var id = null;
        events.forEach(function(ev) { if (ev && typeof ev.id === 'string') id = ev.id; });
        if (id !== stoppingTask) {
          stoppingTask = id;
          var el = document.querySelector('#run-banner .run-banner-text');
          if (el) el.textContent = 'Stopping after ' + (id || 'the current task');
        }
      });
    }

    // POST /api/run/<command> (§12.3). A 409 shows its `error` text; after a 202 the banner
    // refreshes at once.
    function postControl(command, mode) {
      var body = mode ? { mode: mode } : {};
      fetch('/api/run/' + command, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      }).then(function(res) {
        return res.json().catch(function() { return {}; }).then(function(data) {
          if (res.status === 202) {
            runBannerError = '';
            loadRunState();
            return;
          }
          runBannerError = (data && data.error) || ('request failed (' + res.status + ')');
          keepWanted = null;  // a refused checkbox flip shows the supervisor's state again
          toast(runBannerError);
          loadRunState();
        });
      }).catch(function(e) {
        keepWanted = null;
        runBannerError = 'request failed: ' + e;
        toast(runBannerError);
      });
    }

    function refreshAll() {
      loadRunState();
      loadFeatureSelect();
      loadOverview();
      if (selectedSection === 'tasks') loadTasks();
      if (selectedSection === 'blocked') loadBlocked();
      if (selectedSection === 'decisions') loadDecisions();
      if (selectedSection === 'runs') { loadJobs(); loadEvents(); loadStartJob(); }
      if (selectedSection === 'status') loadStatus();
      try { document.getElementById('refresh-time').textContent = new Date().toLocaleTimeString(); } catch(e) {}
    }

    document.addEventListener('DOMContentLoaded', function() {
      document.querySelectorAll('.nav-item').forEach(function(item) {
        item.addEventListener('click', function() {
          var section = item.getAttribute('data-section');
          docFeature = null; docSection = null;
          if (section) showSection(section === 'docs' ? lastDoc : section);
        });
      });

      // `toggle` does not bubble, so listen in the capture phase to remember which done folds are open.
      document.addEventListener('toggle', function(ev) {
        var d = ev.target;
        if (d && d.classList && d.classList.contains('done-fold')) openDone[d.getAttribute('data-phase')] = d.open;
      }, true);

      window.addEventListener('beforeunload', function(ev) { if (editorChanged()) { ev.preventDefault(); ev.returnValue = ''; } });

      // Links inside a rendered document: `#heading` scrolls, a link to another document's file opens that
      // document here, an http(s) link opens in a new tab. Nothing else may change the URL's section.
      document.addEventListener('click', function(ev) {
        var a = ev.target.closest ? ev.target.closest('.markdown a') : null;
        if (!a) return;
        var href = a.getAttribute('href') || '';
        if (href.charAt(0) === '#') {
          ev.preventDefault();
          var target = document.getElementById(decodeURIComponent(href.slice(1)));
          if (target) target.scrollIntoView();
        } else if (href.indexOf('http://') === 0 || href.indexOf('https://') === 0) {
          a.target = '_blank';
          a.rel = 'noopener';
        } else {
          ev.preventDefault();
          var file = href.split('#')[0].split('/').pop();
          var doc = file.slice(-3).toLowerCase() === '.md' ? DOC_BY_FILE[file.slice(0, -3).toLowerCase()] : null;
          if (doc) showSection(doc);
        }
      });

      // Overview rows: `data-go` opens a view, `data-run` opens that session in Runs, inside its run when it has one.
      document.addEventListener('click', function(ev) {
        var row = ev.target.closest ? ev.target.closest('[data-go], [data-run]') : null;
        if (!row) return;
        if (row.hasAttribute('data-run')) {
          selectedRunId = row.getAttribute('data-run'); expandedRun = selectedRunId; reportRun = null; revealRun = selectedRunId;
        }
        showSection(row.hasAttribute('data-run') ? 'runs' : row.getAttribute('data-go'));
        if (row.hasAttribute('data-run')) { loadEvents(); loadReport(); }
      });

      // A task chip naming a run (005 FR-013) opens that run's card in Runs; its sessions are inside it.
      document.addEventListener('click', function(ev) {
        var ref = ev.target.closest ? ev.target.closest('a.doc-ref') : null;
        if (!ref || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button) return;
        ev.preventDefault();
        ev.stopPropagation();
        openDocRef(ref.getAttribute('data-doc'), ref.getAttribute('data-feature'), ref.getAttribute('data-sec'));
      }, true);
      document.addEventListener('click', function(ev) {
        var chip = ev.target.closest ? ev.target.closest('[data-open-job]') : null;
        if (!chip) return;
        expandedJob = chip.getAttribute('data-open-job');
        revealRun = null;
        showSection('runs');
      });

      document.querySelectorAll('button[data-raw]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          var name = btn.getAttribute('data-raw');
          var el = document.getElementById('raw-' + name);
          setRawDoc(name, !el || el.style.display === 'none');
        });
      });

      showSection(sectionFromHash());
      window.addEventListener('popstate', function() { docFeature = null; docSection = null; showSection(sectionFromHash()); });
      document.getElementById('feature-select').addEventListener('change', function(ev) {
        if (ev.target.value) chooseFeature(ev.target.value);
      });

      loadProject();
      loadRunState();
      loadFeatureSelect();
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
    parser.add_argument('--allow-port', type=int, action='append', default=[], metavar='N',
                        help='also accept POSTs from pages on this localhost port (repeatable; the UI server)')
    args = parser.parse_args(argv)
    args.project = args.project.resolve()

    DashboardHandler.project = DashboardHandler.started_project = args.project
    DashboardHandler.allow_ports = tuple(args.allow_port)
    DashboardHandler.html = build_html()

    # Security boundary: localhost only, no auth (FR-022). Do not add a --host option.
    server = http.server.ThreadingHTTPServer(('127.0.0.1', args.port), DashboardHandler)
    # `--port 0` lets the OS pick a free port; the first output line names the real one (the UI tests read it).
    DashboardHandler.port = args.port or server.server_address[1]
    print('speckit-agents dashboard on http://127.0.0.1:%s (project: %s)' % (DashboardHandler.port, args.project), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\\nstopping dashboard', flush=True)
        server.shutdown()


if __name__ == '__main__':
    main()
