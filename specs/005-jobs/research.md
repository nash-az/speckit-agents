# Research: Jobs

Decisions for `specs/005-jobs/`. A task must not reopen a decision listed here.

## R1. Where the Job record lives

- **Decision**: `.sdd/jobs/<job id>.json` in the project (FR-001), git-ignored with a new `.gitignore` line
  `.sdd/jobs/`, written only through `bin/sdd-job.py` functions (atomic write: `<id>.json.tmp` then rename).
- **Rationale**: the spec names it; it sits beside `.sdd/run.json` and `.sdd/runs/`, which the Job links to.
  The run guard already refuses subagent writes to `.sdd/**`, so agents cannot change it.
- **Alternatives**: the supervisor's state directory (like `status.json`): survives a fresh clone less well and
  splits a Job from its runs; rejected.

## R2. Who drives the Job

- **Decision**: the supervisor (`bin/sdd-supervise.py --job <id>`) runs the Job's runs one after another. It
  exits whenever the Job stops needing it: `waiting`, `held`, `stopped`, `failed`, `done`. Continue, the
  auto-continue after a settle (FR-016) and Start job all go through `bin/sdd-launch.py`, which starts a new
  supervisor. No process stays alive waiting for a person.
- **Rationale**: the supervisor already owns the run loop, usage pauses and the dashboard controls; a Job is that
  loop extended across runs. Exiting instead of idling means a reboot loses nothing: the record says what to do.
- **Alternatives**: a long-lived Job daemon; the dashboard starting runs (breaks 003 FR-020); rejected.

## R3. Stored state versus shown state

- **Decision**: the record stores `working`, `waiting`, `held`, `stopped`, `failed`, `done`, `merged`,
  `dropped`. `paused` and `interrupted` are derived when reading (data-model §2): `paused` = record `working`
  and the supervisor's `status.json` says `waiting` with a live pid; `interrupted` = record `working` and no
  live supervisor.
- **Rationale**: a killed supervisor cannot write `interrupted`; deriving it from pid liveness is the 003
  pattern (`current_run`, §4.5).

## R4. Counting progress and limits

- **Decision**: at Job start the record keeps `scope_tasks`, the ids of the open tasks in scope
  (`sdd-tasks.py plan` with the scope, runnable plus held), and the BLOCKED.md entry count. After each run the
  supervisor computes `done` = scope tasks now ticked, plus new open tasks that joined the scope (spec edge case:
  tasks added inside scoped phases join at the next run). "Progress" in a run = more ticks or more BLOCKED.md
  entries than before it. The task cap counts ticks since the Job started.
- **Rationale**: deterministic (Principle V), from files the run already maintains; no parsing of commit
  messages.
- **Alternatives**: counting task commits in `git log` (subjects vary); rejected.

## R5. Linking runs to Jobs

- **Decision**: the supervisor passes `--job <id>` to `/sdd-run`; the skill records `"job": "<id>"` in
  `.sdd/run.json` and in `.sdd/runs/<run id>/workflows.json` beside `feature`. The supervisor appends the run id
  to the record when it sees the run finish (read from `run.json` while the run is live). Runs without `job` are
  "Earlier runs".
- **Rationale**: `workflows.json` already carries `feature` for the same purpose (2026-10-30 lesson).

## R6. Scope as a list of tasks

- **Decision**: add `--tasks T061,T063` to `sdd-tasks.py plan` (keep only those ids; groups and holds as today)
  and to `/sdd-run`'s arguments (passed to its plan step, skips converge like `--phases`).
- **Rationale**: the spec's scope has three forms (`phases`, `tasks`, `all`); today only phases exist.

## R7. Waiting through usage limits

- **Decision**: with `--job`, the supervisor waits for any reset with no maximum (`--max-wait-hours` is ignored
  for the usage wait), keeps reading `hold`/`release` (003 §12.2) during the wait, and at the reset resumes when
  the last request was `release` or none (the default, "checked"), else sets the Job `held` and exits. The
  launcher no longer passes `--max-wait-hours 0`. An operator Stop sets the Job `stopped` and the supervisor
  exits (no `hold()` loop for Jobs).
- **Rationale**: the user's answer (Clarifications 2026-10-31); `sleep_until` already copes with a sleeping
  machine. Exiting on held/stopped follows R2.

## R8. Continuing after a settle (FR-016)

- **Decision**: one function, `sdd_job.after_settle(project)`, called at the end of a successful commit by
  `bin/sdd-decide.py`, `bin/sdd-manual.py`, `bin/sdd-unblock.py` (and 004's apply helper later). It starts the
  next run through the launcher when the open Job is `waiting`, no run or supervisor is active, and the scope has
  a runnable task. It never raises: a failure is returned as a reason for the caller's response.
- **Rationale**: "the helper that made the commit triggers it, not the dashboard" (FR-016).

## R9. Merge and Drop

- **Decision**: `bin/sdd-job.py merge|drop <id>`. Merge: refuse unless the Job is `done`, `stopped` or `failed`
  and the tree is clean and no run is active; `git switch <base>`; `git merge --ff-only auto/<id>`, else
  `git merge --no-ff -m "merge: job <title>" auto/<id>`; on a conflict `git merge --abort`, `git switch
  auto/<id>`, and refuse naming `git diff --name-only --diff-filter=U`. Drop: same refusals, `git switch <base>`,
  state `dropped`. Branches are never deleted, nothing is pushed.

## R10. The Jobs view

- **Decision**: `#jobs` replaces `#runs` (redirect). Job cards come from `GET /api/jobs`; a Job's runs reuse the
  existing run detail (events, report, tasks). The 003 Start run panel becomes Start job. Run ids appear only
  inside a Job's history.

## R11. The usage checkbox

- **Decision**: UI only. The banner shows a checkbox "Continue when the limit resets (<time>)" while the Job is
  `paused`; checking posts `/api/run/release`, unchecking `/api/run/hold` (existing routes); its state comes
  from `status.json` `keep_paused`.

## R12. Ids and branches

- **Decision**: the Job id is `YYYYMMDD-HHMM` at start (suffix `-2`, `-3` if taken); the branch is
  `auto/<job id>`, created by the launcher before the first run. `/sdd-run` already stays on an `auto/` branch
  it starts on (SKILL step 5.1), so every run lands there.

## R13. 006 and "the checkout is in use"

- **Decision**: `sdd_job.open_job(project)` returns the open Job record or None; 006 uses it for its FR-009.
