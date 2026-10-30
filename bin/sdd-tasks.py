#!/usr/bin/env python3
"""Deterministic helpers over a Spec Kit tasks.md for unattended runs.

  sdd-tasks.py parse  <tasks.md> [--blocked BLOCKED.md]
  sdd-tasks.py plan   <tasks.md> [--blocked BLOCKED.md] [--config .sdd/config.toml]
                      [--phases 7,8] [--max-tasks N]
  sdd-tasks.py tick   <tasks.md> T001 [T002 ...]
  sdd-tasks.py block  <BLOCKED.md> --id T057 --tag manual --reason "..." --question "..."
  sdd-tasks.py config [--config .sdd/config.toml]
  sdd-tasks.py checkpoint <journal.jsonl>
  sdd-tasks.py snapshot <tasks.md>
  sdd-tasks.py changed  <tasks.md> --run-json .sdd/run.json
  sdd-tasks.py events   <project dir> <run id>

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

# BLOCKED.md statuses that hold a task: open (waiting for an answer), wontfix
# (dropped) and moved (became work outside this project). A resolved entry does
# not hold: the task is runnable again, with its answer.
HOLDING_STATUSES = {"open", "wontfix", "moved"}

# Dependency-indicating words that make a T-nn reference a real dependency.
DEP_WORDS = {
    "after", "per", "blocked", "by", "on", "needs", "need",
    "depends", "waiting", "waits", "supersedes", "superseded", "follows",
}
# Words that mark a task as superseded by a later task (converge carries its remaining work).
SUPERSEDED_BY_WORDS = {"superseded-by", "superseded_by", "replaced-by", "replaced_by"}
# A phase whose title starts with "Convergence" carries the open work of the phases before it.
CONVERGENCE_RE = re.compile(r"^convergence", re.IGNORECASE)

# Agent tags that keep a task away from the run unless the config allows it.
HOLD_TAGS = {
    "manual": "needs a person to do it (recording, hardware, an account)",
    "decision": "needs a decision that is not in the spec",
    "dep": "adds a new dependency",
    "download": "downloads a large model or dataset",
}
ALLOW_KEYS = {"dep": "dependencies", "download": "downloads"}
# `[dep:ok]` / `[download:ok]`: a person approved that one task's dependency / download.
KNOWN_TAGS = set(HOLD_TAGS) | {"eval", "ui", "dep:ok", "download:ok"}

DEFAULT_CONFIG = {
    "run": {
        "feature": "",
        "max_retries": 2,
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


AGENT_ROLES = {"implementer", "verifier", "evaluator", "bookkeeper"}
SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def config_errors(config: dict) -> list[str]:
    """Problems in a loaded config, one string each naming the entry (data-model §3.8).

    Model strings are not checked beyond being non-empty: aliases, Claude ids,
    Bedrock ARNs and Vertex names all pass.
    """
    errors = []
    agents = config.get("agents")
    if isinstance(agents, dict):
        for key, value in agents.items():
            if key not in AGENT_ROLES:
                errors.append(f"agents.{key}: unknown role")
            elif not isinstance(value, str) or not value.strip():
                errors.append(f"agents.{key}: model must be a non-empty string")
    profiles = config.get("profiles")
    if isinstance(profiles, dict):
        for name, profile in profiles.items():
            if not isinstance(profile, dict):
                profile = {}
            model = profile.get("model")
            if not isinstance(model, str) or not model.strip():
                errors.append(f"profiles.{name}.model: missing")
            if "env" not in profile:
                continue
            env = profile["env"]
            if not isinstance(env, dict) or not all(
                    isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
                errors.append(f"profiles.{name}.env: must map names to strings")
                continue
            for var in env:
                if any(word in var.upper() for word in SECRET_WORDS):
                    errors.append(f"profiles.{name}.env.{var}: "
                                  "secrets must come from your environment, not the config")
    run = config.get("run")
    if isinstance(run, dict) and run.get("isolation") == "worktree":
        errors.append("run.isolation: worktree isolation was removed; delete this line")
    return errors


def hold_reason(task: dict, blocked: dict[str, str], allow: dict) -> tuple[str, str] | None:
    """Why a task is held, or None if runnable.

    A task is held when it is tagged for a person (manual, decision, dep unless
    allowed by the run or by `[dep:ok]` on the task, download unless allowed by
    the run or by `[download:ok]`), or when its BLOCKED.md entry has a
    holding status (open, wontfix, moved). A wontfix or moved entry wins over a tag. A resolved entry does not hold: the
    task is runnable again. A task whose dependency is open and held is held
    too (propagated in plan()).
    """
    status = blocked.get(task["id"])
    if status in ("wontfix", "moved"):  # the person dropped it: that outranks a tag that waits on them
        return {"wontfix": ("wontfix", "dropped in BLOCKED.md (wontfix)"),
                "moved": ("moved", "moved to product/admin use")}[status]
    for tag in task["tags"]:
        if tag not in HOLD_TAGS:
            continue
        allow_key = ALLOW_KEYS.get(tag)
        if allow_key and (allow.get(allow_key) or f"{tag}:ok" in task["tags"]):
            continue
        return tag, HOLD_TAGS[tag]
    if status in HOLDING_STATUSES:
        return {
            "open": ("blocked", "open entry in BLOCKED.md"),
            "wontfix": ("wontfix", "dropped in BLOCKED.md (wontfix)"),
            "moved": ("moved", "moved to product/admin use"),
        }[status]
    return None


def direct_holds(parsed: dict, blocked: dict[str, str], allow: dict) -> dict[str, tuple[str, str]]:
    """{task id: (tag, reason)} for every directly held task (data-model §3.4).

    Tag and BLOCKED.md holds come first. Superseded holds then overwrite them:
    (1) `superseded-by Tnnn` / `replaced-by Tnnn` on an open task X holds X,
    whatever the successor's state; (2) every open task in a non-Convergence
    phase before the newest Convergence phase is held as superseded by that phase.
    """
    holds: dict[str, tuple[str, str]] = {}
    for phase in parsed["phases"]:
        for task in phase["tasks"]:
            if not task["done"] and (reason := hold_reason(task, blocked, allow)) is not None:
                holds[task["id"]] = reason
    # Rule 1: explicit superseded-by / replaced-by on the task itself.
    for phase in parsed["phases"]:
        for task in phase["tasks"]:
            if not task["done"] and task.get("superseded_by"):
                holds[task["id"]] = ("superseded", ", ".join(task["superseded_by"]))
    # Rule 2: open work in phases before the newest Convergence phase moved into it.
    phases = parsed["phases"]
    newest = max((i for i, p in enumerate(phases) if CONVERGENCE_RE.match(p["title"])), default=None)
    if newest is not None:
        by = ("superseded", f"Phase {phases[newest]['id']}")
        for phase in phases[:newest]:
            if CONVERGENCE_RE.match(phase["title"]):
                continue
            for task in phase["tasks"]:
                if task["done"]:
                    continue
                if holds.get(task["id"], ("",))[0] != "superseded":  # rule 1 names the successor
                    holds[task["id"]] = by
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
                if dep in holds and holds[dep][0] != "superseded":
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


def plan(parsed: dict, blocked: dict[str, dict], config: dict,
         phases: list[str] | None, max_tasks: int | None) -> dict:
    """Runnable phases and held tasks.

    `blocked` is the entries dict from parse_blocked_entries() (data-model §3.5); a plain
    {id: status} dict is accepted too. A runnable task whose entry is `resolved` with a
    non-empty answer carries `answer` and `question` into `phases[].tasks`.
    """
    allow = config.get("allow", {})
    entries = {i: e if isinstance(e, dict) else {"status": e} for i, e in blocked.items()}
    statuses = {i: e.get("status", "open") for i, e in entries.items()}
    out_phases, held, budget = [], [], max_tasks
    # Direct holds (tag or BLOCKED.md terminal status or superseded-by converge),
    # then propagate through dependencies.
    direct = direct_holds(parsed, statuses, allow)
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
            entry = entries.get(task["id"])
            if entry and entry.get("status") == "resolved" and entry.get("answer"):
                task = dict(task, answer=entry["answer"], question=entry.get("question", ""))
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
        "Tasks an unattended run could not finish. Answer the question under `- answer:` and set\n"
        "`status: resolved`: the next `/sdd-run` runs the task again with your answer.\n"
        "Set `status: wontfix` to drop the task, or `status: moved` if it became work outside this project.\n"
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


# Resume safety (data-model §3.6): the task fields whose edit makes a finished step stale.
SNAPSHOT_FIELDS = ("text", "tags", "files", "deps", "story")


def snapshot(parsed: dict) -> dict[str, dict]:
    return {t["id"]: {f: t[f] for f in SNAPSHOT_FIELDS} for p in parsed["phases"] for t in p["tasks"]}


def changed(parsed: dict, old: dict[str, dict]) -> list[str]:
    """Ids present in both whose snapshot fields differ. Checkbox (`done`) and `line` are ignored."""
    new = snapshot(parsed)
    return sorted(i for i in old if i in new and old[i] != new[i])


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


WORKFLOW_DIR_RE = re.compile(r"^wf_[A-Za-z0-9_-]+$")
TAIL_BYTES = 64 * 1024


def valid_workflow_dir(path) -> bool:
    """An absolute `wf_…` directory holding `journal.jsonl`. Readers skip anything else, so a
    project file cannot make them read arbitrary paths."""
    p = Path(path)
    return (p.is_absolute() and bool(WORKFLOW_DIR_RE.match(p.name)) and p.is_dir()
            and (p / "journal.jsonl").is_file())


def _line_ts(line: bytes | str):
    try:
        value = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value.get("timestamp") if isinstance(value, dict) else None


def _first_ts(transcript: Path):
    try:
        with transcript.open("rb") as f:
            return _line_ts(f.readline())
    except OSError:
        return None


def _last_ts(transcript: Path):
    """The `timestamp` of the last complete line, reading only the file's last 64 KB."""
    try:
        with transcript.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read()
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        if line.strip():
            return _line_ts(line)
    return None


def journal_events(workflow_dirs: list[Path]) -> list[dict]:
    """Run events derived from the engine's workflow journals (data-model.md §3.9).

    Invalid directories are skipped; `workflow` is the index into `workflow_dirs` as given.
    """
    events: list[dict] = []
    for index, raw in enumerate(workflow_dirs):
        wf = Path(raw)
        if not valid_workflow_dir(wf):
            continue
        started: dict[str, tuple[str, str]] = {}  # key (or agentId) -> (label, agentId)
        for line in (wf / "journal.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # a line cut off when the process was killed
            if not isinstance(entry, dict):
                continue
            kind, key, agent_id = entry.get("type"), entry.get("key"), entry.get("agentId")
            if kind == "started":
                label = entry.get("label", "")
                for k in (key, agent_id):
                    if k:
                        started[k] = (label, agent_id or "")
            elif kind in ("result", "failed"):
                label, known_agent = started.get(key) or started.get(agent_id) or ("", "")
                agent_id = agent_id or known_agent
            else:
                continue
            match = LABEL_RE.match(label or "")
            if not match:
                continue
            label_kind, ids = match["kind"], match["ids"].split(",")
            attempt = int(match["n"] or 1)
            transcript = wf / f"agent-{agent_id}.jsonl" if agent_id else None
            ts = None
            if transcript is not None:
                ts = _first_ts(transcript) if kind == "started" else _last_ts(transcript)
            group = label_kind in ("commit", "repair")

            def ev(name: str, **fields) -> None:
                base = {"ts": ts, "kind": name}
                base.update({"ids": ids} if group else {"id": ids[0]})
                base.update({"label": label, "agent_id": agent_id, "workflow": index})
                base.update(fields)
                events.append(base)

            if kind == "failed":
                ev("task.failed", error=entry.get("error") or "")
                continue
            if kind == "started":
                if label_kind in ("impl", "eval"):
                    ev("task.started", attempt=attempt)
                elif label_kind == "repair":
                    ev("group.repair")
                continue
            result = entry.get("result")
            if not isinstance(result, dict):
                continue
            if label_kind in ("impl", "eval"):
                if result.get("status") == "done":
                    ev("task.implemented", attempt=attempt)
                elif result.get("status") == "blocked":
                    ev("task.blocked", reason=result.get("reason", ""), tag=result.get("tag", ""))
            elif label_kind == "verify":
                ev("task.verified", verdict=result.get("verdict"), problems=result.get("problems", []))
                if result.get("verdict") == "approve":
                    ev("task.approved")
            elif label_kind == "commit":
                ev("group.finished", checks_passed=result.get("checks_passed"),
                   commits=result.get("commits", []))
                for task_id in result.get("blocked_recorded") or []:
                    events.append({"ts": ts, "kind": "task.blocked", "id": task_id, "label": label,
                                   "agent_id": agent_id, "workflow": index})
    return events


def run_events(project: Path, run_id: str) -> dict:
    """The events of run `run_id`, from the directories listed in `.sdd/runs/<id>/workflows.json`."""
    dirs: list[str] = []
    listing = Path(project) / ".sdd" / "runs" / run_id / "workflows.json"
    try:
        data = json.loads(listing.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("workflow_dirs"), list):
            dirs = [str(d) for d in data["workflow_dirs"]]
    except (OSError, json.JSONDecodeError):
        pass
    return {"run_id": run_id, "workflow_dirs": dirs,
            "missing": [d for d in dirs if not valid_workflow_dir(d)],
            "events": journal_events([Path(d) for d in dirs])}


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

    p_snapshot = sub.add_parser("snapshot")
    p_snapshot.add_argument("tasks", type=Path)

    p_changed = sub.add_parser("changed")
    p_changed.add_argument("tasks", type=Path)
    p_changed.add_argument("--run-json", type=Path, required=True)

    p_events = sub.add_parser("events")
    p_events.add_argument("project", type=Path)
    p_events.add_argument("run_id")

    args = parser.parse_args(argv)
    if args.cmd == "parse":
        result = parse_tasks(args.tasks)
        result["blocked"] = parse_blocked(args.blocked)
    elif args.cmd == "plan":
        phases = [p.strip() for p in args.phases.split(",")] if args.phases else None
        result = plan(parse_tasks(args.tasks), parse_blocked_entries(args.blocked),
                      load_config(args.config), phases, args.max_tasks)
    elif args.cmd == "tick":
        result = tick(args.tasks, args.ids)
    elif args.cmd == "block":
        result = block(args.blocked, args.id, args.tag, args.reason, args.question)
    elif args.cmd == "checkpoint":
        result = checkpoint(args.journal)
    elif args.cmd == "snapshot":
        result = snapshot(parse_tasks(args.tasks))
    elif args.cmd == "changed":
        run = json.loads(args.run_json.read_text(encoding="utf-8"))
        result = {"changed": changed(parse_tasks(args.tasks), run.get("task_snapshot") or {})}
    elif args.cmd == "events":
        result = run_events(args.project, args.run_id)
    else:
        result = load_config(args.config)
        result["errors"] = config_errors(result)
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
