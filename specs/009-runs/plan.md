# Implementation Plan: Runs (009)

**Spec**: `spec.md` | **Branch**: `009-runs` | **Created**: 2026-11-06

## Approach

Keep the machinery 005 built and change its meaning, instead of a second record type:

- The 005 Job record (`.sdd/jobs/<id>.json`, git-ignored) **is** the run record. Its `runs` list holds the
  `/sdd-run` sessions' ids. Module and route names (`sdd-job.py`, `/api/jobs/…`, `--job`) stay as internal names;
  every word the person reads says "run" (FR-022). Renaming files and routes is left to 008, which rebuilds them.
- New: per-task history files and a run summary, both committed (`bin/sdd-history.py`).
- New: landing (auto-merge) at a clean end, fast-forward of finished work while suspended, and Drop of a
  suspended run (`bin/sdd-job.py`).
- The supervisor stops at task boundaries, read from the order of the workflow journal (`bin/sdd-supervise.py`).

## Data model

### Run record (`.sdd/jobs/<id>.json`)

Kept keys: `id, title, feature, scope, scope_tasks, blocked_at_start, limits, branch, base, runs, no_progress_runs,
created, ended, contract`. Changed or new:

- `state`: `working` | `suspended` | `ended`.
- `outcome` (ended only): `done` | `stopped` | `limit` | `waiting` | `failed` | `dropped`.
- `reason`: words, as today.
- `landing` (ended only): `{"state": "landed" | "nothing" | "needs-merge" | "pending" | "not-landed",
  "commit": sha | null, "files": [paths]}`. `pending` = not tried yet (carry-over, or a land that could not run);
  `not-landed` = a 005 Job that was dropped.
- `landed_commit`: the last commit already fast-forwarded into the base while suspended (FR-011), or absent.

`normalize(project, record)` reads any record (005 or 009) as 009 without writing it (US5, FR-025):

| stored 005 state | `.sdd/run.json` of this run there | read as |
|---|---|---|
| `merged` | – | `ended`, outcome from reason (`done` if none), landing `landed` with `merge_commit` |
| `dropped` | – | `ended`, outcome `dropped`, landing `not-landed` |
| `done`, `stopped`, `failed`, `waiting`, `held` | yes | `suspended`, reason kept |
| `done`, `stopped`, `failed`, `waiting` | no | `ended`, outcome = state (`stopped` with a "… reached" reason → `limit`), landing `pending` |
| `held` | no | `ended`, outcome `stopped`, landing `pending` |
| `working` | – | `working` |

"This run's `run.json`": `run.json` exists and its `job` is the id, or its `branch` is the record's branch.

Shown state (`display_state`), first rule wins: `working` + live supervisor in usage wait → `paused`;
`working` + no live supervisor → `suspended` when this run's `run.json` exists, else `ended` with landing
`pending`; otherwise the normalized state. A run is **open** (blocks a new run) when shown `working`, `paused`,
`suspended`, or `ended` with landing `needs-merge` or `pending`.

### Task history (`.sdd/tasks/<feature folder>/<task id>.jsonl`, committed)

One JSON object per line, append-only, oldest first:
`{"ts", "run", "session", "step", "attempt"?, "role"?, "model"?, "verdict"?, "problems"?, "commits"?, "reason"?,
"tag"?, "diff"?, "transcript"?, "key"}`.
`step`: `started`, `implemented`, `verified`, `blocked`, `committed`, `failed`, `suspended`, `resumed`, `dropped`.
`key` is unique per entry (`<session>:<workflow>:<label>:<step>:<agent id>` for journal entries,
`<run>:<step>:<ts>` for the others) so harvesting twice adds nothing. Built from `sdd-tasks.py run_events` of each
session id in the record's `runs`. `transcript` is the agent's transcript path while it exists.

### Run summary (`.sdd/runs/<run id>.json`, committed, written once at the end)

`{"contract", "id", "title", "feature", "scope", "created", "ended", "outcome", "reason", "landing", "sessions":
[ids], "tasks": {"done": [ids], "blocked": [ids], "dropped": [ids]}, "attempts": n, "approved_first_time": n,
"models": {role: model}}`. The plain-words report stays in `.sdd/runs/<session id>.md` (the skill writes it); when a
session ends without one (a stop at a boundary, a Drop), the summary step writes a short one from the journal.

## Engine changes

1. `bin/sdd-history.py` (new): `harvest(project, record)`, `append(project, feature, task_id, entry)`,
   `read(project, feature, task_id)`, `write_summary(project, record)`, `finalize(project, record)`: harvest, write
   missing session reports and the summary, `git add` them, commit `chore(run): <id> history and summary` (skipped
   when nothing changed). CLI: `harvest <project> <run id>`, `show <project> <feature> <task id>`.
2. `bin/sdd-job.py`: `normalize`, new `display_state`, `open_job`; `land(project, id)` (fast-forward else merge
   commit `merge: run <title>`; conflict → landing `needs-merge` with files, then `Refused`);
   `land_ready(project, id)` (FR-011, `git merge-base --is-ancestor` then `git update-ref`); `drop(project, id)`
   for a suspended run (patch through a temporary index, `git stash push -u -m "sdd-run <id>: dropped <tasks>"`,
   history entry `dropped` with the patch, `run.json` moved to `.sdd/runs/<session>/run.json`, finalize, ended
   `dropped`, land); `end(project, id, outcome, reason)` (ended, finalize, land; never raises); `suspend(project, id,
   reason)` (suspended, land_ready). `after_settle` starts a new run with the ended run's scope and limits (FR-024).
3. `bin/sdd-supervise.py`: a stop request (operator or stop-by time) waits for a **task boundary**: scanning the
   journal in order, the first `commit:` result after the request at which every task started before it has a
   commit result. Tasks started after that line are returned to the pool. Pressed before any agent started, the
   stop is immediate. After the cycle: a boundary stop with a clean tree closes the session (`run.json` archived) and
   ends the run (`stopped`, or `limit` for a stop-by time); any cut-off with work in flight (stop now, a limit not
   waited for, a crash, a kept pause at the reset) suspends it. Clean ends (`done`, `waiting`, `failed`, `limit`)
   call `end`.
4. `bin/sdd-launch.py`: refusals "a run is suspended: Continue or Drop it first" and "a run needs merge: <title>";
   Continue only for suspended runs.
5. `workflows/sdd-run.js` and `skills/sdd-run/SKILL.md`: commit trailers `Task`, `Run`, `Attempts`, `Model`
   (args `job` and `model`); the skill harvests task histories into its report commit.
6. `bin/sdd-dashboard.py` (the current UI): "run" wording, Start run, run cards by shown state (Continue and Drop
   on suspended, Merge on needs-merge, Land on pending, nothing on ended), banner texts (FR-022), the task card's
   history from `.sdd/tasks/` (FR-019), route `POST /api/jobs/<id>/land`.

## Not in this pass

- A plain in-session `/sdd-run` (no supervisor) gets trailers and task histories but no automatic landing or
  boundary stop; 008's `/sdd-stop` and hand-over build on this feature for that.
- Renaming `sdd-job.py`, `.sdd/jobs/` and `/api/jobs` (008 rebuilds the routes).
- Building task histories for work done before this feature (FR-026 "MAY").
