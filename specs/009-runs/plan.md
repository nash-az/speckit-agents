# Implementation Plan: Runs (009)

**Branch**: `009-runs` (built on `auto/20261106-1204`) | **Date**: 2026-11-06 (amended the same day; amended again
2026-11-07) | **Spec**: `spec.md`

**Input**: `spec.md`, amended 2026-11-06 after run `20261106-1204` (report `.sdd/runs/20261106-1204.md`) and its
analyze pass; constitution v1.2.1. Amendment 2026-11-07 (Stop while preparing and the run-control trace: spec
Clarifications "Session 2026-11-07", FR-031 to FR-034, SC-009, the last edge case): `main` at `fc63f0d`,
constitution v1.3.0; Engine changes item 15, research R15–R22, data-model §8.

## Summary

A Job becomes a short-lived **run**; the task is the lasting record. Keep the machinery 005 built and change its
meaning instead of adding a second record type: the 005 Job record (`.sdd/jobs/<id>.json`) **is** the run record,
its `runs` list holds the run's sessions. Module and route names (`sdd-job.py`, `/api/jobs/…`, `--job`) stay as
internal names; every word the person reads says "run" (FR-022). Renaming files and routes is left to 008.

T001–T012 built the first pass (task histories, run summaries, landing, suspend/Drop, task-boundary stop, the
dashboard's run words). The amendment finishes the switch from 005 to 009 functions, stashes and ends at a boundary
with leftover edits, records `suspended`/`resumed` history steps, writes the summary after landing with durations
and usage, restarts only the newest `waiting` run, adds CLI parity, a carry-over test on a copy of this
repository's records, the `/sdd-run` warning without a supervisor (FR-027), and plain-language task summaries
(FR-028–FR-030). Decisions: `research.md`; shapes: `data-model.md`; interfaces: `contracts/`.

The 2026-11-07 amendment (T001–T032 built) makes Stop work from the moment Start run answers: the launcher writes
a `starting` status for the new supervisor before it returns, a request carries its run id, and the supervisor
carries out a stop for its own run at startup (deleting any other) and ends a run stopped before any task in
either stop mode. Every run-control request leaves a line in a control log in the state directory, with the
supervisor's line for what it did; the run's card lists them as **Requests**; the banner shows "sending…" and
gives up after 10 s. Tests press Stop in both moments with a real launcher, supervisor and dashboard and the fake
`claude` (research R22).

## Technical Context

**Language/Version**: Python 3.11+ (stdlib only) for `bin/`; JavaScript for `workflows/sdd-run.js` and the
dashboard page (inline in `bin/sdd-dashboard.py`); Markdown skills.
**Primary Dependencies**: none new. git CLI; Claude Code (Workflow tool, `claude -p`).
**Storage**: files. Run records `.sdd/jobs/` (ignored), task histories `.sdd/tasks/<feature>/<id>.jsonl` and run
summaries `.sdd/runs/<id>.json` (committed), live session `.sdd/run.json` (ignored), supervisor state outside the
project.
**Testing**: `python3 -m unittest discover -s tests` (unit, supervisor against `tests/fake/claude`, dashboard
routes), `node --test tests/workflow.test.mjs`, `claude plugin test .`; browser tests in `tests/ui/` with
`SDD_UI_TESTS=1`. Amendment 2026-11-07: a process-level start-window test in `tests/test_supervise.py` that the
project checks always run, plus browser tests that may skip inside a run (`.sdd/learnings.md` line 20), so a
`[manual]` task runs them once outside a run (research R22).
**Target Platform**: macOS/Linux, local repository without a remote.
**Project Type**: Claude Code plugin (CLI helpers, skills, workflow, local dashboard).
**Performance Goals**: none beyond today (the dashboard polls every 3 s; history reads are per task).
**Constraints**: constitution v1.2.1 II (only the engine lands, and after landing may commit the run's summary and history files on the base; never force, push or rewrite), VI (one task, one commit),
VII (no model in the engine); the suite stays green on every task commit. Amendment 2026-11-07: constitution
v1.3.0, adding VIII (no run before a clean analyze) and IX (tests in the same task); the run-control files stay in
the state directory, outside the project.
**Scale/Scope**: one run at a time per project; tens of tasks per feature; histories of a few hundred lines.

## Constitution Check (v1.2.1)

| Principle | Check | Result |
|---|---|---|
| I. Spec Kit stays the source of truth | Summaries are an indented line plain Spec Kit still reads; `tasks.md` stays the task list | Pass |
| II. Safe without a person watching | Only `bin/sdd-job.py` (plain code) lands, at the end or by fast-forward while suspended, ff or plain merge, abort on conflict (v1.2.0 exception). After landing, the engine commits only the ended run's summary file on the base, which v1.2.1 allows ("After landing, the engine may also commit the ended run's summary and task-history files (only those files) on the base"; research R2). Leftover edits are stashed, never deleted (R4). Agents still cannot touch protected branches | Pass |
| III. Never guess on a person's behalf | Drop, Continue, Merge stay the person's; a stale `waiting` run never restarts by itself (R6) | Pass |
| IV. Verified, not reported | Landing only lands commits the verifier approved and the checks passed | Pass |
| V. Deterministic where possible | Boundary detection, landing, summaries and parsing are plain code with tests | Pass |
| VI. One task, one commit | Histories go in the report or run commits, not in task commits (FR-017 amended) | Pass |
| VII. AI-free at the core | No model in `bin/`; summaries are written by `/speckit-tasks`, not parsed by a model | Pass |
| Development: runs start through the supervisor | FR-027: `/sdd-run` warns without `SDD_SUPERVISED=1` (R3) | Pass |

Re-checked after the design (data-model, contracts): no violation, no complexity to justify.

## Constitution Check (v1.3.0, amendment 2026-11-07)

Re-done for the amendment against constitution v1.3.0 (`.specify/memory/constitution.md`, amended 2026-11-07),
before research and again after the design (data-model §8, contracts).

| Principle | Check | Result |
|---|---|---|
| I. Spec Kit stays the source of truth | No change to Spec Kit's files or formats; the amendment is in `spec.md` and these documents | Pass |
| II. Safe without a person watching | The `starting` status, `control.json` and the control log are in the state directory outside the project (`sdd-tasks.py` `state_dir`, `bin/sdd-tasks.py:60-66`); nothing in the tree changes, so the run guard and the clean-tree checks are untouched. A stop before any task lands nothing (`end` finds no commit the base lacks); a stop with half-done work in the tree suspends instead of ending (research R17, R18), so work is never dropped. No push, no history rewrite | Pass |
| III. Never guess on a person's behalf | A stop is carried out only for the run it names; a request for another run, or one left from an earlier supervisor, is deleted and logged as ignored, never applied (R17). The browser never retries by itself (R21) | Pass |
| IV. Verified, not reported | No task is ticked or landed by a stop before any task; the control log reports what the supervisor did, written by the supervisor itself at the point it acts (R19) | Pass |
| V. Deterministic where possible | Request matching, the start-window rule and the log are plain code with exact texts (data-model §8.2, §8.5) | Pass |
| VI. One task, one commit | No commit is added or changed by the amendment | Pass |
| VII. AI-free at the core | `sdd-control.py`, `sdd-supervise.py`, `sdd-launch.py` and `sdd-views.py` stay stdlib Python with no model in the path; the Requests list is read from a file | Pass |
| VIII. Every spec through the Spec Kit flow | The amendment went through `/speckit-specify` (as an amendment of 009) and `/speckit-clarify` (3 questions, the person's answers, `fc63f0d`); this `/speckit-plan` adds research, data model, contracts and quickstart; `/speckit-tasks` and `/speckit-analyze` follow. No run starts on 009 until analyze reports no CRITICAL or HIGH finding | Pass (gate for the run: analyze) |
| IX. Tests for every change | Every behaviour in Engine changes item 15 names its tests in the same item (15a–15g); SC-009 has a process test that always runs, a browser test, and a `[manual]` task to run the browser tests outside a run, because a run's agents may not start a browser (`.sdd/learnings.md` line 20; R22) | Pass |
| Development: runs start through the supervisor | Unchanged; the amendment makes the supervisor's own start window controllable | Pass |

Re-checked after the design: no violation, no complexity to justify. One test hook is added (`SDD_CLAUDE` as the
default of the hidden `--claude`, R22 item 4); it changes nothing when unset.

## Project Structure

### Documentation (this feature)

```text
specs/009-runs/
├── spec.md          # amended 2026-11-06
├── plan.md          # this file
├── research.md      # R1–R14; amendment 2026-11-07: R15–R22
├── data-model.md    # §1 run record, §2 normalize, §3 shown state, §4 lifecycle, §5 task history, §6 run summary, §7 task summary line,
│                    # §8 run control: starting status, control.json job, control log, Requests, page texts (2026-11-07)
├── quickstart.md    # validation with the fake claude
├── manual.md        # the person's steps for the [manual] validation task
├── contracts/
│   ├── runs-cli.md          # sdd-launch / sdd-control / sdd-job / sdd-tasks parse / sdd-history
│   └── dashboard-routes.md  # /api/jobs…, /api/project/task-history, run view, page texts
└── tasks.md
```

### Source code touched

```text
bin/sdd-job.py        # run record, normalize, shown_state, land, end, suspend, drop_suspended, stash_and_end, after_settle, CLI
bin/sdd-history.py    # harvest, record_step, finalize (histories), write_summary (after landing)
bin/sdd-supervise.py  # task boundary, endings, usage_peak, "stopped before any task"
bin/sdd-launch.py     # refusals, continue_job (resumed step), preview task_list summary
bin/sdd-tasks.py      # parse: task summary
bin/sdd-views.py      # job_view (changed_files, summary), project_tasks (summary)
bin/sdd-dashboard.py  # Runs view, card buttons, Drop confirm, task card summary + Details, Start run list, routes
skills/sdd-run/SKILL.md, templates/agent-tags.md, skills/sdd-init/SKILL.md
tests/ (test_sdd_job.py, test_sdd_history.py, test_supervise.py, test_sdd_launch.py, test_sdd_tasks.py,
        test_sdd_dashboard.py, test_skill_text.py, test_carry_over.py (new), fixtures/carry-over/ (new), ui/)
README.md, CLAUDE.md

# Amendment 2026-11-07 (item 15)
bin/sdd-control.py    # write_control(job), request(source), log_control, read_control_log, controls ("starting")
bin/sdd-supervise.py  # write_starting_status, startup_control, taken, run_job start-window stop, Cycle.run Stop now
                      # before any task, taken lines in Cycle.run/hold/wait_for_reset, --claude from SDD_CLAUDE
bin/sdd-launch.py     # _start_supervisor(job_id) writes the starting status
bin/sdd-tasks.py      # current_state: "starting" reads as running
bin/sdd-views.py      # job_view / list_job_views: requests
bin/sdd-dashboard.py  # _control source "dashboard", Requests list on the card, banner sending state and timeout
tests/ (test_sdd_control.py, test_supervise.py (StartWindowTest new), test_sdd_launch.py, test_run_state.py,
        test_sdd_job.py, test_sdd_dashboard.py, ui/harness.py, ui/test_banner.py, ui/test_jobs.py,
        ui/test_stop_preparing.py (new))
README.md
```

**Structure Decision**: the existing plugin layout; no new module. One new test file and one fixture folder; the
amendment adds one test file (`tests/ui/test_stop_preparing.py`) and one state-directory file
(`control-log.jsonl`).

## Engine changes

Built (T001–T012), as the code stands on `auto/20261106-1204`:

1. `bin/sdd-history.py` (T001): `harvest`, `append`, `read`, `record_step`, `write_summary`, `finalize`; CLI
   `harvest`, `show`. Shapes: data-model §5, §6.
2. `bin/sdd-job.py` (T002–T004): `owns_run_json`, `normalize`, `shown_state`, `is_open`, `open_run` (beside the
   005 `display_state` and `open_job`, which T002 left in place); `land`, `land_ready`, `end`, `suspend`,
   `drop_suspended`; CLI `land`. Data-model §1–§4.
3. `bin/sdd-supervise.py` (T005–T006): `task_boundary`; endings through `end_run` / `suspend_run`;
   `close_session`.
4. `bin/sdd-launch.py` (T007): `open_run_refusal` texts; `continue_job` only from shown `suspended`.
5. `workflows/sdd-run.js`, `skills/sdd-run/SKILL.md` (T008): trailers `Task`, `Run`, `Attempts`, `Model`; harvest
   before the report commit.
6. `bin/sdd-dashboard.py`, `bin/sdd-views.py` (T009–T010): "run" words, card buttons by shown state, banner,
   `POST /api/jobs/<id>/land`, task History.

Amendment (T013 onward; each cites research and data-model):

7. Task summaries (FR-028–FR-030; R7, R8; data-model §7): `sdd-tasks.py` parses `summary`; the tasks template
   asks for it; the card and Start run show it; 009's tasks.md has one per task (written with this plan).
8. Switch-over (R1, R6, R11): CLI `list`/`show` use `shown_state`; CLI `merge` and the dashboard's `/merge` call
   `land`; `/drop` and CLI `drop` always call `drop_suspended`; `after_settle` newest-only on 009 records;
   `continue_job` without `finish_if_complete`/`from_waiting`; then the unused 005 functions go.
9. Histories (R10): `suspended` and `resumed` steps; `duration_s` and `tokens` on harvested agent steps (R9).
10. Summary after landing (R2, R9; data-model §6): `finalize` commits histories only; `land` writes and commits
    the summary once the landing is final; `usage_peak` from the supervisor.
11. Boundary stop (R4, R5): `stash_and_end`; "stopped before any task"; shown `preparing` (data-model §3).
12. Drop confirm with the half-done tasks and changed files (FR-010); past-run summary line (FR-021, US4).
13. FR-027: `/sdd-run` preflight warning; README and CLAUDE.md say runs start via the launcher or Start run.
14. Carry-over test on a fixture copy of this repository's `.sdd/jobs` and `.sdd/runs` (SC-006, R12); the stdout
    leak in `tests/test_sdd_launch.py` (R13).

Amendment 2026-11-07 (FR-031 to FR-034, SC-009; research R15–R22; data-model §8; contracts "Amendment
2026-11-07" sections). Line numbers are on `main` at `fc63f0d`. Each sub-item is one task's worth, in this order;
its tests go in the same task (Principle IX).

15. Stop while preparing and the run-control trace.
    - **15a. Start window** (FR-032; R16; data-model §8.1). `bin/sdd-supervise.py`: new
      `write_starting_status(path, job_id, pid)` next to `write_status` (`:461-473`). `bin/sdd-launch.py`:
      `_start_supervisor(project, command, job_id=None)` (`:190-203`) calls it with `state_dir/status.json` and
      `proc.pid` after `_spawn` when `job_id` is given; `launch_job` (`:328`) and `continue_job` (`:440`) pass the
      run id; `launch` (`:186`) passes none. `bin/sdd-control.py` `controls()` (`:90`): `state in ("running",
      "starting")`. `bin/sdd-tasks.py` `current_state` (`:876`): add `"starting"` to the `running` tuple.
      Tests: `tests/test_sdd_launch.py` (with `_spawn` patched to return an object whose `pid` is `os.getpid()`):
      `launch_job` and `continue_job` leave `status.json` `{"state": "starting", "job": <id>, "pid": <pid>}`;
      `write_starting_status` writes nothing when `status.json` already has that pid; `launch` leaves no
      `starting` status. `tests/test_sdd_control.py` `ControlsTableTest.test_starting`: `stop` True, `reason` None,
      and `request(…, "stop", "after-task")` is `ok`. `tests/test_run_state.py`: `starting` with a live pid and a
      run.json → `running`. `tests/test_sdd_job.py`: a `working` record with a `starting` status (live pid) and no
      run.json is shown `preparing`.
    - **15b. Requests carry the run id and leave a request line** (FR-031, FR-032; R17, R19; data-model §8.2).
      `bin/sdd-control.py`: `write_control(…, job=None)`; new `log_control(logs, entry)`; `request(project,
      command, mode=None, source="cli")` reads `job` from a live, not `done`/`stopped` status, passes it to
      `write_control`, then logs one request line (accepted with the id, or refused with the exact error, `id`
      null); `send_control` passes `job` on and logs source `until` after a successful write; `main` passes
      `"cli"`. `bin/sdd-dashboard.py` `_control` (`:416`) passes `"dashboard"`. Update the module docstring of
      `sdd-control.py` (`:2-17`): the log file and its writer. Tests (`tests/test_sdd_control.py`): `control.json`
      has `job` when the status names a live run, no `job` key otherwise; an accepted and a refused request each
      leave one line with every §8.2 key; the CLI line has `source` `cli`; with `control-log.jsonl` made a
      directory, `request` still answers `ok` and `log_control` returns False; two processes logging at once
      give two whole lines. `tests/test_sdd_dashboard.py` `RunControlTest`: a 202 and a 409 from `POST
      /api/run/stop` each leave a `dashboard` line; a 400 (unknown mode) leaves none.
    - **15c. Startup keeps a stop for its own run** (FR-031, FR-032, the last edge case; R17; data-model §8.2).
      `bin/sdd-supervise.py`: new `taken(logs, control, text, reason=None)` and `startup_control(logs, job_id)`;
      `main` (`:627-632`) sets `args.startup_stop = startup_control(logs, args.job)` instead of the unlink;
      `parse_args` sets `args.startup_stop = None`; `run_job` carries it out after `JOB_ID = job_id` (`:883`): taken
      "stopping before any task" and `end_run(…, "stopped", "stopped before any task")` when `.sdd/run.json` is
      absent and `tree_clean(project)`, else taken "stopping now" and `suspend_run(…, "stopped by you", 0)`; the
      two early returns (`:877-882`) log taken "ignored", "the run is not working" for a kept stop. The say lines
      of data-model §8.5. Update the module docstring (`:23-28`, "reads and deletes"). Tests
      (`tests/test_supervise.py` `JobTest`): `control.json` written before the supervisor starts with `"job":
      JOB_ID` → no `claude` call, ended `stopped` "stopped before any task", landing `nothing`, the say line and a
      taken line; the same with `mode: now`; `"job": "20200101-0000"` → "deleted a leftover control.json", taken
      `ignored` "for another run", the run goes on (one call); no `job` key → `ignored` "left from before this
      supervisor started"; with `.sdd/run.json` and an uncommitted file (a Continue) → suspended "stopped by you",
      taken "stopping now", the file still there; `RunControlTest.test_a_stale_control_file_is_deleted_at_startup`
      (`:648`) still passes.
    - **15d. Stop now before any task ends the run** (FR-007, FR-032; R18). `Cycle.run` (`:350-354`): with
      `started == 0`, set `self.boundary` and `self.before_any_task`, and say "stop now requested before any task:
      stopping the run". `run_job`'s `before-task` branch (`:913-921`): when `tree_clean(project)` is False,
      return `suspend_run(…, SUSPEND_REASONS["stop-now"], 0)` before `close_session`. Tests (`JobTest`): a
      `{"control": {"command": "stop", "mode": "now"}}` step before any `started` line → ended `stopped` "stopped
      before any task", not suspended; an after-task stop before any agent in a session that leaves the tree dirty
      → suspended "stopped by you", `.sdd/run.json` kept. The existing Stop-now tests with an agent started keep
      passing.
    - **15e. Taken lines wherever a request is read** (FR-031; R19; data-model §8.2 table). `Cycle.run`
      (`:348-365`; `logs = self.log_path.parent`), `hold` (`:525-531`), `wait_for_reset` (`:542-555`): one
      `taken(…)` call per request read, with the texts of the table. Tests (`tests/test_supervise.py`
      `RunControlTest` and `JobTest`, reading `<logs>/control-log.jsonl`): an after-task stop mid-task → "stopping
      after the current task"; a second one → `ignored` "a stop after the current task is already under way"; a
      stop now mid-task → "stopping now"; Resume while held → "resuming"; hold during the usage wait → "staying
      paused after the reset", the same hold again → `ignored` "already so"; a stop-by time → a request line with
      `source` `until` and its taken line.
    - **15f. Requests on the run's card** (FR-034; R20; data-model §8.3–§8.5). `bin/sdd-control.py` new
      `read_control_log(logs)`. `bin/sdd-views.py`: load `sdd-control.py` as the other modules are loaded
      (`:25-32`); `job_view(project, record, requests=None)` adds `requests` (reads the log for its run when None);
      `list_job_views` (`:287-291`) reads the log once and passes each run's list. `bin/sdd-dashboard.py`: new
      `jobRequestsHtml(job)` called from `jobCardHtml` (`:3052-3063`) after `jobSummaryHtml` and before the
      buttons; CSS `.job-requests` and `.job-request` next to `.job-card` (`:1292-1302`). Tests:
      `tests/test_sdd_control.py` `read_control_log`: grouping by run in file order, the `until` id in two runs
      kept apart, a taken-only entry, a bad line skipped, `{}` for a missing file. `tests/test_sdd_dashboard.py`:
      `GET /api/jobs` gives each view `requests` (`[]` without lines). `tests/ui/test_jobs.py`: a card whose run
      has an accepted, taken request shows exactly "<HH:MM:SS> · Stop after current task · dashboard · accepted ·
      stopping before any task"; a refused one "… · refused: not available now"; one not taken "… · not taken
      yet"; a run without lines has no `.job-requests`.
    - **15g. Sending state on the banner** (FR-033; R21; data-model §8.5). `bin/sdd-dashboard.py`: `controlSending`
      and `lastRun` page variables; `loadRunState` (`:3679-3692`) keeps `lastRun`; `controlButton` (`:3717-3723`)
      disables while sending and labels the matching button "sending…"; the usage checkbox in `renderJobBanner`
      (`:3802-3804`) is disabled while sending; `postControl` (`:3929-3952`) with `AbortController`, the 10 000 ms
      timer and the texts of §8.5. Tests (`tests/ui/test_banner.py`, `page.route("**/api/run/stop", …)`): a route
      that holds the request → the pressed button reads "sending…" and every `button[data-control]` is disabled;
      after 10 s "request failed: no answer after 10 s" in `.run-banner-error` and the toast, the buttons enabled
      again, and the route saw one request; `route.abort()` → "request failed: …" and enabled; a 409 still shows
      its error.
    - **15h. Stop while preparing end to end** (SC-009; R22). `bin/sdd-supervise.py` `parse_args` (`:591`):
      `--claude` default `os.environ.get("SDD_CLAUDE") or "claude"`. `tests/ui/harness.py` `Dashboard(project=None,
      env=None)`: `env` merged into `os.environ` for the `Popen` (`:88-92`). New `tests/test_supervise.py`
      `StartWindowTest` (research R22 item 1: delayed spawn, after-task and now, and the preparing moment). New
      `tests/ui/test_stop_preparing.py` (R22 item 2: Start run in the page then Stop while "Preparing"; the delayed
      spawn then Stop; each ended `stopped` "stopped before any task" within 15 s with the two control log lines).
      README "Runs" (`README.md:138`): the control log file and the Requests list in one sentence each. A
      `[manual]` task runs the browser tests with `SDD_UI_TESTS=1` outside a run and records the result (R22
      item 3).

## Not in this pass

- A plain in-session `/sdd-run` (no supervisor) gets trailers and task histories but no automatic landing or
  boundary stop; it warns (FR-027). 008's `/sdd-stop` and hand-over build on this feature.
- Renaming `sdd-job.py`, `.sdd/jobs/` and `/api/jobs` (008 rebuilds the routes).
- Building task histories for work done before this feature (FR-026 "MAY").
- Deleting a landed run branch (FR-014 "MAY").
- Amendment 2026-11-07: rotating or trimming `control-log.jsonl` (a line is about 200 bytes); logging a malformed
  control POST (400); a Stop button on the run card and activity while preparing (moved to 013); the plain
  `launch` (no run record) gets no `starting` status.

## Complexity Tracking

None: no constitution violation to justify.
