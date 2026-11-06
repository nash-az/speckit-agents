#!/usr/bin/env python3
"""sdd-server: the standalone speckit-agents server, one per person (008 data-model §6; research R8,
R10). Start it from a terminal, never from a Claude session.

Usage:
    sdd-server.py [--port N] [--engine <engine folder>]
    sdd-server.py --install-command

- `--port`: default 8765, and a busy default tries the next 10 ports; `0` means any free port.
- `--engine`: the engine folder (one holding `bin/` and `templates/`), default
  `<engine root>/current`. The server starts without one and loads it once it appears (§6).
- `--install-command`: writes the `~/.local/bin/sdd-server` shim (research R10) and exits.

Exit codes: 0 after a clean stop, or when a server already runs (it prints its address); 1 when it
cannot listen or cannot write the shim; 3 inside a Claude session (`CLAUDECODE` set).
`server.json` in `$XDG_STATE_HOME/speckit-agents/server/` (`{"pid", "started", "port", "contract",
"engine_version"}`) is written under `flock` on `server.lock` and removed on a clean exit.
"""

from __future__ import annotations

import argparse
import fcntl
import http.client
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

REFUSAL = ("Start sdd-server from a terminal, not from a Claude session: runs it starts would inherit "
           "the session's sandbox.")
SHIM_MARK = "# written by sdd-server --install-command"
PORT_TRIES = 10  # a busy default port tries this many next ports (§6)


def load_app():
    """server/app.py, loaded from this folder."""
    spec = importlib.util.spec_from_file_location("sdd_server_app", HERE / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


app = load_app()


def process_started(pid: int) -> int | None:
    """The start time of process `pid` in epoch seconds, from `ps -o lstart=` (as the engine's
    `sdd-tasks.py` reads it, 008 data-model §3), or None. The server needs it before any engine is
    loaded, so it has its own copy."""
    env = dict(os.environ, LC_ALL="C")
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                             text=True, env=env, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return int(time.mktime(time.strptime(out.stdout.strip(), "%a %b %d %H:%M:%S %Y")))
    except (ValueError, OverflowError):
        return None


def pid_alive(pid) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def answers_as_server(port) -> bool:
    """True when 127.0.0.1:`port` answers `GET /api/server` with the `X-SDD-Contract` header."""
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        return False
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        conn.request("GET", "/api/server", headers={"Host": f"127.0.0.1:{port}"})
        resp = conn.getresponse()
        resp.read()
        return resp.getheader("X-SDD-Contract") is not None
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def running_server(info) -> bool:
    """True when `server.json` names a live pid whose start time matches `started` (within 1 s, as
    `session_alive` compares them), so a reused pid is not taken for the server. Where `ps` cannot
    run (a sandbox refuses it), the start time cannot be read; then the recorded port must answer as
    a speckit-agents server instead."""
    if not isinstance(info, dict) or not pid_alive(info.get("pid")):
        return False
    started = info.get("started")
    if not isinstance(started, (int, float)) or isinstance(started, bool):
        return False
    now_started = process_started(info["pid"])
    if now_started is None:
        return answers_as_server(info.get("port"))
    return abs(now_started - started) <= 1


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path: Path, data: dict):
    """Atomically: `<name>.tmp`, then `os.replace()` (data-model, top)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def bind(port: int | None, engines):
    """The listening server: `--port` exactly (0 = any free port), or the default and then the next
    PORT_TRIES ports when it is busy. Raises OSError when none is free."""
    if port is not None:
        return app.make_server(port, engines)
    error = None
    for candidate in range(app.DEFAULT_PORT, app.DEFAULT_PORT + PORT_TRIES + 1):
        try:
            return app.make_server(candidate, engines)
        except OSError as exc:
            error = exc
    raise error


def install_command() -> int:
    """Write `~/.local/bin/sdd-server` (research R10). Its first line is SHIM_MARK; a file there that
    does not start with it is never overwritten."""
    shim = Path.home() / ".local/bin/sdd-server"  # one string: "bin" alone is reserved for the engine
    if shim.exists() or shim.is_symlink():
        try:
            first = shim.read_text(encoding="utf-8").splitlines()[:1]
        except (OSError, UnicodeDecodeError):
            first = []
        if first != [SHIM_MARK]:
            print(f"{shim} exists and was not written by sdd-server; leaving it alone", file=sys.stderr)
            return 1
    script = Path(__file__).resolve()
    text = f'{SHIM_MARK}\nexec "{sys.executable}" "{script}" "$@"\n'
    try:
        shim.parent.mkdir(parents=True, exist_ok=True)
        tmp = shim.with_name(shim.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.chmod(0o755)
        os.replace(tmp, shim)
    except OSError as exc:
        print(f"cannot write {shim}: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {shim}")
    return 0


def _stop(signum, frame):  # noqa: ARG001
    raise KeyboardInterrupt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="speckit-agents server (one per person)")
    parser.add_argument("--port", type=int, default=None,
                        help=f"port to listen on (default {app.DEFAULT_PORT}, then the next {PORT_TRIES}; "
                             "0 = any free port)")
    parser.add_argument("--engine", type=Path, default=None,
                        help="engine folder holding bin/ and templates/ (default <engine root>/current)")
    parser.add_argument("--install-command", action="store_true",
                        help="write ~/.local/bin/sdd-server and exit")
    args = parser.parse_args(argv)

    if args.install_command:
        return install_command()
    if os.environ.get("CLAUDECODE"):
        print(REFUSAL, file=sys.stderr, flush=True)
        return 3
    app.scrub_environ()  # what the server and the engine start never sees a session's variables

    engines = app.EngineHolder(args.engine)
    engines.refresh()

    state = app.server_state_dir()
    state.mkdir(parents=True, exist_ok=True)
    info_file = state / "server.json"
    with open(state / "server.lock", "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            info = read_json(info_file)
            if running_server(info):
                print(f"speckit-agents server already running at http://127.0.0.1:{info.get('port')}",
                      flush=True)
                return 0
            try:
                server = bind(args.port, engines)
            except OSError as exc:
                print(f"cannot listen: {exc}", file=sys.stderr, flush=True)
                return 1
            port = server.server_address[1]
            engine = engines.get()
            me = {"pid": os.getpid(), "started": process_started(os.getpid()) or int(time.time()),
                  "port": port, "contract": app.CONTRACT,
                  "engine_version": engine.plugin_version if engine else None}
            write_json(info_file, me)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)

    stop = threading.Event()
    threading.Thread(target=engines.watch, args=(stop,), daemon=True, name="engine-reload").start()
    signal.signal(signal.SIGTERM, _stop)
    print(f"speckit-agents server on http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        with open(state / "server.lock", "a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                if (read_json(info_file) or {}).get("pid") == os.getpid():
                    info_file.unlink(missing_ok=True)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        print("stopping speckit-agents server", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
