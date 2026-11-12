# Contract: run commands (CLI)

Every command prints one JSON object (or list) with `"contract": "1.0"` on stdout. A refusal prints
`{"contract": "1.0", "error": "<reason>"}` and exits 1, changing nothing (except the stored `needs-merge` landing
on a conflict). A usage error prints the usage line on stderr and exits 2. Shapes: `../data-model.md`.
Internal names stay (`sdd-job.py`, `--job`, `.sdd/jobs/`); 008 renames them. FR-023: every dashboard action has
a command here with the same refusal texts, because both call the same functions.

| Action | Command | Function |
|---|---|---|
| Start run | `bin/sdd-launch.py <project> <feature> --job [--phases a,b \| --tasks T1,T2] [--max-tasks N] [--until EPOCH] [--max-runs N] [--title TEXT] [--dry-run]` | `sdd-launch.py` `launch_job` |
| Stop after this task | `bin/sdd-control.py stop --project <dir>` | `write_control` (mode `after-task`) |
| Stop now | `bin/sdd-control.py stop --now --project <dir>` | `write_control` (mode `now`) |
| Continue | `bin/sdd-launch.py <project> <feature> --continue <id> [--max-tasks N\|none] [--until EPOCH\|none] [--max-runs N\|none]` | `continue_job` |
| Drop | `bin/sdd-job.py drop <id> [project]` | `drop_suspended` |
| Land | `bin/sdd-job.py land <id> [project]` | `land` |
| Merge | `bin/sdd-job.py merge <id> [project]` | `land` (a 009 run: merge = land) |
| List / show | `bin/sdd-job.py list [project]`, `show <id> [project]` | `list_jobs` + `shown_state` + `progress` |

## Outputs

```
sdd-launch.py … --job            → {"contract","job": "<id>", "branch": "auto/<id>", "pid": N, "log": "<path>"}
sdd-launch.py … --job --dry-run  → {"contract","job","branch","pid": null,"command": [...],"record": {...}}
sdd-launch.py … --continue <id>  → {"contract","job": "<id>", "pid": N, "log": "<path>", "resumes": "<session id>"|null}
sdd-control.py stop [--now]      → {"contract","ok": true, "id": "<request id>"}
sdd-job.py list                  → [{record…, "shown": {"state","outcome","reason","landing",…}, "progress": {...}|null}, …]
sdd-job.py show <id>             → {"contract", record…, "shown": {...}, "progress": {...}|null}
sdd-job.py land <id>             → {"contract","landed": bool, "commit": "<sha>"|null, "fast_forward": bool,
                                    "landing": {"state": "landed"|"nothing", "commit", "files"}, "summary": "<path>"|null}
sdd-job.py merge <id>            → same as land
sdd-job.py drop <id>             → {"contract","dropped": true, "tasks": ["T014"], "stash": "<message>"|null,
                                    "end": {"finalize": {...}, "land": {...}}}
```

`list` and `show` use `shown_state` (the 005 `display_state` is removed). `merge` no longer writes `merged`; the
stored record stays `ended` with its landing. `drop` of a run that is not shown `suspended` is refused (there is no
005 Drop any more).

## Refusal texts (exact; tests compare them)

Start (`launch_job`, also the dashboard's dry run for Start run):
- "a run is suspended: Continue or Drop it first"
- "a run needs merge: <title>" (the open run ended with landing `needs-merge` or `pending`)
- "another run is going: <title>" (working, preparing or paused)
- "a run is active" · "the project is not a git repository"
- "the working tree has changes; commit or stash them first: <files>"
- "no task in the scope is runnable"

Continue (`continue_job`):
- "no such job: <id>" · "this run cannot be continued: it is <state>"
- "unknown limit: <names>" · "the limit <name> is still reached; give a new value"
- "a run is active" · "the checkout is not on the Job's branch" · "no task in the scope is runnable"

Land and Merge (`land`):
- "no such run: <id>" · "this run has not ended: it is <state>"
- "this run has already ended its landing"
- "a run is active" · "the project is not a git repository"
- "the working tree has changes; commit or stash them first: <files>"
- "the run has no branch or base branch"
- "the merge conflicts in: <files>" (landing stored `needs-merge`, checkout back on the run branch)

Drop (`drop_suspended`):
- "no such run: <id>" · "this run cannot be dropped: it is <state>"
- "a run is active" · "the project is not a git repository"
- "could not stash the half-done work: <git output>"

Stop (`sdd-control.py`): as 008 `contracts/engine-cli.md` (no run going, a request already pending).

## `sdd-tasks.py parse` (task `summary`)

`bin/sdd-tasks.py parse <tasks.md>` → `{"tasks_file", "phases": [{"id", "title", "tasks": [task, …]}]}`; each task:

```json
{"id": "T013", "done": false, "parallel": false, "story": null, "tags": [], "files": ["bin/sdd-tasks.py"],
 "deps": [], "superseded_by": [], "text": "In `bin/sdd-tasks.py` …", "line": 41,
 "summary": "Tasks can carry a plain-language summary that the dashboard shows."}
```

`summary` is the indented line right below the task (data-model §7), stripped, or `null`. `plan` passes it through
in `phases[].tasks`; `snapshot` leaves it out.

## `sdd-history.py`

```
sdd-history.py harvest <project> <run id>         → {"contract","added": {"T013": 4}}
sdd-history.py show <project> <feature> <task id> → {"contract","entries": [entry, …]}   (data-model §5)
```

## Amendment 2026-11-07: run control in the start window and the control log

FR-031, FR-032; research R16–R19, R22; shapes and exact texts in data-model §8.

### `sdd-control.py` (functions other modules call)

```
write_control(logs, command, mode=None, request_id=None, job=None) -> str | False
    as today; `job` given → "job": <job> in control.json (data-model §8.2)
send_control(logs, request) -> str | False
    as today; passes request.get("job") on; after a successful write logs a request line with source "until"
request(project, command, mode=None, source="cli") -> {"ok": True, "id"} | {"error"}
    as today, plus: job = status.json "job" when the supervisor is alive and not done/stopped (else None) is
    written into control.json; one request line is logged, accepted or refused, after the write or the refusal;
    a stop's line has mode `mode or "after-task"` (2026-11-08)
log_control(logs, entry) -> bool
    appends json.dumps(entry) + "\n" to <logs>/control-log.jsonl with one os.write on an O_APPEND fd; no lock;
    any OSError/ValueError → False; never raises
read_control_log(logs) -> {run id: [request, …]}      (data-model §8.3; {} for a missing or unreadable file;
    lines without a run, request or taken, are skipped; 2026-11-08)
controls(project)
    the "running" row also covers status "starting" (stop True, reason None)
```

The command line is unchanged (`stop [--now]`, `resume`, `hold`, `release`, `controls`; same output and exit
codes). It now passes `source="cli"`, so each request leaves a request line. `sdd-control.py stop --project <dir>`
during the start window (a `starting` status, data-model §8.1) is accepted: `{"contract", "ok": true, "id"}`.

### `sdd-supervise.py`

```
write_starting_status(path, job_id, pid) -> bool
    data-model §8.1; False (nothing written) when pid is not a positive int (a bool does not count;
    2026-11-12) or when status.json already has "pid": pid; copies "window" and
    "resets_at" from a previous "waiting" status (2026-11-08); writes through its own status.json.<own pid>.tmp,
    never status.json.tmp; never raises: an OSError from the write or the replace → False, status.json
    unchanged, no temporary file left (2026-11-08, fourth analyze pass C1)
startup_control(logs, job_id) -> dict | None
    reads control.json once (read_control deletes it); returns a "stop" whose "job" == job_id (job_id not
    empty); otherwise logs the ignored taken line, says "deleted a leftover control.json" and returns None;
    returns None at once when there is no control.json; (2026-11-08) a control.json that read_control could
    not use (still there after it returned None) is unlinked and logged ignored "unreadable request"
abandon_start(logs, status, args, reason) -> None                         (2026-11-08)
    status.json "starting" or "waiting" with pid == os.getpid() (its own start window, or the waiting status
    restart_wait wrote; 2026-11-09, research R27; any other status is left alone) →
    write_status(state="stopped", reason=reason); a kept args.startup_stop → taken "ignored" with reason;
    called on every exit before the first session; never raises
restart_wait(args, logs, status, last) -> "go" | "held" | "stop-wait"   (2026-11-08, --job only)
    writes a "waiting" status (window, resets_at), then wait_for_reset(…); the result is args.startup_outcome
taken(logs, control, text, reason=None) -> None
    one taken line (data-model §8.2: ts, id, run, request_run, command, mode, taken, reason) through
    sdd_control.log_control
--claude  hidden; default os.environ.get("SDD_CLAUDE") or "claude"   (tests only, research R22)
```

`main` calls `args.startup_stop = startup_control(logs, args.job)` before its config checks (2026-11-08; it
deleted `control.json` after them before), and keeps a dead supervisor's usage wait from a `waiting` or a
`starting` status with `resets_at`, unless a stop was kept (with `--job` through `restart_wait`). Exit codes of the
early exits are unchanged (2).
`run_job` carries it out right after setting `JOB_ID` (research R17), deciding on `tree_clean` alone (2026-11-08,
I1): a clean tree → `close_session` (archives a `.sdd/run.json` a Continue left; nothing when there is none), then
the existing `end_run` ("stopped", "stopped before any task", or "stopped before its next task" when the run has
task entries from an earlier session: `before_any_task_reason`, 2026-11-09, research R27); a dirty tree →
`suspend_run` ("stopped by you").
Whether `.sdd/run.json` exists does not decide it. A `stop` with `mode: now` read in
`Cycle.run` while no agent has started ends like an after-task one (`before-task`, research R18), and `run_job`'s
`before-task` branch suspends instead when the tree is not clean (reason the stop-by time's when that stop came
from it, else "stopped by you"; 2026-11-08) and first writes a second taken line `stopping now` for the request
`Cycle.run` took (`args.stop_control`, set by `run_cycles` with the `before-task` outcome; 2026-11-08, I2). A
`startup_outcome` of `held` or `stop-wait` suspends the run before
its first session with the existing reasons.

Exit codes are unchanged: 0 for `stopped` and for a suspend from a stop.

### `sdd-launch.py`

```
_start_supervisor(project, command, job_id=None) -> (pid, log)
    after _spawn: write_starting_status(state_dir/status.json, job_id, proc.pid) when job_id is given;
    (2026-11-08) a daemon thread calls proc.wait(), so the child is reaped when it exits;
    the result of write_starting_status is ignored (it never raises, C1)
```

`launch_job` and `continue_job` pass their run id; their output is unchanged. When `launch_job` returns, the new
run already reads as `preparing` (`sdd-job.py show <id>`) and Stop is accepted for it.
