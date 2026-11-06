# Tasks: Runs (009)

**Input**: `spec.md`, `plan.md` in this folder. Implemented in the operator session on branch `009-runs`
(the person's choice, 2026-11-06), one commit per task.

## Phase 1: Engine (US3, US4)

- [x] T001 Create `bin/sdd-history.py` per plan "Task history" and "Run summary" (`harvest`, `append`, `read`, `write_summary`, `finalize`, CLI `harvest` and `show`), with tests in `tests/test_sdd_history.py` (harvest twice adds nothing; a rejected-then-approved task has both attempts and the problems; a session without a report gets a short one; finalize commits only `.sdd/tasks/` and `.sdd/runs/` files; nothing to commit makes no commit).

## Phase 2: The run record and landing (US1, US2, US5)

- [ ] T002 In `bin/sdd-job.py` add `normalize` per the plan's table and rewrite `display_state` and `open_job` per plan "Shown state", with tests in `tests/test_sdd_job.py` for every table row and every shown-state rule.
- [ ] T003 In `bin/sdd-job.py` add `land`, `land_ready`, `end` and `suspend` per plan "Engine changes" 2 (fast-forward, merge commit `merge: run <title>`, needs-merge with files on a conflict, nothing to land, update-ref only when the base is an ancestor), and CLI `land <id>`, with tests.
- [ ] T004 In `bin/sdd-job.py` rewrite `drop` for a suspended run per plan "Engine changes" 2 (patch, stash, `dropped` history entry with the patch, `run.json` archived, finalize, land), and make `after_settle` start a new run with the ended run's scope and limits (FR-024), with tests.

## Phase 3: Stopping at task boundaries (US1, US2)

- [ ] T005 In `bin/sdd-supervise.py` add `task_boundary(journal_lines, from_line)` per plan "Engine changes" 3 and use it for operator and stop-by stops (immediate before any agent started), with tests in `tests/test_supervise.py` (stop during an implementer waits for the commit; the task started 20 ms after the commit is returned; stop during preparing is immediate).
- [ ] T006 In `bin/sdd-supervise.py` replace `end_job` and the Job loop's endings per plan "Engine changes" 3: a boundary stop with a clean tree archives `run.json` and calls `end`; cut-offs call `suspend`; `done`, `waiting`, `failed` and limits call `end`. Tests with the fake `claude` for Stop after this task (lands, `run.json` gone, second task open) and a cut-off (suspended, work kept).
- [ ] T007 In `bin/sdd-launch.py` add the "a run is suspended" and "a run needs merge" refusals and restrict Continue to suspended runs per plan "Engine changes" 4, with tests in `tests/test_sdd_launch.py`.

## Phase 4: Commits and the skill (US3)

- [ ] T008 In `workflows/sdd-run.js` add the trailers `Task`, `Run`, `Attempts`, `Model` to the bookkeeper's commit instructions (args `job`, `model`; attempts per approved task), with tests in `tests/workflow.test.mjs`; in `skills/sdd-run/SKILL.md` pass `job` and `model` and harvest task histories into the report commit (`python3 <plugin>/bin/sdd-history.py harvest . <job or run id>`, `git add -- .sdd/tasks`), with `tests/test_skill_text.py` updated.

## Phase 5: The dashboard (US1–US4)

- [ ] T009 In `bin/sdd-dashboard.py` say "run" for every Job the person reads (view title Runs, `#runs`, Start run, cards, banner texts of spec FR-022), show cards by shown state with Continue and Drop on suspended, Merge on needs-merge, Land on pending, no action on ended, and add `POST /api/jobs/<id>/land`; update `tests/test_sdd_dashboard.py` and `tests/ui/`.
- [ ] T010 In `bin/sdd-dashboard.py` show the task's history from `.sdd/tasks/<feature>/<id>.jsonl` on the task card (steps in plain words, newest last, transcript links while they exist), with a route `GET /api/project/task-history?id=<task>` and tests.

## Phase 6: Docs

- [ ] T011 Update `README.md` ("Jobs" → "Runs": stop, suspend, Continue, Drop, landing, task history), `CLAUDE.md` handoff, and note in `specs/005-jobs/spec.md` and `specs/008-standalone-server/spec.md` that 009 replaces the Job.
