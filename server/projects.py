"""The server's project list (008 data-model §6, §10; contracts/server-api.md).

Python standard library only, loaded by `server/app.py` through `server_module("projects")`; it
imports nothing from the server or the engine. The engine is reached through `handler.engine` (the
modules `app.py` loaded from the engine folder), and the list through `handler.projects`, a `Store`
that `app.make_server()` creates on `<server state dir>/projects.json`.

Routes (`ROUTES`, added to app.py's route table by `app.load_server_modules()`):
- `GET /api/projects` → `{"projects": [{"id", "path", "name", "missing", "state", "origin", "job"}]}`.
  It also answers without an engine; then `state`, `origin` and `job` are null.
- `POST /api/projects/add {"path"}` → the entry; `{"needs_setup": true, "path", "plan"}` for a git
  repository without `.sdd/config.toml` (nothing is added); 403 outside the home folder; 422 for a
  folder that is not a git repository (§10).
- `POST /api/projects/<id>/remove {}` → `{"ok": true}`; the project itself is never touched.
- `POST /api/p/<id>/feature {"feature": name | null}` → `{"ok": true, "feature"}`; sets the feature
  the project's per-feature routes read, or null to follow its `active_feature()` (§6).
- `GET /api/fs/list?path=<abs>&hidden=0|1` → `{"path", "parent", "entries": [{"name", "path", "sdd",
  "listed"}], "truncated"}`: the folder browser (§10). Folders only, sorted, at most 500; `path`
  defaults to the home folder and must resolve inside it (403); `parent` is null at the home folder.

`project_for(handler, id)` is the lookup every `/api/p/<id>/…` route uses: unknown id → 404, missing
project → 410 `{"error": "project folder is missing"}`.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

PROJECT_ID = r"[0-9a-f]{12}"
MISSING = "project folder is missing"
OUTSIDE_HOME = "outside your home folder"
NOT_GIT = "not a git repository"
SETUP_SCRIPT = "sdd-setup.py"  # the engine's setup command (§12); lives next to the other engine files
GIT_TIMEOUT = 30
DETECT_TIMEOUT = 60
FS_LIST_LIMIT = 500  # the folder browser lists at most this many folders (§10)


# --- The list ------------------------------------------------------------------------------------


def project_id(path: Path) -> str:
    """The first 12 hex characters of the sha256 of the resolved path (§6)."""
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]


def is_missing(path) -> bool:
    """A project whose folder no longer exists or has no `.sdd/config.toml` (§6)."""
    try:
        return not (Path(path) / ".sdd" / "config.toml").is_file()
    except OSError:
        return True


def display_names(paths: list[str]) -> list[str]:
    """The folder's base name; when two projects share a base name, the parent's base name is
    added, as in `"app (private)"` (§6)."""
    bases = [Path(p).name for p in paths]
    names = []
    for path, base in zip(paths, bases):
        if bases.count(base) > 1:
            names.append(f"{base} ({Path(path).parent.name})")
        else:
            names.append(base)
    return names


class Store:
    """`projects.json`: `{"contract", "projects": [{"id", "path", "name", "added", "feature"}]}`.
    Every change reads the file, changes it and writes it atomically (`<name>.tmp`, then
    `os.replace()`), under one lock. A file that does not parse is kept as `projects.json.bad`
    before the first write over it."""

    def __init__(self, file: Path, contract: str):
        self.file = Path(file)
        self.contract = contract
        self._lock = threading.Lock()

    def _read(self) -> tuple[list[dict], bool]:
        """(entries, whether the file was unreadable). A missing file is an empty list."""
        try:
            text = self.file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return [], False
        except OSError:
            return [], True
        try:
            data = json.loads(text)
        except ValueError:
            return [], True
        projects = data.get("projects") if isinstance(data, dict) else None
        if not isinstance(projects, list):
            return [], True
        entries = [e for e in projects if isinstance(e, dict) and isinstance(e.get("id"), str)
                   and isinstance(e.get("path"), str)]
        return entries, False

    def _write(self, entries: list[dict], was_bad: bool):
        for entry, name in zip(entries, display_names([e["path"] for e in entries])):
            entry["name"] = name
        self.file.parent.mkdir(parents=True, exist_ok=True)
        if was_bad and self.file.exists():
            os.replace(self.file, self.file.with_name(self.file.name + ".bad"))
        tmp = self.file.with_name(self.file.name + ".tmp")
        tmp.write_text(json.dumps({"contract": self.contract, "projects": entries}, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, self.file)

    def entries(self) -> list[dict]:
        with self._lock:
            return self._read()[0]

    def get(self, pid: str) -> dict | None:
        return next((e for e in self.entries() if e["id"] == pid), None)

    def add(self, path: Path, now: float | None = None) -> dict:
        """Add the resolved `path`; a path already listed returns the existing entry (§6)."""
        path = Path(path).resolve()
        pid = project_id(path)
        with self._lock:
            entries, bad = self._read()
            found = next((e for e in entries if e["id"] == pid), None)
            if found is not None:
                return found
            entry = {"id": pid, "path": str(path), "name": path.name,
                     "added": int(time.time() if now is None else now), "feature": None}
            entries.append(entry)
            self._write(entries, bad)
            return entry

    def remove(self, pid: str) -> bool:
        """Drop the entry; False when it is not listed."""
        with self._lock:
            entries, bad = self._read()
            kept = [e for e in entries if e["id"] != pid]
            if len(kept) == len(entries):
                return False
            self._write(kept, bad)
            return True

    def set_feature(self, pid: str, feature: str | None) -> dict | None:
        """Set the entry's `feature`; None when it is not listed."""
        with self._lock:
            entries, bad = self._read()
            found = next((e for e in entries if e["id"] == pid), None)
            if found is None:
                return None
            found["feature"] = feature
            self._write(entries, bad)
            return found


# --- Helpers for the routes ----------------------------------------------------------------------


def inside_home(path: Path) -> bool:
    """True when the resolved `path` lies inside `Path.home().resolve()` (§10)."""
    try:
        return Path(path).resolve().is_relative_to(Path.home().resolve())
    except (OSError, RuntimeError):
        return False


def is_git_repository(path: Path) -> bool:
    """`git -C <path> rev-parse --git-dir` exits 0 (§10)."""
    if not Path(path).is_dir():
        return False
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "--git-dir"], capture_output=True,
                             text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def setup_plan(engine, path: Path):
    """The output of the engine's `sdd-setup.py detect --project <path>` (§12), or None when the
    engine has no such command yet or it fails. The script sits next to the engine's other files,
    so its path comes from a module the server loaded from the engine folder."""
    script = Path(engine.views.__file__).with_name(SETUP_SCRIPT)
    if not script.is_file():
        return None
    try:
        out = subprocess.run([sys.executable, str(script), "detect",
                              "--project", str(path)],
                             capture_output=True, text=True, timeout=DETECT_TIMEOUT)
        return json.loads(out.stdout) if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def project_view(engine, entry: dict) -> dict:
    """One row of `GET /api/projects`. `state` and `origin` come from the engine's `current_state()`
    (§4), `job` is the open Job as `/api/jobs` gives it; all three are null without an engine or
    for a missing project."""
    missing = is_missing(entry["path"])
    row = {"id": entry["id"], "path": entry["path"], "name": entry.get("name") or Path(entry["path"]).name,
           "missing": missing, "state": None, "origin": None, "job": None}
    if engine is None or missing:
        return row
    path = Path(entry["path"])
    try:
        state = engine.tasks.current_state(path)
        row["state"], row["origin"] = state.get("state"), state.get("origin")
    except Exception:  # noqa: BLE001 — one unreadable project must not break the list
        pass
    try:
        row["job"] = engine.views.open_job_view(path)
    except Exception:  # noqa: BLE001
        pass
    return row


def project_for(handler, pid: str):
    """The entry for `pid`, or None after answering 404 (unknown id) or 410 (missing project)."""
    entry = handler.projects.get(pid)
    if entry is None:
        handler.send_json({"error": "unknown project"}, 404)
        return None
    if is_missing(entry["path"]):
        handler.send_json({"error": MISSING}, 410)
        return None
    return entry


def _body(handler):
    """The JSON body as a dict, or None after answering 400."""
    try:
        data = handler.read_json_body()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        handler.send_json({"error": "the body must be a JSON object"}, 400)
        return None
    return data


# --- Routes --------------------------------------------------------------------------------------


def list_projects(handler, match):  # noqa: ARG001
    engine = handler.engine
    handler.send_json({"projects": [project_view(engine, e) for e in handler.projects.entries()]})


def add_project(handler, match):  # noqa: ARG001
    data = _body(handler)
    if data is None:
        return
    raw = data.get("path")
    if not isinstance(raw, str) or not raw.strip():
        handler.send_json({"error": "path required"}, 400)
        return
    try:
        path = Path(raw.strip()).expanduser().resolve()
    except (OSError, RuntimeError):
        handler.send_json({"error": NOT_GIT}, 422)
        return
    if not inside_home(path):
        handler.send_json({"error": OUTSIDE_HOME}, 403)
        return
    if not is_git_repository(path):
        handler.send_json({"error": NOT_GIT}, 422)
        return
    if is_missing(path):
        handler.send_json({"needs_setup": True, "path": str(path), "plan": setup_plan(handler.engine, path)})
        return
    handler.send_json(handler.projects.add(path))


def remove_project(handler, match):
    if _body(handler) is None:
        return
    if not handler.projects.remove(match.group(1)):
        handler.send_json({"error": "unknown project"}, 404)
        return
    handler.send_json({"ok": True})


def set_feature(handler, match):
    entry = project_for(handler, match.group(1))
    if entry is None:
        return
    data = _body(handler)
    if data is None:
        return
    feature = data.get("feature")
    if feature is not None:
        names = handler.engine.views.feature_names(Path(entry["path"]))
        if not isinstance(feature, str) or feature not in names:
            handler.send_json({"error": "unknown feature", "features": names}, 400)
            return
    handler.projects.set_feature(entry["id"], feature)
    handler.send_json({"ok": True, "feature": feature})


def _query_value(handler, name: str) -> str | None:
    values = (getattr(handler, "query", None) or {}).get(name)
    return values[0] if values else None


def folder_entries(folder: Path, hidden: bool, listed: set[str]) -> tuple[list[dict], bool]:
    """The sub-folders of `folder`, sorted by name, at most FS_LIST_LIMIT (§10); also whether the
    list was cut. Hidden folders (a leading `.`) only with `hidden`."""
    names = []
    try:
        with os.scandir(folder) as it:
            for item in it:
                if not hidden and item.name.startswith("."):
                    continue
                try:
                    if item.is_dir():  # follows symlinks, like the browser does
                        names.append(item.name)
                except OSError:
                    continue
    except OSError:
        return [], False
    names.sort()
    truncated = len(names) > FS_LIST_LIMIT
    entries = []
    for name in names[:FS_LIST_LIMIT]:
        child = folder / name
        try:
            resolved = str(child.resolve())
        except (OSError, RuntimeError):
            resolved = str(child)
        entries.append({"name": name, "path": str(child), "sdd": not is_missing(child),
                        "listed": resolved in listed})
    return entries, truncated


def list_folders(handler, match):  # noqa: ARG001
    """`GET /api/fs/list?path=<abs>&hidden=0|1` (§10). The Origin and Sec-Fetch-Site checks are
    app.py's (`GUARDED_GETS`)."""
    home = Path.home().resolve()
    raw = (_query_value(handler, "path") or "").strip()
    hidden = _query_value(handler, "hidden") == "1"
    try:
        folder = Path(raw).expanduser().resolve() if raw else home
    except (OSError, RuntimeError):
        handler.send_json({"error": OUTSIDE_HOME}, 403)
        return
    if not inside_home(folder):
        handler.send_json({"error": OUTSIDE_HOME}, 403)
        return
    if not folder.is_dir():
        handler.send_json({"error": "not a folder"}, 404)
        return
    listed = {e["path"] for e in handler.projects.entries()}
    entries, truncated = folder_entries(folder, hidden, listed)
    parent = str(folder.parent) if folder != home else None
    handler.send_json({"path": str(folder), "parent": parent, "entries": entries, "truncated": truncated})


# (method, pattern, function(handler, match)), added to app.py's route table.
ROUTES = (
    ("GET", r"/api/fs/list", list_folders),
    ("GET", r"/api/projects", list_projects),
    ("POST", r"/api/projects/add", add_project),
    ("POST", rf"/api/projects/({PROJECT_ID})/remove", remove_project),
    ("POST", rf"/api/p/({PROJECT_ID})/feature", set_feature),
)
