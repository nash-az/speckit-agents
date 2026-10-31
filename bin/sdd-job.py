#!/usr/bin/env python3
"""Jobs: the record of one piece of work that spans several runs (spec 005, data-model §1 and §7).

A Job lives in `.sdd/jobs/<id>.json`. Every write goes through `save()`, which writes `<id>.json.tmp` and
then `os.replace`s it, so a reader never sees half a record; keys this module does not know are kept.
At most one Job per project is open (state not `merged` or `dropped`); `open_job()` returns it.

Functions so far: `load`, `save`, `list_jobs`, `open_job`, `new_id`, `default_title`, `display_state`,
`scope_args`, `progress`, `limit_reached`, `merge`, `drop` (these two raise `Refused`, changing nothing),
`after_settle` (continues a `waiting` Job after a settle; never raises).

    bin/sdd-job.py list [project]          -> [record + shown + progress, ...]
    bin/sdd-job.py show <id> [project]     -> record + shown + progress
    bin/sdd-job.py merge <id> [project]    -> {"merged": true, "commit": "<sha>", "fast_forward": bool}
    bin/sdd-job.py drop <id> [project]     -> {"dropped": true}

A refusal prints `refused: <reason>` and exits 1. Stdlib only; no model.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
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

ID_RE = re.compile(r"^\d{8}-\d{4}(?:-(\d+))?$")
CLOSED_STATES = ("merged", "dropped")
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
    """Write the record atomically (`<id>.json.tmp`, then `os.replace`); every key is kept."""
    job_id = record.get("id")
    if not _valid_id(job_id):
        raise ValueError(f"not a job id: {job_id!r}")
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


def open_job(project) -> dict | None:
    """The record whose state is not `merged` or `dropped` (newest if several), else None."""
    for record in list_jobs(project):
        if record.get("state") not in CLOSED_STATES:
            return record
    return None


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


def display_state(project, record: dict) -> dict:
    """The shown state (data-model §2), first rule wins:
    1. stored `working`, `status.json` state `waiting` and a live pid -> `paused` (with `resets_at`, `keep_paused`);
    2. stored `working` and no live supervisor -> `interrupted`;
    3. otherwise the stored state.
    Returns `{"state": ...}`, plus `resets_at` and `keep_paused` for `paused`."""
    stored = record.get("state")
    if stored == "working":
        status = _status(project)
        alive = _pid_alive(status)
        if alive and status.get("state") == "waiting":
            return {"state": "paused", "resets_at": status.get("resets_at"),
                    "keep_paused": bool(status.get("keep_paused"))}
        if not alive:
            return {"state": "interrupted"}
    return {"state": stored}


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


def _closable(project, job_id: str, verb: str) -> dict:
    """The record, after the refusals shared by Merge and Drop (research R9); raises `Refused`."""
    record = load(project, job_id)
    if record is None:
        raise Refused(f"no such job: {job_id}")
    state = record.get("state")
    if state not in ("done", "stopped", "failed"):
        raise Refused(f"this job cannot be {verb} in state {state}")
    if _run_active(project):
        raise Refused("a run is active")
    if _git(project, "rev-parse", "--is-inside-work-tree").returncode != 0:
        raise Refused("the project is not a git repository")
    files = _dirty_files(project)
    if files:
        raise Refused(f"the working tree has changes; commit or stash them first: {files}")
    return record


def _switch(project, branch: str) -> None:
    switched = _git(project, "switch", branch)
    if switched.returncode != 0:
        raise Refused(f"could not switch to {branch}: {switched.stderr.strip()}")


def merge(project, job_id: str) -> dict:
    """Merge the Job's branch into its base branch (FR-010, research R9): switch to the base, fast-forward
    when possible, else one merge commit "merge: job <title>". On a conflict the merge is aborted, the checkout
    goes back to where it was (the Job's branch) and `Refused` names the files. Never forces, pushes or
    deletes a branch. Returns `{"merged": true, "commit": sha, "fast_forward": bool}`."""
    project = Path(project)
    record = _closable(project, job_id, "merged")
    branch, base = record.get("branch"), (record.get("base") or {}).get("branch")
    if not branch or not base:
        raise Refused("the job has no branch or base branch")
    current = _git(project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    back = current if current and current != "HEAD" else branch
    _switch(project, base)
    fast_forward = _git(project, "merge", "--ff-only", branch).returncode == 0
    if not fast_forward:
        merged = _git(project, "merge", "--no-ff", "-m", f"merge: job {record.get('title') or job_id}", branch)
        if merged.returncode != 0:
            conflicted = _git(project, "diff", "--name-only", "--diff-filter=U").stdout.split()
            _git(project, "merge", "--abort")
            _git(project, "switch", back)
            if conflicted:
                raise Refused(f"the merge conflicts in: {', '.join(conflicted)}")
            raise Refused(f"the merge failed: {(merged.stderr or merged.stdout).strip()}")
    commit = _git(project, "rev-parse", "HEAD").stdout.strip()
    record.update(state="merged", merge_commit=commit, ended=int(time.time()))
    save(project, record)
    return {"merged": True, "commit": commit, "fast_forward": fast_forward}


def drop(project, job_id: str) -> dict:
    """Drop the Job (FR-017): switch to its base branch and set `dropped`; the Job's branch and commits are
    kept. Same refusals as `merge`. Returns `{"dropped": true}`."""
    project = Path(project)
    record = _closable(project, job_id, "dropped")
    base = (record.get("base") or {}).get("branch")
    if not base:
        raise Refused("the job has no base branch")
    _switch(project, base)
    record.update(state="dropped", ended=int(time.time()))
    save(project, record)
    return {"dropped": True}


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
    """Continue a `waiting` Job after the person settled an item (FR-016, research R8).

    Called by the helpers that commit a settle (`sdd-decide.py`, `sdd-manual.py`, `sdd-unblock.py`). Starts
    the next run through `sdd-launch.py`'s `continue_job` when the open Job's shown state is `waiting`, no
    `.sdd/run.json` exists, no supervisor is alive and the scope has a runnable task. Never raises: returns
    `{"started": True, "reason": None}`, or `{"started": False, "reason": "<why not>"}`."""
    try:
        project = Path(project)
        record = open_job(project)
        if record is None:
            return {"started": False, "reason": "no open job"}
        state = display_state(project, record)["state"]
        if state != "waiting":
            return {"started": False, "reason": f"the job is {state}, not waiting"}
        if (project / ".sdd" / "run.json").exists():
            return {"started": False, "reason": "a run is active"}
        if _pid_alive(_status(project)):
            return {"started": False, "reason": "a supervisor is running"}
        if _scope_runnable(project, record) == 0:
            return {"started": False, "reason": "no task in the scope is runnable"}
        launcher = _launcher()
        try:
            launcher.continue_job(project, record["id"])
        except launcher.Refused as e:
            return {"started": False, "reason": str(e)}
        return {"started": True, "reason": None}
    except Exception as e:  # never raise: the caller's commit has already succeeded
        return {"started": False, "reason": f"could not continue the job: {e}"}


def _with_view(project, record: dict) -> dict:
    """The record plus `shown` (display_state) and `progress` (None when tasks.md cannot be read)."""
    try:
        counts = progress(project, record)
    except (OSError, ValueError, KeyError):
        counts = None
    return {**record, "shown": display_state(project, record), "progress": counts}


USAGE = "usage: sdd-job.py list [project] | show <id> [project] | merge <id> [project] | drop <id> [project]"


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("list", "show", "merge", "drop"):
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
        elif command == "merge":
            result = merge(project, job_id)
        else:
            result = drop(project, job_id)
    except Refused as e:
        print(f"refused: {e}")
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
