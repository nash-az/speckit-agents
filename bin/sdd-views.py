#!/usr/bin/env python3
"""speckit-agents read models: the API answers built from a project's files (008 data-model §14, FR-017).

Moved unchanged from `bin/sdd-dashboard.py`, which imports them; the standalone server answers its
`/api/p/<id>/…` routes with the same functions. Nothing here does HTTP or HTML work, and no engine module
imports the dashboard.

Read-only (FR-020), like the dashboard: the only writer is `write_answer()`, which writes one answer file
in the supervisor's state directory, outside the project; `try_apply()` applies an answer with
`sdd-decide.py`, which commits the decision files (FR-033).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import threading
import time
from pathlib import Path

# All task state comes from bin/sdd-tasks.py, so the CLI and the read models cannot disagree
# (data-model.md §4.1). The hyphen in the file name prevents a plain import.
_spec = importlib.util.spec_from_file_location("sdd_tasks", Path(__file__).with_name("sdd-tasks.py"))
sdd_tasks = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_tasks)
_spec = importlib.util.spec_from_file_location("sdd_decide", Path(__file__).with_name("sdd-decide.py"))
sdd_decide = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_decide)
_spec = importlib.util.spec_from_file_location("sdd_launch", Path(__file__).with_name("sdd-launch.py"))
sdd_launch = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_launch)
_spec = importlib.util.spec_from_file_location("sdd_job", Path(__file__).with_name("sdd-job.py"))
sdd_job = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_job)


# Run ids are timestamps (`20261027-1431`, optionally suffixed); anything else is a 404, which
# also rules out path traversal through `/api/runs/<id>/…`.
RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{4}[a-z0-9-]*$")

# Task ids in answer file names and log routes (`T012`, `T012a`).
TASK_ID_RE = re.compile(r"^T\d+[a-z]?$")


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


def run_tasks(events, finished):
    """The tasks one run touched, in the order it first started them: `{id, outcome, started, ended,
    attempts, commits}`. The newest event decides the outcome: approved (committed once its group
    finished), blocked (blocked or failed), rejected (the verifier's last word), else running, or
    interrupted when the run is finished. `commits` come from `group.finished`."""
    tasks, order = {}, []
    for ev in events:
        ids = [ev["id"]] if isinstance(ev.get("id"), str) else [i for i in ev.get("ids") or [] if isinstance(i, str)]
        for task_id in ids:
            if task_id not in tasks:
                tasks[task_id] = {"id": task_id, "outcome": None, "started": ev.get("ts"), "ended": ev.get("ts"),
                                  "attempts": 0, "commits": []}
                order.append(task_id)
            t = tasks[task_id]
            t["ended"] = ev.get("ts") or t["ended"]
            kind = ev.get("kind")
            if kind == "task.started":
                t["attempts"] = max(t["attempts"], ev.get("attempt") or 1)
                t["outcome"] = "running"
            elif kind == "task.implemented":
                t["outcome"] = "running"
            elif kind == "task.verified" and ev.get("verdict") == "reject":
                t["outcome"] = "rejected"
            elif kind == "task.approved":
                t["outcome"] = "approved"
            elif kind in ("task.blocked", "task.failed"):
                t["outcome"] = "blocked"
            elif kind == "group.finished":
                if len(ids) == 1 or not t["commits"]:
                    t["commits"] = [str(c) for c in ev.get("commits") or []]
                if ev.get("checks_passed") and t["outcome"] == "approved":
                    t["outcome"] = "committed"
    for t in tasks.values():
        if t["outcome"] in (None, "running"):
            t["outcome"] = "interrupted" if finished else "running"
    return [tasks[i] for i in order]


CREATED_RE = re.compile(r"\*\*Created\*\*:\s*(\d{4})-(\d{2})-(\d{2})")


def run_feature(project, run_id, listing):
    """The feature a run was for: `feature` in its `workflows.json` (runs since 2026-10-30), else the
    newest feature whose spec says it was created on or before the run's date (older runs)."""
    if isinstance(listing, dict) and isinstance(listing.get("feature"), str):
        return listing["feature"]
    day, best = run_id[:8], None
    for name in feature_names(project):
        m = CREATED_RE.search(read_text(project / "specs" / name / "spec.md") or "")
        if m and "".join(m.groups()) <= day and (best is None or "".join(m.groups()) >= best[0]):
            best = ("".join(m.groups()), name)
    return best[1] if best else None


def runs_by_task(project, feature):
    """`GET /api/runs/tasks`: {task id: [{run, outcome, commits, job, title}, ...]} for the runs of `feature`,
    newest run first, so a task card can say which run did it. Task ids repeat across features, hence the
    filter. `job` is the Job the run's `workflows.json` names (else None) and `title` that Job's title (None
    without a Job, or the Job id when its record is gone), so the chip can say "done in job <title>" (005 FR-013)."""
    index, titles = {}, {}
    for run in list_runs(project):
        if feature is None or run.get("feature") != feature:
            continue
        job = run.get("job")
        if job and job not in titles:
            record = sdd_job.load(project, job)  # None for a malformed, missing or unreadable record
            title = record.get("title") if record else None
            titles[job] = title if isinstance(title, str) and title else job
        for t in run["tasks"]:
            index.setdefault(t["id"], []).append({"run": run["id"], "outcome": t["outcome"], "commits": t["commits"],
                                                  "job": job, "title": titles.get(job) if job else None})
    return index


def list_runs(project):
    """Runs are `.sdd/runs/<id>/` directories holding `workflows.json` (data-model.md §4.4),
    newest id first (ids are timestamps). Flat files such as `.sdd/runs/x.jsonl` are ignored."""
    runs_dir = project / ".sdd" / "runs"
    runs = []
    if not runs_dir.is_dir():
        return runs
    entries = [e for e in runs_dir.iterdir()
               if e.is_dir() and RUN_ID_RE.match(e.name) and (e / "workflows.json").is_file()]
    current = read_json(project / ".sdd" / "run.json")
    current = current.get("run_id") if isinstance(current, dict) else None
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
        # A run with no report that is not the one in .sdd/run.json was cut off (stopped at a usage
        # pause or killed, then never resumed): it is not running, and its open tasks are interrupted.
        if not runs[-1]["finished"] and run_id != current:
            runs[-1]["stopped"] = "cut off"
        runs[-1]["tasks"] = run_tasks(events, runs[-1]["finished"] or runs[-1]["stopped"] == "cut off")
        listing = read_json(entry / "workflows.json")
        runs[-1]["feature"] = run_feature(project, run_id, listing)
        # The Job the run belonged to (005 data-model §5), else None.
        job = listing.get("job") if isinstance(listing, dict) else None
        runs[-1]["job"] = job if isinstance(job, str) else None
    return runs


def _in_flight(project, record) -> list:
    """The tasks a suspended run was on: those its own `.sdd/run.json`'s journal has in flight; [] otherwise."""
    if not sdd_job.owns_run_json(project, record):
        return []
    try:
        run = json.loads((Path(project) / ".sdd" / "run.json").read_text(encoding="utf-8"))
        return sdd_job._in_flight(run if isinstance(run, dict) else None)
    except Exception:  # noqa: BLE001 — an unreadable journal only loses the task's name in the banner
        return []


def _tasks_done(project, run_id):
    """How many tasks the run's summary (`.sdd/runs/<run id>.json`, 009 plan "Run summary") lists as done,
    or None without a readable summary."""
    summary = read_json(Path(project) / ".sdd" / "runs" / f"{run_id}.json")
    done = ((summary or {}).get("tasks") or {}).get("done") if isinstance(summary, dict) else None
    return len(done) if isinstance(done, list) else None


def job_view(project, record):
    """A run as `/api/jobs` gives it (009 plan "Shown state"; 005 data-model §9): the record plus `shown`
    (sdd-job.py `shown_state`), `progress` (None when tasks.md cannot be read), `in_flight` (the tasks a
    suspended run was on, else []) and `tasks_done` (the tasks an ended run's summary lists as done, else None)."""
    try:
        counts = sdd_job.progress(project, record)
    except (OSError, ValueError, KeyError, TypeError):
        counts = None
    shown = sdd_job.shown_state(project, record)
    in_flight = _in_flight(project, record) if shown["state"] == "suspended" else []
    done = _tasks_done(project, record.get("id")) if shown["state"] == "ended" and record.get("id") else None
    return {**record, "shown": shown, "progress": counts, "in_flight": in_flight, "tasks_done": done}


def list_job_views(project):
    """`GET /api/jobs`: every run, open ones (`is_open`) first, then newest (sdd-job.py lists newest first;
    the sort is stable)."""
    records = sorted(sdd_job.list_jobs(project), key=lambda r: not sdd_job.is_open(project, r))
    return [job_view(project, r) for r in records]


def get_job(project, job_id):
    """`GET /api/jobs/<id>`: the Job plus its runs as `/api/runs` gives them (those whose `workflows.json`
    names the Job, or listed in its `runs`), or None for an id not in the run id form or unknown."""
    if not RUN_ID_RE.match(job_id):
        return None
    record = sdd_job.load(project, job_id)
    if record is None:
        return None
    listed = {str(r) for r in record.get("runs") or []}
    runs = [r for r in list_runs(project) if r.get("job") == job_id or r["id"] in listed]
    return {**job_view(project, record), "runs": runs}


def job_plan(project, feature):
    """`GET /api/jobs/plan`: the Start job preview `{phases, runnable, held, can_start, reason}`. The counts
    come from `sdd-launch.py`'s `preview()`; `can_start` and `reason` from a dry run of `launch_job()` for all
    open tasks, so the reasons are the launcher's exact refusal texts. Nothing is changed."""
    info = sdd_launch.preview(project, feature)
    result = {"phases": info["phases"], "runnable": info["runnable"], "held": info["held"],
              "can_start": True, "reason": None}
    try:
        sdd_launch.launch_job(project, feature, None, None, dry_run=True)
    except sdd_launch.Refused as e:
        result.update(can_start=False, reason=str(e))
    return result


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


def open_job_view(project):
    """The open run (sdd-job.py `open_run`) as `/api/jobs` gives it, or None: the banner and the Overview
    headline describe it in words (009 FR-022)."""
    try:
        record = sdd_job.open_run(project)
    except (OSError, ValueError):
        return None
    return job_view(project, record) if record else None


def last_job_view(project):
    """The newest run as `/api/jobs` gives it, or None: with no open run, the banner says how it ended
    ("<title> ended: <n> tasks landed on <base>", 009 FR-022)."""
    try:
        records = sdd_job.list_jobs(project)
    except (OSError, ValueError):
        return None
    return job_view(project, records[0]) if records else None


ANSWER_MAX_CHARS = 4000


def answers_dir(project: Path) -> Path:
    """Where the person's decision answers wait for `/sdd-unblock` (outside the project, like control.json)."""
    return sdd_tasks.state_dir(project) / "answers"


def read_answers(project: Path) -> dict:
    """{task id: {"answer", "answered_at"}} for every answer file that parses."""
    found = {}
    try:
        files = sorted(answers_dir(project).glob("T*.json"))
    except OSError:
        return found
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("answer"), str) and TASK_ID_RE.match(f.stem):
            found[f.stem] = {"answer": data["answer"], "answered_at": data.get("answered_at")}
    return found


def open_decisions(tasks_data: dict, project: Path) -> list:
    """The `[decision]` tasks that still wait on a person, each with the answer the person already
    gave in the dashboard, if any (`pending`: written, not yet applied by `/sdd-unblock`)."""
    answers = read_answers(project)
    found = []
    for phase in (tasks_data or {}).get("phases", []):
        for task in phase.get("tasks", []):
            if task.get("done") or task.get("status_tag") != "decision":
                continue
            found.append({"id": task["id"], "text": task.get("text", ""),
                          "question": task.get("question"), "pending": answers.get(task["id"])})
    return found


_ANSWER_LOCK = threading.Lock()


def write_answer(project: Path, task_id: str, answer: str) -> None:
    """The one writer in this file (FR-033): one answer for `/sdd-unblock` in
    `<state dir>/answers/<task id>.json`, written to a temp file and moved into place. It lives
    beside control.json, outside the project, so FR-020 still holds."""
    with _ANSWER_LOCK:
        target_dir = answers_dir(project)
        target_dir.mkdir(parents=True, exist_ok=True)
        record = {"task": task_id, "answer": answer, "answered_at": int(time.time())}
        tmp = target_dir / f"{task_id}.json.tmp"
        tmp.write_text(json.dumps(record) + "\n", encoding="utf-8")
        os.replace(tmp, target_dir / f"{task_id}.json")


def try_apply(project: Path, feature: Path, task_id: str, answer: str) -> dict:
    """Apply a saved answer with `sdd-decide.py` (FR-033). On success the answer file is removed and the
    result carries the commit; when it is refused (a run is active, other changes in the tree, not a
    repository) the answer stays saved and the result says why, so the person can retry."""
    try:
        result = sdd_decide.apply(project, feature, task_id, answer,
                                  remove_after=answers_dir(project) / f"{task_id}.json")
    except sdd_decide.Refused as e:
        return {"applied": False, "reason": str(e)}
    return {"applied": True, "commit": result["commit"], "files": result["files"]}


# Live output (data-model.md §4.6, FR-021).
LOG_MAX_LINES = 200
LOG_MAX_LINE_CHARS = 2000


def _unavailable(reason):
    return {"available": False, "reason": reason}


def task_log(project, run_id, task_id):
    """`GET /api/runs/<id>/tasks/<task_id>/log` (data-model.md §4.6, FR-021).

    The newest agent started for the task names the transcript `<dir>/agent-<agent_id>.jsonl`
    (research.md D7): the run's workflow directories are searched newest first, so a run resumed
    in a new session still shows the agent of the task that is running (or the last one that ran).
    Anything not found gives `{"available": false, "reason"}`; the route always answers 200.
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
    found = _newest_agent(sdd_tasks.run_events(project, run_id)["workflow_dirs"], task_id)
    if found is None:
        return _unavailable("no agent has started for this task yet")
    wf, agent_id, label = found
    if not re.match(r"^[A-Za-z0-9_-]+$", agent_id):  # it names a file
        return _unavailable("invalid agent id")
    transcript = wf / f"agent-{agent_id}.jsonl"
    try:
        with transcript.open(encoding="utf-8", errors="replace") as f:
            lines = [line.rstrip("\n") for line in f]
    except OSError:
        return _unavailable("transcript not found")
    shown = [t for t in (_readable_line(line) for line in lines) if t is not None]
    return {"available": True, "label": label,
            "lines": [t[:LOG_MAX_LINE_CHARS] for t in shown[-LOG_MAX_LINES:]]}


def _newest_agent(workflow_dirs, task_id):
    """(workflow dir, agent id, label) of the newest agent started for `task_id`: any label kind
    (impl, eval, verify, commit, repair) whose ids include the task, newest directory first."""
    for raw in reversed(workflow_dirs):
        wf = Path(raw)
        if not sdd_tasks.valid_workflow_dir(wf):
            continue
        newest = None
        try:
            lines = (wf / "journal.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # a line cut off when the process was killed
            if not isinstance(entry, dict) or entry.get("type") != "started" or not entry.get("agentId"):
                continue
            match = sdd_tasks.LABEL_RE.match(str(entry.get("label") or ""))
            if match and task_id in match["ids"].split(","):
                newest = (wf, str(entry["agentId"]), entry["label"])
        if newest is not None:
            return newest
    return None


# Transcript record types that carry no reading material (request bodies, attachments, hashes).
TRANSCRIPT_NOISE = ("api-request", "api-request-shape", "api-request-blob", "attachment")
TOOL_ARG_KEYS = ("description", "command", "file_path", "path", "pattern", "url", "query", "prompt")


def _block_text(block):
    """One content block as a line, or None to skip it (empty thinking, unknown block types)."""
    kind = block.get("type")
    if kind == "text":
        return str(block.get("text") or "").strip() or None
    if kind == "tool_use":
        args = block.get("input") if isinstance(block.get("input"), dict) else {}
        arg = next((str(args[k]) for k in TOOL_ARG_KEYS if args.get(k)), "")
        return f"> {block.get('name') or 'tool'}" + (f": {arg}" if arg else "")
    if kind == "tool_result":
        content = block.get("content")
        if isinstance(content, list):
            content = " ".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
        text = str(content or "").strip()
        return ("< error: " if block.get("is_error") else "< ") + (text or "(no output)")
    return None


def _readable_line(line):
    """One transcript line as readable text. A line that is not a JSON record with a `type`, or
    has a type this does not know, is kept as it is; noise records give None."""
    try:
        rec = json.loads(line)
    except ValueError:
        return line
    if not isinstance(rec, dict) or not isinstance(rec.get("type"), str):
        return line
    kind = rec["type"]
    if kind in TRANSCRIPT_NOISE:
        return None
    if kind not in ("user", "assistant"):
        return line
    message = rec.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return f"{kind}: {content.strip()}" if content.strip() else None
    if not isinstance(content, list):
        return line
    parts = [t for t in (_block_text(b) for b in content if isinstance(b, dict)) if t]
    return "\n".join(parts) if parts else None


# Phase rollup (data-model.md §1, FR-004): most urgent first. A phase's status is the first key
# that any of its tasks has. The dropped statuses rank after `done` (T062, answer (b)), so a phase
# whose open work is finished reads `done`; a phase of only dropped tasks still shows its drop.
ROLLUP_ORDER = ("blocked", "retrying", "running", "held", "paused", "queued",
                "done", "moved", "wontfix", "superseded")

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


# Job bodies (005 contracts/jobs-api.md).
JOB_LIMITS = ("max_tasks", "until", "max_runs")


def _whole_or_none(value):
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def _number_or_none(value):
    return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool))


def _id_list(value):
    return isinstance(value, list) and all(isinstance(v, (str, int)) and not isinstance(v, bool) for v in value)


def job_limits_from(data):
    """The limits a job body gives (only the keys present), or None when one has the wrong type:
    `max_tasks` and `max_runs` whole numbers, `until` epoch seconds, each also null for none."""
    limits = {name: data[name] for name in JOB_LIMITS if name in data}
    for name, value in limits.items():
        if not (_number_or_none(value) if name == "until" else _whole_or_none(value)):
            return None
    return limits
