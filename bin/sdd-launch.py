#!/usr/bin/env python3
"""Start a supervised unattended run from the dashboard (spec 003 FR-034).

    bin/sdd-launch.py <project dir> <feature dir> [--phases 11,12] [--no-converge] [--max-tasks N] [--dry-run]

`preview()` says what a run would do and whether it can start now; `launch()` starts
`bin/sdd-supervise.py` detached, which in turn runs `claude -p "/sdd-run --yes …"`. The supervisor's
state directory then carries `status.json` and `control.json`, so the dashboard's Stop, Resume and
Keep paused buttons work on the run it started. Output goes to `launch.log` in that directory.

It refuses, and starts nothing, when a supervisor is already running, when the working tree has
changes (unless an interrupted run is being resumed, whose work is in the tree on purpose), when the
project is not a git repository, or when no task is runnable. Stdlib only; no model of its own.

    bin/sdd-launch.py <project dir> <feature dir> --job [--phases 11,12 | --tasks T061,T063] [--max-tasks N]
                      [--until EPOCH] [--max-runs N] [--title TEXT] [--dry-run]

`launch_job()` starts a Job (spec 005, data-model §8): it writes `.sdd/jobs/<id>.json`, creates the branch
`auto/<id>` and starts `bin/sdd-supervise.py --job <id>` detached, which runs the scope run after run.

    bin/sdd-launch.py <project dir> <feature dir> --continue <id> [--max-tasks N|none] [--until EPOCH|none]
                      [--max-runs N|none]

`continue_job()` continues a `waiting`, `interrupted`, `held` or `stopped` Job on its branch (005 FR-009,
data-model §3 and §8); a Job stopped by a limit needs a new value for it. Counts are never reset.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sdd_tasks = _load("sdd_tasks", "sdd-tasks.py")
sdd_supervise = _load("sdd_supervise", "sdd-supervise.py")
sdd_job = _load("sdd_job", "sdd-job.py")

# CLAUDE.md: without these the orchestrating session runs on the default model and has stopped at
# step 6.2 on a permission denial. Set only when the caller has not chosen a model.
DEFAULT_MODEL = "claude-opus-5-5"
MAX_TASKS_LIMIT = 500
DEFAULT_MAX_RUNS = 20  # a Job's run cap unless the person gives one (005 data-model §1)


class Refused(Exception):
    """The run cannot start now; nothing was started."""


def _supervisor_running(project: Path) -> bool:
    try:
        status = json.loads((sdd_supervise.state_dir(project) / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    pid = status.get("pid") if isinstance(status, dict) else None
    if status.get("state") in (None, "done", "stopped") or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _spawn(command, **kwargs):
    return subprocess.Popen(command, **kwargs)


def _git_output(project: Path, *args: str):
    result = subprocess.run(["git", *args], cwd=project, capture_output=True, text=True)
    return result.returncode, result.stdout.strip()


def preview(project: Path, feature: Path) -> dict:
    """What a run would do, and `can_start` / `reason`. Never changes anything."""
    project, feature = Path(project).resolve(), Path(feature).resolve()
    parsed = sdd_tasks.parse_tasks(feature / "tasks.md")
    blocked = sdd_tasks.parse_blocked_entries(feature / "BLOCKED.md")
    config = sdd_tasks.load_config(project / ".sdd" / "config.toml")
    plan = sdd_tasks.plan(parsed, blocked, config, None, None)
    phases = [{"id": p["id"], "title": p["title"],
               "runnable": sum(len(g) for g in p["groups"])} for p in plan["phases"]]
    result = {"phases": phases, "runnable": plan["runnable"], "held": len(plan["held"]),
              "can_start": True, "reason": None, "resumes": None}

    run_json = project / ".sdd" / "run.json"
    if _supervisor_running(project):
        result.update(can_start=False, reason="a supervised run is already in progress")
        return result
    if run_json.exists():
        try:
            result["resumes"] = json.loads(run_json.read_text(encoding="utf-8")).get("run_id")
        except (OSError, ValueError):
            result["resumes"] = "unknown"
    code, _ = _git_output(project, "rev-parse", "--is-inside-work-tree")
    if code != 0:
        result.update(can_start=False, reason="the project is not a git repository")
        return result
    if not result["resumes"]:
        status = subprocess.run(["git", "status", "--porcelain"], cwd=project, capture_output=True, text=True)
        dirty = status.stdout.rstrip("\n")  # not .strip(): the first line may start with a space
        if dirty:
            files = ", ".join(line[3:] for line in dirty.splitlines()[:5])
            result.update(can_start=False, reason=f"the working tree has changes; commit or stash them first: {files}")
            return result
    if plan["runnable"] == 0 and not result["resumes"]:
        result.update(can_start=False, reason="no task is runnable (everything is done or held)")
    return result


def build_run_args(phases: list[str] | None, no_converge: bool, max_tasks: int | None) -> str:
    parts = []
    if no_converge:
        parts.append("--no-converge")
    if phases:
        parts.append("--phases " + ",".join(phases))
    if max_tasks:
        parts.append(f"--max-tasks {max_tasks}")
    return " ".join(parts)


def launch(project: Path, feature: Path, phases: list[str] | None = None, no_converge: bool = True,
           max_tasks: int | None = None, dry_run: bool = False) -> dict:
    project = Path(project).resolve()
    info = preview(project, feature)
    if not info["can_start"]:
        raise Refused(info["reason"])
    known = {p["id"] for p in info["phases"]}
    phases = [str(p) for p in (phases or [])]
    for p in phases:
        if p not in known:
            raise Refused(f"phase {p} has no runnable task")
    if max_tasks is not None and not (isinstance(max_tasks, int) and not isinstance(max_tasks, bool)
                                      and 1 <= max_tasks <= MAX_TASKS_LIMIT):
        raise Refused(f"max tasks must be a whole number from 1 to {MAX_TASKS_LIMIT}")
    run_args = build_run_args(phases, no_converge, max_tasks)
    command = [sys.executable, str(HERE / "sdd-supervise.py"), "--project", str(project),
               "--run-args", run_args, "--max-wait-hours", "0", "--max-cycles", "1"]
    if dry_run:
        return {"pid": None, "run_args": run_args, "command": command, "resumes": info["resumes"]}
    pid, log = _start_supervisor(project, command)
    return {"pid": pid, "run_args": run_args, "log": log, "resumes": info["resumes"]}


def _start_supervisor(project: Path, command: list[str]) -> tuple[int, str]:
    """Start the supervisor detached, output to `launch.log` in its state directory; (pid, log path)."""
    logs = sdd_supervise.state_dir(project)
    logs.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("ANTHROPIC_MODEL", DEFAULT_MODEL)
    env.setdefault("CLAUDE_CODE_SUBAGENT_MODEL", DEFAULT_MODEL)
    log = open(logs / "launch.log", "ab")
    try:
        proc = _spawn(command, cwd=str(project.parent), env=env, stdin=subprocess.DEVNULL,
                      stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    finally:
        log.close()
    return proc.pid, str(logs / "launch.log")


def _whole_number(value, low: int, high: int | None = None) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool) and value >= low
            and (high is None or value <= high))


def _feature_name(project: Path, feature: Path) -> str:
    """The record's `feature`: the folder name under `specs/`, else the path relative to the project."""
    try:
        relative = feature.relative_to(project)
    except ValueError:
        return str(feature)
    if relative.parent == Path("specs"):
        return relative.name
    return relative.as_posix()


def _check_scope(scope) -> dict:
    """Exactly one of `{"phases": [...]}`, `{"tasks": [...]}`, `{"all": true}` (data-model §1)."""
    scope = scope or {"all": True}
    phases = [str(p) for p in scope.get("phases") or []]
    tasks = [str(t) for t in scope.get("tasks") or []]
    if phases and tasks:
        raise Refused("give phases or tasks, not both")
    if phases:
        return {"phases": phases}
    if tasks:
        return {"tasks": tasks}
    return {"all": True}


def _check_limits(limits) -> dict:
    """`{max_tasks, until, max_runs}`; `max_runs` is 20 unless given (a given null means no cap)."""
    limits = dict(limits or {})
    max_tasks = limits.get("max_tasks")
    until = limits.get("until")
    max_runs = limits.get("max_runs", DEFAULT_MAX_RUNS)
    if max_tasks is not None and not _whole_number(max_tasks, 1, MAX_TASKS_LIMIT):
        raise Refused(f"max tasks must be a whole number from 1 to {MAX_TASKS_LIMIT}")
    if max_runs is not None and not _whole_number(max_runs, 1):
        raise Refused("max runs must be a whole number from 1")
    if until is not None and (isinstance(until, bool) or not isinstance(until, (int, float))):
        raise Refused("until must be a time in epoch seconds")
    return {"max_tasks": max_tasks, "until": until, "max_runs": max_runs}


def _branch_exists(project: Path, branch: str) -> bool:
    code, _ = _git_output(project, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    return code == 0


def _job_id(project: Path) -> str:
    """`sdd_job.new_id`, with a further suffix while `auto/<id>` is already a branch (a run's, say)."""
    job_id = sdd_job.new_id(project)
    base, n = job_id[:13], 1
    while _branch_exists(project, f"auto/{job_id}") or (sdd_job.jobs_dir(project) / f"{job_id}.json").exists():
        n += 1
        job_id = f"{base}-{n}"
    return job_id


def launch_job(project: Path, feature: Path, scope: dict | None, limits: dict | None,
               title: str | None = None, dry_run: bool = False) -> dict:
    """Start a Job (005 FR-003, data-model §8): the record, the branch `auto/<id>`, and the supervisor
    with `--job <id>` (no `--max-wait-hours 0`: a Job waits for any usage reset, research R7).

    Refuses, changing nothing, when another Job is open, when a run is active, when the project is not a
    git repository, when the tree has changes, or when the scope has no runnable task."""
    project, feature = Path(project).resolve(), Path(feature).resolve()
    scope = _check_scope(scope)
    limits = _check_limits(limits)
    other = sdd_job.open_job(project)
    if other is not None:
        raise Refused(f"another job is open: {other.get('title') or other.get('id')}")
    if _supervisor_running(project) or (project / ".sdd" / "run.json").exists():
        raise Refused("a run is active")
    code, _ = _git_output(project, "rev-parse", "--is-inside-work-tree")
    if code != 0:
        raise Refused("the project is not a git repository")
    status = subprocess.run(["git", "status", "--porcelain"], cwd=project, capture_output=True, text=True)
    dirty = status.stdout.rstrip("\n")  # not .strip(): the first line may start with a space
    if dirty:
        files = ", ".join(line[3:] for line in dirty.splitlines()[:5])
        raise Refused(f"the working tree has changes; commit or stash them first: {files}")
    blocked = sdd_tasks.parse_blocked_entries(feature / "BLOCKED.md")
    plan = sdd_tasks.plan(sdd_tasks.parse_tasks(feature / "tasks.md"), blocked,
                          sdd_tasks.load_config(project / ".sdd" / "config.toml"),
                          scope.get("phases"), None, scope.get("tasks"))
    if plan["runnable"] == 0:
        raise Refused("no task in the scope is runnable")

    _, base_branch = _git_output(project, "rev-parse", "--abbrev-ref", "HEAD")
    _, base_commit = _git_output(project, "rev-parse", "--short", "HEAD")
    job_id = _job_id(project)
    branch = f"auto/{job_id}"
    scope_tasks = list(dict.fromkeys([i for p in plan["phases"] for i in p["tasks"]]
                                     + [h["id"] for h in plan["held"]]))
    record = {
        "id": job_id,
        "title": (title or "").strip() or sdd_job.default_title(scope),
        "feature": _feature_name(project, feature),
        "scope": scope,
        "scope_tasks": scope_tasks,
        "blocked_at_start": len(blocked),
        "limits": limits,
        "branch": branch,
        "base": {"branch": base_branch, "commit": base_commit},
        "state": "working",
        "reason": None,
        "runs": [],
        "no_progress_runs": 0,
        "created": int(time.time()),
        "ended": None,
        "merge_commit": None,
    }
    command = [sys.executable, str(HERE / "sdd-supervise.py"), "--project", str(project), "--job", job_id]
    if dry_run:
        return {"job": job_id, "branch": branch, "pid": None, "command": command, "record": record}
    switched = subprocess.run(["git", "switch", "-c", branch], cwd=project, capture_output=True, text=True)
    if switched.returncode != 0:
        raise Refused(f"could not create the branch {branch}: {switched.stderr.strip()}")
    sdd_job.save(project, record)
    pid, log = _start_supervisor(project, command)
    return {"job": job_id, "branch": branch, "pid": pid, "log": log}


CONTINUABLE_STATES = ("waiting", "interrupted", "held", "stopped")


def _limit_in_reason(record: dict) -> str | None:
    """The limit a `stopped` Job was stopped by, from its reason ("max_tasks 10 reached",
    "until 18:00 reached", "max_runs 20 reached"; `sdd-supervise.py` `limit_reason`), else None."""
    reason = record.get("reason")
    if record.get("state") != "stopped" or not isinstance(reason, str) or not reason.endswith(" reached"):
        return None
    name = reason.split(" ", 1)[0]
    return name if name in sdd_job.LIMIT_NAMES else None


def _read_run_json(project: Path) -> dict | None:
    """`.sdd/run.json` as a dict ({} when unreadable), or None when there is none."""
    path = project / ".sdd" / "run.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def continue_job(project: Path, job_id: str, new_limits: dict | None = None) -> dict:
    """Continue a Job (005 FR-009, data-model §3 and §8): from shown `waiting`, `interrupted`, `held` or
    `stopped`, set `working` and start `sdd-supervise.py --job <id>` detached.

    `new_limits` holds only the limits the person changes (`max_tasks`, `until`, `max_runs`; null = none);
    the others and every count (runs, ticked scope tasks) are kept. A Job stopped by a limit needs a new value
    for that limit. Refuses, changing nothing, as `launch_job` does except "another job is open". When
    resuming (`.sdd/run.json` belongs to the Job's branch) the tree may have changes, but the checkout must
    already be on the Job's branch; otherwise the tree must be clean and the Job's branch is checked out."""
    project = Path(project).resolve()
    record = sdd_job.load(project, job_id)
    if record is None:
        raise Refused(f"no such job: {job_id}")
    shown = sdd_job.display_state(project, record)["state"]
    if shown not in CONTINUABLE_STATES:
        raise Refused(f"this job cannot be continued in state {shown}")
    new_limits = dict(new_limits or {})
    unknown = sorted(set(new_limits) - set(sdd_job.LIMIT_NAMES))
    if unknown:
        raise Refused(f"unknown limit: {', '.join(unknown)}")
    limits = _check_limits({**(record.get("limits") or {"max_runs": DEFAULT_MAX_RUNS}), **new_limits})
    stopped_by = _limit_in_reason(record)
    if stopped_by and stopped_by not in new_limits:
        raise Refused(f"the limit {stopped_by} is still reached; give a new value")
    if _supervisor_running(project):
        raise Refused("a run is active")
    branch = record.get("branch")
    run_json = _read_run_json(project)
    resuming = run_json is not None and bool(branch) and (
        run_json.get("branch") == branch or (run_json.get("job") == job_id and "branch" not in run_json))
    if run_json is not None and not resuming:
        raise Refused("a run is active")
    code, _ = _git_output(project, "rev-parse", "--is-inside-work-tree")
    if code != 0:
        raise Refused("the project is not a git repository")
    if not branch:
        raise Refused("the job has no branch")
    _, current = _git_output(project, "rev-parse", "--abbrev-ref", "HEAD")
    switched_from = None
    if resuming:
        if current != branch:
            raise Refused("the checkout is not on the Job's branch")
    else:
        status = subprocess.run(["git", "status", "--porcelain"], cwd=project, capture_output=True, text=True)
        dirty = status.stdout.rstrip("\n")  # not .strip(): the first line may start with a space
        if dirty:
            files = ", ".join(line[3:] for line in dirty.splitlines()[:5])
            raise Refused(f"the working tree has changes; commit or stash them first: {files}")
        if current != branch:
            switched = subprocess.run(["git", "switch", branch], cwd=project, capture_output=True, text=True)
            if switched.returncode != 0:
                raise Refused(f"could not switch to {branch}: {switched.stderr.strip()}")
            switched_from = current

    # The scope and the limits are read on the Job's branch; a refusal here switches back (the tree is clean).
    try:
        updated = {**record, "limits": limits}
        reached = sdd_job.limit_reached(project, updated)
        if reached:
            raise Refused(f"the limit {reached} is still reached; give a new value")
        if not resuming:
            feature = sdd_job.feature_dir(project, record)
            scope = record.get("scope") or {}
            plan = sdd_tasks.plan(sdd_tasks.parse_tasks(feature / "tasks.md"),
                                  sdd_tasks.parse_blocked_entries(feature / "BLOCKED.md"),
                                  sdd_tasks.load_config(project / ".sdd" / "config.toml"),
                                  [str(p) for p in scope.get("phases") or []] or None, None,
                                  [str(t) for t in scope.get("tasks") or []] or None)
            if plan["runnable"] == 0:
                if sdd_job.finish_if_complete(project, record):
                    raise Refused("nothing is left to run: the job is now done, so Merge it")
                raise Refused("no task in the scope is runnable")
    except Refused:
        if switched_from:
            subprocess.run(["git", "switch", switched_from], cwd=project, capture_output=True, text=True)
        raise

    updated.update(state="working", reason=None)
    sdd_job.save(project, updated)
    command = [sys.executable, str(HERE / "sdd-supervise.py"), "--project", str(project), "--job", job_id]
    pid, log = _start_supervisor(project, command)
    return {"job": job_id, "pid": pid, "log": log, "resumes": run_json.get("run_id") if resuming else None}


def _limit_value(kind: type):
    """An argparse type: a number, or `none` for no limit (returned as the string "none")."""
    def parse(text: str):
        if text.strip().lower() == "none":
            return "none"
        try:
            value = float(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number or none: {text}") from None
        if kind is int:
            if not value.is_integer():
                raise argparse.ArgumentTypeError(f"not a whole number or none: {text}")
            return int(value)
        return int(value) if value.is_integer() else value
    return parse


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", type=Path)
    parser.add_argument("feature", type=Path)
    parser.add_argument("--phases", default="")
    parser.add_argument("--no-converge", action="store_true")
    parser.add_argument("--max-tasks", type=_limit_value(int), default=None, help="a number, or none")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--job", action="store_true", help="start a Job (spec 005) instead of a single run")
    parser.add_argument("--continue", dest="continue_id", default=None, metavar="ID",
                        help="continue the Job with this id (with --max-tasks/--until/--max-runs N|none)")
    parser.add_argument("--tasks", default="", help="with --job: the Job's scope as task ids, T061,T063")
    parser.add_argument("--until", type=_limit_value(float), default=None,
                        help="with --job or --continue: stop by this time (epoch seconds), or none")
    parser.add_argument("--max-runs", type=_limit_value(int), default=None,
                        help=f"with --job or --continue: the run cap (default {DEFAULT_MAX_RUNS}), or none")
    parser.add_argument("--title", default=None, help="with --job: the Job's title (default: the scope in words)")
    args = parser.parse_args(argv)
    phases = [p for p in args.phases.split(",") if p]
    tasks = [t for t in args.tasks.split(",") if t]
    given = {name: value for name, value in (("max_tasks", args.max_tasks), ("until", args.until),
                                              ("max_runs", args.max_runs)) if value is not None}
    limits = {name: None if value == "none" else value for name, value in given.items()}
    continuing = args.continue_id is not None
    if continuing and (args.job or phases or tasks or args.title is not None or args.dry_run):
        parser.error("--continue takes only --max-tasks, --until and --max-runs")
    if not args.job and not continuing and (tasks or args.title is not None or "until" in given
                                            or "max_runs" in given):
        parser.error("--tasks and --title need --job; --until and --max-runs need --job or --continue")
    if args.job and phases and tasks:
        parser.error("give --phases or --tasks, not both")
    try:
        if continuing:
            result = continue_job(args.project, args.continue_id, limits)
        elif args.job:
            scope = {"phases": phases} if phases else {"tasks": tasks} if tasks else {"all": True}
            limits = {"max_tasks": None, "until": None, "max_runs": DEFAULT_MAX_RUNS, **limits}
            result = launch_job(args.project, args.feature, scope, limits, args.title, args.dry_run)
        else:
            result = launch(args.project, args.feature, phases, args.no_converge, limits.get("max_tasks"),
                            args.dry_run)
    except Refused as e:
        print(f"refused: {e}")
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
