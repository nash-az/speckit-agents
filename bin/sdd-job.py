#!/usr/bin/env python3
"""Runs: the record of one short-lived run (spec 009, plan "Data model"); 005 Job records are read as runs.

A run record lives in `.sdd/jobs/<id>.json`. Every write goes through `save()`, which writes `<id>.json.tmp`
and then `os.replace`s it, so a reader never sees half a record; keys this module does not know are kept.
At most one run per project is open; `open_run()` returns it.

Functions: `load`, `save`, `list_jobs`, `new_id`, `default_title`, `scope_args`, `progress`, `tasks_done`,
`limit_reached`, `owns_run_json`, `normalize`, `shown_state`, `is_open`, `open_run`, `land` (raises
`Refused`), `land_ready`, `end` (never raises), `suspend`, `in_flight`, `record_in_flight`,
`drop_suspended` (raises `Refused`),
`after_settle` (starts a new run after a settle when the newest run ended `waiting`; never raises).

    bin/sdd-job.py list [project]          -> [record + shown (shown_state) + progress, ...]
    bin/sdd-job.py show <id> [project]     -> record + shown (shown_state) + progress
    bin/sdd-job.py land <id> [project]     -> {"landed": bool, "commit", "fast_forward": bool, "landing", "summary"} (land)
    bin/sdd-job.py merge <id> [project]    -> the same as land (a 009 run: merge = land)
    bin/sdd-job.py drop <id> [project]     -> {"dropped": true, "tasks", "stash", "end"} (drop_suspended; a run
                                              not shown suspended is refused "this run cannot be dropped: ...")

A refusal prints `{"contract": "1.0", "error": "<reason>"}` and exits 1 (contracts/engine-cli.md). Stdlib only; no model.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sdd_tasks = _load_module("sdd_tasks", "sdd-tasks.py")
sdd_supervise = _load_module("sdd_supervise", "sdd-supervise.py")
sdd_history = _load_module("sdd_history", "sdd-history.py")

ID_RE = re.compile(r"^\d{8}-\d{4}(?:-(\d+))?$")
# sdd-tasks.py hold tags for dropped work (not counted in progress). Of the other holds, `held` (behind a
# held dependency) waits on another task; every other tag (manual, decision, dep, download, blocked, or an
# unknown one) waits on the person, as the dashboard's "Waiting on you" counts them.
DEPENDENCY_HOLD_TAG = "held"
DROPPED_HOLD_TAGS = ("wontfix", "moved", "superseded")
LIMIT_NAMES = ("max_tasks", "until", "max_runs")


def jobs_dir(project) -> Path:
    return Path(project) / ".sdd" / "jobs"


def _valid_id(job_id) -> bool:
    return isinstance(job_id, str) and bool(ID_RE.match(job_id))


def _id_key(job_id: str):
    """Sort key: the `YYYYMMDD-HHMM` part, then the suffix as a number (no suffix is 1)."""
    m = ID_RE.match(job_id)
    suffix = int(m.group(1)) if m and m.group(1) else 1
    return (job_id[:13], suffix)


def load(project, job_id: str) -> dict | None:
    """The record with this id, or None when the id is malformed, missing or unreadable."""
    if not _valid_id(job_id):
        return None
    try:
        record = json.loads((jobs_dir(project) / f"{job_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def save(project, record: dict) -> Path:
    """Write the record atomically (`<id>.json.tmp`, then `os.replace`); every key is kept, and
    `contract` is set to this engine's (008 data-model §1)."""
    job_id = record.get("id")
    if not _valid_id(job_id):
        raise ValueError(f"not a job id: {job_id!r}")
    record["contract"] = sdd_tasks.CONTRACT
    directory = jobs_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{job_id}.json"
    tmp = directory / f"{job_id}.json.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def list_jobs(project) -> list[dict]:
    """Every readable record, newest first (by id: start time, then suffix)."""
    directory = jobs_dir(project)
    if not directory.is_dir():
        return []
    ids = [p.name[:-len(".json")] for p in directory.glob("*.json")]
    records = [r for r in (load(project, i) for i in ids if _valid_id(i)) if r is not None]
    return sorted(records, key=lambda r: _id_key(str(r.get("id", ""))), reverse=True)


def new_id(project, now: float | None = None) -> str:
    """`YYYYMMDD-HHMM` in local time; `-2`, `-3`, ... when a record with that id already exists (R12)."""
    base = time.strftime("%Y%m%d-%H%M", time.localtime(time.time() if now is None else now))
    directory = jobs_dir(project)
    candidate, n = base, 1
    while (directory / f"{candidate}.json").exists():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def _ranges(ids: list[str]) -> str:
    """"3, 4, 5, 7" -> "3–5, 7" for numeric ids; other ids are listed as given."""
    if not all(i.isdigit() for i in ids):
        return ", ".join(ids)
    nums = sorted({int(i) for i in ids})
    parts, start, prev = [], nums[0], nums[0]
    for n in nums[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else f"{start}–{prev}")
        if n is not None:
            start = prev = n
    return ", ".join(parts)


def default_title(scope: dict) -> str:
    """The scope in words (FR-014): "Phase 11", "Phases 3–5", "T061, T063", "All open tasks"."""
    scope = scope or {}
    phases = [str(p) for p in scope.get("phases") or []]
    tasks = [str(t) for t in scope.get("tasks") or []]
    if phases:
        return f"Phase {phases[0]}" if len(set(phases)) == 1 else f"Phases {_ranges(phases)}"
    if tasks:
        return ", ".join(tasks)
    return "All open tasks"


def _status(project) -> dict | None:
    """The supervisor's `status.json` (in `sdd-supervise.py`'s `state_dir`), or None."""
    try:
        path = sdd_supervise.state_dir(Path(project)) / "status.json"
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # missing, unreadable, or the state directory cannot be made
        return None
    return status if isinstance(status, dict) else None


def _pid_alive(status) -> bool:
    """As the dashboard's `supervisor_alive`: signal 0 on `status.json`'s pid; `PermissionError` is alive."""
    pid = status.get("pid") if isinstance(status, dict) else None
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# 009 run states (plan "Data model"): the record is the run record; 005 states are read through `normalize`.
RUN_STATES = ("working", "suspended", "ended")
OPEN_LANDINGS = ("needs-merge", "pending")
CUT_OFF_REASON = "the run was cut off"
NO_END_REASON = "the supervisor ended without recording the run's end"


def _landing(state: str, commit: str | None = None) -> dict:
    return {"state": state, "commit": commit, "files": []}


def owns_run_json(project, record: dict) -> bool:
    """This run's `.sdd/run.json`: it is readable and its `job` is the record's `id`, or the record has a
    `branch` and run.json's `branch` equals it (plan "Run record")."""
    try:
        run = json.loads((Path(project) / ".sdd" / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(run, dict):
        return False
    if record.get("id") and run.get("job") == record.get("id"):
        return True
    return bool(record.get("branch")) and run.get("branch") == record.get("branch")


def _outcome_from_reason(reason, default: str) -> str:
    """A 005 record's outcome from its reason words, else `default`."""
    if not isinstance(reason, str):
        return default
    if reason.endswith(" reached"):
        return "limit"
    if reason == "only items that need you are left":
        return "waiting"
    if reason.startswith("no progress"):
        return "failed"
    if reason == "stopped by you" or reason.startswith("kept paused"):
        return "stopped"
    return default


def normalize(project, record: dict) -> dict:
    """The record read as a 009 run record (plan "Run record" table), as a copy; nothing is written.
    A record already `working`, `suspended` or `ended` is kept (an `ended` one without a dict `landing` gets
    landing `pending`); 005 states are mapped by the table; any other state reads as `working`."""
    out = copy.deepcopy(record)
    stored = record.get("state")
    if stored in RUN_STATES:
        if stored == "ended" and not isinstance(out.get("landing"), dict):
            out["landing"] = _landing("pending")
        return out
    if stored == "merged":
        out.update(state="ended", outcome=_outcome_from_reason(record.get("reason"), "done"),
                   landing=_landing("landed", record.get("merge_commit")))
    elif stored == "dropped":
        out.update(state="ended", outcome="dropped", landing=_landing("not-landed"))
    elif stored in ("done", "stopped", "failed", "waiting", "held"):
        if owns_run_json(project, record):
            out["state"] = "suspended"
        else:
            default = "stopped" if stored == "held" else stored
            out.update(state="ended", outcome=_outcome_from_reason(record.get("reason"), default),
                       landing=_landing("pending"))
    else:
        out["state"] = "working"
    return out


def shown_state(project, record: dict) -> dict:
    """The run's shown state (plan "Shown state"), first rule wins: `working` with a live supervisor in the
    usage wait -> `paused` (plus `resets_at` and `keep_paused`); `working` with no live supervisor ->
    `suspended` when this run's run.json exists, else `ended` (outcome `stopped`, landing `pending`);
    otherwise the normalized state. Returns `{"state", "outcome", "reason", "landing"}`."""
    run = normalize(project, record)
    state, reason = run.get("state"), run.get("reason")
    if state == "working":
        status = _status(project)
        if _pid_alive(status):
            if status.get("state") == "waiting":
                return {"state": "paused", "outcome": None, "reason": reason, "landing": None,
                        "resets_at": status.get("resets_at"), "keep_paused": bool(status.get("keep_paused"))}
        elif owns_run_json(project, record):
            return {"state": "suspended", "outcome": None, "reason": reason or CUT_OFF_REASON, "landing": None}
        else:
            return {"state": "ended", "outcome": "stopped", "reason": NO_END_REASON,
                    "landing": _landing("pending")}
    if state == "ended":
        return {"state": "ended", "outcome": run.get("outcome"), "reason": reason, "landing": run.get("landing")}
    return {"state": state, "outcome": None, "reason": reason, "landing": None}


def is_open(project, record: dict) -> bool:
    """A run blocks a new one while shown `working`, `paused` or `suspended`, or `ended` with landing
    `needs-merge` or `pending`."""
    shown = shown_state(project, record)
    if shown["state"] in ("working", "paused", "suspended"):
        return True
    landing = shown.get("landing")
    return shown["state"] == "ended" and isinstance(landing, dict) and landing.get("state") in OPEN_LANDINGS


def open_run(project) -> dict | None:
    """The newest record that `is_open`, else None."""
    for record in list_jobs(project):
        if is_open(project, record):
            return record
    return None


def scope_args(record: dict) -> str:
    """The run's scope arguments: `--phases a,b`, `--tasks a,b`, or "" for all open tasks."""
    scope = record.get("scope") or {}
    phases = [str(p) for p in scope.get("phases") or []]
    tasks = [str(t) for t in scope.get("tasks") or []]
    if phases:
        return "--phases " + ",".join(phases)
    if tasks:
        return "--tasks " + ",".join(tasks)
    return ""


def feature_dir(project, record: dict) -> Path:
    """`specs/<feature>`; a feature given as a path (with a `/`) is taken relative to the project."""
    feature = str(record.get("feature") or "")
    return Path(project) / feature if "/" in feature else Path(project) / "specs" / feature


def _parsed(project, record: dict) -> dict:
    return sdd_tasks.parse_tasks(feature_dir(project, record) / "tasks.md")


def _ticked(parsed: dict) -> set[str]:
    return {t["id"] for p in parsed["phases"] for t in p["tasks"] if t["done"]}


def progress(project, record: dict) -> dict:
    """`{done, total, waiting_on_you, left}` over the Job's scope (data-model §3–§4, FR-006, FR-012).

    The scope's tasks are `scope_tasks` plus the open tasks the scope matches now (`sdd-tasks.py plan` with
    the scope, runnable and held), so tasks added inside scoped phases count. `done` = those ticked in
    `tasks.md`; `waiting_on_you` = open ones held for the person (a tag or an open BLOCKED.md entry);
    `left` = the other open ones (runnable, or held behind a dependency). Dropped work (wontfix, moved,
    superseded) is not counted; `total = done + waiting_on_you + left`.
    """
    project = Path(project)
    feature = feature_dir(project, record)
    parsed = sdd_tasks.parse_tasks(feature / "tasks.md")
    blocked = sdd_tasks.parse_blocked_entries(feature / "BLOCKED.md")
    config = sdd_tasks.load_config(project / ".sdd" / "config.toml")
    scope = record.get("scope") or {}
    phases = [str(p) for p in scope.get("phases") or []] or None
    tasks = [str(t) for t in scope.get("tasks") or []] or None
    plan = sdd_tasks.plan(parsed, blocked, config, phases, None, tasks)

    matched = [i for p in plan["phases"] for i in p["tasks"]] + [h["id"] for h in plan["held"]]
    scoped = [str(i) for i in record.get("scope_tasks") or []]
    scoped += [i for i in matched if i not in scoped]
    ticked = _ticked(parsed)
    # The whole file's holds, worked out as plan() does, so a scope task outside the filters is classed too.
    all_holds = sdd_tasks.propagate_holds(parsed, sdd_tasks.direct_holds(
        parsed, {i: e.get("status", "open") for i, e in blocked.items()}, config.get("allow", {})))
    known = {t["id"] for p in parsed["phases"] for t in p["tasks"]}

    done = waiting = left = 0
    for task_id in dict.fromkeys(scoped):
        if task_id not in known:
            continue  # removed from tasks.md
        if task_id in ticked:
            done += 1
            continue
        tag = (all_holds.get(task_id) or (None,))[0]
        if tag in DROPPED_HOLD_TAGS:
            continue
        if tag is None or tag == DEPENDENCY_HOLD_TAG:
            left += 1
        else:
            waiting += 1
    return {"done": done, "total": done + waiting + left, "waiting_on_you": waiting, "left": left}


def tasks_done(project, record: dict) -> int:
    """Ids in `scope_tasks` now ticked in `tasks.md` (data-model §3; counting never resets)."""
    ticked = _ticked(_parsed(project, record))
    return sum(1 for i in dict.fromkeys(record.get("scope_tasks") or []) if i in ticked)


def limit_reached(project, record: dict, now: float | None = None) -> str | None:
    """The first reached limit (data-model §3) as its name, or None: `max_tasks` when tasks_done >= max_tasks,
    `until` when now >= until, `max_runs` when len(runs) >= max_runs. A null limit is never reached."""
    limits = record.get("limits") or {}
    now = time.time() if now is None else now
    max_tasks = limits.get("max_tasks")
    if max_tasks is not None and tasks_done(project, record) >= max_tasks:
        return "max_tasks"
    until = limits.get("until")
    if until is not None and now >= until:
        return "until"
    max_runs = limits.get("max_runs")
    if max_runs is not None and len(record.get("runs") or []) >= max_runs:
        return "max_runs"
    return None


class Refused(Exception):
    """The action cannot be done now; nothing was changed."""


def _git(project, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=project, capture_output=True, text=True)


def _run_active(project) -> bool:
    """`.sdd/run.json` exists, or the supervisor in `status.json` is alive and not `done` or `stopped`
    (as `sdd-launch.py`'s `_supervisor_running`)."""
    if (Path(project) / ".sdd" / "run.json").exists():
        return True
    status = _status(project)
    return bool(status) and status.get("state") not in (None, "done", "stopped") and _pid_alive(status)


def _dirty_files(project) -> str:
    """The first five changed paths of `git status --porcelain`, joined, or "" for a clean tree."""
    dirty = _git(project, "status", "--porcelain").stdout.rstrip("\n")  # the first line may start with a space
    return ", ".join(line[3:] for line in dirty.splitlines()[:5]) if dirty else ""


def _refuse_unsafe_checkout(project) -> None:
    """The refusals that keep a branch switch safe: an active run, no git repository, a dirty tree."""
    if _run_active(project):
        raise Refused("a run is active")
    if _git(project, "rev-parse", "--is-inside-work-tree").returncode != 0:
        raise Refused("the project is not a git repository")
    files = _dirty_files(project)
    if files:
        raise Refused(f"the working tree has changes; commit or stash them first: {files}")


def _switch(project, branch: str) -> None:
    switched = _git(project, "switch", branch)
    if switched.returncode != 0:
        raise Refused(f"could not switch to {branch}: {switched.stderr.strip()}")


# 009 landing (plan "Engine changes" 2): an ended run's branch goes into its base by itself.
DONE_LANDINGS = ("landed", "nothing", "not-landed")


def _branches(record: dict) -> tuple[str, str]:
    branch, base = record.get("branch"), (record.get("base") or {}).get("branch")
    if not branch or not base:
        raise Refused("the run has no branch or base branch")
    return branch, base


def land(project, run_id: str) -> dict:
    """Land an ended run on its base branch (plan "Engine changes" 2): switch to the base, fast-forward when
    possible, else one merge commit "merge: run <title>". A branch with no commit the base lacks gets landing
    `nothing`. On a conflict the merge is aborted, the checkout goes back to the run's branch, the landing is
    stored as `needs-merge` with the files, and `Refused` names them. Other refusals change nothing. Never
    forces, pushes or deletes a branch. Once the landing is `landed` or `nothing`, the run summary is written
    and committed on the base (`sdd-history.py` `commit_summary`, research R2), so the base's HEAD is then the
    summary commit. Returns `{"landed", "commit", "fast_forward", "landing", "summary"}` (+ "summary_error")."""
    project = Path(project)
    stored = load(project, run_id)
    if stored is None:
        raise Refused(f"no such run: {run_id}")
    record = normalize(project, stored)
    if record.get("state") != "ended":
        raise Refused(f"this run has not ended: it is {record.get('state')}")
    if (record.get("landing") or {}).get("state") in DONE_LANDINGS:
        raise Refused("this run has already ended its landing")
    _refuse_unsafe_checkout(project)
    branch, base = _branches(record)
    ahead = _git(project, "rev-list", "--count", f"{base}..{branch}")
    if ahead.returncode != 0:
        raise Refused(f"could not compare {branch} with {base}: {ahead.stderr.strip()}")
    ended = record.get("ended") or int(time.time())
    if int(ahead.stdout.strip() or 0) == 0:
        _switch(project, base)
        record.update(state="ended", landing=_landing("nothing"), ended=ended)
        save(project, record)
        return _with_summary(project, record, {"landed": False, "commit": None, "fast_forward": False,
                                               "landing": record["landing"]})
    _switch(project, base)
    fast_forward = _git(project, "merge", "--ff-only", branch).returncode == 0
    if not fast_forward:
        merged = _git(project, "merge", "--no-ff", "-m", f"merge: run {record.get('title') or run_id}", branch)
        if merged.returncode != 0:
            conflicted = _git(project, "diff", "--name-only", "--diff-filter=U").stdout.split()
            _git(project, "merge", "--abort")
            _git(project, "switch", branch)
            if conflicted:
                record["landing"] = {"state": "needs-merge", "commit": None, "files": conflicted}
                save(project, record)
                raise Refused(f"the merge conflicts in: {', '.join(conflicted)}")
            raise Refused(f"the merge failed: {(merged.stderr or merged.stdout).strip()}")
    commit = _git(project, "rev-parse", "HEAD").stdout.strip()
    record.update(state="ended", landing=_landing("landed", commit), merge_commit=commit, ended=ended)
    save(project, record)
    return _with_summary(project, record, {"landed": True, "commit": commit, "fast_forward": fast_forward,
                                           "landing": record["landing"]})


def _with_summary(project: Path, record: dict, result: dict) -> dict:
    """Once the landing is final, write and commit the run summary on the base (research R2, data-model §6).
    Adds "summary" (its path, or None) to `result`; a failure is kept as "summary_error"."""
    result["summary"] = None
    try:
        done = sdd_history.commit_summary(project, record)
        result["summary"] = done.get("path")
        if done.get("error"):
            result["summary_error"] = done["error"]
    except Exception as e:  # the landing has happened; a summary problem must not undo or hide it
        result["summary_error"] = str(e)
    return result


def land_ready(project, run_id: str) -> str | None:
    """Fast-forward the base branch to the run's branch while the run is suspended (FR-011): only when the
    base's commit is an ancestor of the branch's and they differ, and the base is not checked out. Moves the
    ref with `git update-ref` (checked against the old sha), never the checkout or the tree; stores
    `landed_commit`. Returns the new base sha, else None. Never raises."""
    try:
        project = Path(project)
        record = load(project, run_id)
        if record is None:
            return None
        branch, base = _branches(record)
        if _git(project, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip() == base:
            return None  # moving the checked-out branch would change what the tree is compared with
        base_sha = _git(project, "rev-parse", "--verify", "--quiet", f"refs/heads/{base}").stdout.strip()
        branch_sha = _git(project, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").stdout.strip()
        if not base_sha or not branch_sha or base_sha == branch_sha:
            return None
        if _git(project, "merge-base", "--is-ancestor", base_sha, branch_sha).returncode != 0:
            return None
        if _git(project, "update-ref", f"refs/heads/{base}", branch_sha, base_sha).returncode != 0:
            return None
        record["landed_commit"] = branch_sha
        save(project, record)
        return branch_sha
    except Exception:  # never raise: the caller is suspending a run
        return None


def end(project, run_id: str, outcome: str, reason: str | None) -> dict:
    """End the run (plan "Engine changes" 2): store `ended` with the outcome and reason, commit the task
    histories and missing session reports on the run branch (`sdd-history.py` `finalize`), then `land`, which
    commits the summary on the base once the landing is final. A refusal from `land` is kept as the
    landing (`needs-merge` stays; any other refusal stores `pending` with its `reason`). Never raises.
    Returns `{"finalize", "land"}`."""
    out: dict = {"finalize": None, "land": None}
    try:
        project = Path(project)
        record = load(project, run_id)
        if record is None:
            out["land"] = {"landed": False, "error": f"no such run: {run_id}"}
            return out
        record.update(state="ended", outcome=outcome, reason=reason, ended=int(time.time()),
                      landing=_landing("pending"))
        save(project, record)
        try:
            out["finalize"] = sdd_history.finalize(project, record)
        except Exception as e:
            out["finalize"] = {"commit": None, "files": [], "error": str(e)}
        try:
            out["land"] = land(project, run_id)
        except Refused as e:
            saved = load(project, run_id) or record
            landing = saved.get("landing") if isinstance(saved.get("landing"), dict) else None
            if not landing or landing.get("state") != "needs-merge":
                landing = {**_landing("pending"), "reason": str(e)}
                saved["landing"] = landing
                save(project, saved)
            out["land"] = {"landed": False, "error": str(e), "landing": landing}
    except Exception as e:  # never raise: the caller is ending a run
        out["land"] = out["land"] or {"landed": False, "error": f"could not end the run: {e}"}
    return out


def suspend(project, run_id: str, reason: str | None) -> dict:
    """Suspend the run (plan "Engine changes" 2): store `suspended` and the reason, then fast-forward the base
    to the finished work (`land_ready`); the tree is left as it is. Each task in flight of the run's own
    `.sdd/run.json` gets step `suspended` with the reason in its history (research R10). Returns
    `{"landed_commit"}`."""
    project = Path(project)
    record = load(project, run_id)
    if record is None:
        raise Refused(f"no such run: {run_id}")
    record.update(state="suspended", reason=reason)
    save(project, record)
    record_in_flight(project, record, "suspended", reason=reason)
    return {"landed_commit": land_ready(project, run_id)}


def record_in_flight(project, record: dict, step: str, **fields) -> list[str]:
    """Record `step` (`suspended`, `resumed`) in the history of each task in flight of the record's own
    `.sdd/run.json`, with its `run_id` as the session (research R10); no own run.json → nothing. Returns the
    tasks."""
    if not owns_run_json(project, record):
        return []
    try:
        run = json.loads((Path(project) / ".sdd" / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(run, dict):
        return []
    tasks = in_flight(run)
    session = str(run["run_id"]) if run.get("run_id") else None
    for task_id in tasks:
        sdd_history.record_step(project, record, task_id, step, session=session, **fields)
    return tasks


def note_usage(project, run_id: str, windows: dict) -> dict | None:
    """Raise the record's `usage_peak` (research R9) to each window's utilization in `windows`
    (`{"five_hour": 0.92}`) when it is higher; the record is saved only when one rose. Returns the new
    `usage_peak`, or None for an unknown run; never raises."""
    try:
        record = load(project, run_id)
        if record is None:
            return None
        peak = record.get("usage_peak")
        peak = dict(peak) if isinstance(peak, dict) else {}
        rose = False
        for name, value in (windows or {}).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            old = peak.get(name)
            if not isinstance(old, (int, float)) or isinstance(old, bool) or value > old:
                peak[name] = value
                rose = True
        if rose:
            record["usage_peak"] = peak
            save(project, record)
        return peak
    except Exception:  # noqa: BLE001 - a usage note must never break the supervisor
        return None


DROP_DIFF_LIMIT = 200_000
# Run bookkeeping that a Drop writes before it stashes; it stays in the tree for `finalize` to commit.
KEEP_OUT_OF_STASH = (".", ":(exclude).sdd/tasks")


def in_flight(run: dict | None) -> list[str]:
    """The tasks the run.json's workflow journal has in flight (`sdd-tasks.py checkpoint`); [] without one."""
    workflow_dir = (run or {}).get("workflow_dir")
    if not workflow_dir:
        return []
    return list(sdd_tasks.checkpoint(Path(workflow_dir) / "journal.jsonl")["tasks"])


def _uncommitted_patch(project: Path) -> str:
    """The tree's changes against HEAD, untracked files included, as a patch. Built in a copy of the index
    (`GIT_INDEX_FILE`), so the real index and the tree are left as they are."""
    git_dir = Path(project) / _git(project, "rev-parse", "--git-dir").stdout.strip()
    fd, tmp = tempfile.mkstemp(prefix="sdd-drop-index-", dir=git_dir)
    os.close(fd)
    try:
        index = git_dir / "index"
        if index.exists():
            shutil.copyfile(index, tmp)
        else:
            os.unlink(tmp)
        env = {**os.environ, "GIT_INDEX_FILE": tmp}
        subprocess.run(["git", "add", "-A", "--", *KEEP_OUT_OF_STASH], cwd=project, capture_output=True,
                       text=True, env=env)
        return subprocess.run(["git", "diff", "--cached", "HEAD", "--", *KEEP_OUT_OF_STASH], cwd=project,
                              capture_output=True, text=True, env=env).stdout
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def drop_suspended(project, run_id: str) -> dict:
    """Drop a suspended run (plan "Engine changes" 2): save the half-done work as a patch in the history of each
    task in flight (step `dropped`), stash it (`sdd-run <id>: dropped <tasks>`), archive `.sdd/run.json` under
    `.sdd/runs/<session>/`, then `end` the run `dropped`, which commits the histories and lands the finished
    commits. Refusals change nothing. Returns `{"dropped": true, "tasks", "stash", "end"}`."""
    project = Path(project)
    record = load(project, run_id)
    if record is None:
        raise Refused(f"no such run: {run_id}")
    state = shown_state(project, record)["state"]
    if state != "suspended":
        raise Refused(f"this run cannot be dropped: it is {state}")
    if _pid_alive(_status(project)):
        raise Refused("a run is active")
    if _git(project, "rev-parse", "--is-inside-work-tree").returncode != 0:
        raise Refused("the project is not a git repository")
    tasks, stash = _stash_in_flight(project, run_id, record, "dropped")
    reason = f"dropped by you during {', '.join(tasks)}" if tasks else "dropped by you"
    return {"dropped": True, "tasks": tasks, "stash": stash, "end": end(project, run_id, "dropped", reason)}


STASHED_REASON = "started after the stop; its edits were stashed"


def stash_and_end(project, run_id: str, outcome: str, reason: str | None) -> dict:
    """End a run stopped at a task boundary while a later task left edits in the tree (research R4): save the
    edits as a patch in the history of each task in flight (step `dropped`, reason `STASHED_REASON`), stash
    them (`sdd-run <id>: stopped, stashed <tasks>`), archive `.sdd/run.json` under `.sdd/runs/<session>/`, then
    `end` the run with `outcome` and `reason`, which commits the histories and lands the finished commits.
    Those tasks stay open. Returns `{"tasks", "stash", "end"}`."""
    project = Path(project)
    record = load(project, run_id)
    if record is None:
        raise Refused(f"no such run: {run_id}")
    if _git(project, "rev-parse", "--is-inside-work-tree").returncode != 0:
        raise Refused("the project is not a git repository")
    tasks, stash = _stash_in_flight(project, run_id, record, "stopped, stashed", live=True,
                                   reason=STASHED_REASON)
    return {"tasks": tasks, "stash": stash, "end": end(project, run_id, outcome, reason)}


def _live_run_json(project: Path) -> dict | None:
    """`.sdd/run.json` as a dict, or None when it is missing or unreadable."""
    try:
        run = json.loads((project / ".sdd" / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return run if isinstance(run, dict) else None


def _stash_in_flight(project: Path, run_id: str, record: dict, verb: str, live: bool = False,
                     **fields) -> tuple[list[str], str | None]:
    """The part Drop and a dirty boundary stop share: record step `dropped` with the patch (cut to
    `DROP_DIFF_LIMIT`) and `fields` in the history of each task in flight of the run's `.sdd/run.json`, stash
    the tree over `KEEP_OUT_OF_STASH` as `sdd-run <id>: <verb> <tasks or 'partial work'>`, archive run.json
    under `.sdd/runs/<session>/` and add the session to the record's `runs`. The run.json is the run's when
    the record owns it (`owns_run_json`), or whenever it exists with `live` (the supervisor's own session has
    just stopped). Returns the tasks and the stash message (None when there was nothing to stash). `Refused`
    when the stash fails."""
    run_json = project / ".sdd" / "run.json"
    run = _live_run_json(project) if live or owns_run_json(project, record) else None
    tasks = in_flight(run)
    session = str(run["run_id"]) if run and run.get("run_id") else None

    patch = _uncommitted_patch(project)
    for task_id in tasks:
        sdd_history.record_step(project, record, task_id, "dropped", session=session,
                                diff=patch[:DROP_DIFF_LIMIT], **fields)
    what = ", ".join(tasks) if tasks else "partial work"
    stash = None
    if _git(project, "status", "--porcelain", "--", *KEEP_OUT_OF_STASH).stdout.strip():
        message = f"sdd-run {run_id}: {verb} {what}"
        stashed = _git(project, "stash", "push", "-u", "-m", message, "--", *KEEP_OUT_OF_STASH)
        if stashed.returncode != 0:
            raise Refused(f"could not stash the half-done work: {(stashed.stderr or stashed.stdout).strip()}")
        stash = message
    if run is not None:
        archive = project / ".sdd" / "runs" / (session or "unknown") / "run.json"
        archive.parent.mkdir(parents=True, exist_ok=True)
        os.replace(run_json, archive)
    if session:
        record = load(project, run_id) or record
        runs = record.setdefault("runs", [])
        if session not in runs:
            runs.append(session)
            save(project, record)
    return tasks, stash


_launcher_module = None


def _launcher():
    """`bin/sdd-launch.py`, loaded on first use (it loads this module itself, so not at import time)."""
    global _launcher_module
    if _launcher_module is None:
        _launcher_module = _load_module("sdd_launch", "sdd-launch.py")
    return _launcher_module


def _scope_runnable(project, record: dict) -> int:
    """How many tasks in the Job's scope `sdd-tasks.py plan` finds runnable now."""
    project = Path(project)
    feature = feature_dir(project, record)
    scope = record.get("scope") or {}
    plan = sdd_tasks.plan(sdd_tasks.parse_tasks(feature / "tasks.md"),
                          sdd_tasks.parse_blocked_entries(feature / "BLOCKED.md"),
                          sdd_tasks.load_config(project / ".sdd" / "config.toml"),
                          [str(p) for p in scope.get("phases") or []] or None, None,
                          [str(t) for t in scope.get("tasks") or []] or None)
    return plan["runnable"]


def after_settle(project) -> dict:
    """Go on after the person settled an item (009 FR-024, research R6: only the newest run restarts).

    Called by the helpers that commit a settle (`sdd-decide.py`, `sdd-manual.py`, `sdd-unblock.py`).
    Takes the newest record (`list_jobs`, read through `normalize`) and starts a new run through
    `sdd-launch.py`'s `launch_job` with its scope, limits and title only when it ended with outcome `waiting`,
    no run is open (`open_run`), its stop-by time has not passed and its scope has a runnable task. Never
    raises: returns `{"started": True, "reason": None}`, or `{"started": False, "reason": "<why not>"}`."""
    try:
        project = Path(project)
        records = list_jobs(project)
        if not records:
            return {"started": False, "reason": "no runs yet"}
        newest = normalize(project, records[0])
        if newest.get("state") != "ended" or newest.get("outcome") != "waiting":
            return {"started": False, "reason": "the newest run did not end waiting"}
        other = open_run(project)
        if other is not None:
            return {"started": False, "reason": f"a run is open: {other.get('title') or other.get('id')}"}
        until = (newest.get("limits") or {}).get("until")
        if until is not None and time.time() >= until:
            return {"started": False, "reason": "the run's stop-by time has passed"}
        if _scope_runnable(project, newest) == 0:
            return {"started": False, "reason": "no task in the scope is runnable"}
        launcher = _launcher()
        try:
            launcher.launch_job(project, feature_dir(project, newest), newest.get("scope"), newest.get("limits"),
                                title=newest.get("title"))
        except launcher.Refused as e:
            return {"started": False, "reason": str(e)}
        return {"started": True, "reason": None}
    except Exception as e:  # never raise: the caller's commit has already succeeded
        return {"started": False, "reason": f"could not start a run: {e}"}


def _with_view(project, record: dict) -> dict:
    """The record plus `shown` (shown_state) and `progress` (None when tasks.md cannot be read)."""
    try:
        counts = progress(project, record)
    except (OSError, ValueError, KeyError):
        counts = None
    return {**record, "shown": shown_state(project, record), "progress": counts}


USAGE = ("usage: sdd-job.py list [project] | show <id> [project] | merge <id> [project] | drop <id> [project]"
         " | land <id> [project]")


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("list", "show", "merge", "drop", "land"):
        print(USAGE, file=sys.stderr)
        return 2
    command, rest = argv[0], argv[1:]
    if command == "list":
        if len(rest) > 1:
            print(USAGE, file=sys.stderr)
            return 2
        project = Path(rest[0] if rest else ".")
        print(json.dumps([_with_view(project, r) for r in list_jobs(project)], ensure_ascii=False))
        return 0
    if not 1 <= len(rest) <= 2:
        print(USAGE, file=sys.stderr)
        return 2
    job_id, project = rest[0], Path(rest[1] if len(rest) > 1 else ".")
    try:
        if command == "show":
            record = load(project, job_id)
            if record is None:
                raise Refused(f"no such job: {job_id}")
            result = _with_view(project, record)
        elif command in ("merge", "land"):  # a 009 run: merge = land (FR-023, research R11)
            result = land(project, job_id)
        else:
            result = drop_suspended(project, job_id)
    except Refused as e:
        print(json.dumps({"contract": sdd_tasks.CONTRACT, "error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps({"contract": sdd_tasks.CONTRACT, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
