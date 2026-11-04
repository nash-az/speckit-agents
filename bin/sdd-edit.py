#!/usr/bin/env python3
"""Edit a feature's `spec.md` or the project's constitution, and commit the edit (spec 003 FR-035).

Used by the dashboard's Docs editor and usable from the shell:

    bin/sdd-edit.py state  <project dir> <feature dir> <spec|constitution>
    bin/sdd-edit.py commit <project dir> <feature dir> <spec|constitution> "<message>"

`save()` replaces the file after checking that it still holds the text the person opened (a hash of
it), and moves a temp file into place so a crash never leaves half a file. `commit()` commits that one
file with the message as given, whatever else is changed in the tree. Both refuse, and change nothing,
while a run is active (`.sdd/run.json`): the run guard only covers agents, so this is the person's guard.
From the shell it prints the result of `state()` or `commit()` as one JSON object with `"contract"`
(008 data-model §1), or `{"contract", "error"}` with exit code 1 (contracts/engine-cli.md). Stdlib only; no model.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("sdd_tasks", Path(__file__).resolve().with_name("sdd-tasks.py"))
sdd_tasks = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_tasks)

EDITABLE = ("spec", "constitution")
MAX_CHARS = 500_000
RUN_ACTIVE = "a run is active (.sdd/run.json exists); edit after it finishes"


class Refused(Exception):
    """The edit or commit cannot happen now; nothing was changed."""


def _git(project: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=project, capture_output=True, text=True)
    if result.returncode != 0:
        raise Refused((result.stderr or result.stdout).strip() or f"git {args[0]} failed")
    return result.stdout


def target(project: Path, feature: Path | None, name: str) -> Path:
    """The one file `name` stands for; a name outside EDITABLE never reaches a path."""
    project = Path(project).resolve()
    if name == "constitution":
        return project / ".specify" / "memory" / "constitution.md"
    if name == "spec":
        if feature is None:
            raise Refused("choose a feature first")
        feature = Path(feature).resolve()
        if feature.parent != (project / "specs").resolve() or not feature.is_dir():
            raise Refused("the feature is not a folder under specs/")
        return feature / "spec.md"
    raise Refused(f"{name} cannot be edited here")


def digest(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _is_repo(project: Path) -> bool:
    try:
        _git(project, "rev-parse", "--is-inside-work-tree")
    except Refused:
        return False
    return True


def state(project: Path, feature: Path | None, name: str) -> dict:
    """`{path, exists, hash, dirty, locked}`: `locked` is why editing is off now (or None)."""
    project = Path(project).resolve()
    path = target(project, feature, name)
    text = _read(path)
    rel = str(path.relative_to(project))
    dirty = False
    if _is_repo(project):
        dirty = bool(_git(project, "status", "--porcelain", "--", rel).strip())
    locked = RUN_ACTIVE if (project / ".sdd" / "run.json").exists() else None
    return {"path": rel, "exists": text is not None, "hash": digest(text), "dirty": dirty, "locked": locked}


def save(project: Path, feature: Path | None, name: str, text: str, base: str) -> dict:
    """Replace the file with `text` when it still hashes to `base`; returns the new state."""
    project = Path(project).resolve()
    if (project / ".sdd" / "run.json").exists():
        raise Refused(RUN_ACTIVE)
    if not isinstance(text, str) or not text.strip():
        raise Refused("the document is empty")
    if len(text) > MAX_CHARS:
        raise Refused(f"the document is longer than {MAX_CHARS} characters")
    path = target(project, feature, name)
    if not path.parent.is_dir():
        raise Refused(f"{path.parent.relative_to(project)} does not exist")
    if digest(_read(path)) != base:
        raise Refused("the file changed since you opened it; reload it and apply your edit again")
    text = text.replace("\r\n", "\n").rstrip("\n") + "\n"
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return state(project, feature, name)


def commit(project: Path, feature: Path | None, name: str, message: str) -> dict:
    """Commit just that file with `message`; other changes in the tree stay as they are."""
    project = Path(project).resolve()
    if (project / ".sdd" / "run.json").exists():
        raise Refused(RUN_ACTIVE)
    message = (message or "").strip()
    if not message:
        raise Refused("the commit message is empty")
    path = target(project, feature, name)
    rel = str(path.relative_to(project))
    if not _is_repo(project):
        raise Refused("the project is not a git repository")
    if not _git(project, "status", "--porcelain", "--", rel).strip():
        raise Refused(f"{rel} has no uncommitted change")
    _git(project, "add", "--", rel)
    try:
        _git(project, "commit", "-m", message, "--", rel)
    except Refused:
        subprocess.run(["git", "reset", "-q", "--", rel], cwd=project, capture_output=True)
        raise
    return {"commit": _git(project, "rev-parse", "--short", "HEAD").strip(), "file": rel}


def main(argv: list[str]) -> int:
    if len(argv) not in (4, 5) or argv[0] not in ("state", "commit") or (argv[0] == "commit") != (len(argv) == 5):
        print(__doc__)
        return 2
    project, feature, name = Path(argv[1]), Path(argv[2]), argv[3]
    try:
        if argv[0] == "state":
            result = state(project, feature, name)
        else:
            result = commit(project, feature, name, argv[4])
    except Refused as e:
        print(json.dumps({"contract": sdd_tasks.CONTRACT, "error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps({"contract": sdd_tasks.CONTRACT, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
