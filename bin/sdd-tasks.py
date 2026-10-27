#!/usr/bin/env python3
"""Deterministic helpers over a Spec Kit tasks.md for unattended runs.

  sdd-tasks.py parse  <tasks.md> [--blocked BLOCKED.md]
  sdd-tasks.py plan   <tasks.md> [--blocked BLOCKED.md] [--config .sdd/config.toml]
                      [--phases 7,8] [--max-tasks N]
  sdd-tasks.py tick   <tasks.md> T001 [T002 ...]
  sdd-tasks.py block  <BLOCKED.md> --id T057 --tag manual --reason "..." --question "..."
  sdd-tasks.py config [--config .sdd/config.toml]
  sdd-tasks.py checkpoint <journal.jsonl>

Every command prints JSON on stdout. Only the standard library is used.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

TASK_RE = re.compile(r"^- \[(?P<mark>[ xX])\] (?P<id>T\d+[a-z]?)\b\s*(?P<rest>.*)$")
PHASE_RE = re.compile(r"^## Phase (?P<num>[0-9]+[a-z]?)\s*[:\-–—]\s*(?P<title>.*)$")
H2_RE = re.compile(r"^## ")
LEAD_TAG_RE = re.compile(r"^\[(?P<tag>[^\]]+)\]\s*")
CODE_RE = re.compile(r"`([^`]+)`")
BLOCKED_HEAD_RE = re.compile(r"^## (?P<id>T\d+[a-z]?)\b")
STATUS_RE = re.compile(r"^- status:\s*(?P<status>\w+)", re.IGNORECASE)
BLOCKED_FIELD_RE = re.compile(r"^- (?P<key>\w+):\s*(?P<value>.*)$")
BLOCKED_FIELDS = {"status", "tag", "reason", "question", "answer"}
TASK_REF_RE = re.compile(r"\b(T\d+[a-z]?)\b")

# Terminal BLOCKED.md statuses that hold a task forever: it is never re-run.
TERMINAL_STATUSES = {"open", "resolved", "wontfix"}

# Dependency-indicating words that make a T-nn reference a real dependency.
DEP_WORDS = {
    "after", "per", "blocked", "by", "on", "needs", "need",
    "depends", "waiting", "waits", "supersedes", "superseded", "follows",
}
# Words that mark a task as superseded by a later task (converge carries its remaining work).
SUPERSEDED_BY_WORDS = {"superseded-by", "superseded_by", "replaced-by", "replaced_by"}

# Agent tags that keep a task away from the run unless the config allows it.
HOLD_TAGS = {
    "manual": "needs a person to do it (recording, hardware, an account)",
    "decision": "needs a decision that is not in the spec",
    "dep": "adds a new dependency",
    "download": "downloads a large model or dataset",
}
ALLOW_KEYS = {"dep": "dependencies", "download": "downloads"}
KNOWN_TAGS = set(HOLD_TAGS) | {"eval", "ui"}

DEFAULT_CONFIG = {
    "run": {
        "feature": "",
        "max_retries": 2,
        "isolation": "branch",
        "branch_prefix": "auto/",
        "protected_branches": ["main", "master"],
        "protected_paths": [
            ".specify/memory/constitution.md",
            "specs/*/spec.md",
            ".claude/settings.json",
            ".claude/settings.local.json",
            ".sdd/config.toml",
            ".sdd/run.json",
        ],
    },
    "checks": {"commands": []},
    "allow": {"dependencies": False, "downloads": False},
}


def looks_like_path(span: str) -> bool:
    if " " in span or span.startswith(("-", "$")):
        return False
    return "/" in span or bool(re.search(r"\.[A-Za-z0-9]{1,5}$", span))


def parse_task(match: re.Match, line_no: int) -> dict:
    rest = match["rest"]
    parallel, story, tags = False, None, []
    while (lead := LEAD_TAG_RE.match(rest)) is not None:
        for raw in lead["tag"].split(","):
            tag = raw.strip()
            if tag == "P":
                parallel = True
            elif re.fullmatch(r"US\d+[a-z]?", tag):
                story = tag
            elif tag.lower() in KNOWN_TAGS:
                tags.append(tag.lower())
            else:
                tags.append(tag)
        rest = rest[lead.end():]
    files = []
    for span in CODE_RE.findall(rest):
        # "`path/a.py` → `path/b.py`" style spans and plain paths both count.
        for part in re.split(r"\s*(?:,|→|->)\s*", span):
            if looks_like_path(part) and part not in files:
                files.append(part)
    # Cross-task references: after Tnnn / per Tnnn / blocked by Tnnn / needs Tnnn / …
    deps: list[str] = []
    superseded_by: list[str] = []
    search_text = match.group(0) + " " + rest
    for m in TASK_REF_RE.finditer(search_text):
        ref = m.group(1)
        if ref == match["id"]:
            continue
        before = search_text[max(0, m.start() - 20):m.start()]
        preceding = before.split()[-1].lower().strip(".,:;[]()") if before.split() else ""
        if preceding in SUPERSEDED_BY_WORDS:
            if ref not in superseded_by:
                superseded_by.append(ref)
        elif preceding in DEP_WORDS:
            if ref not in deps:
                deps.append(ref)
    return {
        "id": match["id"],
        "done": match["mark"] in "xX",
        "parallel": parallel,
        "story": story,
        "tags": tags,
        "files": files,
        "deps": deps,
        "superseded_by": superseded_by,
        "text": rest.strip(),
        "line": line_no,
    }


def parse_tasks(path: Path) -> dict:
    phases: list[dict] = []
    current = None
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if (head := PHASE_RE.match(line)) is not None:
            current = {"id": head["num"], "title": head["title"].strip(), "tasks": []}
            phases.append(current)
            continue
        if H2_RE.match(line):
            current = None  # Dependencies, Notes and other non-phase sections
            continue
        if current is not None and (task := TASK_RE.match(line)) is not None:
            current["tasks"].append(parse_task(task, line_no))
    return {"tasks_file": str(path), "phases": phases}


def parse_blocked_entries(path: Path | None) -> dict[str, dict]:
    """{task id: {"status", "tag", "reason", "question", "answer"}} — the LAST entry per id wins.

    An entry starts at a `## T…` line and ends at the next `## ` line. `- <key>: <value>` sets a
    field (status lowercased, default "open"). The answer also takes the lines below `- answer:`
    up to the next `## …` or `- <word>:` line, because `/sdd-unblock` writes answers there.
    """
    if path is None or not path.exists():
        return {}
    entries: dict[str, dict] = {}
    current: dict | None = None
    answer_lines: list[str] | None = None

    def finish_answer() -> None:
        nonlocal answer_lines
        if current is not None and answer_lines is not None:
            current["answer"] = "\n".join(answer_lines).strip()
        answer_lines = None

    for line in path.read_text(encoding="utf-8").splitlines():
        if H2_RE.match(line):
            finish_answer()
            current = None
            if (head := BLOCKED_HEAD_RE.match(line)) is not None:
                current = {"status": "open", "tag": "", "reason": "", "question": "", "answer": ""}
                entries[head["id"]] = current  # a later entry for the same id replaces this one
            continue
        if current is None:
            continue
        if (field := BLOCKED_FIELD_RE.match(line)) is not None:
            finish_answer()
            key, value = field["key"].lower(), field["value"].strip()
            if key == "status":
                if (status := STATUS_RE.match(line)) is not None:
                    current["status"] = status["status"].lower()
            elif key == "answer":
                answer_lines = [value]
            elif key in BLOCKED_FIELDS:
                current[key] = value
        elif answer_lines is not None:
            answer_lines.append(line)
    finish_answer()
    return entries


def parse_blocked(path: Path | None) -> dict[str, str]:
    """Return {task id: status} for every entry in BLOCKED.md (the last entry per id wins)."""
    return {task_id: e["status"] for task_id, e in parse_blocked_entries(path).items()}


def load_config(path: Path | None) -> dict:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if path is not None and path.exists():
        with path.open("rb") as handle:
            loaded = tomllib.load(handle)
        for section, values in loaded.items():
            if isinstance(values, dict):
                config.setdefault(section, {}).update(values)
            else:
                config[section] = values
    return config


def hold_reason(task: dict, blocked: dict[str, str], allow: dict) -> tuple[str, str] | None:
    """Why a task is held, or None if runnable.

    A task is held when it is tagged for a person (manual, decision, dep unless
    allowed, download unless allowed), or when its BLOCKED.md entry has any
    terminal status (open, resolved, wontfix): it is never re-entered into the
    plan. A task whose dependency is open and held is held too (propagated in
    plan()).
    """
    for tag in task["tags"]:
        if tag not in HOLD_TAGS:
            continue
        allow_key = ALLOW_KEYS.get(tag)
        if allow_key and allow.get(allow_key):
            continue
        return tag, HOLD_TAGS[tag]
    status = blocked.get(task["id"])
    if status in TERMINAL_STATUSES:
        reason = {
            "open": "open entry in BLOCKED.md",
            "resolved": "resolved in BLOCKED.md",
            "wontfix": "dropped in BLOCKED.md (wontfix)",
        }[status]
        tag = "blocked" if status == "open" else status
        return tag, reason
    return None


def direct_holds(parsed: dict, blocked: dict[str, str], allow: dict) -> dict[str, tuple[str, str]]:
    """{task id: (tag, reason)} for every directly held task (by tag or BLOCKED.md).

    Also holds tasks marked as superseded by a later task (converge carried their
    remaining work), so plan() does not list them as runnable again.
    """
    holds: dict[str, tuple[str, str]] = {}
    superseded: dict[str, list[str]] = {}
    for phase in parsed["phases"]:
        for task in phase["tasks"]:
            if task["done"]:
                continue
            if (reason := hold_reason(task, blocked, allow)) is not None:
                holds[task["id"]] = reason
            for ref in task.get("superseded_by", []):
                superseded.setdefault(ref, []).append(task["id"])
    for ref, superseded_by in superseded.items():
        holds[ref] = (
            "superseded",
            f"remaining work carried by {', '.join(superseded_by)}",
        )
    return holds


def propagate_holds(parsed: dict, holds: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """Hold a task when any of its dependencies is held. Iterate to a fixpoint."""
    by_id: dict[str, dict] = {}
    for phase in parsed["phases"]:
        for task in phase["tasks"]:
            by_id[task["id"]] = task
    changed = True
    while changed:
        changed = False
        for task in by_id.values():
            if task["id"] in holds or task["done"]:
                continue
            for dep in task.get("deps", []):
                if dep in holds:
                    holds[task["id"]] = (
                        "held",
                        f"depends on {dep}, which is held ({holds[dep][0]})",
                    )
                    changed = True
                    break
    return holds


def group_phase(tasks: list[dict]) -> list[list[str]]:
    """Consecutive [P] tasks with disjoint, known files run together."""
    groups: list[list[str]] = []
    batch: list[dict] = []
    seen: set[str] = set()

    def flush():
        nonlocal batch, seen
        if batch:
            groups.append([t["id"] for t in batch])
        batch, seen = [], set()

    for task in tasks:
        files = set(task["files"])
        if not task["parallel"] or not files:
            flush()
            groups.append([task["id"]])
            continue
        if files & seen:
            flush()
        batch.append(task)
        seen |= files
    flush()
    return groups


def plan(parsed: dict, blocked: dict[str, str], config: dict,
         phases: list[str] | None, max_tasks: int | None) -> dict:
    allow = config.get("allow", {})
    out_phases, held, budget = [], [], max_tasks
    # Direct holds (tag or BLOCKED.md terminal status or superseded-by converge),
    # then propagate through dependencies.
    direct = direct_holds(parsed, blocked, allow)
    all_holds = propagate_holds(parsed, direct)
    for phase in parsed["phases"]:
        if phases and phase["id"] not in phases:
            continue
        runnable = []
        for task in phase["tasks"]:
            if task["done"]:
                continue
            if task["id"] in all_holds:
                tag, reason = all_holds[task["id"]]
                held.append({"id": task["id"], "tag": tag, "reason": reason})
                continue
            if budget is not None:
                if budget <= 0:
                    break
                budget -= 1
            runnable.append(task)
        if runnable:
            out_phases.append({
                "id": phase["id"],
                "title": phase["title"],
                "groups": group_phase(runnable),
                "tasks": {t["id"]: t for t in runnable},
            })
    open_count = sum(1 for p in parsed["phases"] for t in p["tasks"] if not t["done"])
    return {
        "tasks_file": parsed["tasks_file"],
        "open": open_count,
        "runnable": sum(len(p["tasks"]) for p in out_phases),
        "held": held,
        "phases": out_phases,
    }


def tick(path: Path, ids: list[str]) -> dict:
    wanted, ticked = set(ids), []
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        match = TASK_RE.match(line.rstrip("\n"))
        if match and match["id"] in wanted and match["mark"] == " ":
            lines[i] = line.replace("- [ ]", "- [x]", 1)
            ticked.append(match["id"])
    path.write_text("".join(lines), encoding="utf-8")
    return {"ticked": ticked, "missing": sorted(wanted - set(ticked))}


def block(path: Path, task_id: str, tag: str, reason: str, question: str) -> dict:
    existing = parse_blocked(path)
    if existing.get(task_id) == "open":
        return {"appended": False, "id": task_id, "note": "already open"}
    header = "" if path.exists() else (
        "# Blocked tasks\n\n"
        "Tasks an unattended run skipped. Answer the question, set `status: resolved`,\n"
        "then run `/sdd-unblock` or the next `/sdd-run` picks the task up again.\n"
    )
    entry = (
        f"\n## {task_id}\n"
        f"- status: open\n"
        f"- tag: {tag}\n"
        f"- reason: {reason.strip()}\n"
        f"- question: {question.strip()}\n"
        f"- answer:\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(header + entry)
    return {"appended": True, "id": task_id}


LABEL_RE = re.compile(r"^(?P<kind>impl|eval|verify|commit|repair):(?P<ids>[^#\s]+)(?:#(?P<n>\d+))?$")


def checkpoint(journal: Path) -> dict:
    """What an interrupted run already finished, read from its workflow journal.

    Per task that was not committed: the last implementer (or evaluator) result with its attempt
    number, the files earlier attempts changed (`files`, so a resumed commit still holds them), the verifier's verdict on it if one came back, or `interrupted: "implement"` when an
    attempt was cut off with nothing returned (its edits may still be in the tree). The workflow
    skips the finished steps when it gets this as `args.resume`.
    """
    tasks: dict[str, dict] = {}
    labels: dict[str, str] = {}
    open_keys: dict[str, str] = {}
    if not journal.exists():
        return {"journal": str(journal), "found": False, "tasks": {}, "interrupted_agents": []}
    for line in journal.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # a line cut off when the process was killed
        key, kind = event.get("key"), event.get("type")
        if kind == "started" and key:
            labels[key] = event.get("label", "")
            open_keys[key] = labels[key]
            continue
        if kind not in ("result", "failed") or not key:
            continue
        open_keys.pop(key, None)
        match = LABEL_RE.match(labels.get(key, ""))
        result = event.get("result")
        if not match or kind == "failed" or not isinstance(result, dict):
            continue  # a failed step leaves the last good state in place
        ids, attempt = match["ids"].split(","), int(match["n"] or 1) - 1
        if match["kind"] in ("impl", "eval"):
            earlier = tasks.get(ids[0], {})
            files = sorted(set(earlier.get("files", [])) | set(earlier.get("impl", {}).get("files_changed", [])))
            tasks[ids[0]] = {"attempt": attempt, "impl": result, **({"files": files} if files else {})}
        elif match["kind"] == "verify" and ids[0] in tasks:
            tasks[ids[0]]["verdict"] = result
        elif match["kind"] == "commit" and result.get("checks_ran") and result.get("checks_passed"):
            for task_id in ids:
                tasks.pop(task_id, None)
    interrupted = []
    for label in open_keys.values():
        interrupted.append(label)
        match = LABEL_RE.match(label)
        if match and match["kind"] in ("impl", "eval"):
            task_id, attempt = match["ids"], int(match["n"] or 1) - 1
            earlier = tasks.get(task_id, {})
            files = sorted(set(earlier.get("files", [])) | set(earlier.get("impl", {}).get("files_changed", [])))
            tasks[task_id] = {"attempt": attempt, "interrupted": "implement", **({"files": files} if files else {})}
    return {"journal": str(journal), "found": True, "tasks": tasks, "interrupted_agents": interrupted}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_parse = sub.add_parser("parse")
    p_parse.add_argument("tasks", type=Path)
    p_parse.add_argument("--blocked", type=Path)

    p_plan = sub.add_parser("plan")
    p_plan.add_argument("tasks", type=Path)
    p_plan.add_argument("--blocked", type=Path)
    p_plan.add_argument("--config", type=Path, default=Path(".sdd/config.toml"))
    p_plan.add_argument("--phases", help="comma-separated phase ids, e.g. 7,8,9a")
    p_plan.add_argument("--max-tasks", type=int)

    p_tick = sub.add_parser("tick")
    p_tick.add_argument("tasks", type=Path)
    p_tick.add_argument("ids", nargs="+")

    p_block = sub.add_parser("block")
    p_block.add_argument("blocked", type=Path)
    p_block.add_argument("--id", required=True)
    p_block.add_argument("--tag", default="failed")
    p_block.add_argument("--reason", required=True)
    p_block.add_argument("--question", default="")

    p_config = sub.add_parser("config")
    p_config.add_argument("--config", type=Path, default=Path(".sdd/config.toml"))

    p_checkpoint = sub.add_parser("checkpoint")
    p_checkpoint.add_argument("journal", type=Path)

    args = parser.parse_args(argv)
    if args.cmd == "parse":
        result = parse_tasks(args.tasks)
        result["blocked"] = parse_blocked(args.blocked)
    elif args.cmd == "plan":
        phases = [p.strip() for p in args.phases.split(",")] if args.phases else None
        result = plan(parse_tasks(args.tasks), parse_blocked(args.blocked),
                      load_config(args.config), phases, args.max_tasks)
    elif args.cmd == "tick":
        result = tick(args.tasks, args.ids)
    elif args.cmd == "block":
        result = block(args.blocked, args.id, args.tag, args.reason, args.question)
    elif args.cmd == "checkpoint":
        result = checkpoint(args.journal)
    else:
        result = load_config(args.config)
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
