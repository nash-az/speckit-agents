# Contract: Jobs (CLI and dashboard routes)

All state-changing routes follow 003 data-model §4.9 (allowed Origin, `Content-Type: application/json`) and
answer `409 {"error": "<reason>"}` on a refusal, changing nothing. Shapes are in `../data-model.md`.

## CLI

```
bin/sdd-launch.py <project> <feature> --job [--phases 11,12 | --tasks T061,T063] [--max-tasks N]
                  [--until EPOCH] [--max-runs N] [--title TEXT] [--dry-run]
    → {"job": "<id>", "branch": "auto/<id>", "pid": N}            refused: <reason> (exit 1)
bin/sdd-launch.py <project> <feature> --continue <id> [--max-tasks N|none] [--until EPOCH|none] [--max-runs N|none]
    → {"job": "<id>", "pid": N}
bin/sdd-job.py list [project]             → [record + shown + progress, ...]
bin/sdd-job.py show <id> [project]        → record + shown + progress + runs
bin/sdd-job.py merge <id> [project]       → {"merged": true, "commit": "<sha>", "fast_forward": bool}
bin/sdd-job.py drop <id> [project]        → {"dropped": true}
bin/sdd-supervise.py --project DIR --job <id> [--threshold …] [--profile NAME]
bin/sdd-tasks.py plan <tasks> [--phases …] [--tasks T061,T063] [--max-tasks N]
/sdd-run [--phases …] [--tasks …] [--job <id>] [--no-converge] [--max-tasks N] [--yes]
```

Refusal reasons (exact words, tests compare them):
- "another job is open: <title>"
- "the working tree has changes; commit or stash them first: <files>"
- "the project is not a git repository"
- "no task in the scope is runnable"
- "a run is active"
- "this job cannot be merged in state <state>" / "… dropped in state …"
- "the merge conflicts in: <files>"
- "the limit <name> is still reached; give a new value"

## Routes

| Method | Path | Body | Answer |
|---|---|---|---|
| GET | `/api/jobs` | — | `{"jobs": [...]}` open first, then newest |
| GET | `/api/jobs/<id>` | — | one Job with `runs`; 404 for an unknown or malformed id |
| GET | `/api/jobs/plan` | — | Start job preview: `{phases, runnable, held, can_start, reason}` |
| POST | `/api/jobs/start` | `{"phases"? , "tasks"?, "max_tasks"?, "until"?, "max_runs"?, "title"?}` | `202 {"ok": true, "job"}` |
| POST | `/api/jobs/<id>/continue` | `{"max_tasks"?, "until"?, "max_runs"?}` (number or `null` for none) | `202 {"ok": true}` |
| POST | `/api/jobs/<id>/merge` | `{}` | `200 {"ok": true, "commit"}` |
| POST | `/api/jobs/<id>/drop` | `{}` | `200 {"ok": true}` |
| POST | `/api/run/stop`, `/api/run/hold`, `/api/run/release` | as 003 | as 003 |

`GET /api/run/plan` and `POST /api/run/start` stay for compatibility; the page uses the job routes.
The dashboard source spawns nothing: the routes call `sdd-launch.py` and `sdd-job.py` functions (003 FR-020).
