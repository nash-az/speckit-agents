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

---

# Amendment 2026-11-07: Stop while preparing and the run-control trace

Decisions for spec Clarifications "Session 2026-11-07", FR-031 to FR-034, SC-009 and the last edge case. Line
numbers are on `main` at `fc63f0d` (the spec amendment commit). Exact texts and shapes are in data-model §8; the
commands and routes in `contracts/`.

## R15. Why Stop pressed while preparing was lost (2026-11-06, run 20261106-1631)

- **What was seen**: the person pressed Stop after current task on the banner during run 1631's preflight. No
  `control.json` appeared and the supervisor logged no "stop requested" line. The dashboard drops its request log
  (`log_message` → `pass`, `bin/sdd-dashboard.py:336-337`), so the POST's fate is unknown. The dashboard process
  had been started before T028 (`c09d2d0`, 22:58 on 2026-11-06, the commit that added the `preparing` state and its
  Stop buttons), so the exact page code that ran is not known either.
- **What the code shows** (four gaps, each fixed by a later decision):
  1. **The start window.** `launch_job` saves the record, spawns the supervisor and returns at once
     (`bin/sdd-launch.py:327-329`); the supervisor writes its first `status.json` only in `run_cycles`
     (`bin/sdd-supervise.py:672`), after `main`'s config checks (`:604-645`) and `run_job`'s checks (`:876-904`).
     Until then `status.json` is the previous run's (`done`, dead pid). `controls()` then falls through to
     `current_state` (`bin/sdd-control.py:86-108`), which says `none` without a `.sdd/run.json`, so `stop` is False
     and `request()` refuses with "not available now" (`bin/sdd-control.py:184-185`); the banner draws both Stop
     buttons disabled (`bin/sdd-dashboard.py:3788-3792`, `!!c.stop`). `shown_state` reads the same stale
     `status.json` and shows the new run as ended with "the supervisor ended without recording the run's end" and
     landing `pending` (`bin/sdd-job.py:300-302`); Land would be refused (`land` needs a stored `ended`,
     `bin/sdd-job.py:484-485`), but the card is wrong. → R16.
  2. **Startup deletes every request.** `main` unlinks any `control.json` before doing anything
     (`bin/sdd-supervise.py:627-632`), so a request written in the start window is lost even when it was accepted.
     → R17.
  3. **Stop now before any task suspends.** In `Cycle.run` a `stop` with `mode: now` sets `stop_reason =
     "operator"` without `boundary` (`bin/sdd-supervise.py:350-354`); `run_cycles` returns `stop-now` (`:680`) and
     `run_job` suspends the run "stopped by you" (`:935-936`, `SUSPEND_REASONS`), although no task started. FR-007
     says a run cut off at a task boundary ends cleanly, and FR-032 says a stop in the start window ends it
     `stopped`. → R18.
  4. **No trace.** A refused request is answered 409 and logged nowhere; a taken request is only a `say` line in
     `launch.log`. → R19.
- **Rationale**: the supervisor itself already stops at once on an after-task stop while no agent has started
  (`bin/sdd-supervise.py:375-382`, test `test_a_stop_before_any_agent_starts_ends_the_run_stopped_before_any_task`,
  `tests/test_supervise.py:1051`), so the session part works; the failure is before the supervisor's first status
  and in what nobody logged. SC-009's tests (R22) press Stop in both moments with real processes, so a cause not
  found here still shows up.

## R16. The launcher writes a `starting` status before it returns

- **Decision**: new `bin/sdd-supervise.py` function `write_starting_status(path, job_id, pid)` writes
  `status.json` atomically (as `write_status`, `bin/sdd-supervise.py:461-473`) with `{"state": "starting", "job":
  <job_id>, "pid": <pid>, "contract", "engine_version", "updated"}`, but only when the current `status.json` does
  not already carry that `pid` (the supervisor got there first; never overwrite it). `bin/sdd-launch.py`
  `_start_supervisor(project, command, job_id=None)` (`:190-203`) calls it right after `_spawn` returns, when
  `job_id` is given; `launch_job` (`:328`) and `continue_job` (`:440`) pass the run id. The plain `launch` (`:186`,
  no run record) does not.
- Every reader treats `starting` as a live supervisor that is going:
  - `sdd-control.py` `controls()`: the `running` row (`:90-91`) becomes `state in ("running", "starting")` → `stop`
    True, `reason` None.
  - `sdd-tasks.py` `current_state` (`:876`): `("running", "stopping")` becomes `("running", "stopping",
    "starting")` → `running`.
  - Unchanged and already right: `sdd-launch.py` `_supervisor_running` (`:74`, only `done`/`stopped`/missing are not
    running), `sdd-job.py` `_run_active` (`:434`), `shown_state` (`:290-297`: a live pid and no started agent →
    `preparing`), the banner's `supervisorState` (`bin/sdd-dashboard.py:3701-3709`, `c.stop` → running).
- **Rationale**: FR-032 ("From Start run until the new supervisor has written its state, Stop … MUST be accepted").
  The dashboard's `POST /api/jobs/start` answers only after `launch_job` returns (`bin/sdd-dashboard.py:542`; was
  cited as :556-561, analyze L6), and the CLI prints only then, so no page or command can see the new run before
  its `starting` status exists: the window is closed rather than handled. The pid is the real supervisor's
  (`proc.pid`), so the liveness probe (`sdd-tasks.py` `supervisor_alive`, `:81-95`) works unchanged.
- **A supervisor that exits early (2026-11-08, analyze M1).** "Shows as dead" held only when the launcher exits
  (the command line: the orphan is reaped by init). When the dashboard launched it, the dead supervisor is the
  dashboard's unreaped child, a zombie, and signal 0 still succeeds, so the run read `preparing` for ever, Stop was
  accepted and never carried out, and new runs were refused. Two fixes, both kept: (1) `_start_supervisor` starts
  a daemon `threading.Thread(target=proc.wait)` right after `_spawn`, so the child is reaped whoever the parent
  is; (2) the supervisor's early exits (`main`'s no config, config errors, unknown profile, `bin/sdd-supervise.py:
  606-622`; `run_job`'s no record, not working, `:877-882`) call `abandon_start` (R17), which replaces its own
  `starting` status with `stopped` and logs a kept request as ignored. Both are needed: (2) alone is not enough,
  because `shown_state` checks the pid only (`bin/sdd-job.py` `_pid_alive`, `:169-177`), so a zombie still reads
  `preparing` whatever the status says, and (2) cannot run for a supervisor killed before its first line of
  Python; (1) alone leaves a `starting` status with a dead pid, which every reader treats as dead, but no reason
  and no line for a kept request. With both, the run shows `ended`, `stopped`, landing `pending` (`shown_state` `:300-302`, no live
  supervisor, no run.json; Merge closes it) or `suspended` when its run.json is there (a Continue), and the launch
  log says why. Alternative rejected: `signal(SIGCHLD, SIG_IGN)` in the dashboard (process-wide; it would
  break every `subprocess.run` return code there).
- **The usage wait survives the starting status (2026-11-08, analyze M2).** `main` keeps a dead supervisor's usage
  wait only when the previous status is `waiting` (`:633-643`); the `starting` status replaced it, so a Continue
  after a crash during the wait would start a session while the limit is still in force. `write_starting_status`
  copies `window` and `resets_at` from a previous `waiting` status (whatever its pid), and `main` accepts `state in
  ("waiting", "starting")` with a `resets_at` (R17 has the wait itself). Alternative: `main` reads the run record
  for the reset (the record has no reset time).
- **The read-then-replace race (2026-11-08, analyze L7).** `write_starting_status` reads `status.json`, then
  replaces it, so in principle it could overwrite the supervisor's first status written in between. In practice it
  cannot: the launcher writes microseconds after `_spawn`, and the supervisor first starts an interpreter, imports
  its modules, reads its config and runs git before its first `write_status`. Should it happen, the `starting`
  status still names the same live pid and run, so every reader still sees a live run, until the supervisor's
  next `write_status`. No lock is added.
- **Alternatives**: a `supervisor_pid` key on the run record plus a new rule in `controls()` and `shown_state`
  (two readers to change, and the record is the engine's, not the launcher's); refusing or hiding Stop until the
  supervisor writes (rejected by the person in clarify).

## R17. A request carries the run id; startup keeps one for its own run

- **Decision**: `write_control(logs, command, mode=None, request_id=None, job=None)` (`bin/sdd-control.py:127-148`)
  adds `"job": <job>` to `control.json` when `job` is given. `request()` passes the run id of the live supervisor:
  `status["job"]` when `supervisor_alive(status)` and its state is not `done`/`stopped`, else None. With R16 that
  is the launcher's id from the first moment.
- `bin/sdd-supervise.py` `main` replaces the unlink (`:627-632`) with `startup_control(logs, job_id)`: it reads the
  pending request with `read_control` (which deletes the file). A `stop` whose `job` equals this supervisor's
  `--job` is returned and kept as `args.startup_stop`; anything else (another run id, no `job` key, a plain
  supervisor without `--job`, or a command other than `stop`) is logged as ignored (R19) with `say("deleted a
  leftover control.json")` (the text `tests/test_supervise.py:648` already checks) and dropped.
- `run_job` carries out `args.startup_stop` right after `JOB_ID = job_id` (`:883`), before the first session,
  deciding on `tree_clean(project)` alone (2026-11-08, second analyze pass I1). Clean: it logs taken "stopping
  before any task", calls `close_session(sdd_job, project, job_id)` (`:789-811`: it archives a `.sdd/run.json` a
  Continue left under `.sdd/runs/<session>/` and returns None when there is none), then `end_run(…, "stopped",
  "stopped before any task")`. Dirty (a Continue whose half-done work is in the tree): taken "stopping now" and
  `suspend_run(…, "stopped by you", 0)`, so the run is suspended again exactly as it was. No `claude -p` starts.
  The earlier rule (end only when run.json is absent and the tree is clean) suspended a Continue with a clean tree
  again, while FR-006's exception and FR-005 require it to end, and while the in-session `before-task` branch
  (`:913-921`) ends that same case; the same press then gave a different outcome depending on whether the
  supervisor had started. `close_session` comes first because `land` refuses while run.json exists
  (`bin/sdd-job.py:431`, `_run_active`, through `_refuse_unsafe_checkout`, `:445-446`). When `run_job` returns earlier (no record, `:877-879`; not `working`, `:880-882`), the kept request is
  logged ignored with "the run is not working".
- **Rationale**: FR-032 ("carry out a request with its own run id … MUST delete, without carrying it out, any
  request with another run id or none, logging it as ignored with its id, run id (or none), command and why";
  wording of the 2026-11-08 clarification) and the edge case (a request left from an earlier run is still deleted
  and logged as ignored). The Continue branch keeps FR-007: work in flight means suspended, never ended over a
  dirty tree.
- **What an ignored request keeps (2026-11-08, spec Clarifications, FR-032).** The person chose to keep what the
  request said. Every taken line carries the request's `command`, `mode` and `request_run` (its `job`, null when it
  had none) besides `run` (data-model §8.2), so the line for a leftover reads "Stop after current task · ignored:
  left from before this supervisor started" and says which run it was meant for.
- **A malformed `control.json` (2026-11-08, analyze M3).** Today `main` unlinks `control.json` whatever it holds
  (`:627-632`). `read_control` returns None for a file that does not parse or is not an object without deleting it
  (`bin/sdd-control.py:161-166`), so `startup_control` built only on it would leave the file, and every later
  request would be refused "a request is already pending" (`write_control`'s `target.exists()`, `:137`).
  `startup_control` therefore unlinks `control.json` when it still exists after `read_control` returned None, says
  "deleted a leftover control.json", and logs taken `ignored` "unreadable request" with `id`, `command` and `mode`
  null. `read_control` itself is not changed: the mid-run readers keep today's behaviour (plan "Not in this
  pass").
- **Startup order and early exits (2026-11-08, analyze M1).** `main` computes `logs` and `status` and calls
  `startup_control` before the config checks (`:606-622`), so every exit knows the kept request. New
  `abandon_start(logs, status, args, reason)` is called on `main`'s three early returns (reason "the supervisor
  could not start") and `run_job`'s two (reason "the run is not working"): it writes `stopped` with the reason only
  when `status.json` is `starting` with `pid == os.getpid()` (the launcher's status for this very process; a status
  of any other supervisor is left alone), and logs taken `ignored` with the reason for a kept `args.startup_stop`.
- **The restarted usage wait (2026-11-08, analyze M2; R16).** `main`'s restart check reads `state in ("waiting",
  "starting")` with a `resets_at` and is skipped when a stop was kept at startup (the stop wins; R16's carried
  reset only delays a session, and a stopped run starts none). With `--job` the wait goes through new
  `restart_wait(args, logs, status, last)`: it writes a `waiting` status of its own (`window`, `resets_at`,
  `cycle` 0) so the run shows `paused` with its reset time and the banner's Keep paused (the `waiting` row of
  `controls()`, `bin/sdd-control.py:97-103`) is honoured, then calls `wait_for_reset` (`bin/sdd-supervise.py:
  535-555`, with its taken lines, R19). Its result is kept as `args.startup_outcome`: `go` starts the run as
  today; `held` and `stop-wait` make `run_job` return `suspend_run(…, SUSPEND_REASONS[…], 0)` before the first
  session, as `run_cycles` does for those outcomes. Without `--job` today's `sleep_until` stays: the plain `launch`
  writes no `starting` status, so its `waiting` status survives. Alternative rejected: keep `sleep_until` with the
  `starting` status in place (the run would read `preparing` for hours, and a Stop sent then would wait unread
  until the reset).
- **The order of the startup checks (2026-11-08, third analyze pass A4).** A plain supervisor (no `--job`) that
  meets a request carrying a `job` matched both "for another run" and "left from before this supervisor started".
  `startup_control(logs, job_id)` checks in this order, first match wins: (1) no `control.json` → None, nothing
  logged; (2) `read_control` gave None and the file still exists → unlink, ignored "unreadable request"; (3)
  `job_id` empty (no `--job`) → ignored "left from before this supervisor started", whatever the request
  carries; (4) the request has no `job` key (or an empty one) → the same reason; (5) its `job` differs from
  `job_id` → ignored "for another run"; (6) its command is not `stop` → ignored "only stop is taken at startup";
  (7) otherwise the request is returned (kept). Every ignored case says "deleted a leftover control.json". Reason
  for (3) before (5): a plain supervisor has no run of its own, so no request can be "another" run's from its
  point of view; the line's `request_run` still says which run it was meant for. Test (`JobTest` or
  `RunControlTest`): a supervisor without `--job` and `control.json` with `"job": "20200101-0000"` → taken
  `ignored` "left from before this supervisor started", `request_run` `20200101-0000`.

## R18. Stop now before any task ends the run, as Stop after this task does

- **Decision**: in `Cycle.run`, the `mode == "now"` branch (`bin/sdd-supervise.py:350-354`) sets `self.boundary =
  True` and `self.before_any_task = True` when `started == 0`, and says "stop now requested before any task:
  stopping the run" instead of "stop requested by the operator: stopping now". `run_cycles` then returns
  `before-task` (`:678-679`). `run_job`'s `before-task` branch (`:913-921`) first checks the tree: when
  `tree_clean(project)` is False (a resumed session's half-done work) it returns `suspend_run(…,
  SUSPEND_REASONS["stop-now"], 0)`; otherwise it closes the session and ends as today.
- **Rationale**: FR-007 ("Cut off at a task boundary, it MUST end cleanly"; before the first task is a boundary)
  and FR-032 (a stop in the start window, either mode, ends `stopped` "stopped before any task"). The tree check
  fixes the same gap for an after-task stop early in a Continue session, which today would archive `run.json` and
  end over half-done work (landing then refuses the dirty tree). The spec now says this outright (Clarifications
  2026-11-08: FR-006's exception for Stop now before any agent of the current session has started, with a clean
  tree; FR-007's "with a clean tree"), which settles analyze H1; the decision above is unchanged.
- **The stop-by time with a dirty tree (2026-11-08, analyze L1).** The `before-task` branch already computes
  `by_time` (`:916-917`). In the new dirty-tree suspend, the reason is `limit_reason("until", record["limits"])`
  when `by_time`, else `SUSPEND_REASONS["stop-now"]` ("stopped by you"), so a run cut off by its stop-by time never
  says the person stopped it.

## R19. The control log

- **Decision**: one append-only file `<state dir>/control-log.jsonl` (outside the project, beside `control.json`;
  `sdd-tasks.py` `state_dir`, `:60-66`). Two kinds of line (data-model §8.2): a **request line** written by
  `sdd-control.py` `request()` for every accepted or refused request, and by `send_control` for the supervisor's
  own stop-by request; a **taken line** written by the supervisor at every place it reads a request:
  `startup_control` and `run_job` (R17), `Cycle.run` (`:348-365`), `hold` (`:525-531`), `wait_for_reset`
  (`:542-555`). Both go through one writer, `sdd-control.py` `log_control(logs, entry)`: it opens the file with
  `os.O_WRONLY | os.O_APPEND | os.O_CREAT` and writes the line with one `os.write`, takes no lock (so it can never
  wait on `control.lock`, which `write_control` holds, and is called only after `write_control` returns), and
  catches every `OSError` and `ValueError`, returning False. `request()` and the supervisor ignore its result.
- `request(project, command, mode=None, source="cli")` gets the source: `"cli"` from `main` (`:206`),
  `"dashboard"` from `DashboardHandler._control` (`bin/sdd-dashboard.py:416`). `send_control` logs `"until"`.
  A 400 for a bad body or `mode` (`bin/sdd-dashboard.py:401-414`) never reaches `request()` and is not logged
  (FR-031 counts requests, not malformed posts).
- Reader: `sdd-control.py` `read_control_log(logs) -> dict[str, list]` returns each run id's requests in file order
  (data-model §8.3); lines that do not parse, and request or taken lines without a run id, are skipped; a
  taken line without
  a matching request line becomes its own entry (2026-11-08: it carries `command` and `mode`, R17).
- **The stop mode on a request line (2026-11-08, analyze L2).** `request(project, "stop")` with no mode writes
  `"mode": "after-task"` into `control.json` (`write_control`, `bin/sdd-control.py:140-141`), so its request line
  logs `mode or "after-task"` for a stop (null for the other commands), and the line says what the file says.
- **A stop that lets the run finish (2026-11-08, analyze L5).** Without `--job`, a stop taken as "stopping after
  the current task" can then reach the branch that lets the run finish because no task is left
  (`bin/sdd-supervise.py:386-397`). Logging the decision only at the boundary would leave the request "not taken
  yet" until then; so the first line stays, and that branch writes a second taken line for the same request,
  `letting the run finish: no task left`. The reader keeps the last taken line of a request (data-model §8.3), so
  the card shows what finally happened. `Cycle` keeps the request it is carrying out (`self.stop_control`) for
  that second line.
- **A stop before any task that meets a dirty tree (2026-11-08, second analyze pass I2).** `Cycle.run` logs a stop
  taken before any agent as "stopping before any task", but `run_job`'s `before-task` branch then suspends the run
  when the tree is not clean (R18). The same pattern as L5 applies: `Cycle` keeps the request as
  `self.stop_control` for either mode, `run_cycles` hands it to `run_job` as `args.stop_control` with the
  `before-task` outcome, and the dirty-tree suspend writes a second taken line `stopping now` for it. The startup
  path (R17) already writes "stopping now" for the same outcome, so both paths end with the same text.
- **Lines without a run (2026-11-08, second analyze pass A4).** A taken line whose `run` is null (a plain
  supervisor without `--job` taking a request) is skipped by the reader, like a request line without a run id; it
  stays in the file for anyone reading it by hand.
- **A request refused while the run is suspended (2026-11-08, third analyze pass I2; accepted).** `request()` takes
  the run id only from a live supervisor's status (R17), so a request refused while a run is suspended (no live
  supervisor) is logged with `run` null and shows on no card, although the person may have meant that run.
  Accepted and not changed: (a) the page draws no Stop, Resume or Keep paused button for a suspended run (its
  banner offers Continue and Drop through `data-job-action`, `tests/ui/test_banner.py:482-489`, and `controls()`
  allows no stop without a live supervisor: a supervisor-started run that is gone gets only a reason,
  `bin/sdd-control.py:87-111`), so only the command line can send it;
  (b) the refusal is answered at once where it was asked (the CLI exits 1 and prints it), so nothing is lost;
  (c) falling back to the open run's id would make `sdd-control.py` load `sdd-job.py` and decide which run is
  "open" for a request no run will read, and would put a row on the card for a run that was never asked to do
  anything. The line stays in the file with `run` null, as quickstart §9 step 1 already says for a refusal with no
  run going. No test change.
- **Rationale**: FR-031 (one line per request, the outcome, the supervisor's line with the same id, nothing in the
  project tree, a failing write never stops the request) and FR-034 (the file is the only store). JSON lines
  because the dashboard reads it back (R20) and `launch.log` mixes every supervisor message.
- **Alternatives**: the `say` lines in `launch.log` (not tied to a request id, and each launch appends to one
  file); a log in `.sdd/` (would dirty the tree during a run, Principle II and the run guard); a copy in the run
  summary (rejected by the person in clarify).

## R20. The Requests list on the run's card

- **Decision**: `bin/sdd-views.py` `job_view(project, record, requests=None)` (`:268-284`) adds `"requests"`: the
  run's list from `read_control_log` (`[]` when none). `list_job_views` (`:287-291`) reads the log once and passes
  each run's list; `get_job` reads it for its one run. The page's `jobCardHtml` (`bin/sdd-dashboard.py:3052-3063`)
  appends `jobRequestsHtml(job)`, which returns `''` for an empty list and otherwise a `Requests` title and one
  row per request (texts in data-model §8.5). It is shown on every card, live and past. `loadJobs`' redraw
  signature already includes the whole `jobs` list (`:3520`), so a new line redraws the card.
- **Where `jobRequestsHtml` is defined (2026-11-08, second analyze pass U1).** Right after `jobSummaryHtml`
  (`bin/sdd-dashboard.py:3021-3048`), inside the "Runs view" slice ("// Runs view (009 FR-021, FR-022)." at
  `:2852` to "// End of the Runs view helpers." at `:3245`) that `JobsViewTest` (`tests/test_sdd_dashboard.py:
  2545-2552`) and `JobBannerTest` (`:2828-2833`) run in node. `jobCardHtml` is called there by five existing
  tests (`:2597`, `:2605`, `:2664`, `:2669`, `:2688`); outside the slice the new call throws a ReferenceError in
  each. Same failure as R21's H2.
- **Reading the log never fails a view (2026-11-08, second analyze pass U2).** `state_dir` calls `mkdir`
  (`bin/sdd-tasks.py:60-66`) and can raise `OSError` (a sandbox); `get_status` (`:69-78`) already guards it. The
  views read the log through one helper that catches that `OSError` and gives `{}`, and `read_control_log` gives
  `{}` for any `OSError` on the read, so `requests` is `[]` and `/api/jobs`, `/api/jobs/<id>` and
  `/api/run/current` (via `open_job_view`/`last_job_view`, `bin/sdd-views.py:337-354`) never answer 500 because
  of it.
- **Rationale**: FR-034. The card is where the person looks for a run; reading the file on each 3-second poll is
  cheap (a line is about 200 bytes and a run has a handful).

## R21. The banner while a request is unanswered

- **Decision**: a page variable `controlSending` (`null`, or `{command, mode}` while a banner request is out).
  `controlButton` (`bin/sdd-dashboard.py:3717-3723`) draws every control button disabled while it is set, and the
  one whose `command` and `mode` match reads "sending…"; the usage checkbox (`#run-banner-continue`,
  `:3802-3804`) is disabled too. Because the banner is redrawn on every poll, the state lives in the variable, not
  in the DOM. `postControl` (`:3929-3952`) sets it, redraws the banner from the last `/api/run/current` answer
  (kept in a new variable `lastRun` that `loadRunState`, `:3679-3692`, sets), and posts with an `AbortController`
  whose `setTimeout(…, 10000)` aborts the fetch. Every ending clears the timer and `controlSending`, then calls
  `loadRunState()`: 202 → as today; another status → the existing error and toast; an abort → `runBannerError =
  'request failed: no answer after 10 s'` and a toast with the same text; any other rejection → the existing
  `'request failed: ' + e`. No retry anywhere.
- **Where the variables are declared (2026-11-08, analyze H2).** `tests/test_sdd_dashboard.py` `JobBannerTest`
  runs only slices of the page in node; the banner slice runs from "// Run banner (data-model.md §12.4"
  (`bin/sdd-dashboard.py:3694`) to "// POST /api/run/<command>" (`:3927`) (`tests/test_sdd_dashboard.py:
  2834-2835`). `controlButton` and `renderJobBanner` are in that slice and will read `controlSending`, so `var
  controlSending = null;` and `var lastRun = null;` go right after `var runBannerError = '';` (`:3696`). Declared
  beside `postControl` (outside the slice) they would throw a ReferenceError there and fail every `JobBannerTest`
  case. `loadRunState` (`:3679`, before the slice) only assigns `lastRun`, which is fine since `var` is hoisted in
  the page's one script. The usage checkbox is drawn only by `renderJobBanner`; `renderRunBanner` (`:3842`) draws
  none (analyze L3).
- **The Stop-now confirmation while preparing (2026-11-08, analyze M5).** `renderJobBanner`'s `stopQuestion`
  (`:3784`) says "its task is resumed from the journal on the next start", which FR-006's exception makes wrong
  before any task. While shown `preparing` and not `stopping` the confirmation is the preparing text of data-model
  §8.5; otherwise it stays.
- **A resumed session's Stop now confirmation (2026-11-08, second analyze pass A1). Decision: accept and keep the
  working text.** FR-006's exception also covers "before a resumed session's first agent", but a Continue reads
  `working` from its start, not `preparing`: run.json still names the earlier session's workflow, whose journal
  has `started` lines (`_has_started_agent`, `bin/sdd-job.py:252-278`). The banner therefore asks the working
  question ("… its task is resumed from the journal on the next start"). Rationale: (1) the page has no way to
  tell that moment from an agent at work, short of a new field on the view, and the window lasts only until the
  session's first agent starts (seconds); (2) the usual Continue has half-done work in the tree, and then the run
  is suspended, as the text says; (3) with a clean tree the run ends `stopped` "stopped before any task" with
  nothing lost (no task was half done; its tasks stay open for the next run), and the card's reason and its
  Requests row ("stopping before any task") then say what happened (FR-031). Alternatives rejected: a working
  text that names both outcomes (vaguer for the common case, an agent mid-task, where only suspension is
  possible); a new `resumed_not_started` flag in the view (more code for a seconds-long window).
- **How the timeout is told apart (2026-11-08, second analyze pass A5).** `postControl` keeps a local `timedOut`
  flag; the timer sets it and then calls `controller.abort()`; the `catch` shows "request failed: no answer after
  10 s" when the flag is set, else today's `'request failed: ' + e`. Chosen over `e.name === 'AbortError'`,
  which says only that some abort happened, not that this page's timer did it.
- **Rationale**: FR-033 and its clarification (an error beside the buttons, press again to retry, no automatic
  retry). The browser cannot tell an unanswered request from one the server took slowly; 10 s is the spec's
  number.

## R22. Testing Stop while preparing with real processes (SC-009, Principle IX)

- **Decision**: three layers, plus one test hook.
  1. **Process test, always runs** (`tests/test_supervise.py`, new class `StartWindowTest`): a temp git
     repository built as `JobTest.setUp` builds it (`tests/test_supervise.py:678-695`, but on `main`, since
     `launch_job` creates the run branch itself), with `XDG_STATE_HOME`, `FAKE_DIR`, `FAKE_TASKS`, `FAKE_CONTROL`
     and `SDD_CLAUDE` set through `unittest.mock.patch.dict(os.environ, …)`. It calls `sdd-launch.py` `launch_job`
     with `_spawn` patched (`bin/sdd-launch.py:85-86`) to start `[sys.executable, "-c", delay(seconds),
     *command[1:]]`, where `delay(seconds)` returns `"import os,sys,time; time.sleep(<seconds>);
     os.execv(sys.executable, [sys.executable] + sys.argv[1:])"`; the pid survives `execv`, so the `starting`
     status stays right. The delay is a parameter (2026-11-08, third analyze pass A3): 3 s here, where the test
     itself sends the request; about 10 s in the browser test (item 2), where the page must load, draw the
     "Preparing" banner and take the click before the supervisor starts. The wait for the supervisor's exit is
     `proc.wait(timeout=seconds + 15)`, so the 15 s budget counts from the end of the delay. Each of the two test
     files defines the one-line helper at module level (no import between `tests/` and `tests/ui/`).
     During the 3-second delay the
     test calls `sdd_control.request(project, "stop", "after-task", "dashboard")` (what the route calls), asserts
     it answers `ok`, waits for the supervisor to exit with `proc.wait(timeout=15)` on the `Popen` the patched
     `_spawn` kept (2026-11-08, analyze H3: polling `os.kill(pid, 0)` never ends, because a child the test process
     never waits on stays a zombie and signal 0 still succeeds; R16's reaper thread also waits on it, and
     `Popen.wait` from two threads is safe), and asserts: the record is ended `stopped`, "stopped before any task",
     landing `nothing`; `$FAKE_DIR/calls` does not exist (no `claude` started); the control log has the accepted
     request and a taken line "stopping before any task" with the same id. The same with `mode: now`. A third case
     spawns without the delay, with a fake session `[{"transcript": true}, {"run": "s1"}, {"sleep": 30}]`, waits
     until the session has started, that is `<project>/.sdd/run.json` exists (the fake writes it on the `run`
     step, `tests/fake/claude:56-57`) while `shown_state` is still `preparing`, then sends the request and asserts
     the same ending within 15 s (here `$FAKE_DIR/calls` exists) plus the `Cycle.run` say line "stop requested
     before any task: stopping the run" in `launch.log`. (2026-11-08, analyze M4: waiting only for `preparing` is
     true as soon as the `starting` status exists, so the stop could be taken at startup instead and the
     in-session path would be tested only by luck.)
  2. **Browser test** (`tests/ui/test_stop_preparing.py`, strict with `SDD_UI_TESTS=1`) on a git toy project. The
     environment (`XDG_STATE_HOME`, `FAKE_DIR`, `FAKE_TASKS`, `SDD_CLAUDE`) goes in the way
     `tests/ui/test_banner.py:85-86` already passes it: `mock.patch.dict(os.environ, …)` around
     `harness.start_dashboard`, whose `Popen` inherits it (`tests/ui/harness.py:88-90`). Here the patcher is
     started in `setUpClass` and stopped in `tearDownClass`, because test (2) also calls `launch_job` in-process
     and the test reads the state directory. The harness gets no `env` argument (2026-11-08, analyze L9: one way,
     the existing one). Start run in the page, wait for the banner text "Preparing" and for `.sdd/run.json` (as
     item 1, M4), click Stop after current task, and within 15 s read the record as ended `stopped` with "stopped
     before any task" and the control log lines. A second test uses `launch_job` with the delayed spawn of item 1, at
     `delay(10)` (A3), and clicks Stop before the supervisor runs. A third uses Playwright's `page.route` on
     `**/api/run/stop` for FR-033 (data-model §8.5).
  3. **`[manual]` record**: `.sdd/learnings.md` line 20 says a run's agents cannot always launch a browser, so
     `tests/ui/` may skip during a run. One `[manual]` task runs `SDD_UI_TESTS=1 python3 -m unittest
     tests.ui.test_stop_preparing tests.ui.test_banner tests.ui.test_jobs` and records the result.
  4. **Hook**: `bin/sdd-supervise.py` `--claude` (hidden, `:591`) defaults to `os.environ.get("SDD_CLAUDE") or
     "claude"`, so a supervisor that the launcher or the dashboard starts can be pointed at `tests/fake/claude`
     from the environment. The launcher already passes its environment on (`bin/sdd-launch.py:194`).
- **Rationale**: SC-009 ("covered by a browser test with a real dashboard, launcher and supervisor and the fake
  `claude`") and Principle IX (tests in the same task; what cannot run automatically gets a `[manual]` task). Layer
  1 keeps the behaviour covered by the project checks even where the browser skips.

## R23. The first analyze pass of the amendment (2026-11-08)

`/speckit-analyze` on `3f95a4b` (report `.sdd/scratch/analyze-009-1.md`) found 1 CRITICAL, 3 HIGH, 5 MEDIUM and 9
LOW. Code line numbers were re-checked on `49b0d90` (`bin/`, `tests/` and `README.md` unchanged since `735714b`).

| Finding | Settled in | Where |
|---|---|---|
| C1 test of the stop-by request's `job` not in T035 | task split for `/speckit-tasks` | plan 15b, 15e, the note after 15h |
| H1 FR-006 had no exception for Stop now before any task | spec Clarifications 2026-11-08 (`49b0d90`) | R18 already matched; noted there |
| H2 `controlSending` outside the `JobBannerTest` slice | placement rule | R21, plan 15g |
| H3 polling `os.kill(pid, 0)` on an unwaited child | test design: `proc.wait(timeout=15)` | R22 item 1, plan 15h |
| M1 an early-exiting supervisor stays `starting`, a zombie | reaper thread + `abandon_start` | R16, R17, plan 15a, 15c, data-model §8.1, §8.2 |
| M2 `starting` hides a dead supervisor's usage wait | carried `window`/`resets_at` + `restart_wait` | R16, R17, plan 15a, 15c, data-model §8.1 |
| M3 a malformed `control.json` kept for ever | `startup_control` deletes it | R17, plan 15c, data-model §8.2 |
| M4 the preparing case could take the stop at startup | wait for `.sdd/run.json` before the stop | R22 item 1 and 2, plan 15h, quickstart §8 |
| M5 README and the Stop-now confirmation before any task | README `:163` in the docs task; new confirmation text | R21, plan 15g, 15h, data-model §8.5 |
| L1 stop-by time with a dirty tree said "stopped by you" | `limit_reason("until", …)` | R18, plan 15d |
| L2 a stop with no mode logged `mode: null` | `mode or "after-task"` | R19, plan 15b, data-model §8.2 |
| L3 "the other banner renderer" | only `renderJobBanner` | R21, plan 15g |
| L4 "no test reads the control log yet" in T038 | reword in `/speckit-tasks` | the note after plan 15h |
| L5 a stop under way that lets the run finish | second taken line; the reader keeps the last | R19, plan 15e, data-model §8.2, §8.3 |
| L6 stale line references | README `## Runs` `:154`, `/api/jobs/start` `:542`, `JobTest.setUp` `:678-695` | R16, R22, plan 15h |
| L7 read-then-replace race in `write_starting_status` | noted, no lock | R16 |
| L8 "deleted unread" vs a logged id | spec Clarifications 2026-11-08 (FR-032 wording) | R17 rationale |
| L9 harness `env` vs the `os.environ` pattern | the existing `os.environ` pattern; no harness change | R22 item 2, plan 15h |

The 2026-11-08 clarification that an ignored request keeps what it said (FR-032) adds `command`, `mode` and
`request_run` to every taken line (R17, data-model §8.2).

## R24. The second analyze pass of the amendment (2026-11-08)

`/speckit-analyze` on `2e205ce` (report `.sdd/scratch/analyze-009-2.md`) found 0 CRITICAL, 1 HIGH, 6 MEDIUM and 10
LOW. Line numbers re-checked on `f1ea4ae` (`bin/`, `tests/` and `README.md` unchanged since `735714b`). Design
findings are settled in these documents; test-design and wording findings go straight into the tasks.

| Finding | Settled in | Where |
|---|---|---|
| I1 a kept stop chose its ending by `.sdd/run.json`, so a clean Continue was suspended again | decide on `tree_clean` alone; clean → `close_session`, `end_run` | R17, plan 15c, data-model §8.2, §8.5, runs-cli contract, quickstart §8 step 10 |
| I2 the taken line said "stopping before any task" for a run then suspended | second taken line "stopping now" via `args.stop_control` | R19, plan 15e, data-model §8.2 |
| U1 `jobRequestsHtml` outside the node slice | placement right after `jobSummaryHtml` | R20, plan 15f |
| U2 `OSError` from `state_dir` or the read | `requests` `[]`, never a 500 | R20, plan 15f, data-model §8.3, §8.4, dashboard-routes contract |
| A1 a resumed session's Stop now confirmation | accepted: the working text stays | R21, plan 15g, data-model §8.5, dashboard-routes contract |
| A4 a taken line with `run` null | skipped by the reader | R19, plan 15f, data-model §8.3 |
| A5 telling the timer's abort apart | a `timedOut` flag set by the timer | R21, plan 15g, data-model §8.5 |
| D1 the note after 15h used the old task numbers; "15a–15g" | rewritten with T034–T045; "15a–15h" | plan |
| README "Stop and resume" is `:131`, not `:134` | `:131` (its Stopping bullet is `:134`) | plan 15h |
| D2 FR-005 lacked the clean-tree clause | spec (`f1ea4ae`) | spec FR-005 |
| T1 T038's stop-by "1 s ahead" is flaky | tasks: +2 to 3 s and a 5 s session sleep, as `tests/test_supervise.py:827` | T038 |
| T2 T043 test (2) does not prove the startup path | tasks: assert the startup say line in `launch.log` and no `$FAKE_DIR/calls` | T043 |
| A2 in-process `main`/`run_job` need `XDG_STATE_HOME` on `os.environ` | tasks: patch `os.environ`, build `args` with `parse_args` | T037 |
| A3 "control.json had job" cannot be observed | tasks: assert the request line's `run` and the taken line's `request_run` | T039 |
| A6 the toy project and the Start run dialog | tasks: `build_toy_project` in the test's own folder, git init, commit, `start_dashboard(project)`; accept the dialog as `tests/ui/test_start_run.py` | T043 |
| C1 400/403/415 tests should assert no log file | tasks | T035 |
| C2 "no task ticked" not asserted | tasks: every task still `- [ ]` | T042, T043 |
| C3 checkbox-flip tests not named | tasks: `tests/ui/test_banner.py:438-480` must still pass | T041 |
| K1 "what done looks like" in manual.md T045 | manual.md: "Done: the last line is `OK`, with no skipped tests." | manual.md |

## R25. The third analyze pass of the amendment (2026-11-08)

`/speckit-analyze` on `5c1c3d0` (report `.sdd/scratch/analyze-009-3.md`) found 0 CRITICAL, 2 HIGH, 3 MEDIUM and 4
LOW. Line numbers re-checked on `56c8ffb` (`bin/`, `tests/` and `README.md` unchanged since `735714b`).

- **B1, decided by the person (answer B, spec Clarifications 2026-11-08, FR-017, `56c8ffb`).** A session with no
  task entries gets no short report, so a run stopped before any task commits nothing on its branch and lands
  `nothing`, whether or not its session wrote `.sdd/run.json`. The alternative (a): keep the report and reword
  "nothing landed" to "no task commit landed" in the spec, the tests and the page text. The person chose (b).
  It also fits the data: such a report says only "Committed (0)" and "Not committed: None.", which the run summary
  (its `sessions`, outcome and reason) already keeps, and without the change the landing depended on whether the
  session had got as far as writing `.sdd/run.json`.
  Evidence: with the one-line change applied to a copy of `56c8ffb`, the whole unit suite passes (765 tests, 1
  skipped), so no existing test expects a report for such a session; a new `JobTest` case (a `run` step, then an
  after-task stop before any agent) fails on `56c8ffb` with "'landed' != 'nothing'" and passes with the change.
  Design and tests: plan 15i, data-model §4.
- **Rationale for doing it in the engine, not in the tests:** FR-017 now says it, and every test and text written
  for the amendment (T036, T038, T042, quickstart §8 steps 3 and 7, data-model §8.5, the README) already assumed
  it.

| Finding | Settled in | Where |
|---|---|---|
| B1 a stop before any task landed the session's short report | engine: `write_session_reports` skips a session with no task entries | spec FR-017 (`56c8ffb`), plan 15i, data-model §4, quickstart §8 steps 3 and 10 |
| B2 `WorkingJobBannerTest` fails on `main` since T028 (`c09d2d0`) | move the test into a class whose `RUN_JSON` has a `started` line; add a preparing test where it was | plan 15g, the note after 15h (T041) |
| A1 T038's clean stop-now test did not say "no `run` step" | task wording | the note after 15h (T038) |
| A2 T037's in-process `main` setup and its stdout | task wording: the `JobTest` project, no record, `contextlib.redirect_stdout` | the note after 15h (T037) |
| A3 a 3 s start delay is too short for the browser | `delay(seconds)`: 3 s in the process test, 10 s in the browser; `proc.wait(timeout=seconds + 15)` | R22 item 1 and 2, plan 15h |
| A4 two startup reasons matched a plain supervisor with a `job` request | the order of the startup checks: no `--job` → "left from before…" first | R17, plan 15c, data-model §8.2 |
| A5 `FAKE_CONTROL` in T043 is not needed | task wording: drop it | the note after 15h (T043) |
| I1 FR-032 had no clean-tree clause | spec (`56c8ffb`) | spec FR-032 |
| I2 a request refused while suspended has `run` null | accepted, not changed | R19, plan 15f |
