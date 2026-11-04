#!/usr/bin/env python3
"""Harvest plugin feedback from a project into this plugin repo's logs (data-model.md §8, FR-024).

Each run of `/sdd-run` may append problems with the plugin itself to the project's
`.sdd/plugin-feedback.jsonl`, one JSON object per line:
    {"id": "<run id>-<n>", "date": "YYYY-MM-DD", "run_id": "...", "project": "...",
     "text": "...", "evidence": "..."}

This script copies the entries it has not seen before into a markdown log in the plugin repo, so
plugin feedback stays here and never in the app repo.

Usage:
    python3 bin/sdd-harvest.py <project dir> [--log logs/<file>.md]

- Lines that are not valid JSON objects with an "id" are skipped.
- Harvested ids are kept in `logs/.harvested.json` (`{"ids": [...]}`), relative to the plugin repo
  (the directory above `bin/`); entries with those ids are skipped.
- New entries are appended as `- <date> <run_id>: <text> (evidence: <evidence>)` under a
  `## Harvested <today>` heading in the log (default `logs/<YYYY-MM-DD>-<project dir name>-feedback.md`).
- Prints `{"contract": "1.0", "harvested": n, "skipped": m, "log": "<path>"}`; `skipped` counts bad
  lines and already-harvested entries.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
# 008 data-model §1. This script runs on its own (it may be copied without sdd-tasks.py), so it keeps
# its own copy; tests/test_contract_field.py fails when it differs from sdd-tasks.py's CONTRACT.
CONTRACT = "1.0"


def load_ids(path: Path) -> list[str]:
    """Return the harvested ids from `path`; a missing or unreadable file means none."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    ids = data.get("ids") if isinstance(data, dict) else None
    return [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []


def read_entries(path: Path) -> tuple[list[dict], int]:
    """Return (entries, bad line count) from a plugin-feedback.jsonl file."""
    entries, bad = [], 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(obj, dict) or not isinstance(obj.get("id"), str) or not obj["id"]:
            bad += 1
            continue
        entries.append(obj)
    return entries, bad


def format_entry(e: dict) -> str:
    return f"- {e.get('date', '')} {e.get('run_id', '')}: {e.get('text', '')} (evidence: {e.get('evidence', '')})"


def append_log(log: Path, project_name: str, today: str, lines: list[str]) -> None:
    """Append `lines` under `## Harvested <today>`, reusing that heading if it is the file's last one."""
    heading = f"## Harvested {today}"
    existing = log.read_text(encoding="utf-8") if log.exists() else ""
    out = []
    if not existing:
        out.append(f"# Plugin feedback from {project_name}\n\n")
    elif not existing.endswith("\n"):
        out.append("\n")
    headings = [l for l in existing.splitlines() if l.startswith("## ")]
    if not headings or headings[-1].strip() != heading:
        out.append(("\n" if existing else "") + heading + "\n\n")
    out.extend(l + "\n" for l in lines)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write("".join(out))


def harvest(project: Path, log: Path | None = None, plugin_dir: Path = PLUGIN_DIR,
            today: str | None = None) -> dict:
    today = today or datetime.date.today().isoformat()
    project = project.resolve()
    if log is None:
        log = plugin_dir / "logs" / f"{today}-{project.name}-feedback.md"
    ids_path = plugin_dir / "logs" / ".harvested.json"
    known = load_ids(ids_path)
    seen = set(known)
    entries, skipped = read_entries(project / ".sdd" / "plugin-feedback.jsonl")
    new = []
    for e in entries:
        if e["id"] in seen:
            skipped += 1
            continue
        seen.add(e["id"])
        new.append(e)
    if new:
        append_log(log, project.name, today, [format_entry(e) for e in new])
        ids_path.parent.mkdir(parents=True, exist_ok=True)
        ids_path.write_text(json.dumps({"ids": known + [e["id"] for e in new]}, indent=2) + "\n",
                            encoding="utf-8")
    return {"harvested": len(new), "skipped": skipped, "log": str(log)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Harvest a project's plugin feedback into logs/.")
    ap.add_argument("project", help="project directory holding .sdd/plugin-feedback.jsonl")
    ap.add_argument("--log", help="markdown log to append to (default logs/<date>-<project>-feedback.md)")
    args = ap.parse_args(argv)
    project = Path(args.project)
    if not project.is_dir():
        print(f"not a directory: {project}", file=sys.stderr)
        return 2
    result = harvest(project, Path(args.log) if args.log else None)
    print(json.dumps({"contract": CONTRACT, **result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
