---
description: "Task list for Jobs"
---

# Tasks: Jobs

**Input**: `specs/005-jobs/` — spec.md, plan.md, research.md (R1–R13), data-model.md, contracts/jobs-api.md, quickstart.md

**Tests**: required. The constitution requires `python3 -m unittest discover -s tests` and
`claude plugin test .` to pass before every commit. Each task adds or updates the tests for what it
changes, in the same task.

**Read first, every task**: the `data-model.md` section the task names holds the exact design (function
names, fields, state moves, texts); `research.md` holds the decisions R1–R13; `contracts/jobs-api.md` holds the
CLI, the routes and the exact refusal texts. Implement exactly that; do not redesign. If something the task
needs is missing or contradicts the design, stop and report `blocked` with the specific question.

**Test commands** (run all three before finishing any task):
`python3 -m unittest discover -s tests` · `node --test tests/workflow.test.mjs` · `claude plugin test .`

**Rules that hold for every task**: the dashboard source `bin/sdd-dashboard.py` never contains process
spawning, `git ` commands or file writes (`ReadOnlyTest` enforces it); routes call `bin/sdd-launch.py` and
`bin/sdd-job.py` functions, loaded with `importlib` like `sdd_edit`. Backend code is Python stdlib only. Never
push, never delete a branch, never rewrite history. Tests use temporary git repositories under
`tempfile`/`os.tmpdir()`, never a hard-coded `/tmp`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the user story from spec.md (US1–US5)

---

## Phase 1: Setup

- [x] T001 [P] Extend the fake `claude` in `tests/fake/claude` with two steps (data-model.md §11): `{"tick": "T001"}` changes `- [ ] T001` to `- [x] T001` in the file named by `$FAKE_TASKS`, and `{"finish": true}` deletes `<project>/.sdd/run.json` (the project is the current directory). Existing steps keep their behaviour. Tests in a new `tests/test_fake_claude.py`: a scenario with both steps ticks the task and removes `run.json`; an existing scenario runs unchanged.
- [x] T002 [P] Add `--tasks T061,T063` to the `plan` command in `bin/sdd-tasks.py` (research R6, data-model.md §5): keep only the listed ids (with `--phases`, both filters apply); groups, holds and `--max-tasks` behave as today; an unknown id is ignored. Tests in `tests/test_sdd_tasks.py`: only the listed tasks planned, combined with `--phases`, an unknown id, held tasks in the list stay held.
- [x] T003 [P] Add the ignore line `.sdd/jobs/` (research R1, data-model.md §11): append it to `.gitignore`, make it the fifth line in `skills/sdd-init/SKILL.md`'s list (change "these four lines" and "any of the four lines" to five), and add it to the preflight's list of required lines in `skills/sdd-run/SKILL.md`. Tests in a new `tests/test_skill_text.py`: both skill files name `.sdd/jobs/`, and `.gitignore` contains it.

---

## Phase 2: Foundational (the Job record and run links)

- [x] T004 Create `bin/sdd-job.py` (stdlib) with `load(project, id)`, `save(project, record)` (atomic: write `<id>.json.tmp`, then `os.replace`), `list_jobs(project)` (newest first), `open_job(project)` (the record whose `state` is not `merged` or `dropped`, else None), `new_id(project)` (`YYYYMMDD-HHMM` local time, suffix `-2`, `-3` when taken; research R12) and `default_title(scope)` ("Phase 11", "Phases 3–5", "T061, T063", "All open tasks"; FR-014), exactly as data-model.md §1 and §7. `save` keeps unknown keys. Tests in a new `tests/test_sdd_job.py` on temp projects: round trip, atomic write leaves no `.tmp`, ordering, `open_job` with merged/dropped/other states, id suffixes, every title form.
- [x] T005 In `bin/sdd-job.py` add `display_state(project, record)` (data-model.md §2: first rule wins; reads the supervisor's `status.json` through `sdd-supervise.py`'s `state_dir`, pid liveness as `bin/sdd-dashboard.py`'s `supervisor_alive` does), `scope_args(record)` (`--phases a,b` / `--tasks a,b` / ""), `progress(project, record)` (`{done, total, waiting_on_you, left}` from `sdd-tasks.py` parse and plan; data-model.md §3–§4) and `limit_reached(project, record)` (the name of the first reached limit or None; data-model.md §3). Tests in `tests/test_sdd_job.py`: each display rule (with a fake `status.json` and a live and a dead pid), each scope form, progress counts with ticked, held and blocked tasks, each limit.
- [x] T006 In `skills/sdd-run/SKILL.md` add the arguments `--tasks a,b` (passed to the plan step like `--phases`, and it skips converge) and `--job <id>` (data-model.md §5): with `--job`, write `"job": "<id>"` into `.sdd/run.json` (step 5.2) and into `.sdd/runs/<run id>/workflows.json` beside `feature` (step 5.3); update the `argument-hint`. Tests in `tests/test_skill_text.py`: the skill names `--tasks`, `--job`, and `"job"` in both files' descriptions.

---

## Phase 3: User Story 1 — Start a Job and let it finish across runs (P1)

**Goal**: one Start job runs the scope to the end across runs. **Independent test**: quickstart.md §1.

- [x] T007 [US1] In `bin/sdd-supervise.py` add `--job <id>` and the Job loop of data-model.md §6 and the transition table in §2: before each run check the branch (`git rev-parse --abbrev-ref HEAD` equals the record's `branch`, else `stopped` with reason "the checkout is not on the Job's branch") and `limit_reached`; snapshot `(ticked, blocked entries)`; run the existing cycles with `/sdd-run --yes --no-converge <scope_args> --job <id>` plus `--max-tasks <max_tasks − tasks_done>` when `max_tasks` is set; count `--max-cycles` per run; when `limits.until` passes during a run, send the "stop after the current task" request and then set `stopped` with reason "until <HH:MM> reached" (data-model.md §6); append the run id (read from `.sdd/run.json` while live) to `runs`; re-plan the scope, append newly found open tasks of scoped phases to `scope_tasks`, and set `working` (next run), `waiting`, `done`, `stopped` (limit named) or `failed` ("no progress in two runs", FR-006). `status.json` gains `"job"`. Without `--job` the supervisor behaves exactly as today. Tests in `tests/test_supervise.py` with the fake (T001): three runs → `done` with three run ids; only person items left → `waiting`; two runs without progress → `failed`; `max_runs` → `stopped`; a checkout on another branch → `stopped`, nothing run; `max_tasks 2` with one task done passes `--max-tasks 1` to the next run; `until` passing mid-run stops after the current task; `--max-cycles` counts per run.
- [x] T008 [US1] In `bin/sdd-supervise.py`, for `--job` only (research R7): the usage wait has no maximum (`--max-wait-hours` ignored for it); at the reset, `keep_paused` true → record `held`, exit 0; false → resume the same run; an operator `stop` → record `stopped` with reason "stopped by you", exit 0 (no `hold()` loop). Tests in `tests/test_supervise.py`: a reset 60 s ahead with `--no-sleep` resumes; `hold` then `release` then `hold` during the wait → `held` (only the last request counts); stop → `stopped`; without `--job` the 003 behaviour is unchanged.
- [x] T009 [US1] In `bin/sdd-launch.py` add `launch_job(project, feature, scope, limits, title=None, dry_run=False)` and the CLI flags `--job`, `--tasks`, `--until`, `--max-runs`, `--title` (data-model.md §8, contracts/jobs-api.md): refuse with the exact texts "another job is open: <title>", "the working tree has changes; commit or stash them first: <files>", "the project is not a git repository", "no task in the scope is runnable"; otherwise create the record (`state` `working`, `scope_tasks`, `blocked_at_start`, `base` branch and commit, `limits` with `max_runs` default 20, title from `default_title` unless given), `git switch -c auto/<id>`, and start `sdd-supervise.py --project <p> --job <id>` detached as `launch()` does, without `--max-wait-hours 0`. The plain `launch()` stays. Tests in `tests/test_sdd_launch.py` with `_spawn` replaced: each refusal, the record's fields, the branch, the command line.

---

## Phase 4: User Story 2 — Merge a finished Job into its base branch (P1)

**Goal**: Merge or Drop ends a Job with no terminal step. **Independent test**: quickstart.md §5.

- [x] T010 [US2] In `bin/sdd-job.py` add `merge(project, id)` and `drop(project, id)` and the CLI `list|show|merge|drop` (research R9, data-model.md §7, contracts/jobs-api.md): refuse with "this job cannot be merged in state <state>" / "this job cannot be dropped in state <state>", "a run is active", "the working tree has changes; commit or stash them first: <files>"; merge fast-forwards when possible, else `git merge --no-ff -m "merge: job <title>"`; on a conflict `git merge --abort`, switch back to the Job's branch and refuse "the merge conflicts in: <files>"; set `merged` with `merge_commit` and `ended`, or `dropped`; never delete the branch. Tests in `tests/test_sdd_job.py` on temp repos: fast-forward, merge commit after the base moved, conflict leaves base, branch and tree unchanged, each refusal, drop keeps the branch, CLI JSON output.

---

## Phase 5: User Story 3 — Continue after you answer (P2)

**Goal**: settling an item continues a waiting Job by itself; Continue resumes the others. **Independent test**: quickstart.md §2 and §4.

- [x] T011 [US3] In `bin/sdd-launch.py` add `continue_job(project, id, new_limits=None)` and `--continue <id>` with `--max-tasks N|none`, `--until EPOCH|none`, `--max-runs N|none` (data-model.md §3, §8): allowed from shown `waiting`, `interrupted`, `held`, `stopped`; the FR-003 refusals except "another job is open", but when resuming (`.sdd/run.json` exists on the Job's branch) skip the clean-tree refusal and refuse "the checkout is not on the Job's branch" if it is elsewhere (data-model.md §8); for a Job stopped by a limit, require a new value for that limit and refuse "the limit <name> is still reached; give a new value" while `limit_reached` would still be true; switch to the Job's branch when the tree is clean; set `working` and start the supervisor. Tests in `tests/test_sdd_launch.py`: each allowed and refused state, the limit rule, counts not reset, an interrupted Job with a dirty tree and `run.json` continues, the same with the checkout on another branch is refused.
- [x] T012 [US3] In `bin/sdd-job.py` add `after_settle(project)` (research R8): when `open_job` is `waiting`, no `.sdd/run.json` exists, no supervisor is alive, and the scope has a runnable task, call `sdd-launch.py`'s `continue_job`; return `{"started": bool, "reason": str|None}`; never raise. Tests in `tests/test_sdd_job.py` with `_spawn` replaced: starts when runnable, does not start for each blocking condition, a launcher error comes back as a reason.
- [x] T013 [P] [US3] In `bin/sdd-decide.py`, after the apply commit succeeds, call `sdd_job.after_settle(project)` and add its result as `"job"` to the JSON output (data-model.md §10). Tests in `tests/test_sdd_decide.py`: the key is present; with no open Job it says not started; with an open Job the decision commit is on `auto/<id>` (FR-008).
- [x] T014 [P] [US3] In `bin/sdd-manual.py`, after the `done` or `skip` commit succeeds, call `sdd_job.after_settle(project)` and add `"job"` to the JSON output (data-model.md §10). Tests in `tests/test_sdd_manual.py` as in T013.
- [x] T015 [P] [US3] In `bin/sdd-unblock.py`, after the `done`, `answer` or `drop` commit succeeds, call `sdd_job.after_settle(project)` and add `"job"` to the JSON output (data-model.md §10). Tests in `tests/test_sdd_unblock.py` as in T013.

---

## Phase 6: User Story 4 — A Jobs view instead of a Runs view (P2)

**Goal**: the person watches Jobs. **Independent test**: open a Job of two runs, both runs are inside it.

- [ ] T016 [US4] In `bin/sdd-dashboard.py` add the routes of contracts/jobs-api.md: `GET /api/jobs`, `GET /api/jobs/<id>` (404 for an id not matching the run id form or unknown), `GET /api/jobs/plan`, `POST /api/jobs/start`, `/api/jobs/<id>/continue`, `/merge`, `/drop`, calling `sdd-launch.py` and `sdd-job.py` functions; refusals are `409 {"error"}`; the 003 §4.9 POST checks apply. `GET /api/runs` gains each run's `job` from its `workflows.json`. Update the module docstring's route list. Tests in `tests/test_sdd_dashboard.py` through the handler: each route, a refusal, a bad id, `ReadOnlyTest` still passes.
- [ ] T017 [US4] [ui] In `build_html()` of `bin/sdd-dashboard.py` replace the Runs view with a Jobs view at `#jobs` (`#runs` redirects): open Jobs first, then newest; each card shows title, shown state in words, a progress bar and counts from `progress`, branch, and buttons Stop, Continue (asks for a new limit value when the reason names one), Merge, Drop (with a confirm); opening a card lists its tasks and runs, each run opening the existing run detail; runs without a Job under "Earlier runs"; the Start run panel becomes Start job (scope: phases or tasks, limits, title) using `/api/jobs/plan` and `/api/jobs/start`. Tests: page-script tests in `tests/test_sdd_dashboard.py` for the strings and routes; a new `tests/ui/test_jobs.py` opening a Job with two runs and checking both runs are inside it, and that Drop asks for confirmation.
- [ ] T018 [US4] [ui] In `bin/sdd-dashboard.py` make a task card's run chip say "done in job <title>" and open that Job when the run's `workflows.json` has `job` (FR-013); `/api/runs/tasks` adds `job` and the Job's `title` per entry. Tests in `tests/test_sdd_dashboard.py` and one case in `tests/ui/test_jobs.py`.

---

## Phase 7: User Story 5 — Plain words in the headline (P2)

**Goal**: the banner and Overview say what is happening. **Independent test**: every shown state against data-model.md §9.

- [ ] T019 [US5] [ui] In `bin/sdd-dashboard.py` render the banner and the Overview headline from the open Job with the texts of data-model.md §9 (never a run id), and, while shown `paused`, the checkbox "Continue when the limit resets (<HH:MM>)", checked unless `status.json` has `keep_paused` true; checking posts `/api/run/release`, unchecking `/api/run/hold` (research R11). Tests: update `tests/ui/test_banner.py` for the new texts and the checkbox (three flips write hold, release, hold; the page shows the last); a page test in `tests/test_sdd_dashboard.py` that no banner text for any shown state contains a run id (SC-003).

---

## Phase 8: Polish

- [ ] T020 [P] In `README.md` add a "Jobs" section: what a Job is, Start job, waiting and settling, the usage checkbox, limits and Continue, Merge and Drop, and that a plain `/sdd-run` still works. No test.
- [ ] T021 Run quickstart.md §1–§5 with the fake `claude` (the test suite covers them; run the named tests and the CLI steps by hand on a temp repo) and record each section's result (pass/fail and one line of evidence) under a new heading `## Validation` in `specs/005-jobs/research.md`.
- [ ] T022 [manual] Run quickstart.md §6 for real on this repo: start a Job on a small open phase from the Jobs view, leave it, come back, check the banner never showed a run id, and Merge it. Record the result under `## Validation` in `specs/005-jobs/research.md`.

---

## Dependencies and order

- Phase 1 tasks are independent ([P]). Phase 2 needs T001–T003 only for tests; T004 before T005.
- US1: T007 needs T001, T004, T005, T006; T008 after T007; T009 needs T004.
- US2: T010 needs T004, T005.
- US3: T011 needs T009; T012 needs T011; T013–T015 need T012 and run in parallel.
- US4: T016 needs T009, T010, T011; T017 after T016; T018 after T017.
- US5: T019 needs T016.
- Polish: T020 any time after T017; T021 after T019; T022 last.

## MVP and strategy

MVP = Phases 1–4 (start a Job, finish across runs, Merge it from the CLI). Then US3 (continue by itself), then the
Jobs view and headline. Each phase leaves the plugin working: without `--job` every 003 path is unchanged.

## Parallel examples

- Phase 1: T001, T002, T003 together (different files).
- Phase 5: T013, T014, T015 together after T012.
