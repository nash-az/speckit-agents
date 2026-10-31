# Data model: Jobs

Shapes, state moves and function names for `specs/005-jobs/`. Decisions are in `research.md`.

## 1. The Job record (`.sdd/jobs/<id>.json`)

```json
{
  "id": "20261031-1012",
  "title": "Phase 11",
  "feature": "003-plugin-redesign",
  "scope": {"phases": ["11"]},
  "scope_tasks": ["T053", "T054"],
  "blocked_at_start": 4,
  "limits": {"max_tasks": null, "until": null, "max_runs": 20},
  "branch": "auto/20261031-1012",
  "base": {"branch": "main", "commit": "fb10ba3"},
  "state": "working",
  "reason": null,
  "runs": ["20261031-1012", "20261031-1530"],
  "no_progress_runs": 0,
  "created": 1793450000,
  "ended": null,
  "merge_commit": null
}
```

- `scope`: exactly one of `{"phases": [ids]}`, `{"tasks": [ids]}`, `{"all": true}`.
- `scope_tasks`: open task ids in scope at start (runnable and held), from `sdd-tasks.py plan`; new open tasks
  found in scoped phases at a later run are appended (research R4).
- `limits.until`: epoch seconds or null. `limits` changes only through Continue (§3).
- `reason`: the last state change's reason in words ("max_tasks 10 reached", "no progress in two runs",
  "stopped by you").
- All writes go through `sdd_job.save(project, record)` (atomic). Unknown keys are kept.

## 2. States

Stored (`state`): `working`, `waiting`, `held`, `stopped`, `failed`, `done`, `merged`, `dropped`.

Shown (`sdd_job.display_state(project, record)`), first rule wins:
1. stored `working` and `status.json` state `waiting` with a live pid → `paused` (with `resets_at`,
   `keep_paused`)
2. stored `working` and no live supervisor → `interrupted`
3. otherwise the stored state.

Open = stored state not in (`merged`, `dropped`). At most one open Job per project.

| From | Event | To | Who |
|---|---|---|---|
| — | Start job | `working` | launcher |
| `working` | run ended, runnable tasks left, no limit reached | `working` (next run) | supervisor |
| `working` | run ended, only person items left | `waiting` | supervisor |
| `working` | run ended, nothing open in scope | `done` | supervisor |
| `working` | a limit reached | `stopped` (reason names it) | supervisor |
| `working` | two runs in a row without progress | `failed` | supervisor |
| `working` | operator Stop | `stopped` ("stopped by you") | supervisor |
| `working` (paused) | reset with `keep_paused` true | `held` | supervisor |
| `waiting` | a settle makes a scope task runnable | `working` | `after_settle` → launcher |
| `waiting`, `interrupted`, `held`, `stopped` | Continue | `working` | launcher |
| `done`, `stopped`, `failed` | Merge | `merged` | `sdd-job.py` |
| `done`, `stopped`, `failed` | Drop | `dropped` | `sdd-job.py` |

## 3. Continue and limits (FR-005, FR-009)

- Counting never resets. `tasks_done` = ids in `scope_tasks` now ticked in `tasks.md`.
- Limits reached: `max_tasks` when `tasks_done >= max_tasks`; `until` when now ≥ until; `max_runs` when
  `len(runs) >= max_runs`.
- Continue on a Job whose `reason` names a limit MUST receive a new value for that limit (`--max-tasks N`,
  `--until EPOCH`, `--max-runs N`, or `none`) and refuse while the limit would still be reached.

## 4. Progress (FR-006)

Before each run the supervisor stores `(ticked, blocked_entries)`; after it, progress = either grew. No progress
increments `no_progress_runs`; progress sets it to 0; reaching 2 sets `failed`, reason "no progress in two runs".

## 5. Run additions

- `/sdd-run --job <id>`: the skill writes `"job": "<id>"` into `.sdd/run.json` and into
  `.sdd/runs/<run id>/workflows.json` (beside `feature`).
- `/sdd-run --tasks a,b` and `sdd-tasks.py plan --tasks a,b`: keep only these ids; with `--phases`, both apply.
- Before each run the supervisor checks `git rev-parse --abbrev-ref HEAD` equals the Job's branch; otherwise the
  Job is `stopped` with reason "the checkout is not on the Job's branch" (spec edge case), nothing changed.

## 6. Supervisor with `--job` (`bin/sdd-supervise.py`)

`main()` with `--job <id>` loops runs; inside a run the existing usage cycles apply. Per run:
1. checks (§5 branch, limits §3); 2. snapshot progress (§4); 3. cycles of `/sdd-run --yes --no-converge
   <scope args> --job <id>` until the run finishes (`.sdd/run.json` gone) or the Job stops; 4. append the run id;
   5. re-plan the scope (`sdd-tasks.py plan`) and apply the table in §2.
Usage wait: no maximum (R7); at the reset `keep_paused` → `held`, exit 0. Operator `stop` → `stopped`, exit 0.
`status.json` gains `"job": "<id>"`.

## 7. `bin/sdd-job.py` (functions and CLI)

| Function | Purpose |
|---|---|
| `load(project, id)`, `save(project, record)`, `list_jobs(project)` | records, newest first |
| `open_job(project)` | the open record or None (006 uses it) |
| `display_state(project, record)` | §2 |
| `scope_args(record)` | `--phases …` / `--tasks …` / nothing |
| `progress(project, record)` | `{done, total, waiting_on_you, left}` |
| `default_title(scope)` | "Phase 11", "Phases 3–5", "T061, T063", "All open tasks" (FR-014) |
| `merge(project, id)`, `drop(project, id)` | research R9; raise `Refused` |
| `after_settle(project)` | research R8; returns `{started: bool, reason}` |

CLI: `sdd-job.py list|show <id>|merge <id>|drop <id> [project]`, printing JSON; `refused: <reason>` and exit 1.

## 8. Launcher (`bin/sdd-launch.py`)

- `launch_job(project, feature, scope, limits, title=None, dry_run=False)`: refuse (FR-003) when a Job is open,
  the tree is dirty, not a git repo, or no runnable task in scope; create the record and `git switch -c
  auto/<id>`; start `sdd-supervise.py --job <id>` detached (no `--max-wait-hours 0`).
- `continue_job(project, id, new_limits=None)`: FR-009 refusals; check out the Job's branch if needed (clean
  tree only); set `working`; start the supervisor.
- CLI: `--job` (with `--phases`/`--tasks`/`--max-tasks`/`--until`/`--max-runs`/`--title`) and
  `--continue <id>`. The 003 plain `launch()` stays for runs without a Job.

## 9. Dashboard (`bin/sdd-dashboard.py`)

- `GET /api/jobs`: `[{record fields…, "shown": display_state, "progress": progress()}]`, open first, then newest.
- `GET /api/jobs/<id>`: one Job plus its runs as `/api/runs` gives them (filtered by `job`).
- `POST /api/jobs/start|<id>/continue|<id>/merge|<id>/drop`: call the launcher or `sdd-job.py`; refusals are
  `409 {error}`. Stop, hold and release keep the `/api/run/*` routes.
- Banner and Overview text (FR-012), from the open Job:

| Shown | Text |
|---|---|
| `working` | "Working on <title>: <task>, <step> (<time>)" |
| `paused`, box checked | "Paused until <HH:MM>: usage limit; continues by itself" + checkbox |
| `paused`, box unchecked | "Paused until <HH:MM>: usage limit; stays paused" + checkbox |
| `held` | "Held after the usage reset: Continue to resume" |
| `waiting` | "Waiting on you: <n> items" |
| `interrupted` | "Stopped unexpectedly: Continue to resume" |
| `stopped`, `done`, `failed` | "<title> <state>: <n> tasks done; Merge to bring them into <base>, or Drop" |
| none open | "No job running" + Start job |

- Task chip (FR-013): "done in job <title>" when the run's `workflows.json` has `job`.

## 10. Helpers that call `after_settle` (FR-016)

`sdd-decide.py` (apply), `sdd-manual.py` (done, skip), `sdd-unblock.py` (done, answer, drop): after their
commit succeeds, call `sdd_job.after_settle(project)` and include its result as `"job"` in their JSON output.

## 11. Files

- `.gitignore` and `/sdd-init`'s list: add `.sdd/jobs/` (a fifth line; preflight checks it).
- `tests/fake/claude`: new steps `{"tick": "T001"}` (ticks a task in `$FAKE_TASKS`) and `{"finish": true}`
  (removes `.sdd/run.json`), so a scenario can play several runs of a Job.
