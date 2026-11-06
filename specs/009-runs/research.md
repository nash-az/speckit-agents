# Research: Runs (009)

**Spec**: `spec.md` (amended 2026-11-06) | **Plan**: `plan.md` | **Date**: 2026-11-06

Decisions for the amendment after run `20261106-1204` and its analyze pass. Each one names the code it rests on
(file:line on branch `auto/20261106-1204` at `f92915e`).

## R1. Landing under constitution v1.2.1

- **Decision**: Only `bin/sdd-job.py` lands, never an agent: `land` (fast-forward, else `git merge --no-ff -m
  "merge: run <title>"`, a conflict aborts and stores `needs-merge`; `bin/sdd-job.py:540-582`) and `land_ready`
  (fast-forward by `git update-ref` while suspended, only when the base is an ancestor and not checked out;
  `bin/sdd-job.py:585-610`). Both are called by the supervisor (`end_run`/`suspend_run`,
  `bin/sdd-supervise.py:800-822`), by Drop (`drop_suspended` → `end`) and by the person (Land/Merge). The 005
  `merge()` (`bin/sdd-job.py:486-512`) goes: it refuses a stored `ended` record (`_closable`, line 463), so the
  dashboard's Merge on a 009 `needs-merge` run fails today (`bin/sdd-dashboard.py:575-577`).
- **Rationale**: Constitution v1.2.0 Principle II allows exactly this: "the plain-code engine (`bin/sdd-job.py`),
  never an agent, may land a run's approved, committed work on the run's base branch … by fast-forward or a plain
  merge commit … On a conflict it aborts the merge". The run guard still refuses agents' commits on protected
  branches while `.sdd/run.json` exists, and `land` refuses while it exists (`_run_active`, line 442).
- **Alternatives**: keep a Merge click (rejected by the person: it adds no check); rebase onto a moved base
  (out of scope: history is never rewritten).

## R2. Where task histories and the run summary are committed

- **Decision**: Task histories go with the session's report commit (`skills/sdd-run/SKILL.md` step 7 item 4,
  `sdd-history.py harvest`) and with the run's `chore(run): <id> history` commit that `end` makes on the run branch
  **before** landing, so they land with the work. The run summary (`.sdd/runs/<id>.json`) is written **after**
  landing, once the landing is final (`landed` or `nothing`), and committed on the base branch as
  `chore(run): <id> summary`. A run whose landing is `needs-merge` or `pending` gets its summary when Land or
  Merge succeeds. Today `finalize` writes the summary before landing (`bin/sdd-history.py:258-278`, called from
  `end`, `bin/sdd-job.py:629`), so the summary has no landing.
- **Rationale**: FR-017 (amended: "the run's history and summary commits") and FR-020 ("written once, after
  landing … how it ended and its landing"). Constitution v1.2.1 Principle II (commit `c1901d5`) allows it: "After
  landing, the engine may also commit the ended run's summary and task-history files (only those files) on the
  base." The summary commit holds only `.sdd/runs/<id>.json`. A merge commit's sha cannot be known before the
  merge, so the summary cannot be written on the run branch with its landing.
- **Alternatives**: (a) summary on the run branch before landing with a predicted landing (`git merge-tree`):
  cannot name a merge commit's sha, and a race would make it wrong; (b) histories in each task's own commit
  (the original FR-017): the bookkeeper commits only the task's files (Principle VI) and the history of a task
  is not complete until its commit exists; rejected by the person in the amendment.

## R3. Usage-limit check only in the supervisor; `/sdd-run` warns

- **Decision**: The usage pause stays in `bin/sdd-supervise.py` (`Usage`, the threshold and `hold`). Every run
  starts through the supervisor: `bin/sdd-launch.py` (`launch_job`, `continue_job`) or the dashboard's Start run,
  which call it. The supervisor already sets `SDD_SUPERVISED=1` for its `claude -p` (`bin/sdd-supervise.py:292-295`)
  and `sdd-tasks.py session` records `started_by` from it (`bin/sdd-tasks.py:146`). `/sdd-run` gets one preflight
  item: run `python3 -c "import os; print(os.environ.get('SDD_SUPERVISED', ''))"` (allowed by `Bash(python3 *)`;
  `printenv` is not on the allowlist in dontAsk mode); when it does not print `1`, or the call is denied or fails, say "This run is not started through the
  supervisor, so it has no usage-limit protection. Runs are started with the launcher (bin/sdd-launch.py) or Start
  run in the dashboard." and go on. It never starts a supervisor.
- **Rationale**: FR-027, SC-008, constitution v1.2.0 "Development". Run `20261106-1204` (a direct Workflow call)
  hit the 5-hour limit mid-task because no supervisor watched (`.sdd/runs/20261106-1204.md`, Lessons). The
  workflow cannot read usage events (no file system, no `Date.now()`), so a check there would be a second,
  weaker copy.
- **Alternatives**: refuse to run without `SDD_SUPERVISED` (rejected: the supervisor's own cycle is a `/sdd-run`,
  and a person may still want a quick in-session run); the skill starts a supervisor (rejected by the person:
  concerns stay separate).

## R4. Stop after this task when a later task left edits: patch, stash and end

- **Decision**: After a boundary stop with a dirty tree, the supervisor calls a new `sdd-job.py`
  `stash_and_end(project, run_id, outcome, reason)` instead of `suspend_run` (today
  `bin/sdd-supervise.py:881-890`). It reuses `drop_suspended`'s parts (`_in_flight`, `_uncommitted_patch`, the stash
  pathspec `KEEP_OUT_OF_STASH`; `bin/sdd-job.py:661-740`): the tasks in flight (from run.json's journal checkpoint)
  each get a history step `dropped` with `diff` and `reason` "started after the stop; its edits were stashed", the
  edits are stashed as `sdd-run <id>: stopped, stashed <tasks>`, `.sdd/run.json` is archived, and the run ends
  through `end` with the boundary stop's outcome (`stopped`, or `limit` for the stop-by time) and reason. Those
  tasks are open again (never ticked).
- **Rationale**: FR-005 (amended) and the person's answer "Stash and end". The edits are never lost (Principle II:
  failed work is stashed) and readable in the task's history; the finished tasks land at once.
- **Alternatives**: suspend (today's code): leaves a run open over work the person did not ask for; discard the
  edits: loses work.

## R5. "Stopped before any task"

- **Decision**: The supervisor's cycle already stops at once when no agent has started (`started == 0`,
  `bin/sdd-supervise.py:366-372`) and returns `operator` with `boundary` set, so the run ends with "stopped by
  you". The cycle gets a flag `before_any_task`; `run_cycles` returns the outcome `before-task`, and `run_job`
  closes the session and calls `end_run(…, "stopped", "stopped before any task")` (landing `nothing` follows,
  since the branch has no commit the base lacks).
- **Rationale**: FR-005 / US1 scenario 2 name the exact reason; the CLAUDE.md "open bug" (a stop during start-up)
  is gone once the run ends cleanly with that reason.

## R6. Newest-only restart after a settle

- **Decision**: `after_settle` (`bin/sdd-job.py:767-800`) loses its 005 path (`open_job`, `display_state`,
  `finish_if_complete`, `continue_job(from_waiting=True)`). It becomes: take the newest record
  (`list_jobs(project)[0]`), read it through `normalize`; start a new run (`sdd-launch.py` `launch_job` with its
  scope, limits and title) only when it is `ended` with outcome `waiting`, no run is open (`open_run` is None),
  its stop-by time has not passed and its scope has a runnable task. Otherwise return `{"started": False,
  "reason"}` with "the newest run did not end waiting" when the newest run is anything else. Today
  `_start_after_waiting` picks the newest run **that ended `waiting`**, so an old run restarts after newer runs
  (`bin/sdd-job.py:809-810`).
- **Rationale**: FR-024 (amended) and clarification "Only the newest run of the project". A 005 record stored
  `waiting` without its run.json normalizes to ended `waiting` with landing `pending`, so `open_run` returns it
  and nothing starts until it is landed; that is the carry-over rule (FR-025), not a restart.
- **Alternatives**: the newest `waiting` run (today): restarts stale runs, flagged by analyze.

## R7. Task summary line: format and parsing

- **Decision**: A summary is the line right below a task line that starts with exactly two spaces followed by a
  non-space, non-`-` character, and is not itself a task line: `  Dashboard says "run" and shows the right
  buttons for each run state.` `sdd-tasks.py` `parse_tasks` reads it into the task's `summary` (stripped text)
  and keeps `summary: null` when the next line is anything else. It is never part of `text`, `files` or `deps`.
  The tasks template's agent-tags section (`templates/agent-tags.md`, copied into a project's
  `.specify/templates/overrides/tasks-template.md` by `/sdd-init`) tells `/speckit-tasks` to write one for every
  task: one sentence, no file paths, function names or task ids.
- **Rationale**: FR-028. Today `parse_tasks` matches only `TASK_RE` (`^- \[…\] T…`) inside a phase
  (`bin/sdd-tasks.py:281-294`) and ignores every other line, so a summary line already parses cleanly with today's
  parser: it is not swallowed into the text, files or deps (checked on `specs/009-runs/tasks.md` with the
  summaries in place). In Markdown an indented line under a list item continues the item, so plain Spec Kit and
  any Markdown viewer still show it with its task. Ids in a summary are avoided so a reader of the raw file never
  mistakes them for dependencies.
- **Alternatives**: a trailing `— summary` on the task line (makes the task line longer and mixes the two
  audiences); an HTML comment (invisible in the raw view the dashboard offers); a separate file (drifts).

## R8. Summary on the card and in Start run

- **Decision**: `project_tasks` (`bin/sdd-views.py:554`) passes `summary` through from the parse. The task card
  (`taskCardHtml`, `bin/sdd-dashboard.py:1912`) and "Waiting on you" items show `summary` as the main text when
  it is set, with the exact text in `<details class="task-details"><summary>Details</summary>…</details>`; the
  Start run form (`startJobHtml`, `bin/sdd-dashboard.py:3111-3121`) shows `summary || text` from `preview()`'s
  `task_list`, which gains `summary` (`bin/sdd-launch.py:119`).
- **Rationale**: FR-029, SC-007.

## R9. Run summary contents and the highest usage

- **Decision**: The supervisor keeps the highest `utilization` it saw per window on the record as `usage_peak`
  (`{"five_hour": 0.93, "seven_day": 0.41}`), updated in `Cycle.on_event` (`bin/sdd-supervise.py:221-238`) through
  a small `sdd-job.py` helper `note_usage(project, run_id, windows)` that only raises the stored values. The
  summary adds `landing`, `duration_s` (`ended - created`), `usage_peak`, `reports` (the `.sdd/runs/<session>.md`
  paths that exist) and per-task `duration_s` and `tokens` totals from the histories. History entries harvested
  from a journal gain `duration_s` (first to last transcript timestamp, from `sdd-tasks.py`'s `_first_ts` /
  `_last_ts`) and `tokens` (the sum of `usage.input_tokens + usage.output_tokens` of the transcript's assistant
  lines) when the transcript exists at harvest time.
- **Rationale**: FR-016 ("duration and tokens when known") and FR-020 ("the highest usage the supervisor saw per
  window, and a link to the plain-words report"); US4 needs per-model approval rates without transcripts.
- **Alternatives**: read usage from the stream logs at the end (the logs live in the supervisor's state
  directory and may be gone); leave tokens out (FR-016 asks for them when known).

## R10. Suspended and resumed steps in the history

- **Decision**: `suspend` records step `suspended` (with `reason`) and `continue_job`, when it resumes a suspended
  run, records step `resumed`, each for every task in flight in run.json's journal (`_in_flight`), through
  `sdd-history.py` `record_step`. Today only Drop records a step (`bin/sdd-job.py:719`).
- **Rationale**: FR-016 lists both; US3 scenario 1. The entries stay in `.sdd/tasks/` in the tree and are
  committed by the session's report commit or the run's history commit (R2); `drop_suspended` already keeps
  `.sdd/tasks` out of its stash.

## R11. Removing what is left of 005

- **Decision**: After the callers move (R1, R6, the CLI's `_with_view`), remove from `bin/sdd-job.py`:
  `open_job`, `display_state`, `merge`, `drop`, `_closable`, `finish_if_complete`, `COMPLETE_REASON`,
  `CLOSED_STATES`, and the `from_waiting` path of `continue_job`. Kept: `progress`, `tasks_done`,
  `limit_reached`, `scope_args` (009 still uses them: `bin/sdd-supervise.py:860-907`, `bin/sdd-views.py:235`,
  `bin/sdd-launch.py:415`). T012 removed nothing because every one of these still had a caller
  (`550bf99`); the callers named above are the ones that should have moved.

## R12. Carry-over test on a copy of this repository's records

- **Decision**: `tests/fixtures/carry-over/` holds copies of this repository's `.sdd/jobs/*.json` (three merged
  Jobs: `20261104-1602`, `20261104-1710`, `20261106-1014`) and the `.sdd/runs/<id>.md` and
  `.sdd/runs/<id>/workflows.json` files they name, plus two made-up 005 records for the open cases (stopped with
  its run.json; done, not merged). A test copies the fixture into a temp git repository and checks US5 scenarios
  1–3 through `normalize`, `shown_state`, `is_open`, `sdd-views.py` `list_job_views`, `land` and
  `drop_suspended`. The live `.sdd/` is never read by the test.
- **Rationale**: SC-006 asks for "a copy of this repository's `.sdd/`"; `.sdd/jobs/` and `.sdd/runs/*/` are
  git-ignored, so the fixture must be a committed copy.

## R13. The test that writes to stdout

- **Decision**: `tests/test_sdd_launch.py` `test_cli_passes_only_the_given_limits_and_none` calls
  `launch.main([... "--continue" ...])` with `continue_job` mocked to return `{"job": …, "pid": 1}`, and `main`
  prints it (`bin/sdd-launch.py:503`), which is the `{"contract":"1.0","job":...,"pid":1}` line in the suite's
  output. Wrap the call in `contextlib.redirect_stdout(io.StringIO())` and assert the printed JSON.

## R14. Phase dependencies for the new tasks

- **Decision**: tasks.md gets a "Dependencies & Execution Order" section, which `/sdd-run` step 4 reads to build
  `phase_deps` (`skills/sdd-run/SKILL.md:67-70`). Run `20261106-1204` had none, so its phases ran one after
  another (report, Lessons). Task-level order uses "after T0xx" in the task text, which `sdd-tasks.py` reads as a
  dependency (`DEP_WORDS`, `bin/sdd-tasks.py:184-187`).
