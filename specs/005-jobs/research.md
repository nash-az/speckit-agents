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

## Validation

quickstart.md §1–§5, run on 2026-11-02 (T021) on the run branch with the fake `claude` (`tests/fake/claude`).
Prerequisites: `python3 -m unittest discover -s tests` 470 tests OK, `node --test tests/workflow.test.mjs` 24
pass, `.gitignore` has `.sdd/jobs/`. The Job tests named below (`test_supervise.JobTest`,
`test_sdd_launch.LaunchJobTest`/`ContinueJobTest`, `test_sdd_decide.OpenJobTest`, `test_sdd_manual.JobTest`,
`test_sdd_job.MergeDropTest`, `test_sdd_dashboard.JobBannerTest`) ran with `-v`: 67 tests, all OK. The CLI
steps were run on fresh temp repos (a three-task Phase 1, `.sdd/` ignored) with the fake on `PATH` as
`claude`, through `sdd-launch.py`, `sdd-supervise.py` (started detached by the launcher, real 2 s poll, real
sleep to the reset), `sdd-decide.py` and `sdd-job.py`. `claude plugin test .` and the browser tests
(`tests/ui`) were not run here.

- **§1 A Job across three runs — pass.** `sdd-launch.py <repo> specs/001-x --job --phases 1` → checkout on
  `auto/20261102-1216`; `sdd-job.py show` → `state done`, `runs ["r1","r2","r3"]`, progress 3/3. Each of the
  three `claude` calls was `/sdd-run --yes --no-converge --phases 1 --job 20261102-1216`. No person action.
  Test: `JobTest.test_three_runs_end_done_with_three_run_ids`.
- **§2 Waiting on you, settle, continue by itself — pass.** T002 `[decision]`, T003 after T002: after run 1
  `state waiting`, reason "only items that need you are left", `waiting_on_you 1`. `sdd-decide.py … T002 "(b)
  blue"` printed `applied T002 in c7a8cc8` / `job: continued` (the CLI prints this line; the
  `{"started": true}` JSON is `apply()`'s result, checked by `OpenJobTest.test_a_waiting_job_with_a_runnable_task_is_continued`).
  The commit `chore(decisions): T002 (b) blue` is on `auto/<id>`. A new supervisor started with no click and ran
  r2; the Job ended `done`, `runs ["r1","r2"]`, still on `auto/<id>`. Banner text "Waiting on you: N items" is
  checked by `JobBannerTest.test_banner_texts_follow_the_table`. Note: for one item the page says "Waiting on
  you: 1 item" (singular); quickstart's "1 items" is a typo, not a defect.
- **§3 Usage limit with the checkbox — pass.** The fake emits `rate_limit_event` at 95 % with `resetsAt` 60 s ahead.
  (a) While waiting, `show` → shown `paused` with `keep_paused false` (box checked). The supervisor slept until
  the reset plus its 120 s margin and then resumed the same run. The Job ended `done` with `runs ["r1"]` and 2
  `claude` calls. (b) During the wait, `hold`, `release`, `hold` were written through the dashboard's
  `write_control` (the function behind `/api/run/hold` and `/release`; `keep_paused` went true, false, true).
  At the reset: Job `held`, reason "kept paused at the usage reset", supervisor exited.
  `sdd-launch.py --continue <id>` → `resumes "r1"`, Job `done`, `runs ["r1"]` (same run id). Evidence gap:
  the fake writes `run.json` with `run_id` only, and Continue on that file is refused "a run is active". A real
  `/sdd-run --job` also writes `branch` and `job` (SKILL step 5.2), so the driver wrote those before Continue.
  The fake does not play that part. Banner texts "Paused until HH:MM: usage limit; continues by itself" / "…;
  stays paused" and the checkbox: `JobBannerTest`, `JobTest.test_hold_release_hold_during_the_wait_ends_held`.
- **§4 Limits and Continue — pass.** `--max-tasks 1` → run 1 got `… --job <id> --max-tasks 1`; Job `stopped`,
  reason "max_tasks 1 reached". `--continue <id>` with no value → `refused: the limit max_tasks is still reached;
  give a new value`. `--continue <id> --max-tasks 3` → `done`, `runs ["r1","r2","r3"]`, progress `done 3` of 3.
- **§5 Merge, conflict, Drop — pass.** (a) Done Job, base unchanged: `sdd-job.py merge` →
  `{"merged": true, "fast_forward": true}`, checkout on `main`, `main` 3f6840d → 7a6719c = `auto/<id>`.
  (b) `main` moved with a conflicting edit to `tasks.md`: `refused: the merge conflicts in: specs/001-x/tasks.md`,
  `git status` empty, checkout still on `auto/<id>`, `main` unchanged, Job still `done`. (c) Two runs without
  progress → `failed` ("no progress in two runs"). `drop` → `{"dropped": true}`, checkout on `main`, `auto/<id>`
  kept. A new `--job --dry-run` start is accepted (exit 0): Start job is enabled again.
