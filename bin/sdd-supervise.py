#!/usr/bin/env python3
"""Run /sdd-run unattended across usage limits: pause before the limit, resume after the reset.

  sdd-supervise.py [--project DIR] [--threshold five_hour=0.95,seven_day=0.99] [--run-args "--phases 15"]
                   [--max-wait-hours 12] [--max-cycles 20] [--profile NAME]

Each cycle starts a headless `claude -p "/sdd-run --yes …"` and reads its stream-json output.
Claude Code reports the account's usage there as `rate_limit_event` lines (five-hour and
seven-day windows, with `utilization` from 0.90 up and `resetsAt`). When a window reaches the
threshold (its own per window: by default 95 % for the five-hour window, 99 % for the seven-day
one), the supervisor waits for the next agent to finish (its result is then in the workflow
journal, so nothing it did is lost), stops the run, sleeps until the window resets, and starts
`/sdd-run` again. That run reads the journal and resumes: finished steps are skipped and a step
that was cut off continues from the edits in the tree. If the limit is hit before a safe point,
the run is stopped at once; at most the step in flight is redone.

It ends when a run finishes without being cut off, when a reset is further away than
--max-wait-hours, or after --max-cycles runs. --profile NAME runs `claude` with the model and
environment of [profiles.NAME] in the project's .sdd/config.toml and passes the name on to
/sdd-run, which records it in the report. Progress lines go to stdout (one line per event),
and the current state to <state dir>/status.json. Only the standard library is used.

The dashboard can stop and resume a supervised run (data-model §12): it writes one request to
<state dir>/control.json, which this script reads and deletes. `stop` (after the current task, or
now) ends the cycle and holds until `resume`; while waiting for a usage window, `hold` keeps the
run paused after the reset and `release` undoes that. Held longer than --max-wait-hours ends it.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# The project config (run profiles) is read with the task helper's own loader (data-model §9).
_spec = importlib.util.spec_from_file_location("sdd_tasks", Path(__file__).with_name("sdd-tasks.py"))
sdd_tasks = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_tasks)

TRANSCRIPT_RE = re.compile(r"Transcript dir: (\S+)")
SAFE_GRACE_S = 20     # after SIGINT, before SIGTERM
KILL_GRACE_S = 10     # after SIGTERM, before SIGKILL
RESET_MARGIN_S = 120  # start again this long after the window resets
DEFAULT_THRESHOLDS = "five_hour=0.95,seven_day=0.99"  # the user's choice, 2026-10-18


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

    def over(self, thresholds: dict[str, float]) -> tuple[str, float] | None:
        """The window furthest past its own threshold ('*' for the others), or None."""
        best = None
        for name, window in self.windows.items():
            u = window.get("utilization") or 0.0
            margin = u - thresholds.get(name, thresholds["*"])
            if margin >= -1e-9 and (best is None or margin > best[2]):
                best = (name, u, margin)
        return best and best[:2]

    def reset_of(self, name: str | None) -> int | None:
        return (self.windows.get(name or "") or {}).get("resets_at")


class Cycle:
    """One headless /sdd-run, watched until it ends or is stopped."""

    def __init__(self, args: argparse.Namespace, log_path: Path, status: Path | None = None, cycle_no: int = 1) -> None:
        self.args = args
        self.log_path = log_path
        self.status = status
        self.cycle_no = cycle_no
        self.last_step: dict[str, int] = {}  # window → last 5 % step reported
        self.usage = Usage()
        self.workflow_dir: Path | None = None
        self.result_seen = False
        self.stop_reason: str | None = None
        self.pause_window: str | None = None  # the window that crossed the threshold
        self.operator = False  # an operator asked to stop after the current task (data-model §12.2)
        self.lock = threading.Lock()

    def command(self) -> list[str]:
        profile_arg = f"--profile {self.args.profile} " if self.args.profile else ""
        prompt = f"/sdd-run --yes {profile_arg}{self.args.run_args}".strip()
        model = ["--model", self.args.profile_config["model"]] if self.args.profile else []
        return [self.args.claude, "-p", prompt, *model, "--permission-mode", "dontAsk", "--allowedTools", "Skill",
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
            before = {k: v.get("utilization") for k, v in self.usage.windows.items()}
            self.usage.update(event.get("rate_limit_info") or {})
            if self.usage.status == "rejected":
                say(f"usage limit reached ({self.usage.limit_type})")
            for name, window in self.usage.windows.items():
                u = window.get("utilization")
                if u is None:
                    continue
                step = int(round(u * 100)) if u >= 0.9 else int(u * 100) // 5 * 5
                if step != self.last_step.get(name) and (u >= 0.9 or step > self.last_step.get(name, -1)):
                    self.last_step[name] = step
                    resets = window.get("resets_at")
                    say(f"usage {name} {u:.0%}" + (f", resets {time.strftime('%a %H:%M', time.localtime(resets))}" if resets else ""))
            if self.status and before != {k: v.get("utilization") for k, v in self.usage.windows.items()}:
                extra = {"state": "stopping", "mode": "after-task"} if self.operator else {"state": "running"}
                write_status(self.status, **extra, cycle=self.cycle_no, stream=str(self.log_path),
                             usage=self.usage.windows, threshold=self.args.threshold)
        elif kind == "result":
            self.result_seen = True
        # Follow the newest workflow: the main loop may launch it again (run #20, 2026-10-18), and the
        # first journal then never shows the safe point.
        if "Transcript dir:" in raw:
            match = TRANSCRIPT_RE.search(raw.replace("\\n", "\n"))
            if match and Path(match.group(1)) != self.workflow_dir:
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
        """Run until the process ends. Returns 'finished', 'paused', 'limit' or 'operator'."""
        env = dict(os.environ, **self.args.profile_config.get("env", {}), CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS="0")
        proc = subprocess.Popen(self.command(), cwd=self.args.project, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        reader = threading.Thread(target=self.read_stream, args=(proc.stdout,), daemon=True)
        reader.start()
        # The run has its own process group (so it can be stopped cleanly); if the supervisor is
        # terminated, stop the run too instead of leaving it unsupervised. Its journal keeps the
        # finished steps, so the next start resumes it.
        def on_terminate(signum, frame):  # noqa: ANN001, ARG001
            say(f"supervisor got signal {signum}: stopping the run first")
            stop(proc)
            sys.exit(128 + signum)
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            signal.signal(sig, on_terminate)
        pause_at: int | None = None  # journal 'finished' count when the pause was asked for
        while proc.poll() is None:
            time.sleep(self.args.poll)
            with self.lock:
                over = self.usage.over(self.args.threshold)
                hard = self.usage.over(self.args.hard_stops)
                rejected = self.usage.status == "rejected"
            started, finished = self.journal_counts()
            if rejected:
                self.stop_reason = "limit"
                say("stopping now: the limit was hit before a safe point")
                stop(proc)
                break
            if pause_at is None and over:
                name, u = over
                pause_at = finished
                self.pause_window = name
                say(f"{name} at {u:.0%}: pausing after the next agent finishes")
            # Operator requests from the dashboard (data-model §12.2). Anything but `stop` is
            # meaningless while a run is going; read_control() has already deleted it.
            control = read_control(self.log_path.parent)
            if control and control.get("command") == "stop":
                if control.get("mode") == "now":
                    self.stop_reason = "operator"
                    say("stop requested by the operator: stopping now")
                    stop(proc)
                    break
                if not self.operator:
                    self.operator = True
                    if pause_at is None:
                        pause_at = finished
                    say("stop requested by the operator: stopping after the current agent finishes")
                    if self.status:
                        with self.lock:
                            write_status(self.status, state="stopping", mode="after-task", cycle=self.cycle_no,
                                         stream=str(self.log_path), usage=self.usage.windows,
                                         threshold=self.args.threshold)
            elif control:
                say(f"ignoring operator request {control.get('command')!r} while the run is going")
            if self.args.pause_after and finished >= self.args.pause_after:
                self.args.pause_after = 0  # once, for end-to-end tests of the resume path
                self.stop_reason = "paused"
                say(f"test pause after {finished} agents: stopping the run")
                stop(proc)
                break
            if pause_at is not None:
                idle = started == finished
                if finished > pause_at or idle or hard:
                    self.stop_reason = "operator" if self.operator else "paused"
                    if finished > pause_at or idle:
                        say("safe point: stopping the run" + (" (no agent running)" if idle else ""))
                    else:
                        name, u = hard
                        say(f"{name} at {u:.0%} before the agent finished: stopping now; its edits stay in the tree "
                            "and the next run continues them")
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
        except (ProcessLookupError, PermissionError):
            return  # already gone (macOS answers EPERM for a group whose leader has exited)
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def sleep_until(target: float) -> None:
    """Wait for a wall-clock time. time.sleep() pauses while the computer sleeps (macOS), so one
    long sleep would overshoot the reset by as long as the lid was closed: sleep in short steps."""
    while (left := target - time.time()) > 0:
        time.sleep(min(left, 30))


def write_status(path: Path, **fields) -> None:
    fields["pid"] = os.getpid()  # the dashboard checks that the supervisor is alive (data-model §12.1)
    fields["updated"] = int(time.time())
    path.write_text(json.dumps(fields, indent=2) + "\n", encoding="utf-8")


def read_control(logs: Path) -> dict | None:
    """The dashboard's pending request in <state dir>/control.json (data-model §12.1), or None.
    The supervisor owns deletion: the file is removed whenever a request is returned."""
    path = logs / "control.json"
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


def hold(args: argparse.Namespace, logs: Path, status: Path, cycle_no: int, reason: str) -> bool:
    """Held after an operator stop or a kept pause: True when Resume comes, False after --max-wait-hours."""
    write_status(status, state="held", reason=reason, cycle=cycle_no)
    say(f"held ({reason}): waiting for Resume from the dashboard")
    deadline = time.time() + args.max_wait_hours * 3600
    while time.time() < deadline:
        time.sleep(max(0.0, min(args.poll, deadline - time.time())))
        control = read_control(logs)
        if not control:
            continue
        if control.get("command") == "resume":
            say("resume requested by the operator")
            return True
        say(f"ignoring operator request {control.get('command')!r} while held")
    return False


def wait_for_reset(args: argparse.Namespace, logs: Path, status: Path, target: float, fields: dict) -> bool:
    """Sleep until the usage window resets, in steps of --poll, reading operator requests.
    Returns keep_paused: True when the operator asked to stay paused after the reset."""
    keep_paused = False
    while (left := target - time.time()) > 0:
        time.sleep(min(left, args.poll))
        control = read_control(logs)
        if not control:
            continue
        command = control.get("command")
        if command in ("hold", "release") and keep_paused != (command == "hold"):
            keep_paused = command == "hold"
            say("the operator asked to stay paused after the reset" if keep_paused
                else "the operator let the run resume after the reset")
            write_status(status, **fields, keep_paused=keep_paused)
        elif command not in ("hold", "release"):
            say(f"ignoring operator request {command!r} while waiting for the usage window")
    return keep_paused


def thresholds(text: str) -> dict[str, float]:
    """'0.95' for every window, or 'five_hour=0.95,seven_day=0.99' (unnamed windows: 0.95)."""
    out = {"*": 0.95}
    for item in filter(None, (part.strip() for part in text.split(","))):
        name, _, share = item.rpartition("=")
        if name:
            out[name] = float(share)
        else:
            out = {"*": float(share)}
    return out


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--threshold", type=thresholds, default=DEFAULT_THRESHOLDS,
                        help="pause when a window reaches this share (0–1): one for all, or per window, "
                             f"e.g. {DEFAULT_THRESHOLDS}")
    parser.add_argument("--hard-stop", type=float, default=None,
                        help="stop at once at this utilization (default: each window's threshold + 0.01)")
    parser.add_argument("--run-args", default="", help='extra /sdd-run arguments, e.g. "--phases 15"')
    parser.add_argument("--max-wait-hours", type=float, default=12.0)
    parser.add_argument("--max-cycles", type=int, default=20)
    parser.add_argument("--profile", default="",
                        help="run with [profiles.NAME] from .sdd/config.toml: its model and env for claude")
    parser.add_argument("--claude", default="claude", help=argparse.SUPPRESS)
    parser.add_argument("--poll", type=float, default=2.0, help=argparse.SUPPRESS)
    parser.add_argument("--no-sleep", action="store_true", help=argparse.SUPPRESS)  # tests
    parser.add_argument("--pause-after", type=int, default=0, help=argparse.SUPPRESS)  # e2e tests
    args = parser.parse_args(argv)
    args.project = args.project.resolve()
    # Per window (data-model §10): an explicit --hard-stop for all, else each window's threshold + 1 point.
    args.hard_stops = {name: min(1.0, args.hard_stop if args.hard_stop is not None else round(t + 0.01, 2))
                       for name, t in args.threshold.items()}
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if not (args.project / ".sdd" / "config.toml").exists():
        say(f"{args.project} has no .sdd/config.toml: run /sdd-init there first")
        return 2
    args.profile_config = {}
    if args.profile:
        config = sdd_tasks.load_config(args.project / ".sdd" / "config.toml")
        errors = sdd_tasks.config_errors(config)
        for error in errors:
            say(f"config error: {error}")
        if errors:
            return 2
        profile = (config.get("profiles") or {}).get(args.profile)
        if not isinstance(profile, dict):
            say(f"unknown profile {args.profile}")
            return 2
        args.profile_config = profile
        say(f"profile {args.profile}: model {profile['model']}")

    logs = state_dir(args.project)
    status = logs / "status.json"
    say(f"project {args.project}; logs in {logs}")
    # A request left over from an earlier supervisor is not meant for this one (data-model §12.2).
    try:
        (logs / "control.json").unlink()
        say("deleted a leftover control.json")
    except FileNotFoundError:
        pass
    # A supervisor restarted while an earlier one was waiting for a reset keeps waiting.
    try:
        last = json.loads(status.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    if last.get("state") == "waiting" and (last.get("resets_at") or 0) + RESET_MARGIN_S > time.time():
        target = last["resets_at"] + RESET_MARGIN_S
        say(f"an earlier supervisor was waiting for the {last.get('window')} window: waiting until "
            f"{time.strftime('%H:%M', time.localtime(target))}")
        if not args.no_sleep:
            sleep_until(target)
    for cycle_no in range(1, args.max_cycles + 1):
        log_path = logs / f"{time.strftime('%Y%m%d-%H%M%S')}-cycle{cycle_no}.jsonl"
        say(f"cycle {cycle_no}: starting /sdd-run")
        cycle = Cycle(args, log_path, status, cycle_no)
        write_status(status, state="running", cycle=cycle_no, stream=str(log_path), threshold=args.threshold)
        outcome = cycle.run()
        if outcome == "operator":
            say(f"cycle {cycle_no}: stopped by the operator")
            if not hold(args, logs, status, cycle_no, "stopped by operator"):
                say("held longer than --max-wait-hours: stopping")
                write_status(status, state="stopped", reason="held too long", cycle=cycle_no)
                return 3
            continue
        if outcome == "finished" and (args.project / ".sdd" / "run.json").exists():
            say(f"cycle {cycle_no}: the run ended but .sdd/run.json is still there, so it was cut off by "
                "something other than the usage limit: read its report, then start /sdd-run or this script again to resume")
            write_status(status, state="stopped", reason="interrupted, not by the usage limit", cycle=cycle_no)
            return 1
        if outcome == "finished":
            say(f"cycle {cycle_no}: the run finished" + ("" if cycle.result_seen else " (no result line: check the log)"))
            write_status(status, state="done", cycle=cycle_no, stream=str(log_path))
            return 0 if cycle.result_seen else 1
        # Wait only for the window that stopped the run: the one that hit the limit, or the one that
        # crossed the threshold. A test pause (--pause-after) has none and starts again at once.
        window = cycle.usage.limit_type if outcome == "limit" else cycle.pause_window
        if outcome == "paused" and window is None:
            resets_at = int(time.time()) - RESET_MARGIN_S
        else:
            resets_at = cycle.usage.reset_of(window)
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
        waiting = {"state": "waiting", "window": window, "resets_at": resets_at, "cycle": cycle_no,
                   "usage": cycle.usage.windows}
        write_status(status, **waiting, keep_paused=False)
        # --no-sleep (tests) skips the wait and so ignores `hold`.
        if not args.no_sleep and wait_for_reset(args, logs, status, time.time() + wait, waiting):
            if not hold(args, logs, status, cycle_no, "kept paused; the usage window has reset"):
                say("held longer than --max-wait-hours: stopping")
                write_status(status, state="stopped", reason="held too long", cycle=cycle_no)
                return 3
    say(f"stopping after {args.max_cycles} cycles")
    write_status(status, state="stopped", reason="max cycles")
    return 4


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
