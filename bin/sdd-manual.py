#!/usr/bin/env python3
"""Settle an open `[manual]` task: the person did it, or does not want it (spec 003 FR-036).

Used by the dashboard's "I did it" and "Skip" buttons and usable from the shell:

    bin/sdd-manual.py done <project dir> <feature dir> <task id> ["<note>"]
    bin/sdd-manual.py skip <project dir> <feature dir> <task id> "<reason>"

`done` ticks the task in `<feature>/tasks.md`; a note is added as a line to `<feature>/manual-notes.md`
(created when missing). When the task's section in `<feature>/manual.md` (the heading that starts with its id)
has a line `Record: <file>.md ## <heading>`, the note is required and is also written, verbatim, under that
heading of that file in the feature folder (the heading is added at the end when missing), in the same commit. `skip` appends a `status: wontfix` entry with the reason to
`<feature>/BLOCKED.md`, so the task stays open in `tasks.md` but no run waits on it. Either one commits
just the files it touched as `chore(manual): <task id> ...`.

It refuses, and changes nothing, when a run is active (`.sdd/run.json`), when a file it would touch
already has uncommitted changes, when the project is not a git repository, when the task is not an open
`[manual]` task, or (skip) when the reason is empty. If the commit fails the files are put back.

After the commit it calls `sdd_job.after_settle(project)` (spec 005 FR-016, data-model §10): a `waiting`
Job whose scope now has a runnable task continues by itself. Its result is the `"job"` key of the output.
Stdlib only; no model.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location("sdd_job", Path(__file__).with_name("sdd-job.py"))
sdd_job = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_job)

TASK_LINE = r"^- \[ \] {id}\b(?P<rest>.*)$"
RECORD_LINE = re.compile(r"^Record:\s*`?(?P<file>[\w.-]+\.md)`?\s+`?(?P<heading>#{2,4} [^`]+?)`?\s*$", re.M)
NOT_RECORDABLE = {"tasks.md", "BLOCKED.md", "spec.md", "manual.md", "manual-notes.md"}


class Refused(Exception):
    """The task cannot be settled now; nothing was changed."""


def _git(project: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=project, capture_output=True, text=True)
    if result.returncode != 0:
        raise Refused((result.stderr or result.stdout).strip() or f"git {args[0]} failed")
    return result.stdout


def _one_line(text: str) -> str:
    return " ".join(text.split())


def manual_section(text: str, task_id: str) -> str | None:
    """The section of manual.md whose heading starts with the task id, down to the next heading of the same
    or a higher level (the dashboard's `manualSection`), or None."""
    lines, start, level = text.splitlines(), None, 0
    for i, line in enumerate(lines):
        h = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not h:
            continue
        if start is not None and len(h.group(1)) <= level:
            return "\n".join(lines[start:i])
        if start is None and (h.group(2) + " ").startswith(task_id + " "):
            start, level = i, len(h.group(1))
    return "\n".join(lines[start:]) if start is not None else None


def record_target(feature: Path, task_id: str) -> tuple[Path, str] | None:
    """`(file, heading)` from the task's `Record: <file>.md ## <heading>` line in manual.md, or None.
    The file is a plain name in the feature folder, never tasks.md, BLOCKED.md, spec.md or the manual files."""
    try:
        section = manual_section((Path(feature) / "manual.md").read_text(encoding="utf-8"), task_id)
    except OSError:
        return None
    m = RECORD_LINE.search(section or "")
    if not m or m.group("file") in NOT_RECORDABLE:
        return None
    return Path(feature) / m.group("file"), m.group("heading").strip()


def record_note(text: str, heading: str, task_id: str, today: str, note: str) -> str:
    """text with the note under `heading`: at the end of that section, or a new section at the end."""
    body = note.strip("\n")
    entry = (f"From {task_id} ({today}), written in the dashboard:\n\n"
             + (f"```\n{body}\n```\n" if "\n" in body else f"> {body}\n"))
    lines = text.splitlines()
    level = len(heading.split(" ", 1)[0])
    at = next((i for i, l in enumerate(lines) if l.strip() == heading), None)
    if at is None:
        return text.rstrip("\n") + f"\n\n{heading}\n\n{entry}"
    end = next((j for j in range(at + 1, len(lines))
                if re.match(r"^(#{1,6})\s", lines[j]) and len(re.match(r"^(#+)", lines[j]).group(1)) <= level), len(lines))
    head = "\n".join(lines[:end]).rstrip("\n")
    tail = "\n".join(lines[end:])
    return head + "\n\n" + entry + ("\n" + tail + ("\n" if text.endswith("\n") else "") if tail else "")


def settle(project: Path, feature: Path, task_id: str, action: str, note: str = "", commit: bool = True) -> dict:
    """`action` is "done" or "skip"; returns `{task, action, commit, files, job}`, where `job` is
    `sdd_job.after_settle`'s `{started, reason}` (not called when nothing was committed)."""
    project, feature = Path(project).resolve(), Path(feature).resolve()
    if action not in ("done", "skip"):
        raise Refused(f"unknown action {action}")
    note = (note or "").strip()
    if action == "skip" and not note:
        raise Refused("say why the task is skipped")
    if (project / ".sdd" / "run.json").exists():
        raise Refused("a run is active (.sdd/run.json exists); settle the task after it finishes")
    if commit:
        try:
            _git(project, "rev-parse", "--is-inside-work-tree")
        except Refused:
            raise Refused("the project is not a git repository") from None

    tasks_path = feature / "tasks.md"
    try:
        tasks_text = tasks_path.read_text(encoding="utf-8")
    except OSError:
        raise Refused("tasks.md not found") from None
    m = re.search(TASK_LINE.format(id=re.escape(task_id)), tasks_text, re.M)
    if not m or not re.search(r"\[[^\]]*\bmanual\b[^\]]*\]", m.group("rest"), re.I):
        raise Refused(f"{task_id} is not an open manual task")

    record = record_target(feature, task_id) if action == "done" else None
    if record and not note:
        raise Refused(f"write what you found: it is saved under {record[1]} in {record[0].name}")
    targets = {"done": [tasks_path, feature / "manual-notes.md"] if note else [tasks_path],
               "skip": [feature / "BLOCKED.md"]}[action]
    if record:
        targets.append(record[0])
    if commit:
        rel = [str(p.relative_to(project)) for p in targets]
        dirty = _git(project, "status", "--porcelain", "--", *rel).rstrip("\n")  # keep the first line's leading space
        if dirty:
            raise Refused("these files have other changes; commit or stash them first: "
                          + ", ".join(l[3:] for l in dirty.splitlines()))

    today = time.strftime("%Y-%m-%d")
    touched: dict[Path, str | None] = {}

    def write(path: Path, text: str):
        touched[path] = path.read_text(encoding="utf-8") if path.exists() else None
        path.write_text(text, encoding="utf-8")

    try:
        if action == "done":
            write(tasks_path, re.sub(TASK_LINE.format(id=re.escape(task_id)),
                                     lambda mm: f"- [x] {task_id}{mm.group('rest')}", tasks_text,
                                     count=1, flags=re.M))
            if note:
                notes = feature / "manual-notes.md"
                old = notes.read_text(encoding="utf-8") if notes.exists() else "# Notes from manual tasks\n"
                write(notes, old.rstrip("\n") + f"\n- {task_id} ({today}): {_one_line(note)}\n")
            if record:
                path, heading = record
                old = path.read_text(encoding="utf-8") if path.exists() else ""
                write(path, record_note(old, heading, task_id, today, note))
            subject = f"chore(manual): {task_id} done" + (f" - {_one_line(note)[:50]}" if note else "")
        else:
            blocked = feature / "BLOCKED.md"
            old = blocked.read_text(encoding="utf-8") if blocked.exists() else (
                "# Blocked tasks\n\nTasks an unattended run could not finish. Answer the question under "
                "`- answer:` and set\n`status: resolved`: the next `/sdd-run` runs the task again with your "
                "answer.\nSet `status: wontfix` to drop the task, or `status: moved` if it became work "
                "outside this project.\n")
            write(blocked, old.rstrip("\n") + f"\n\n## {task_id}\n- status: wontfix\n- tag: manual\n"
                  f"- reason: skipped on {today}: {_one_line(note)}\n- question:\n- answer:\n")
            subject = f"chore(manual): {task_id} skipped - {_one_line(note)[:50]}"

        sha = None
        if commit:
            rel = [str(p.relative_to(project)) for p in touched]
            _git(project, "add", "--", *rel)
            _git(project, "commit", "-m", subject, "--", *rel)
            sha = _git(project, "rev-parse", "--short", "HEAD").strip()
    except Exception:
        for path, before in touched.items():
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(before, encoding="utf-8")
        if commit:
            subprocess.run(["git", "reset", "-q", "--", *[str(p.relative_to(project)) for p in touched]],
                           cwd=project, capture_output=True)
        raise
    job = sdd_job.after_settle(project) if commit else {"started": False, "reason": "nothing was committed"}
    return {"task": task_id, "action": action, "commit": sha,
            "files": [str(p.relative_to(project)) for p in touched], "job": job}


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--no-commit"]
    if len(args) not in (4, 5) or args[0] not in ("done", "skip"):
        print(__doc__)
        return 2
    try:
        result = settle(Path(args[1]), Path(args[2]), args[3], args[0], args[4] if len(args) == 5 else "",
                        commit="--no-commit" not in argv)
    except Refused as e:
        print(f"refused: {e}")
        return 1
    print(f"{result['task']} {result['action']}" + (f" in {result['commit']}" if result["commit"] else ""))
    job = result["job"]
    if job["started"]:
        print("the job continues: its next run started")
    elif job["reason"] not in ("no open job", "nothing was committed"):
        print(f"the job did not continue: {job['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
