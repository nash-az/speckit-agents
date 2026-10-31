#!/usr/bin/env python3
"""Jobs: the record of one piece of work that spans several runs (spec 005, data-model §1 and §7).

A Job lives in `.sdd/jobs/<id>.json`. Every write goes through `save()`, which writes `<id>.json.tmp` and
then `os.replace`s it, so a reader never sees half a record; keys this module does not know are kept.
At most one Job per project is open (state not `merged` or `dropped`); `open_job()` returns it.

Functions so far: `load`, `save`, `list_jobs`, `open_job`, `new_id`, `default_title`.
Stdlib only; no model.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ID_RE = re.compile(r"^\d{8}-\d{4}(?:-(\d+))?$")
CLOSED_STATES = ("merged", "dropped")


def jobs_dir(project) -> Path:
    return Path(project) / ".sdd" / "jobs"


def _valid_id(job_id) -> bool:
    return isinstance(job_id, str) and bool(ID_RE.match(job_id))


def _id_key(job_id: str):
    """Sort key: the `YYYYMMDD-HHMM` part, then the suffix as a number (no suffix is 1)."""
    m = ID_RE.match(job_id)
    suffix = int(m.group(1)) if m and m.group(1) else 1
    return (job_id[:13], suffix)


def load(project, job_id: str) -> dict | None:
    """The record with this id, or None when the id is malformed, missing or unreadable."""
    if not _valid_id(job_id):
        return None
    try:
        record = json.loads((jobs_dir(project) / f"{job_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def save(project, record: dict) -> Path:
    """Write the record atomically (`<id>.json.tmp`, then `os.replace`); every key is kept."""
    job_id = record.get("id")
    if not _valid_id(job_id):
        raise ValueError(f"not a job id: {job_id!r}")
    directory = jobs_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{job_id}.json"
    tmp = directory / f"{job_id}.json.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def list_jobs(project) -> list[dict]:
    """Every readable record, newest first (by id: start time, then suffix)."""
    directory = jobs_dir(project)
    if not directory.is_dir():
        return []
    ids = [p.name[:-len(".json")] for p in directory.glob("*.json")]
    records = [r for r in (load(project, i) for i in ids if _valid_id(i)) if r is not None]
    return sorted(records, key=lambda r: _id_key(str(r.get("id", ""))), reverse=True)


def open_job(project) -> dict | None:
    """The record whose state is not `merged` or `dropped` (newest if several), else None."""
    for record in list_jobs(project):
        if record.get("state") not in CLOSED_STATES:
            return record
    return None


def new_id(project, now: float | None = None) -> str:
    """`YYYYMMDD-HHMM` in local time; `-2`, `-3`, ... when a record with that id already exists (R12)."""
    base = time.strftime("%Y%m%d-%H%M", time.localtime(time.time() if now is None else now))
    directory = jobs_dir(project)
    candidate, n = base, 1
    while (directory / f"{candidate}.json").exists():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def _ranges(ids: list[str]) -> str:
    """"3, 4, 5, 7" -> "3–5, 7" for numeric ids; other ids are listed as given."""
    if not all(i.isdigit() for i in ids):
        return ", ".join(ids)
    nums = sorted({int(i) for i in ids})
    parts, start, prev = [], nums[0], nums[0]
    for n in nums[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else f"{start}–{prev}")
        if n is not None:
            start = prev = n
    return ", ".join(parts)


def default_title(scope: dict) -> str:
    """The scope in words (FR-014): "Phase 11", "Phases 3–5", "T061, T063", "All open tasks"."""
    scope = scope or {}
    phases = [str(p) for p in scope.get("phases") or []]
    tasks = [str(t) for t in scope.get("tasks") or []]
    if phases:
        return f"Phase {phases[0]}" if len(set(phases)) == 1 else f"Phases {_ranges(phases)}"
    if tasks:
        return ", ".join(tasks)
    return "All open tasks"


def main(argv: list[str]) -> int:
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
