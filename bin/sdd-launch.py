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
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sdd_tasks = _load("sdd_tasks", "sdd-tasks.py")
sdd_supervise = _load("sdd_supervise", "sdd-supervise.py")

# CLAUDE.md: without these the orchestrating session runs on the default model and has stopped at
# step 6.2 on a permission denial. Set only when the caller has not chosen a model.
DEFAULT_MODEL = "claude-opus-5-5"
MAX_TASKS_LIMIT = 500


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
    return {"pid": proc.pid, "run_args": run_args, "log": str(logs / "launch.log"), "resumes": info["resumes"]}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", type=Path)
    parser.add_argument("feature", type=Path)
    parser.add_argument("--phases", default="")
    parser.add_argument("--no-converge", action="store_true")
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = launch(args.project, args.feature, [p for p in args.phases.split(",") if p],
                        args.no_converge, args.max_tasks, args.dry_run)
    except Refused as e:
        print(f"refused: {e}")
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
