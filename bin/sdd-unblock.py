#!/usr/bin/env python3
"""Settle an open BLOCKED.md entry, a task a run could not finish (spec 003 FR-037).

Used by the dashboard's buttons on a blocked task and usable from the shell:

    bin/sdd-unblock.py done   <project dir> <feature dir> <task id> ["<note>"]
    bin/sdd-unblock.py answer <project dir> <feature dir> <task id> "<answer>"
    bin/sdd-unblock.py drop   <project dir> <feature dir> <task id> "<reason>"

`done`: the person did the step by hand; the task is ticked in `tasks.md` and the entry becomes `resolved`
with the note as its answer. `answer`: the entry becomes `resolved` with the answer, so the next `/sdd-run`
runs the task again with it (FR-023). `drop`: the entry becomes `wontfix` with the reason. The task's last
entry is rewritten in place; the other entries stay as they are. Each commits just the files it touched as
`chore(unblock): <task id> ...`. After the commit, a waiting Job continues when nothing blocks it
(spec 005 FR-016, `sdd-job.py after_settle`); its result is the `job` key of the output.

It refuses, and changes nothing, when a run is active (`.sdd/run.json`), when a file it would touch already
has uncommitted changes, when the project is not a git repository, when the task has no open entry, or when
the answer or reason is empty. If the commit fails the files are put back. Stdlib only; no model.
From the shell it prints `settle`'s result as one JSON object with `"contract"` (008 data-model §1), or
`{"contract", "error"}` with exit code 1 (contracts/engine-cli.md).
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location("sdd_job", Path(__file__).with_name("sdd-job.py"))
sdd_job = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sdd_job)

ACTIONS = ("done", "answer", "drop")
HEAD_RE = re.compile(r"^## (T\d+)\b")
FIELD_RE = re.compile(r"^- (?P<key>[A-Za-z_]+):(?P<value>.*)$")
TASK_LINE = r"^- \[ \] {id}\b(?P<rest>.*)$"


class Refused(Exception):
    """The entry cannot be settled now; nothing was changed."""


def _git(project: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=project, capture_output=True, text=True)
    if result.returncode != 0:
        raise Refused((result.stderr or result.stdout).strip() or f"git {args[0]} failed")
    return result.stdout


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _last_entry(lines: list[str], task_id: str) -> tuple[int, int] | None:
    """(start, end) line indexes of the task's last `## <id>` entry, end exclusive."""
    found = None
    for i, line in enumerate(lines):
        m = HEAD_RE.match(line)
        if m and m.group(1) == task_id:
            end = next((j for j in range(i + 1, len(lines)) if lines[j].startswith("## ")), len(lines))
            found = (i, end)
    return found


def _rewrite(block: list[str], status: str, answer: str) -> list[str]:
    """The entry with its status and answer replaced; the answer's old continuation lines are dropped."""
    out, in_answer, seen = [], False, set()
    for line in block:
        m = FIELD_RE.match(line)
        if m:
            in_answer = False
            key = m.group("key").lower()
            if key == "status":
                line, seen = f"- status: {status}", seen | {"status"}
            elif key == "answer":
                line, in_answer, seen = f"- answer: {answer}", True, seen | {"answer"}
            out.append(line)
        elif in_answer and line.strip():
            continue
        else:
            in_answer = False
            out.append(line)
    while out and not out[-1].strip():
        out.pop()
    if "status" not in seen:
        out.insert(1, f"- status: {status}")
    if "answer" not in seen:
        out.append(f"- answer: {answer}")
    return out + [""]


def settle(project: Path, feature: Path, task_id: str, action: str, text: str = "", commit: bool = True) -> dict:
    """`action` is "done", "answer" or "drop"; returns `{task, action, commit, files, job}`, where `job` is
    `sdd-job.py`'s `after_settle` result once the commit succeeded (005 FR-016)."""
    project, feature = Path(project).resolve(), Path(feature).resolve()
    if action not in ACTIONS:
        raise Refused(f"unknown action {action}")
    text = _one_line(text or "")
    if action == "answer" and not text:
        raise Refused("write the answer first")
    if action == "drop" and not text:
        raise Refused("say why the task is dropped")
    if (project / ".sdd" / "run.json").exists():
        raise Refused("a run is active (.sdd/run.json exists); settle the task after it finishes")
    if commit:
        try:
            _git(project, "rev-parse", "--is-inside-work-tree")
        except Refused:
            raise Refused("the project is not a git repository") from None

    blocked_path, tasks_path = feature / "BLOCKED.md", feature / "tasks.md"
    try:
        blocked_text = blocked_path.read_text(encoding="utf-8")
    except OSError:
        raise Refused("BLOCKED.md not found") from None
    lines = blocked_text.splitlines()
    span = _last_entry(lines, task_id)
    status = None
    if span:
        for line in lines[span[0]:span[1]]:
            m = FIELD_RE.match(line)
            if m and m.group("key").lower() == "status":
                status = m.group("value").strip().lower()
    if span is None or (status or "open") != "open":
        raise Refused(f"{task_id} has no open entry in BLOCKED.md")

    targets = [blocked_path] + ([tasks_path] if action == "done" else [])
    if commit:
        rel = [str(p.relative_to(project)) for p in targets]
        dirty = _git(project, "status", "--porcelain", "--", *rel).rstrip("\n")  # keep the first line's leading space
        if dirty:
            raise Refused("these files have other changes; commit or stash them first: "
                          + ", ".join(l[3:] for l in dirty.splitlines()))

    today = time.strftime("%Y-%m-%d")
    new_status, answer, subject = {
        "done": ("resolved", f"Done by hand ({today})" + (f": {text}" if text else "."),
                 f"chore(unblock): {task_id} done by hand" + (f" - {text[:50]}" if text else "")),
        "answer": ("resolved", f"{text} ({today})", f"chore(unblock): {task_id} answered - {text[:50]}"),
        "drop": ("wontfix", f"Dropped ({today}): {text}", f"chore(unblock): {task_id} dropped - {text[:50]}"),
    }[action]
    new_lines = lines[:span[0]] + _rewrite(lines[span[0]:span[1]], new_status, answer) + lines[span[1]:]
    new_blocked = "\n".join(new_lines).rstrip("\n") + "\n"

    new_tasks = None
    if action == "done":
        try:
            tasks_text = tasks_path.read_text(encoding="utf-8")
        except OSError:
            raise Refused("tasks.md not found") from None
        if re.search(TASK_LINE.format(id=re.escape(task_id)), tasks_text, re.M):
            new_tasks = re.sub(TASK_LINE.format(id=re.escape(task_id)),
                               lambda m: f"- [x] {task_id}{m.group('rest')}", tasks_text, count=1, flags=re.M)

    touched: dict[Path, str] = {}
    try:
        touched[blocked_path] = blocked_text
        blocked_path.write_text(new_blocked, encoding="utf-8")
        if new_tasks is not None:
            touched[tasks_path] = tasks_text
            tasks_path.write_text(new_tasks, encoding="utf-8")
        sha = None
        if commit:
            rel = [str(p.relative_to(project)) for p in touched]
            _git(project, "add", "--", *rel)
            _git(project, "commit", "-m", subject, "--", *rel)
            sha = _git(project, "rev-parse", "--short", "HEAD").strip()
    except Exception:
        for path, before in touched.items():
            path.write_text(before, encoding="utf-8")
        if commit:
            subprocess.run(["git", "reset", "-q", "--", *[str(p.relative_to(project)) for p in touched]],
                           cwd=project, capture_output=True)
        raise
    # 005 FR-016: a settled item may let a waiting Job go on (data-model §10); never raises.
    job = sdd_job.after_settle(project) if commit else {"started": False, "reason": "nothing was committed"}
    return {"task": task_id, "action": action, "commit": sha,
            "files": [str(p.relative_to(project)) for p in touched], "job": job}


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--no-commit"]
    if len(args) not in (4, 5) or args[0] not in ACTIONS:
        print(__doc__)
        return 2
    try:
        result = settle(Path(args[1]), Path(args[2]), args[3], args[0], args[4] if len(args) == 5 else "",
                        commit="--no-commit" not in argv)
    except Refused as e:
        print(json.dumps({"contract": sdd_job.sdd_tasks.CONTRACT, "error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps({"contract": sdd_job.sdd_tasks.CONTRACT, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
