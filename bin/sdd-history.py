#!/usr/bin/env python3
"""Task histories and run summaries (spec 009, plan "Task history" and "Run summary").

Every task has an append-only history in `.sdd/tasks/<feature folder>/<task id>.jsonl`, one JSON object per line,
oldest first. The entries come from the workflow journals of the run's sessions (`sdd-tasks.py run_events`) and
from the run's own steps (suspended, resumed, dropped). Each entry has a `key`, so harvesting twice adds nothing.
A run's summary is `.sdd/runs/<run id>.json`, written once when the run ends. Both are committed, so a task's
history does not depend on Claude's transcripts, which Claude Code may delete.

    bin/sdd-history.py harvest <project> <run id>              -> {"contract", "added": {task: n}}
    bin/sdd-history.py show <project> <feature> <task id>      -> {"contract", "entries": [...]}

A run id is a record in `.sdd/jobs/` (009 plan "Run record"). Stdlib only; no model.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_tasks", HERE / "sdd-tasks.py")
sdd_tasks = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_tasks)

TASK_ID_RE = re.compile(r"^T\d+[A-Za-z0-9]*$")
ROLE_BY_LABEL = {"impl": "implementer", "eval": "evaluator", "verify": "verifier", "commit": "bookkeeper",
                 "repair": "implementer"}


def feature_name(record: dict) -> str:
    """The feature folder's name (`008-standalone-server`), also for a feature given as a path."""
    return Path(str(record.get("feature") or "")).name


def history_path(project, feature: str, task_id: str) -> Path:
    if not TASK_ID_RE.match(task_id or ""):
        raise ValueError(f"not a task id: {task_id!r}")
    return Path(project) / ".sdd" / "tasks" / Path(feature).name / f"{task_id}.jsonl"


def read(project, feature: str, task_id: str) -> list[dict]:
    """The task's entries, oldest first; [] when it has none. Unreadable lines are skipped."""
    try:
        lines = history_path(project, feature, task_id).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def append(project, feature: str, task_id: str, entry: dict) -> bool:
    """Add one entry unless an entry with its `key` is already there. Returns True when it was added."""
    if not entry.get("key"):
        raise ValueError("an entry needs a key")
    path = history_path(project, feature, task_id)
    if any(e.get("key") == entry["key"] for e in read(project, feature, task_id)):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return True


def _agent_meta(workflow_dir: Path, agent_id: str) -> dict:
    try:
        meta = json.loads((workflow_dir / f"agent-{agent_id}.meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _entries_from_event(run_id: str, session: str, event: dict, workflow_dir: Path | None) -> list[tuple[str, dict]]:
    """(task id, entry) pairs for one `run_events` event; [] for kinds a history does not keep."""
    kind, label, agent_id = event.get("kind"), event.get("label") or "", event.get("agent_id") or ""
    prefix = label.split(":", 1)[0]
    base = {"ts": event.get("ts"), "run": run_id, "session": session}
    if prefix in ROLE_BY_LABEL:
        base["role"] = ROLE_BY_LABEL[prefix]
    if workflow_dir is not None and agent_id:
        model = _agent_meta(workflow_dir, agent_id).get("pickedEffortModel")
        if isinstance(model, str) and model:
            base["model"] = model
        transcript = workflow_dir / f"agent-{agent_id}.jsonl"
        if transcript.exists():
            base["transcript"] = str(transcript)
    steps = {"task.started": "started", "task.implemented": "implemented", "task.verified": "verified",
             "task.blocked": "blocked", "task.failed": "failed"}
    pairs: list[tuple[str, dict]] = []
    if kind in steps:
        entry = {**base, "step": steps[kind]}
        for field in ("attempt", "verdict", "problems", "reason", "tag"):
            if event.get(field) not in (None, ""):
                entry[field] = event[field]
        if kind == "task.failed" and event.get("error"):
            entry["reason"] = event["error"]
        pairs.append((event.get("id"), entry))
    elif kind == "group.finished":
        step = "committed" if event.get("checks_passed") else "checks failed"
        for task_id in event.get("ids") or []:
            pairs.append((task_id, {**base, "step": step, "commits": event.get("commits") or []}))
    elif kind == "group.repair":
        for task_id in event.get("ids") or []:
            pairs.append((task_id, {**base, "step": "repair"}))
    out = []
    for task_id, entry in pairs:
        if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
            continue
        entry["key"] = f"{session}:{event.get('workflow')}:{label}:{entry['step']}:{agent_id}:{task_id}"
        out.append((task_id, entry))
    return out


def sessions(project, record: dict) -> list[str]:
    """The record's session ids (its `runs`), plus the live `.sdd/run.json` session when it belongs to it."""
    ids = [str(i) for i in record.get("runs") or []]
    try:
        live = json.loads((Path(project) / ".sdd" / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        live = None
    if isinstance(live, dict) and live.get("run_id") and (
            live.get("job") == record.get("id") or (live.get("branch") and live.get("branch") == record.get("branch"))):
        if live["run_id"] not in ids:
            ids.append(str(live["run_id"]))
    return ids


def _session_events(project, session: str) -> tuple[list[dict], list[str]]:
    data = sdd_tasks.run_events(Path(project), session)
    return data["events"], data["workflow_dirs"]


def harvest(project, record: dict) -> dict[str, int]:
    """Append every journal step of the run's sessions to the tasks' histories. Returns {task id: entries added}."""
    feature = feature_name(record)
    added: dict[str, int] = {}
    for session in sessions(project, record):
        events, dirs = _session_events(project, session)
        for event in events:
            index = event.get("workflow")
            wf = Path(dirs[index]) if isinstance(index, int) and 0 <= index < len(dirs) else None
            for task_id, entry in _entries_from_event(str(record.get("id")), session, event, wf):
                if append(project, feature, task_id, entry):
                    added[task_id] = added.get(task_id, 0) + 1
    return added


def record_step(project, record: dict, task_id: str, step: str, **fields) -> bool:
    """A run step that is not in a journal (`suspended`, `resumed`, `dropped`) for one task."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    entry = {"ts": ts, "run": str(record.get("id")), "session": fields.pop("session", None), "step": step, **fields}
    entry["key"] = f"{record.get('id')}:{step}:{ts}:{task_id}"
    return append(project, feature_name(record), task_id, entry)


def _run_task_entries(project, record: dict) -> dict[str, list[dict]]:
    """{task id: entries of this run} for every task with a history file in the run's feature."""
    folder = Path(project) / ".sdd" / "tasks" / feature_name(record)
    out: dict[str, list[dict]] = {}
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.jsonl")):
        entries = [e for e in read(project, feature_name(record), path.stem) if e.get("run") == str(record.get("id"))]
        if entries:
            out[path.stem] = entries
    return out


def summary(project, record: dict) -> dict:
    """The run summary (plan "Run summary") from the record and the tasks' histories."""
    per_task = _run_task_entries(project, record)
    done, blocked, dropped = [], [], []
    attempts = approved_first = 0
    models: dict[str, str] = {}
    for task_id, entries in per_task.items():
        steps = [e.get("step") for e in entries]
        if "committed" in steps:
            done.append(task_id)
        elif "dropped" in steps:
            dropped.append(task_id)
        elif "blocked" in steps or "failed" in steps:
            blocked.append(task_id)
        attempts += steps.count("started")
        verdicts = [e for e in entries if e.get("step") == "verified"]
        if verdicts and verdicts[0].get("verdict") == "approve" and (verdicts[0].get("attempt") or 1) == 1 \
                and steps.count("started") <= 1:
            approved_first += 1
        for e in entries:
            if e.get("role") and e.get("model"):
                models.setdefault(e["role"], e["model"])
    return {"contract": sdd_tasks.CONTRACT, "id": record.get("id"), "title": record.get("title"),
            "feature": feature_name(record), "scope": record.get("scope"), "created": record.get("created"),
            "ended": record.get("ended"), "outcome": record.get("outcome"), "reason": record.get("reason"),
            "sessions": sessions(project, record),
            "tasks": {"done": sorted(done), "blocked": sorted(blocked), "dropped": sorted(dropped)},
            "attempts": attempts, "approved_first_time": approved_first, "models": models}


def summary_path(project, run_id: str) -> Path:
    return Path(project) / ".sdd" / "runs" / f"{run_id}.json"


def write_summary(project, record: dict) -> Path | None:
    """Write `.sdd/runs/<run id>.json` once; returns its path, or None when it already exists."""
    path = summary_path(project, str(record.get("id")))
    if path.exists():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary(project, record), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _git(project, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=project, capture_output=True, text=True)


def write_session_reports(project, record: dict) -> list[Path]:
    """A short `.sdd/runs/<session>.md` for each session that ended without the skill's report (a stop at a
    task boundary, a Drop). Returns the paths written."""
    written = []
    per_task = _run_task_entries(project, record)
    for session in sessions(project, record):
        path = Path(project) / ".sdd" / "runs" / f"{session}.md"
        if path.exists():
            continue
        mine = {t: [e for e in entries if e.get("session") == session] for t, entries in per_task.items()}
        mine = {t: e for t, e in mine.items() if e}
        committed = sorted(t for t, e in mine.items() if any(x.get("step") == "committed" for x in e))
        commits = sorted({c for e in mine.values() for x in e if x.get("step") == "committed" for c in x.get("commits") or []})
        other = sorted(t for t in mine if t not in committed)
        lines = [f"# Run {session}", "",
                 f"- Run: {record.get('title') or record.get('id')} (`{record.get('id')}`), feature `{feature_name(record)}`",
                 f"- Branch: `{record.get('branch')}`",
                 f"- Ended: {record.get('outcome') or 'stopped'}" + (f" ({record['reason']})" if record.get("reason") else ""),
                 "- This report was written by the engine: the session ended before the skill's report step.", "",
                 f"## Committed ({len(committed)})", ""]
        lines += [f"- {t}" for t in committed] or ["None."]
        if commits:
            lines += ["", "Commits: " + ", ".join(commits)]
        lines += ["", "## Not committed", ""]
        lines += [f"- {t}: " + (mine[t][-1].get("step") or "") for t in other] or ["None."]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(path)
    return written


def finalize(project, record: dict) -> dict:
    """Harvest the histories, write missing session reports and the summary, and commit them as
    `chore(run): <id> history and summary`. Commits only `.sdd/tasks/` and `.sdd/runs/` files; makes no commit
    when nothing changed. Returns {"commit": sha | None, "files": [relative paths]}."""
    project = Path(project)
    harvest(project, record)
    write_session_reports(project, record)
    write_summary(project, record)
    status = _git(project, "status", "--porcelain", "--untracked-files=all", "--", ".sdd/tasks", ".sdd/runs")
    files = [line[3:] for line in status.stdout.splitlines() if line.strip()]
    files = [f for f in files if f.startswith(".sdd/tasks/") or (f.startswith(".sdd/runs/") and f.count("/") == 2)]
    if not files:
        return {"commit": None, "files": []}
    added = _git(project, "add", "--", *files)
    if added.returncode != 0:
        return {"commit": None, "files": files, "error": added.stderr.strip()}
    done = _git(project, "commit", "-m", f"chore(run): {record.get('id')} history and summary", "--", *files)
    if done.returncode != 0:
        _git(project, "reset", "-q", "--", *files)
        return {"commit": None, "files": files, "error": (done.stderr or done.stdout).strip()}
    return {"commit": _git(project, "rev-parse", "--short", "HEAD").stdout.strip(), "files": files}


USAGE = "usage: sdd-history.py harvest <project> <run id> | show <project> <feature> <task id>"


def _load_record(project: Path, run_id: str) -> dict | None:
    try:
        record = json.loads((project / ".sdd" / "jobs" / f"{run_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "harvest":
        project = Path(argv[1])
        record = _load_record(project, argv[2])
        if record is None:
            # A plain /sdd-run (no run record): the session id is the run id.
            config = sdd_tasks.load_config(project / ".sdd" / "config.toml")
            feature = str((config.get("run") or {}).get("feature") or "")
            if not feature:
                try:
                    feature = json.loads((project / ".specify" / "feature.json").read_text())["feature_directory"]
                except (OSError, ValueError, KeyError, TypeError):
                    feature = ""
            record = {"id": argv[2], "feature": feature, "runs": [argv[2]]}
        if not feature_name(record):
            print(json.dumps({"contract": sdd_tasks.CONTRACT, "error": "no feature for this run"}))
            return 1
        print(json.dumps({"contract": sdd_tasks.CONTRACT, "added": harvest(project, record)}))
        return 0
    if len(argv) == 4 and argv[0] == "show":
        try:
            entries = read(Path(argv[1]), argv[2], argv[3])
            history_path(argv[1], argv[2], argv[3])
        except ValueError as e:
            print(json.dumps({"contract": sdd_tasks.CONTRACT, "error": str(e)}))
            return 1
        print(json.dumps({"contract": sdd_tasks.CONTRACT, "entries": entries}, ensure_ascii=False))
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
