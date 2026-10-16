#!/usr/bin/env python3
"""Run /sdd-run unattended across usage limits: pause before the limit, resume after the reset.

  sdd-supervise.py [--project DIR] [--threshold 0.95] [--run-args "--phases 15"]
                   [--max-wait-hours 12] [--max-cycles 20]

Each cycle starts a headless `claude -p "/sdd-run --yes …"` and reads its stream-json output.
Claude Code reports the account's usage there as `rate_limit_event` lines (five-hour and
seven-day windows, with `utilization` from 0.90 up and `resetsAt`). When a window reaches the
threshold, the supervisor waits for the next agent to finish (its result is then in the workflow
journal, so nothing it did is lost), stops the run, sleeps until the window resets, and starts
`/sdd-run` again. That run reads the journal and resumes: finished steps are skipped and a step
that was cut off continues from the edits in the tree. If the limit is hit before a safe point,
the run is stopped at once; at most the step in flight is redone.

It ends when a run finishes without being cut off, when a reset is further away than
--max-wait-hours, or after --max-cycles runs. Progress lines go to stdout (one line per event),
and the current state to <state dir>/status.json. Only the standard library is used.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

TRANSCRIPT_RE = re.compile(r"Transcript dir: (\S+)")
SAFE_GRACE_S = 20     # after SIGINT, before SIGTERM
KILL_GRACE_S = 10     # after SIGTERM, before SIGKILL
RESET_MARGIN_S = 120  # start again this long after the window resets


def say(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} supervise: {message}", flush=True)


def state_dir(project: Path) -> Path:
    """Logs live outside the project, so a run's clean-tree check never sees them."""
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(project.resolve())).strip("-")
    path = base / "speckit-agents" / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


class Usage:
    """The latest rate_limit_event: per window utilization (None = below the warning level)."""

    def __init__(self) -> None:
        self.status = "allowed"
        self.windows: dict[str, dict] = {}
        self.limit_type: str | None = None

    def update(self, info: dict) -> None:
        self.status = info.get("status", self.status)
        self.limit_type = info.get("rateLimitType", self.limit_type)
        for name, window in (info.get("unifiedWindows") or {}).items():
            self.windows[name] = {"utilization": window.get("utilization"), "resets_at": window.get("resetsAt")}
        if self.limit_type and (info.get("utilization") is not None or info.get("resetsAt")):
            window = self.windows.setdefault(self.limit_type, {})
            if info.get("utilization") is not None:
                window["utilization"] = info["utilization"]
            if info.get("resetsAt"):
                window["resets_at"] = info["resetsAt"]

    def highest(self) -> tuple[str | None, float, int | None]:
        best = (None, 0.0, None)
        for name, window in self.windows.items():
            u = window.get("utilization") or 0.0
            if u >= best[1]:
                best = (name, u, window.get("resets_at"))
        return best

    def reset_of(self, name: str | None) -> int | None:
        return (self.windows.get(name or "") or {}).get("resets_at")


class Cycle:
    """One headless /sdd-run, watched until it ends or is stopped."""

    def __init__(self, args: argparse.Namespace, log_path: Path) -> None:
        self.args = args
        self.log_path = log_path
        self.usage = Usage()
        self.workflow_dir: Path | None = None
        self.result_seen = False
        self.stop_reason: str | None = None
        self.lock = threading.Lock()

    def command(self) -> list[str]:
        prompt = f"/sdd-run --yes {self.args.run_args}".strip()
        return [self.args.claude, "-p", prompt, "--permission-mode", "dontAsk", "--allowedTools", "Skill",
                "--output-format", "stream-json", "--verbose"]

    def read_stream(self, stream) -> None:  # noqa: ANN001
        with self.log_path.open("a", encoding="utf-8") as log:
            for line in stream:
                log.write(line)
                log.flush()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                with self.lock:
                    self.on_event(event, line)

    def on_event(self, event: dict, raw: str) -> None:
        kind = event.get("type")
        if kind == "rate_limit_event":
            before = self.usage.highest()[1]
            self.usage.update(event.get("rate_limit_info") or {})
            name, u, _ = self.usage.highest()
            if self.usage.status == "rejected":
                say(f"usage limit reached ({self.usage.limit_type})")
            elif u > before and u >= 0.9:
                say(f"usage {name} {u:.0%}")
        elif kind == "result":
            self.result_seen = True
        if self.workflow_dir is None and "Transcript dir:" in raw:
            match = TRANSCRIPT_RE.search(raw.replace("\\n", "\n"))
            if match:
                self.workflow_dir = Path(match.group(1))
                say(f"workflow {self.workflow_dir.name}")

    def journal_counts(self) -> tuple[int, int]:
        """(agents started, agents finished) in the workflow journal so far."""
        if not self.workflow_dir:
            return 0, 0
        started = finished = 0
        try:
            for line in (self.workflow_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines():
                if '"type": "started"' in line or '"type":"started"' in line:
                    started += 1
                elif '"type": "result"' in line or '"type":"result"' in line or '"type": "failed"' in line:
                    finished += 1
        except OSError:
            pass
        return started, finished

    def run(self) -> str:
        """Run until the process ends. Returns 'finished', 'paused' or 'limit'."""
        env = dict(os.environ, CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS="0")
        proc = subprocess.Popen(self.command(), cwd=self.args.project, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        reader = threading.Thread(target=self.read_stream, args=(proc.stdout,), daemon=True)
        reader.start()
        pause_at: int | None = None  # journal 'finished' count when the pause was asked for
        while proc.poll() is None:
            time.sleep(self.args.poll)
            with self.lock:
                name, u, _ = self.usage.highest()
                rejected = self.usage.status == "rejected"
            started, finished = self.journal_counts()
            if rejected:
                self.stop_reason = "limit"
                say("stopping now: the limit was hit before a safe point")
                stop(proc)
                break
            if pause_at is None and u >= self.args.threshold:
                pause_at = finished
                say(f"{name} at {u:.0%}: pausing after the next agent finishes")
            if self.args.pause_after and finished >= self.args.pause_after:
                self.args.pause_after = 0  # once, for end-to-end tests of the resume path
                self.stop_reason = "paused"
                say(f"test pause after {finished} agents: stopping the run")
                stop(proc)
                break
            if pause_at is not None:
                idle = started == finished
                if finished > pause_at or idle or u >= self.args.hard_stop:
                    self.stop_reason = "paused"
                    say("safe point: stopping the run" + (" (no agent running)" if idle else ""))
                    stop(proc)
                    break
        proc.wait()
        reader.join(timeout=5)
        if self.stop_reason:
            return self.stop_reason
        # It ended by itself: finished, or killed by the limit (no supervisor stop was in time).
        if self.usage.status == "rejected":
            return "limit"
        return "finished"


def stop(proc: subprocess.Popen) -> None:
    """SIGINT ends the turn cleanly; SIGTERM, then SIGKILL, if it doesn't exit."""
    for sig, grace in ((signal.SIGINT, SAFE_GRACE_S), (signal.SIGTERM, KILL_GRACE_S), (signal.SIGKILL, 5)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def write_status(path: Path, **fields) -> None:
    fields["updated"] = int(time.time())
    path.write_text(json.dumps(fields, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--threshold", type=float, default=0.95, help="pause when a window reaches this share (0–1)")
    parser.add_argument("--hard-stop", type=float, default=0.99, help="stop at once at this share, safe point or not")
    parser.add_argument("--run-args", default="", help='extra /sdd-run arguments, e.g. "--phases 15"')
    parser.add_argument("--max-wait-hours", type=float, default=12.0)
    parser.add_argument("--max-cycles", type=int, default=20)
    parser.add_argument("--claude", default="claude", help=argparse.SUPPRESS)
    parser.add_argument("--poll", type=float, default=2.0, help=argparse.SUPPRESS)
    parser.add_argument("--no-sleep", action="store_true", help=argparse.SUPPRESS)  # tests
    parser.add_argument("--pause-after", type=int, default=0, help=argparse.SUPPRESS)  # e2e tests
    args = parser.parse_args(argv)
    args.project = args.project.resolve()
    if not (args.project / ".sdd" / "config.toml").exists():
        say(f"{args.project} has no .sdd/config.toml: run /sdd-init there first")
        return 2

    logs = state_dir(args.project)
    status = logs / "status.json"
    say(f"project {args.project}; logs in {logs}")
    for cycle_no in range(1, args.max_cycles + 1):
        log_path = logs / f"{time.strftime('%Y%m%d-%H%M%S')}-cycle{cycle_no}.jsonl"
        say(f"cycle {cycle_no}: starting /sdd-run")
        cycle = Cycle(args, log_path)
        write_status(status, state="running", cycle=cycle_no, stream=str(log_path))
        outcome = cycle.run()
        name, u, _ = cycle.usage.highest()
        if outcome == "finished" and (args.project / ".sdd" / "run.json").exists():
            say(f"cycle {cycle_no}: the run ended but .sdd/run.json is still there, so it was cut off by "
                "something other than the usage limit: read its report, then start /sdd-run or this script again to resume")
            write_status(status, state="stopped", reason="interrupted, not by the usage limit", cycle=cycle_no)
            return 1
        if outcome == "finished":
            say(f"cycle {cycle_no}: the run finished" + ("" if cycle.result_seen else " (no result line: check the log)"))
            write_status(status, state="done", cycle=cycle_no, stream=str(log_path))
            return 0 if cycle.result_seen else 1
        window = cycle.usage.limit_type if outcome == "limit" else name
        resets_at = cycle.usage.reset_of(window) or cycle.usage.highest()[2]
        if not resets_at and args.no_sleep:
            resets_at = int(time.time())
        if not resets_at:
            say("stopped, but no reset time is known: start again by hand")
            write_status(status, state="stopped", reason="no reset time", cycle=cycle_no)
            return 1
        wait = max(0, resets_at + RESET_MARGIN_S - time.time())
        if wait > args.max_wait_hours * 3600:
            say(f"{window} resets at {time.strftime('%a %H:%M', time.localtime(resets_at))}, beyond --max-wait-hours: stopping")
            write_status(status, state="stopped", reason=f"{window} resets too late", resets_at=resets_at, cycle=cycle_no)
            return 3
        say(f"{outcome}: waiting until {time.strftime('%H:%M', time.localtime(resets_at + RESET_MARGIN_S))} for the {window} window to reset")
        write_status(status, state="waiting", window=window, resets_at=resets_at, cycle=cycle_no)
        if not args.no_sleep:
            time.sleep(wait)
    say(f"stopping after {args.max_cycles} cycles")
    write_status(status, state="stopped", reason="max cycles")
    return 4


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
