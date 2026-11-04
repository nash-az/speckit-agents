#!/usr/bin/env python3
"""Apply a person's answer to an open `[decision]` task (spec 003 FR-033).

Used by the dashboard's "Save decision" button and usable from the shell:

    bin/sdd-decide.py <project dir> <feature dir> <task id> "<answer>" [--no-commit]

It records the answer and commits just the files it touched:
  1. appends one line to `<feature>/decisions.md` (created when missing);
  2. when the task names a heading in `research.md` ("under `## Heading` in `research.md`"), adds the
     answer under that heading;
  3. ticks the task in `<feature>/tasks.md`;
  4. commits those files as `chore(decisions): <task id> <answer>`.

It refuses, and changes nothing, when a run is active (`.sdd/run.json`), when the working tree has any
other change, when the project is not a git repository, or when the task is not an open decision.
If the commit fails, the files are put back. After the commit succeeds, `sdd-job.py`'s `after_settle` may
continue a waiting Job (spec 005 FR-016); its result is the `"job"` key of `apply`'s result (data-model §10).
The commit lands on the checked-out branch, which is the open Job's `auto/<id>` (FR-008). Stdlib only; no model.
From the shell it prints `apply`'s result as one JSON object with `"contract"` (008 data-model §1), or
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

_spec = importlib.util.spec_from_file_location("sdd_job", Path(__file__).resolve().with_name("sdd-job.py"))
sdd_job = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sdd_job)

TASK_LINE = r"^- \[ \] {id}\b(?P<rest>.*)$"
RESEARCH_HEADING = re.compile(r"under\s+`(##+ [^`]+)`\s+in\s+`research\.md`")


class Refused(Exception):
    """The decision cannot be applied now; nothing was changed."""


def _git(project: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=project, capture_output=True, text=True)
    if result.returncode != 0:
        raise Refused((result.stderr or result.stdout).strip() or f"git {args[0]} failed")
    return result.stdout


def _first_sentence(text: str, limit: int = 160) -> str:
    text = re.sub(r"`", "", text)
    cut = re.split(r"(?<=[.?!])\s", text, maxsplit=1)[0]
    return cut if len(cut) <= limit else cut[: limit - 1].rstrip() + "…"


def _add_under_heading(text: str, heading: str, line: str) -> str:
    """Put `line` at the end of the section that starts with `heading`, or add the section."""
    lines = text.splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    start = next((i for i, l in enumerate(lines) if l.strip() == heading), None)
    if start is None:
        body = text.rstrip("\n")
        return (body + "\n\n" if body else "") + f"{heading}\n\n{line}\n"
    end = len(lines)
    for i in range(start + 1, len(lines)):
        m = re.match(r"^(#+) ", lines[i])
        if m and len(m.group(1)) <= level:
            end = i
            break
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    lines[end:end] = ["", line]
    return "\n".join(lines) + "\n"


def apply(project: Path, feature: Path, task_id: str, answer: str, commit: bool = True,
          remove_after: Path | None = None) -> dict:
    """Apply the answer; `remove_after` (the dashboard's saved answer file) is deleted once it is applied."""
    project, feature = Path(project).resolve(), Path(feature).resolve()
    answer = answer.strip()
    if not answer:
        raise Refused("the answer is empty")
    if (project / ".sdd" / "run.json").exists():
        raise Refused("a run is active (.sdd/run.json exists); apply the decision after it finishes")
    if commit:
        try:
            _git(project, "rev-parse", "--is-inside-work-tree")
        except Refused:
            raise Refused("the project is not a git repository") from None
        dirty = _git(project, "status", "--porcelain").rstrip("\n")  # keep the first line's leading space
        if dirty:
            raise Refused("the working tree has other changes; commit or stash them first: "
                          + ", ".join(l[3:] for l in dirty.splitlines()[:5]))

    tasks_path = feature / "tasks.md"
    try:
        tasks_text = tasks_path.read_text(encoding="utf-8")
    except OSError:
        raise Refused("tasks.md not found") from None
    m = re.search(TASK_LINE.format(id=re.escape(task_id)), tasks_text, re.M)
    if not m or "[decision]" not in m.group("rest"):
        raise Refused(f"{task_id} is not an open decision task")
    task_text = m.group("rest")

    today = time.strftime("%Y-%m-%d")
    touched: dict[Path, str | None] = {}  # path -> its text before (None: did not exist)

    def write(path: Path, text: str):
        touched[path] = path.read_text(encoding="utf-8") if path.exists() else None
        path.write_text(text, encoding="utf-8")

    try:
        decisions = feature / "decisions.md"
        old = decisions.read_text(encoding="utf-8") if decisions.exists() else "# Decisions\n"
        line = (f"- {task_id} ({today}): {_first_sentence(task_text.replace('[decision]', '').strip())} "
                f"→ {answer.replace(chr(10), ' ')} — to change it, edit this line and the affected files.")
        write(decisions, old.rstrip("\n") + "\n" + line + "\n")

        h = RESEARCH_HEADING.search(task_text)
        if h:
            research = feature / "research.md"
            old = research.read_text(encoding="utf-8") if research.exists() else ""
            write(research, _add_under_heading(old, h.group(1), f"Decision ({today}): {answer}"))

        write(tasks_path, re.sub(TASK_LINE.format(id=re.escape(task_id)),
                                 lambda mm: f"- [x] {task_id}{mm.group('rest')}", tasks_text,
                                 count=1, flags=re.M))

        sha = None
        if commit:
            rel = [str(p.relative_to(project)) for p in touched]
            _git(project, "add", "--", *rel)
            subject = f"chore(decisions): {task_id} {answer.splitlines()[0][:60]}"
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
    if remove_after is not None:
        Path(remove_after).unlink(missing_ok=True)
    # FR-016: only a committed settle may continue a Job (after_settle never raises).
    job = sdd_job.after_settle(project) if sha else {"started": False, "reason": "nothing was committed"}
    return {"task": task_id, "commit": sha, "files": [str(p.relative_to(project)) for p in touched], "job": job}


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--no-commit"]
    if len(args) != 4:
        print(__doc__)
        return 2
    try:
        result = apply(Path(args[0]), Path(args[1]), args[2], args[3], commit="--no-commit" not in argv)
    except Refused as e:
        print(json.dumps({"contract": sdd_job.sdd_tasks.CONTRACT, "error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps({"contract": sdd_job.sdd_tasks.CONTRACT, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
