#!/usr/bin/env python3
"""Run controls: who may stop, resume, hold, release or take over a run, and the one writer of
`control.json` (008 data-model §5; 003 data-model §12.1 to §12.3).

`control.json` lives in the supervisor's state directory, outside the project. Every write goes
through `write_control()`, which holds `fcntl.flock` on `<state dir>/control.lock` across its check
that no request is pending and the replace, so several processes (the dashboard, the server, this
command line, a supervisor's own stop-by request) never overwrite a pending request. Only the
supervisor reads and deletes it (`read_control()`).

Usage:
    sdd-control.py controls --project <dir>
    sdd-control.py stop [--now] --project <dir>
    sdd-control.py resume | hold | release --project <dir>

Each prints one JSON object with "contract". A request prints {"ok": true, "id"} and exits 0, or
{"error"} with the dashboard route's 409 text and exits 1.
"""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import sys
import time
import uuid
from pathlib import Path

# The run state and the liveness probes live in sdd-tasks.py (008 data-model §3, §4). The hyphen in
# the file name prevents a plain import.
_spec = importlib.util.spec_from_file_location("sdd_tasks", Path(__file__).resolve().with_name("sdd-tasks.py"))
sdd_tasks = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_tasks)

CONTRACT = sdd_tasks.CONTRACT
COMMANDS = ("stop", "resume", "hold", "release")
STOP_MODES = ("after-task", "now")

# The reasons of the §5 table, exact.
IN_SESSION = "running in a Claude session"
SESSION_ENDED = "the session that started this run has ended; take it over to finish it"
SESSION_UNKNOWN = "the session that started this run is unknown; confirm it is closed to take it over"
SESSION_IDLE = ("the session is still open but its run has done nothing for 30 minutes; "
                "confirm the session no longer runs it to take it over")
SUPERVISOR_STOPPED = "the supervisor stopped; Start run resumes it"
WATCHING = "watching the session's run; takes over if the session ends"
PENDING = "a request is already pending"
NOT_AVAILABLE = "not available now"


def _reset_time(status) -> str:
    resets_at = status.get("resets_at")
    if isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool):
        return time.strftime("%H:%M", time.localtime(resets_at))
    return "?"


def read_attach(project: Path) -> dict | None:
    """`attach.json` (008 data-model §7) in the state directory, or None when missing or malformed."""
    try:
        data = json.loads((sdd_tasks.state_dir(project) / "attach.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def attachment_alive(project: Path) -> bool:
    """True when `attach.json` names a watcher pid that is alive (the same probe as a supervisor's)."""
    attach = read_attach(project)
    return attach is not None and sdd_tasks.supervisor_alive(attach)


def controls(project: Path) -> dict:
    """The `controls` object (008 data-model §5): which requests are possible now, and why not.

    A live supervisor (its `status.json` state not `stopped` or `done`) follows the 003 §12.3 table.
    Otherwise the run decides: no run → nothing and no reason; a run started by a supervisor that
    is gone → "Start run resumes it"; an in-session run → take over (`adopt`), with `confirm_closed`
    when the session is unknown or idle, unless a live watcher is already attached. An in-session
    run whose session is unknown but which is not stale reads as running in a session."""
    project = Path(project)
    out = {"stop": False, "resume": False, "hold": False, "release": False,
           "adopt": False, "confirm_closed": False, "reason": None}
    status = sdd_tasks.get_status(project)
    if (isinstance(status, dict) and sdd_tasks.supervisor_alive(status)
            and status.get("state") not in ("stopped", "done")):
        state = status.get("state")
        if state == "running":
            out["stop"] = True
        elif state == "stopping":
            out["stop"] = True  # only `mode: now`; an after-task stop is already under way
            out["reason"] = "stopping after the current task"
        elif state == "held":
            out["resume"] = True
        elif state == "waiting":
            if status.get("keep_paused") is True:
                out["release"] = True
                out["reason"] = f"will stay paused after the reset at {_reset_time(status)}"
            else:
                out["hold"] = True
                out["reason"] = f"waiting for the usage window to reset at {_reset_time(status)}"
        return out

    run = sdd_tasks.current_state(project)
    if run["state"] == "none":
        return out
    if run["origin"] != "session":
        out["reason"] = SUPERVISOR_STOPPED
        return out
    if attachment_alive(project):
        out["reason"] = WATCHING
        return out
    out["adopt"] = True
    alive, stale = run["session_alive"], run["state"] == "stale"
    if alive is False:
        out["reason"] = SESSION_ENDED
    elif stale:
        out["confirm_closed"] = True
        out["reason"] = SESSION_IDLE if alive else SESSION_UNKNOWN
    else:
        out["reason"] = IN_SESSION
    return out


def write_control(logs: Path, command: str, mode: str | None = None, request_id: str | None = None):
    """One request for the supervisor in `<logs>/control.json`, written to `control.json.tmp` and moved
    into place, so the supervisor never reads half a file. Holds `fcntl.flock` on `<logs>/control.lock`
    across the pending check and the replace. Returns the request id, or False when a request is already
    pending (it is never overwritten before the supervisor consumes it)."""
    logs = Path(logs)
    with open(logs / "control.lock", "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            target = logs / "control.json"
            if target.exists():
                return False
            control = {"contract": CONTRACT, "id": request_id or uuid.uuid4().hex, "command": command}
            if command == "stop":
                control["mode"] = mode or "after-task"
            control["requested_at"] = int(time.time())
            tmp = logs / "control.json.tmp"
            tmp.write_text(json.dumps(control) + "\n", encoding="utf-8")
            os.replace(tmp, target)
            return control["id"]
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def send_control(logs: Path, request: dict):
    """A request built by the supervisor itself (a Job's stop-by time), through `write_control()`.
    Returns its id, or False when another request is pending."""
    return write_control(logs, request["command"], request.get("mode"), request.get("id"))


def read_control(logs: Path) -> dict | None:
    """The pending request in <state dir>/control.json (003 data-model §12.1), or None. The supervisor
    owns deletion: the file is removed whenever a request is returned."""
    path = Path(logs) / "control.json"
    try:
        control = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(control, dict):
        return None
    try:
        path.unlink()
    except OSError:
        pass
    return control


def request(project: Path, command: str, mode: str | None = None) -> dict:
    """The checks of `POST /api/run/<command>` (003 data-model §12.3) and the write: `{"ok": true, "id"}`,
    or `{"error"}` with the route's 409 text. `mode` must already be one of STOP_MODES for `stop`."""
    project = Path(project)
    available = controls(project)
    allowed = available.get(command) is True
    status = sdd_tasks.get_status(project)
    state = status.get("state") if isinstance(status, dict) else None
    if command == "stop" and state == "stopping" and mode != "now":
        allowed = False  # an after-task stop is already under way; only `now` is left
    if not allowed:
        return {"error": available["reason"] or NOT_AVAILABLE}
    request_id = write_control(sdd_tasks.state_dir(project), command, mode)
    if not request_id:
        return {"error": PENDING}
    return {"ok": True, "id": request_id}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("controls", *COMMANDS):
        p = sub.add_parser(name)
        p.add_argument("--project", type=Path, required=True)
        if name == "stop":
            p.add_argument("--now", action="store_true", help="stop at once, not after the current task")
    args = parser.parse_args(argv)

    if args.cmd == "controls":
        print(json.dumps({"contract": CONTRACT, **controls(args.project)}))
        return 0
    mode = ("now" if args.now else "after-task") if args.cmd == "stop" else None
    result = request(args.project, args.cmd, mode)
    print(json.dumps({"contract": CONTRACT, **result}))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
