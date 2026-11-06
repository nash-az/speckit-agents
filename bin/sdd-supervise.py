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
now) ends the cycle and holds until `resume`. After the current task means at the next task boundary
(spec 009, `task_boundary`): a commit result at which every task started so far is committed; asked
before any agent started, the run stops at once; while waiting for a usage window, `hold` keeps the
run paused after the reset and `release` undoes that. Held longer than --max-wait-hours ends it.

--job ID carries out the Job in .sdd/jobs/ID.json (spec 005, data-model §6) as a series of runs of
`/sdd-run --yes --no-converge <scope> --job ID [--max-tasks <tasks left under the cap>]`. Before each
run it checks that the checkout is on the Job's branch and that no limit is reached; after each run it
appends the run id, re-reads the scope and ends the run (spec 009, `sdd-job.py end`: the summary is
committed and the run lands on its base) as `done`, `waiting` (only items that need the person) or
`failed` (no progress in two runs), or starts the next run; a limit reached before a run ends it as
`limit`. A stop-by time passing mid-run stops the run at the next task boundary. --max-cycles then
counts per run; status.json names the run. A run waits for any usage reset (--max-wait-hours does not
apply to that wait). A stop after the current task (the operator's or the stop-by time's) that reaches a
task boundary with a clean tree closes the session (.sdd/run.json moved to .sdd/runs/<session>/) and ends
the run `stopped` (or `limit`); any other cut-off (a boundary with work in the tree, Stop now, a stop
during the usage wait, a kept pause at the reset, a session that ended with .sdd/run.json still there)
suspends it (`sdd-job.py suspend`) for Continue or Drop. status.json then says `done` (ended) or
`stopped` (suspended) with the reason, and the supervisor exits instead of holding for Resume.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# The project config (run profiles) is read with the task helper's own loader (data-model §9).
_spec = importlib.util.spec_from_file_location("sdd_tasks", Path(__file__).with_name("sdd-tasks.py"))
sdd_tasks = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_tasks)
# control.json's reader and its one writer live in sdd-control.py (008 data-model §5).
_spec = importlib.util.spec_from_file_location("sdd_control", Path(__file__).with_name("sdd-control.py"))
sdd_control = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_control)
read_control = sdd_control.read_control
send_control = sdd_control.send_control

TRANSCRIPT_RE = re.compile(r"Transcript dir: (\S+)")
SAFE_GRACE_S = 20     # after SIGINT, before SIGTERM
KILL_GRACE_S = 10     # after SIGTERM, before SIGKILL
RESET_MARGIN_S = 120  # start again this long after the window resets
DEFAULT_THRESHOLDS = "five_hour=0.95,seven_day=0.99"  # the user's choice, 2026-10-18


def say(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} supervise: {message}", flush=True)


# Logs live outside the project; the helper lives in sdd-tasks.py (008 data-model §3).
state_dir = sdd_tasks.state_dir


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


def task_boundary(lines: list[str], from_line: int) -> int | None:
    """The first task boundary at or after `from_line` in a workflow journal (spec 009, plan "Changes" 3).

    Reading the lines in order, a task is started at its first `impl:`/`eval:` started line and settled
    when a `result` line of a `commit:` label names it. A boundary is a `commit:` result line at which
    every task started before it is settled. A commit result is not a boundary while a later line starts
    a `commit:` or `repair:` agent for the same tasks (failing checks: the group is repaired and committed
    again). Returns the line's index, or None when no boundary has come yet."""
    labels: dict[str, str] = {}
    seen: set[str] = set()     # tasks started so far
    open_tasks: set[str] = set()  # started and not settled
    events: list[dict | None] = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = None  # a line cut off when the process was killed
        events.append(event if isinstance(event, dict) else None)
    for index, event in enumerate(events):
        if event is None or not event.get("key"):
            continue
        key, kind = event["key"], event.get("type")
        if kind == "started":
            labels[key] = event.get("label") or ""
            match = sdd_tasks.LABEL_RE.match(labels[key])
            if match and match["kind"] in ("impl", "eval") and match["ids"] not in seen:
                seen.add(match["ids"])
                open_tasks.add(match["ids"])
            continue
        if kind != "result":
            continue
        match = sdd_tasks.LABEL_RE.match(labels.get(key, ""))
        if not match or match["kind"] != "commit":
            continue
        ids = set(match["ids"].split(","))
        open_tasks -= ids
        if index >= from_line and not open_tasks and not _redone_later(events[index + 1:], ids):
            return index
    return None


def _redone_later(events: list[dict | None], ids: set[str]) -> bool:
    """True when one of these journal events starts a `commit:` or `repair:` agent for any of `ids`."""
    for event in events:
        if event is None or event.get("type") != "started":
            continue
        match = sdd_tasks.LABEL_RE.match(event.get("label") or "")
        if match and match["kind"] in ("commit", "repair") and ids & set(match["ids"].split(",")):
            return True
    return False


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
        # (workflow dir, journal line count) when that stop request (or the stop-by time) was first read:
        # the run stops at the first task boundary from there (spec 009).
        self.stop_from: tuple[Path | None, int] | None = None
        self.boundary = False  # the run was stopped at a task boundary, or before any task (spec 009)
        self.run_id: str | None = None  # the run's id, read from .sdd/run.json while it is live (005 R5)
        self.until_sent = False  # the Job's stop-by time passed and the stop request was sent (005 §6)
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

    def journal_lines(self) -> list[str]:
        """The lines of the current workflow's journal so far (none before a workflow is seen)."""
        if not self.workflow_dir:
            return []
        try:
            return (self.workflow_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

    def journal_counts(self, lines: list[str] | None = None) -> tuple[int, int]:
        """(agents started, agents finished) in the workflow journal so far."""
        started = finished = 0
        for line in self.journal_lines() if lines is None else lines:
            if '"type": "started"' in line or '"type":"started"' in line:
                started += 1
            elif '"type": "result"' in line or '"type":"result"' in line or '"type": "failed"' in line:
                finished += 1
        return started, finished

    def nothing_left(self) -> bool:
        """True when this run has no runnable task left (its --phases/--tasks scope, held tasks excluded).
        False when unsure: a Job (its feature may not be the active one, and its stop-by time must hold),
        a --max-tasks cap, no feature found, or an unreadable tasks.md."""
        if getattr(self.args, "job", ""):
            return False
        try:
            scope = run_scope(self.args.run_args)
            if scope["max_tasks"] is not None:
                return False
            feature = run_feature_dir(self.args.project)
            if feature is None or not (feature / "tasks.md").is_file():
                return False
            result = sdd_tasks.plan(sdd_tasks.parse_tasks(feature / "tasks.md"),
                                    sdd_tasks.parse_blocked_entries(feature / "BLOCKED.md"),
                                    sdd_tasks.load_config(self.args.project / ".sdd" / "config.toml"),
                                    scope["phases"], None, scope["tasks"])
            return result.get("runnable") == 0
        except Exception:  # noqa: BLE001 - never let this check break a stop
            return False

    def run(self) -> str:
        """Run until the process ends. Returns 'finished', 'paused', 'limit' or 'operator'."""
        # SDD_SUPERVISED=1: the run's `session` command records `started_by: supervisor`, and the engine
        # install step skips itself (008 data-model §2, §3).
        env = dict(os.environ, **self.args.profile_config.get("env", {}), CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS="0",
                   SDD_SUPERVISED="1")
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
            lines = self.journal_lines()
            started, finished = self.journal_counts(lines)
            self.run_id = read_run_id(self.args.project) or self.run_id
            # A Job's stop-by time (005 data-model §6): send ourselves the "stop after the current task"
            # request; the operator path below then stops the run at the next safe point.
            until = getattr(self.args, "until", None)
            # A pending request is never overwritten (data-model §5): try again at the next poll.
            if (until is not None and not self.until_sent and time.time() >= until
                    and send_control(self.log_path.parent, {"id": "until", "command": "stop", "mode": "after-task",
                                                            "requested_at": int(time.time())})):
                self.until_sent = True
                say(f"the job's stop-by time {time.strftime('%H:%M', time.localtime(until))} has passed")
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
                    self.stop_from = (self.workflow_dir, len(lines))
                    say("stop requested by the operator: stopping at the next task boundary")
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
            # A stop after the current task (the operator's, or the stop-by time's) waits for a task
            # boundary (spec 009): every task started so far is committed. Tasks started after that line
            # go back to the pool. Usage-limit pauses keep the agent-level safe point below.
            if self.operator and self.stop_from is not None:
                if started == 0:
                    self.stop_reason = "operator"
                    self.boundary = True
                    say("stop requested before any task: stopping the run")
                    stop(proc)
                    break
                stop_dir, stop_line = self.stop_from
                boundary = task_boundary(lines, stop_line if stop_dir == self.workflow_dir else 0)
                if boundary is not None:
                    if self.pause_window is None and not hard and self.nothing_left():
                        # No task left: holding would leave a run that Resume can only close, so let
                        # it finish and write its report instead.
                        self.operator = False
                        self.stop_from = None
                        say("stop requested after the current task, but no task is left in this run: letting it finish")
                        if self.status:
                            with self.lock:
                                write_status(self.status, state="running", cycle=self.cycle_no,
                                             stream=str(self.log_path), usage=self.usage.windows,
                                             threshold=self.args.threshold)
                        continue
                    self.stop_reason = "operator"
                    self.boundary = True
                    say("task boundary: stopping the run")
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


JOB_ID: str | None = None  # set by --job; every status.json then names the Job (005 data-model §6)


def engine_version() -> str:
    """The engine this supervisor runs from (008 data-model §6): `plugin_version` of the `VERSION.json` next
    to its own `bin/` (an engine copy, §2), or `"repo"` when there is none (a checkout or the plugin)."""
    try:
        version = json.loads((Path(__file__).resolve().parent.parent / "VERSION.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "repo"
    value = version.get("plugin_version") if isinstance(version, dict) else None
    return value if isinstance(value, str) and value else "repo"


def write_status(path: Path, **fields) -> None:
    """Write `status.json` atomically (`status.json.tmp`, then `os.replace`), so a reader never sees half
    a file, with `contract` (008 data-model §1) and `engine_version` (§6)."""
    if JOB_ID:
        fields.setdefault("job", JOB_ID)
    fields["contract"] = sdd_tasks.CONTRACT
    fields["engine_version"] = engine_version()
    fields["pid"] = os.getpid()  # the dashboard checks that the supervisor is alive (data-model §12.1)
    fields["updated"] = int(time.time())
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(fields, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run_scope(run_args: str) -> dict:
    """--phases, --tasks and --max-tasks from the /sdd-run arguments (either `--x a,b` or `--x=a,b`)."""
    words = shlex.split(run_args or "")
    found: dict[str, str] = {}
    for i, word in enumerate(words):
        for name in ("--phases", "--tasks", "--max-tasks"):
            if word == name and i + 1 < len(words):
                found[name] = words[i + 1]
            elif word.startswith(name + "="):
                found[name] = word.split("=", 1)[1]
    split = lambda v: [x for x in v.split(",") if x] or None  # noqa: E731
    return {"phases": split(found["--phases"]) if "--phases" in found else None,
            "tasks": split(found["--tasks"]) if "--tasks" in found else None,
            "max_tasks": found.get("--max-tasks")}


def run_feature_dir(project: Path) -> Path | None:
    """The feature a plain /sdd-run works on: `[run] feature` in .sdd/config.toml, else .specify/feature.json."""
    config = sdd_tasks.load_config(project / ".sdd" / "config.toml")
    name = str((config.get("run") or {}).get("feature") or "").strip()
    if name:
        return project / name if "/" in name else project / "specs" / name
    try:
        data = json.loads((project / ".specify" / "feature.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    directory = data.get("feature_directory") if isinstance(data, dict) else None
    return project / directory if isinstance(directory, str) and directory else None


def read_run_id(project: Path) -> str | None:
    """The `run_id` in the project's .sdd/run.json, or None when there is no live run."""
    try:
        data = json.loads((project / ".sdd" / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    run_id = data.get("run_id") if isinstance(data, dict) else None
    return run_id if isinstance(run_id, str) and run_id else None


def hold(args: argparse.Namespace, logs: Path, status: Path, cycle_no: int, reason: str) -> bool:
    """Held after an operator stop or a kept pause: True when Resume comes, False after --max-hold-hours.
    The hold has its own limit: --max-wait-hours bounds only the usage-window wait, and the dashboard
    starts runs with --max-wait-hours 0, which would end every hold at once."""
    write_status(status, state="held", reason=reason, cycle=cycle_no)
    say(f"held ({reason}): waiting for Resume from the dashboard")
    deadline = time.time() + args.max_hold_hours * 3600
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


def wait_for_reset(args: argparse.Namespace, logs: Path, status: Path, target: float, fields: dict) -> str:
    """Sleep until the usage window resets, in steps of --poll, reading operator requests.
    Returns `go` (resume after the reset), `hold` (the operator asked to stay paused after it; only the
    last hold/release counts) or, with --job only, `stop` (an operator stop ends the wait at once, 005 R7)."""
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
        elif command == "stop" and args.job:
            say("stop requested by the operator while waiting for the usage window")
            return "stop"
        elif command not in ("hold", "release"):
            say(f"ignoring operator request {command!r} while waiting for the usage window")
    return "hold" if keep_paused else "go"


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
    parser.add_argument("--max-cycles", type=int, default=20,
                        help="cycles the supervisor starts by itself (the first and those after usage resets); "
                             "a cycle started by an operator Resume does not count")
    parser.add_argument("--max-hold-hours", type=float, default=24.0,
                        help="how long a run stopped by the operator (or kept paused) waits for Resume")
    parser.add_argument("--profile", default="",
                        help="run with [profiles.NAME] from .sdd/config.toml: its model and env for claude")
    parser.add_argument("--job", default="",
                        help="carry out the Job .sdd/jobs/ID.json as a series of runs (spec 005); --run-args is "
                             "then ignored and --max-cycles counts per run")
    parser.add_argument("--claude", default="claude", help=argparse.SUPPRESS)
    parser.add_argument("--poll", type=float, default=2.0, help=argparse.SUPPRESS)
    parser.add_argument("--no-sleep", action="store_true", help=argparse.SUPPRESS)  # tests
    parser.add_argument("--pause-after", type=int, default=0, help=argparse.SUPPRESS)  # e2e tests
    args = parser.parse_args(argv)
    args.project = args.project.resolve()
    args.until = None  # a Job's stop-by time (epoch seconds), set per run by run_job()
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
    config = sdd_tasks.load_config(args.project / ".sdd" / "config.toml")
    errors = sdd_tasks.config_errors(config)
    for error in errors:
        say(f"config error: {error}")
    if errors:
        return 2
    if args.profile:
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
    if args.job:
        return run_job(args, logs, status)
    return run_cycles(args, logs, status)[1]


def run_cycles(args: argparse.Namespace, logs: Path, status: Path) -> tuple[str, int, str | None]:
    """One /sdd-run, cycle after cycle across usage resets, until it ends.

    Returns (how it ended, exit code, the run id seen in .sdd/run.json or None). How it ended:
    `finished`, `cut-off` (ended with .sdd/run.json still there), `operator` (only with --job: a stop after
    the current task, the operator's or the run's stop-by time, stopped at a task boundary or before any
    task; without --job an operator stop holds), `stop-now` (only with --job: an operator stop that did
    not wait for a task boundary: Stop now, or a usage pause reached first), `stop-wait` (only with --job:
    an operator stop during the usage wait), `held` (only with --job: kept paused at the usage reset),
    `held-too-long`, `no-reset`, `too-late` (never with --job: a run waits for any reset) or `max-cycles`."""
    run_id = None
    cycle_no = 0
    counted = 0  # cycles started by the supervisor itself; one started by a Resume is not counted
    resumed = False
    while resumed or counted < args.max_cycles:
        cycle_no += 1
        if not resumed:
            counted += 1
        resumed = False
        log_path = logs / f"{time.strftime('%Y%m%d-%H%M%S')}-cycle{cycle_no}.jsonl"
        say(f"cycle {cycle_no}: starting /sdd-run")
        cycle = Cycle(args, log_path, status, cycle_no)
        write_status(status, state="running", cycle=cycle_no, stream=str(log_path), threshold=args.threshold)
        outcome = cycle.run()
        run_id = cycle.run_id or read_run_id(args.project) or run_id
        if outcome == "operator":
            say(f"cycle {cycle_no}: stopped by the operator" + (" (the job's stop-by time)" if cycle.until_sent else ""))
            if args.job:
                return ("operator" if cycle.boundary else "stop-now"), 0, run_id
            if not hold(args, logs, status, cycle_no, "stopped by operator"):
                say("held longer than --max-hold-hours: stopping")
                write_status(status, state="stopped", reason="held too long", cycle=cycle_no)
                return "held-too-long", 3, run_id
            resumed = True
            continue
        if outcome == "finished" and (args.project / ".sdd" / "run.json").exists():
            say(f"cycle {cycle_no}: the run ended but .sdd/run.json is still there, so it was cut off by "
                "something other than the usage limit: read its report, then start /sdd-run or this script again to resume")
            write_status(status, state="stopped", reason="interrupted, not by the usage limit", cycle=cycle_no)
            return "cut-off", 1, run_id
        if outcome == "finished":
            say(f"cycle {cycle_no}: the run finished" + ("" if cycle.result_seen else " (no result line: check the log)"))
            write_status(status, state="done", cycle=cycle_no, stream=str(log_path))
            return "finished", 0 if cycle.result_seen else 1, run_id
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
            return "no-reset", 1, run_id
        wait = max(0, resets_at + RESET_MARGIN_S - time.time())
        # A Job waits for any reset: --max-wait-hours does not apply to its usage wait (005 R7).
        if not args.job and wait > args.max_wait_hours * 3600:
            say(f"{window} resets at {time.strftime('%a %H:%M', time.localtime(resets_at))}, beyond --max-wait-hours: stopping")
            write_status(status, state="stopped", reason=f"{window} resets too late", resets_at=resets_at, cycle=cycle_no)
            return "too-late", 3, run_id
        say(f"{outcome}: waiting until {time.strftime('%H:%M', time.localtime(resets_at + RESET_MARGIN_S))} for the {window} window to reset")
        waiting = {"state": "waiting", "window": window, "resets_at": resets_at, "cycle": cycle_no,
                   "usage": cycle.usage.windows}
        write_status(status, **waiting, keep_paused=False)
        # --no-sleep (tests) skips the wait and so ignores `hold`.
        after = "go" if args.no_sleep else wait_for_reset(args, logs, status, time.time() + wait, waiting)
        if after == "stop":
            return "stop-wait", 0, run_id
        if after == "hold" and args.job:
            say("kept paused: the usage window has reset")
            return "held", 0, run_id
        if after == "hold":
            if not hold(args, logs, status, cycle_no, "kept paused; the usage window has reset"):
                say("held longer than --max-hold-hours: stopping")
                write_status(status, state="stopped", reason="held too long", cycle=cycle_no)
                return "held-too-long", 3, run_id
            resumed = True
    say(f"stopping after {args.max_cycles} cycles")
    write_status(status, state="stopped", reason="max cycles")
    return "max-cycles", 4, run_id


def load_sdd_job():
    """bin/sdd-job.py, loaded only for --job (it loads this script itself, so not at import time)."""
    spec = importlib.util.spec_from_file_location("sdd_job", Path(__file__).with_name("sdd-job.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git_branch(project: Path) -> str | None:
    """`git rev-parse --abbrev-ref HEAD` in the project, or None when git cannot say."""
    try:
        out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=project, capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def limit_reason(name: str, limits: dict) -> str:
    """The reason in words for a reached limit: "max_tasks 10 reached", "until 18:00 reached"."""
    if name == "until":
        return f"until {time.strftime('%H:%M', time.localtime(limits['until']))} reached"
    return f"{name} {limits.get(name)} reached"


def scope_plan(sdd_job, project: Path, record: dict) -> dict:  # noqa: ANN001
    """`sdd-tasks.py plan` over the Job's scope (its phases or tasks; everything for `all`)."""
    feature = sdd_job.feature_dir(project, record)
    scope = record.get("scope") or {}
    return sdd_tasks.plan(sdd_tasks.parse_tasks(feature / "tasks.md"),
                          sdd_tasks.parse_blocked_entries(feature / "BLOCKED.md"),
                          sdd_tasks.load_config(project / ".sdd" / "config.toml"),
                          [str(p) for p in scope.get("phases") or []] or None, None,
                          [str(t) for t in scope.get("tasks") or []] or None)


def progress_snapshot(sdd_job, project: Path, record: dict) -> tuple[int, int]:  # noqa: ANN001
    """(ticked tasks, BLOCKED.md entries) in the Job's feature (005 data-model §4)."""
    feature = sdd_job.feature_dir(project, record)
    parsed = sdd_tasks.parse_tasks(feature / "tasks.md")
    ticked = sum(1 for p in parsed["phases"] for t in p["tasks"] if t["done"])
    return ticked, len(sdd_tasks.parse_blocked_entries(feature / "BLOCKED.md"))


def tree_clean(project: Path) -> bool:
    """`git status --porcelain` is empty (False when git cannot say)."""
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=project, capture_output=True, text=True,
                             timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and not out.stdout.strip()


def close_session(sdd_job, project: Path, job_id: str) -> str | None:  # noqa: ANN001
    """Close the session a stop at a task boundary left (spec 009): move `.sdd/run.json` to
    `.sdd/runs/<its run_id>/run.json` and add that session id to the run's `runs`. Returns the session id,
    or None when there was no run.json."""
    run_json = project / ".sdd" / "run.json"
    if not run_json.exists():
        return None
    try:
        data = json.loads(run_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    session = str(data.get("run_id")) if isinstance(data, dict) and data.get("run_id") else "unknown"
    archive = project / ".sdd" / "runs" / session / "run.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    os.replace(run_json, archive)
    record = sdd_job.load(project, job_id)
    if record is not None and session != "unknown":
        runs = record.setdefault("runs", [])
        if session not in runs:
            runs.append(session)
            sdd_job.save(project, record)
    say(f"session {session} closed: .sdd/run.json moved to {archive.relative_to(project)}")
    return session


def end_run(sdd_job, project: Path, status: Path, job_id: str, outcome: str, reason: str | None) -> int:  # noqa: ANN001
    """End the run (spec 009, `sdd-job.py end`: the summary is committed and the run lands on its base) and
    give the supervisor's exit code. status.json says `done` first, so landing does not see a live run."""
    write_status(status, state="done", reason=reason or outcome, outcome=outcome)
    result = sdd_job.end(project, job_id, outcome, reason)
    landing = (sdd_job.load(project, job_id) or {}).get("landing") or {}
    error = (result.get("land") or {}).get("error")
    say(f"run {job_id}: ended {outcome}" + (f" ({reason})" if reason else "")
        + f"; landing {landing.get('state')}" + (f": {error}" if error else ""))
    write_status(status, state="done", reason=reason or outcome, outcome=outcome, landing=landing)
    return 1 if outcome == "failed" else 0


def suspend_run(sdd_job, project: Path, status: Path, job_id: str, reason: str, code: int) -> int:  # noqa: ANN001
    """Suspend the run (spec 009, `sdd-job.py suspend`): `.sdd/run.json` and the tree stay for Continue or
    Drop; finished work is fast-forwarded onto the base when it can be. Returns `code`."""
    try:
        landed = sdd_job.suspend(project, job_id, reason).get("landed_commit")
    except sdd_job.Refused as e:
        landed = None
        say(f"could not suspend run {job_id}: {e}")
    say(f"run {job_id}: suspended ({reason})" + (f"; finished work landed on the base at {landed[:7]}" if landed else ""))
    write_status(status, state="stopped", reason=reason)
    return code


# How a session that did not reach a clean end leaves the run suspended (spec 009 FR-007), in words.
SUSPEND_REASONS = {
    "stop-now": "stopped by you",
    "stop-wait": "stopped by you during the usage wait",
    "held": "kept paused at the usage reset",
    "no-reset": "the usage limit was reached and no reset time is known",
    "held-too-long": "held longer than the hold limit",
    "too-late": "the usage limit resets too late",
}


def run_job(args: argparse.Namespace, logs: Path, status: Path) -> int:
    """The run loop (005 data-model §6; endings per spec 009): session after session until the run ends
    (`end_run`) or is suspended (`suspend_run`)."""
    global JOB_ID
    sdd_job = load_sdd_job()
    project, job_id = args.project, args.job
    record = sdd_job.load(project, job_id)
    if record is None:
        say(f"no job {job_id} in {project / '.sdd' / 'jobs'}")
        return 2
    if record.get("state") != "working":
        say(f"job {job_id} is {record.get('state')}, not working: Continue it to start it again")
        return 2
    JOB_ID = job_id
    if args.run_args:
        say("--run-args is ignored with --job: the Job's scope and limits make the run's arguments")
    while True:
        # 1. Checks: the branch (§5) and the limits (§3).
        record = sdd_job.load(project, job_id) or record
        if git_branch(project) != record.get("branch"):
            return end_run(sdd_job, project, status, job_id, "stopped", "the checkout is not on the run's branch")
        limits = record.get("limits") or {}
        reached = sdd_job.limit_reached(project, record)
        if reached:
            return end_run(sdd_job, project, status, job_id, "limit", limit_reason(reached, limits))
        # 2. Snapshot progress (§4).
        before = progress_snapshot(sdd_job, project, record)
        # 3. The run, cycle after cycle (--max-cycles counts per run).
        run_args = ["--no-converge", sdd_job.scope_args(record), "--job", job_id]
        if limits.get("max_tasks") is not None:
            run_args += ["--max-tasks", str(limits["max_tasks"] - sdd_job.tasks_done(project, record))]
        args.run_args = " ".join(a for a in run_args if a)
        args.until = limits.get("until")
        say(f"job {job_id}: run {len(record.get('runs') or []) + 1} with /sdd-run --yes {args.run_args}")
        outcome, code, run_id = run_cycles(args, logs, status)
        # 4. Append the run id.
        record = sdd_job.load(project, job_id) or record
        runs = record.setdefault("runs", [])
        if run_id and run_id not in runs:
            runs.append(run_id)
        elif not run_id:
            say("the run's id is unknown (no .sdd/run.json was seen)")
        sdd_job.save(project, record)
        if outcome == "operator":
            # A stop at a task boundary (spec 009): with a clean tree the session is closed and the run ends
            # and lands; work left in the tree (a task started after the boundary) suspends it instead.
            until = (record.get("limits") or {}).get("until")
            by_time = until is not None and time.time() >= until
            reason = limit_reason("until", record["limits"]) if by_time else "stopped by you"
            if tree_clean(project):
                close_session(sdd_job, project, job_id)
                return end_run(sdd_job, project, status, job_id, "limit" if by_time else "stopped", reason)
            return suspend_run(sdd_job, project, status, job_id, f"{reason}; the tree has uncommitted changes", code)
        if outcome == "cut-off":
            return suspend_run(sdd_job, project, status, job_id, sdd_job.CUT_OFF_REASON, code)
        if outcome in SUSPEND_REASONS:
            return suspend_run(sdd_job, project, status, job_id, SUSPEND_REASONS[outcome], code)
        if outcome not in ("finished", "max-cycles"):
            return code
        # 5. Re-plan the scope; tasks added inside scoped phases join the Job (research R4).
        plan = scope_plan(sdd_job, project, record)
        scoped = record.setdefault("scope_tasks", [])
        for task_id in [i for p in plan["phases"] for i in p["tasks"]] + [h["id"] for h in plan["held"]]:
            if task_id not in scoped:
                scoped.append(task_id)
        after = progress_snapshot(sdd_job, project, record)
        progressed = after[0] > before[0] or after[1] > before[1]
        record["no_progress_runs"] = 0 if progressed else int(record.get("no_progress_runs") or 0) + 1
        sdd_job.save(project, record)
        counts = sdd_job.progress(project, record)
        if counts["waiting_on_you"] == 0 and counts["left"] == 0:
            return end_run(sdd_job, project, status, job_id, "done", None)
        if plan["runnable"] == 0:
            return end_run(sdd_job, project, status, job_id, "waiting", "only items that need you are left")
        if record["no_progress_runs"] >= 2:
            return end_run(sdd_job, project, status, job_id, "failed", "no progress in two runs")
        say(f"job {job_id}: {counts['done']} of {counts['total']} done; starting the next run")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
