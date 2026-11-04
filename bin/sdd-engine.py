#!/usr/bin/env python3
"""The engine copy: a fixed, versioned folder holding the plugin's `bin/` and `templates/`, so the
server has a stable path that does not move with every plugin update (008 data-model §2; research R3).

The root is `$XDG_DATA_HOME/speckit-agents/engine/`, or `~/.local/share/speckit-agents/engine/` when
that variable is unset. It holds one folder per plugin version (`<version>/bin`, `<version>/templates`,
`<version>/VERSION.json`) and a `current` symlink to one of them. `install` runs under `fcntl.flock`
on `<root>/.lock`, and both the version folder and `current` are moved into place with
`os.replace()`, so a reader never sees half a copy.

Usage:
    sdd-engine.py install --from <plugin root>
    sdd-engine.py path

Each prints one JSON object with "contract". `install` exits 0, or 1 with {"error"} on a copy error;
`path` exits 0, or 2 with {"error": "no engine copy"}.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path

# CONTRACT lives in sdd-tasks.py (008 data-model §1). The hyphen in the file name prevents a plain import.
_spec = importlib.util.spec_from_file_location("sdd_tasks", Path(__file__).resolve().with_name("sdd-tasks.py"))
sdd_tasks = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_tasks)

CONTRACT = sdd_tasks.CONTRACT
KEEP = 5  # version folders kept after an install, by `copied_at`
EXCLUDED_BIN = ("sdd-dashboard.py",)  # top-level files of bin/ that are not part of the engine copy
NO_COPY = "no engine copy"
SKIPPED = "supervised run"


def engine_root() -> Path:
    """`$XDG_DATA_HOME/speckit-agents/engine`, or `~/.local/share/speckit-agents/engine`."""
    base = os.environ.get("XDG_DATA_HOME")
    data = Path(base) if base else Path.home() / ".local" / "share"
    return data / "speckit-agents" / "engine"


def _bin_files(plugin_root: Path) -> list[Path]:
    """Every file under `<plugin root>/bin`, outside `__pycache__`, sorted by its path inside bin/."""
    bin_dir = plugin_root / "bin"
    files = [p for p in bin_dir.rglob("*") if p.is_file() and "__pycache__" not in p.relative_to(bin_dir).parts]
    return sorted(files, key=lambda p: p.relative_to(bin_dir).as_posix())


def plugin_version(plugin_root: Path) -> str:
    """`version` from `<plugin root>/.claude-plugin/plugin.json`, or `0.0.0+<12 hex>` (sha256 over the
    sorted bin/ file contents) when the file, or the field, is missing or unreadable."""
    try:
        manifest = json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = None
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if isinstance(version, str) and version.strip():
        return version.strip()
    digest = hashlib.sha256()
    for path in _bin_files(plugin_root):
        digest.update(path.read_bytes())
    return "0.0.0+" + digest.hexdigest()[:12]


def read_version(folder: Path) -> dict | None:
    """`<folder>/VERSION.json`, or None when missing or malformed."""
    try:
        data = json.loads((folder / "VERSION.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _ignore(directory: str, names: list[str]) -> set[str]:
    """`shutil.copytree` ignore: `__pycache__` anywhere."""
    return {name for name in names if name == "__pycache__"}


def _ignore_bin_top(top: Path):
    """`shutil.copytree` ignore for bin/: `__pycache__` anywhere, and EXCLUDED_BIN at the top level."""
    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = _ignore(directory, names)
        if Path(directory).resolve() == top:
            skipped |= {name for name in names if name in EXCLUDED_BIN}
        return skipped
    return ignore


def _remove(path: Path):
    """Delete a folder, a file or a symlink, if there."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _prune(root: Path, keep_name: str):
    """Keep the KEEP newest version folders by `copied_at` (ties by the VERSION.json mtime), delete the
    older ones, and never delete `keep_name` (the one `current` points to)."""
    versions = []
    for child in root.iterdir():
        if (child.name == "current" or child.name.startswith(".")
                or ".tmp-" in child.name or ".old-" in child.name):
            continue
        if child.is_symlink() or not child.is_dir():
            continue
        info = read_version(child)
        if info is None:
            continue
        copied_at = info.get("copied_at")
        if not isinstance(copied_at, (int, float)) or isinstance(copied_at, bool):
            copied_at = 0
        try:
            mtime = (child / "VERSION.json").stat().st_mtime_ns
        except OSError:
            mtime = 0
        versions.append((copied_at, mtime, child))
    versions.sort(key=lambda item: (item[0], item[1]), reverse=True)
    for _, _, folder in versions[KEEP:]:
        if folder.name != keep_name:
            shutil.rmtree(folder, ignore_errors=True)


def _current_name(root: Path) -> str | None:
    """The folder name `current` points to, or None when there is no `current` symlink."""
    current = root / "current"
    if not current.is_symlink():
        return None
    return Path(os.readlink(current)).name


def install(plugin_root: Path) -> tuple[int, dict]:
    """Copy `<plugin root>/bin` and `templates` into the engine root and point `current` at the copy
    (008 data-model §2, steps 1 to 7). Returns the exit code and the object to print."""
    if os.environ.get("SDD_SUPERVISED") == "1":
        return 0, {"contract": CONTRACT, "installed": False, "skipped": SKIPPED}
    plugin_root = Path(plugin_root).resolve()
    source = str(plugin_root)
    if not (plugin_root / "bin").is_dir() or not (plugin_root / "templates").is_dir():
        return 1, {"contract": CONTRACT, "error": f"{source} has no bin/ and templates/"}
    root = engine_root()
    tmp = None
    try:
        version = plugin_version(plugin_root)
        root.mkdir(parents=True, exist_ok=True)
        with open(root / ".lock", "a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                current = root / "current"
                info = read_version(current) if current.is_symlink() else None
                if info is not None and info.get("plugin_version") == version and info.get("source") == source:
                    return 0, {"contract": CONTRACT, "installed": False, "path": str(current.resolve())}

                target = root / version
                tmp = root / f"{version}.tmp-{os.getpid()}"
                _remove(tmp)
                tmp.mkdir()
                shutil.copytree(plugin_root / "bin", tmp / "bin", ignore=_ignore_bin_top((plugin_root / "bin").resolve()))
                shutil.copytree(plugin_root / "templates", tmp / "templates", ignore=_ignore)
                stamp = {"plugin_version": version, "contract": CONTRACT,
                         "copied_at": int(time.time()), "source": source}
                (tmp / "VERSION.json").write_text(json.dumps(stamp) + "\n", encoding="utf-8")
                # A directory cannot be replaced onto a non-empty one, so an existing copy of this version
                # (a different source, or the same version re-installed) is moved aside and deleted after.
                old = root / f"{version}.old-{os.getpid()}"
                _remove(old)
                if target.exists() or target.is_symlink():
                    os.replace(target, old)
                os.replace(tmp, target)
                tmp = None
                _remove(old)

                link = root / f"current.tmp-{os.getpid()}"
                _remove(link)
                os.symlink(version, link)  # relative, so the engine root can move
                os.replace(link, current)

                _prune(root, version)
                return 0, {"contract": CONTRACT, "installed": True, "path": str(target.resolve()),
                           "plugin_version": version}
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except OSError as exc:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
        return 1, {"contract": CONTRACT, "error": f"copy failed: {exc}"}


def path_info() -> tuple[int, dict]:
    """The resolved `current` engine folder and its plugin version, or exit 2 when there is no copy."""
    current = engine_root() / "current"
    info = read_version(current) if current.is_symlink() else None
    if info is None or not (current / "bin").is_dir():
        return 2, {"contract": CONTRACT, "error": NO_COPY}
    return 0, {"contract": CONTRACT, "path": str(current.resolve()), "plugin_version": info.get("plugin_version")}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("install", help="copy bin/ and templates/ into the engine root")
    p.add_argument("--from", dest="plugin_root", type=Path, required=True, help="the plugin root")
    sub.add_parser("path", help="print the current engine folder")
    args = parser.parse_args(argv)

    code, out = install(args.plugin_root) if args.cmd == "install" else path_info()
    print(json.dumps(out))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
