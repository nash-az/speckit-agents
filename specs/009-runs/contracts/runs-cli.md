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
