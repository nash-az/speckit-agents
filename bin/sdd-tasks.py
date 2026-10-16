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
    return {
        "id": match["id"],
        "done": match["mark"] in "xX",
        "parallel": parallel,
        "story": story,
        "tags": tags,
        "files": files,
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


def parse_blocked(path: Path | None) -> dict[str, str]:
    """Return {task id: status} for every entry in BLOCKED.md."""
    if path is None or not path.exists():
        return {}
    entries, current = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        if (head := BLOCKED_HEAD_RE.match(line)) is not None:
            current = head["id"]
            entries[current] = "open"
        elif current and (status := STATUS_RE.match(line)) is not None:
            entries[current] = status["status"].lower()
    return entries


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


def hold_reason(task: dict, allow: dict) -> tuple[str, str] | None:
    for tag in task["tags"]:
        if tag not in HOLD_TAGS:
            continue
        allow_key = ALLOW_KEYS.get(tag)
        if allow_key and allow.get(allow_key):
            continue
        return tag, HOLD_TAGS[tag]
    return None


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
    for phase in parsed["phases"]:
        if phases and phase["id"] not in phases:
            continue
        runnable = []
        for task in phase["tasks"]:
            if task["done"]:
                continue
            if blocked.get(task["id"]) == "open":
                held.append({"id": task["id"], "tag": "blocked", "reason": "open entry in BLOCKED.md"})
                continue
            if blocked.get(task["id"]) == "wontfix":
                held.append({"id": task["id"], "tag": "wontfix", "reason": "dropped in BLOCKED.md (wontfix)"})
                continue
            if (reason := hold_reason(task, allow)) is not None:
                held.append({"id": task["id"], "tag": reason[0], "reason": reason[1]})
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
    number, the verifier's verdict on it if one came back, or `interrupted: "implement"` when an
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
            tasks[ids[0]] = {"attempt": attempt, "impl": result}
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
            tasks[task_id] = {"attempt": attempt, "interrupted": "implement"}
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
