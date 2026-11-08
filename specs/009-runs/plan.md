# Implementation Plan: Runs (009)

**Branch**: `009-runs` (built on `auto/20261106-1204`) | **Date**: 2026-11-06 (amended the same day; amended again
2026-11-07) | **Spec**: `spec.md`

**Input**: `spec.md`, amended 2026-11-06 after run `20261106-1204` (report `.sdd/runs/20261106-1204.md`) and its
analyze pass; constitution v1.2.1. Amendment 2026-11-07 (Stop while preparing and the run-control trace: spec
Clarifications "Session 2026-11-07", FR-031 to FR-034, SC-009, the last edge case): `main` at `fc63f0d`,
constitution v1.3.0; Engine changes item 15, research R15–R22, data-model §8. Updated 2026-11-08 for the analyze
pass `.sdd/scratch/analyze-009-1.md` and the spec's Clarifications "Session 2026-11-08" (FR-006 exception, FR-007,
FR-032 wording): research R23 lists every finding and where it is resolved. Updated again 2026-11-08 for the
third analyze pass `.sdd/scratch/analyze-009-3.md` and the spec's clarification on B1 (FR-017: no session report
for a session with no task entries, `56c8ffb`): research R25, Engine changes 15i.

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
| IX. Tests for every change | Every behaviour in Engine changes item 15 names its tests in the same item (15a–15h); SC-009 has a process test that always runs, a browser test, and a `[manual]` task to run the browser tests outside a run, because a run's agents may not start a browser (`.sdd/learnings.md` line 20; R22) | Pass |
| Development: runs start through the supervisor | Unchanged; the amendment makes the supervisor's own start window controllable | Pass |

Re-checked after the design: no violation, no complexity to justify. One test hook is added (`SDD_CLAUDE` as the
default of the hidden `--claude`, R22 item 4); it changes nothing when unset.

Re-checked 2026-11-08 after the first analyze pass (research R23). VIII: the analyze found 1 CRITICAL and 3 HIGH;
H1 went back through `/speckit-clarify` (spec `49b0d90`), H2, H3 and the MEDIUM/LOW findings are resolved here,
C1 is a task split for `/speckit-tasks`; the gate stays closed until `/speckit-tasks` and a fresh
`/speckit-analyze` report no CRITICAL or HIGH. IX: each 2026-11-08 behaviour (M1–M3, L1, L2, L5, M5) names its test
in the same sub-item. II: `abandon_start` only replaces the supervisor's own `starting` status (its own pid), and
the reaper thread only waits on the launcher's own child; nothing in the tree changes. III: a malformed or
leftover request is deleted and logged, never applied; a restarted wait honours Keep paused instead of starting a
session the person asked to hold. Pass.

Re-checked 2026-11-08 after the second analyze pass (`.sdd/scratch/analyze-009-2.md`, research R24). VIII: it
found 0 CRITICAL and 1 HIGH (I1); I1 is a design fix here (a kept stop decides on the tree alone), so the gate
stays closed until `/speckit-tasks` and a fresh `/speckit-analyze` report no CRITICAL or HIGH. IX: the I1, I2 and
U2 behaviours name their tests in 15c, 15e and 15f. II: a kept stop with a clean tree archives `.sdd/run.json`
with the existing `close_session` (it moves the file under `.sdd/runs/<session>/`, nothing is deleted) before
`end_run`; with uncommitted work it suspends, as before. Pass.

Re-checked 2026-11-08 after the third analyze pass (`.sdd/scratch/analyze-009-3.md`, research R25). VIII: it
found 0 CRITICAL and 2 HIGH. B1 went back through `/speckit-clarify` (spec `56c8ffb`, FR-017) and is designed here
as 15i; B2 is a fix to an existing browser test, folded into 15g. The gate stays closed until `/speckit-tasks` and
a fresh `/speckit-analyze` report no CRITICAL or HIGH. IX: 15i changes `write_session_reports` and names its new
tests and the existing tests it changes in the same sub-item; B2's moved test keeps its assertions. II: 15i writes
less, never more: a session with no task entries gets no report file and no commit, so a run stopped before any
task leaves its branch and the base as they were, apart from the run summary that `land` already commits on the
base after a landing of `nothing` (`bin/sdd-job.py:494-498`, `_with_summary`, `:520-531`). VI: unchanged. Pass.

## Project Structure

### Documentation (this feature)

```text
specs/009-runs/
├── spec.md          # amended 2026-11-06
├── plan.md          # this file
├── research.md      # R1–R14; amendment 2026-11-07: R15–R22; 2026-11-08: R23–R25 (analyze findings)
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
bin/sdd-history.py    # harvest, record_step, finalize (histories), write_summary (after landing);
                      # (2026-11-08, 15i) write_session_reports skips a session with no task entries
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
                      # (2026-11-08) abandon_start on every early exit, restart_wait from a starting status
bin/sdd-launch.py     # _start_supervisor(job_id) writes the starting status and reaps its child
bin/sdd-tasks.py      # current_state: "starting" reads as running
bin/sdd-views.py      # job_view / list_job_views: requests
bin/sdd-dashboard.py  # _control source "dashboard", Requests list on the card, banner sending state and timeout,
                      # the Stop-now confirmation while preparing
tests/ (test_sdd_control.py, test_supervise.py (StartWindowTest new), test_sdd_launch.py, test_run_state.py,
        test_sdd_job.py, test_sdd_dashboard.py, test_sdd_history.py (15i), ui/test_banner.py (B2: the working
        test gets its own class), ui/test_jobs.py, ui/test_stop_preparing.py (new))
README.md             # "Runs": the control log, Requests, and Stop now before any task (:163); the short-report
                      # sentence of "Run summary" (:172, 15i)
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

Amendment 2026-11-07 (FR-031 to FR-034, SC-009; research R15–R23; data-model §8; contracts "Amendment
2026-11-07" sections). Line numbers are on `main` at `fc63f0d`; `bin/`, `tests/` and `README.md` are unchanged
through `f1ea4ae` (checked again 2026-11-08 after the second analyze pass: `git diff 735714b f1ea4ae -- bin tests
README.md` is empty), and through `56c8ffb` (checked again after the third pass: `git diff 735714b 56c8ffb -- bin
tests README.md` is empty). Each sub-item is one task's worth, in this order, except 15i (added after the third
pass), which comes first because the tests of 15c, 15d and 15h assert landing `nothing` for a session that has
written `.sdd/run.json`; its tests go in the same task (Principle IX). The 2026-11-08 additions (research R23, R24,
R25) are marked "(2026-11-08)".

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
      (2026-11-08, M2) `write_starting_status` copies `window` and `resets_at` from the status it replaces when that
      status's state is `waiting` (whatever its pid), so a Continue after a crash during the usage wait still knows
      the reset. (2026-11-08, M1) `_start_supervisor` reaps its child: right after `_spawn`, a daemon
      `threading.Thread(target=proc.wait)`, so a supervisor that exits while the dashboard (its parent) lives on is
      not left as a zombie that `supervisor_alive` (`bin/sdd-tasks.py:81-95`, signal 0) reads as alive. More tests
      (`tests/test_sdd_launch.py`): a previous `{"state": "waiting", "window": "five_hour", "resets_at": T, "pid":
      <dead>}` gives a `starting` status with the same `window` and `resets_at`; any other previous state gives
      neither key; `_start_supervisor` with `_spawn` left real on `[sys.executable, "-c", "pass"]` and a run id →
      within 5 s `sdd_tasks.supervisor_alive(status)` is False (reaped).
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
      (2026-11-08, L2) A stop's request line has `mode` `mode or "after-task"` (what `write_control`,
      `bin/sdd-control.py:140-141`, writes), so `request(project, "stop")` logs `after-task`, never null; test.
      (2026-11-08, C1) T035 changes `send_control` (`:151-154`) only: it passes `request.get("job")` on and logs the
      `until` request line; its test calls `send_control` directly with `"job"` and reads both files. The call site
      in `Cycle.run` that adds `"job": JOB_ID` (`bin/sdd-supervise.py:331-333`) is the supervisor's file and moves to
      15e with its test (note for `/speckit-tasks`, below).
    - **15c. Startup keeps a stop for its own run** (FR-031, FR-032, the last edge case; R17; data-model §8.2).
      `bin/sdd-supervise.py`: new `taken(logs, control, text, reason=None)` and `startup_control(logs, job_id)`;
      `main` (`:627-632`) sets `args.startup_stop = startup_control(logs, args.job)` instead of the unlink;
      `parse_args` sets `args.startup_stop = None`; `run_job` carries it out after `JOB_ID = job_id` (`:883`),
      deciding on `tree_clean(project)` (`:779-786`) alone (2026-11-08, analyze I1; FR-005, FR-006's exception,
      FR-007): clean → taken "stopping before any task", `close_session(sdd_job, project, job_id)` (`:789-811`;
      it archives `.sdd/run.json` under `.sdd/runs/<session>/` when a Continue left one and returns None when
      there is none), then `end_run(…, "stopped", "stopped before any task")`; dirty → taken "stopping now" and
      `suspend_run(…, "stopped by you", 0)`. Whether `.sdd/run.json` exists does not decide the ending: a Continue
      whose run was cut off at a boundary (clean tree, run.json kept) ends like a new run, exactly as the
      in-session `before-task` branch (`:913-921`) ends it, and `close_session` comes first because `land` refuses
      while run.json exists (`bin/sdd-job.py:431`, `_run_active` through `_refuse_unsafe_checkout`, `:445-446`).
      The two early returns (`:877-882`) log taken "ignored", "the run is not working" for a kept stop. The say
      lines of data-model §8.5. Update the module docstring (`:23-28`, "reads and deletes"). Tests
      (`tests/test_supervise.py` `JobTest`): `control.json` written before the supervisor starts with `"job":
      JOB_ID` → no `claude` call, ended `stopped` "stopped before any task", landing `nothing`, the say line and a
      taken line; the same with `mode: now`; `"job": "20200101-0000"` → "deleted a leftover control.json", taken
      `ignored` "for another run", the run goes on (one call); no `job` key → `ignored` "left from before this
      supervisor started"; with `.sdd/run.json` and an uncommitted file (a Continue) → suspended "stopped by you",
      taken "stopping now", the file still there; (I1) with `.sdd/run.json` and a clean tree (a Continue after a
      boundary cut-off) → no `claude` call, ended `stopped` "stopped before any task", `.sdd/run.json` gone and
      archived as `.sdd/runs/<its run_id>/run.json`, the landing not refused for an active run (landing `nothing`
      in the fixture, whose branch has no commit the base lacks), taken "stopping before any task"; `RunControlTest.test_a_stale_control_file_is_deleted_at_startup`
      (`:648`) still passes.
      (2026-11-08, Clarifications FR-032) Every taken line also carries the request's `command`, `mode` and
      `request_run` (the `job` it carried, null when none; data-model §8.2), so an ignored leftover keeps what it
      asked for. Test: the "for another run" and "left from before" lines hold `command` `stop`, the mode, and
      `request_run` `20200101-0000` and null.
      (2026-11-08, third analyze pass A4) `startup_control` checks in the order of research R17 "The order of the
      startup checks", first match wins: no file; unreadable; no `--job` → "left from before this supervisor
      started" whatever the request carries; no `job` key → the same; another `job` → "for another run"; not
      `stop` → "only stop is taken at startup"; else kept. Test: a supervisor without `--job` (`RunControlTest`)
      and `control.json` with `"job": "20200101-0000"` → taken `ignored` "left from before this supervisor
      started", `request_run` `20200101-0000`, and the run goes on.
      (2026-11-08, M3) `startup_control`: when `read_control` returns None but `control.json` still exists
      (malformed JSON or not an object, `bin/sdd-control.py:161-166`), it unlinks the file, says "deleted a leftover
      control.json" and logs taken `ignored` "unreadable request" (`id`, `command`, `mode` null). Test: `control.json`
      holding `not json` and one holding `[]` → the file is gone after startup, the run goes on, and a later
      `request(…, "stop")` answers `ok`, not "a request is already pending".
      (2026-11-08, M1) `main` moves `logs`, `status` and `args.startup_stop = startup_control(logs, args.job)` above
      the config checks (`:606-622`), so every exit has them. New `abandon_start(logs, status, args, reason)`:
      writes `write_status(status, state="stopped", reason=reason)` only when `status.json` is `starting` with
      `pid == os.getpid()` (its own start window; never another supervisor's status), and logs taken `ignored`
      `reason` for a kept `args.startup_stop`. Called by `main`'s three early returns (no config, config errors,
      unknown profile; reason "the supervisor could not start") and `run_job`'s two (`:877-882`; reason "the run is
      not working"; this replaces the separate ignored line above). Tests (`JobTest`): a `starting` status with
      this process's pid and no `.sdd/config.toml` → `main` returns 2 and `status.json` is `stopped` "the supervisor
      could not start", and a kept stop has its ignored line; the same with a `starting` status of another pid →
      `status.json` unchanged; `run_job` with no record → `stopped` "the run is not working".
      (2026-11-08, M2) `main`'s restart check (`:633-643`) reads `state in ("waiting", "starting")` with a
      `resets_at` (R16 carries it), and is skipped when a stop was kept at startup. With `--job` the wait goes
      through new `restart_wait(args, logs, status, last) -> str`: it writes a `waiting` status of its own
      (`window`, `resets_at`, so the run shows `paused` with its reset time and Keep paused works) and calls
      `wait_for_reset` (`:535-555`; taken lines as 15e); it returns `go`, `held` or `stop-wait`, kept as
      `args.startup_outcome`. `run_job`, after the kept stop and before the first session, returns
      `suspend_run(…, SUSPEND_REASONS[args.startup_outcome], 0)` for `held` or `stop-wait`. Without `--job`, today's
      `sleep_until` stays (the plain `launch` writes no `starting` status). `--no-sleep` still skips the wait.
      Tests: `restart_wait` with `resets_at` 1 s ahead, `RESET_MARGIN_S` patched to 0, `--poll 0.05`: nothing
      pending → `go` and a `waiting` status with the window was written; a `hold` in `control.json` → `held`; and
      `run_job` with `startup_outcome` `held` → suspended "kept paused at the usage reset" with no `claude` call;
      `main` with a `starting` status carrying `window`/`resets_at` and `--no-sleep` says "an earlier supervisor was
      waiting for the five_hour window".
    - **15d. Stop now before any task ends the run** (FR-007, FR-032; R18). `Cycle.run` (`:350-354`): with
      `started == 0`, set `self.boundary` and `self.before_any_task`, and say "stop now requested before any task:
      stopping the run". `run_job`'s `before-task` branch (`:913-921`): when `tree_clean(project)` is False,
      return `suspend_run(…, SUSPEND_REASONS["stop-now"], 0)` before `close_session`. Tests (`JobTest`): a
      `{"control": {"command": "stop", "mode": "now"}}` step before any `started` line → ended `stopped` "stopped
      before any task", not suspended; an after-task stop before any agent in a session that leaves the tree dirty
      → suspended "stopped by you", `.sdd/run.json` kept. The existing Stop-now tests with an agent started keep
      passing. (Spec Clarifications 2026-11-08: FR-006's exception and FR-007 now say exactly this; no change here.)
      (2026-11-08, L1) In that dirty-tree suspend, the reason is `limit_reason("until", record["limits"])` when
      `by_time` (the stop came from the stop-by time), else `SUSPEND_REASONS["stop-now"]`. Test: a stop-by time
      already past with a dirty tree before any agent → suspended with the stop-by reason.
    - **15e. Taken lines wherever a request is read** (FR-031; R19; data-model §8.2 table). `Cycle.run`
      (`:348-365`; `logs = self.log_path.parent`), `hold` (`:525-531`), `wait_for_reset` (`:542-555`): one
      `taken(…)` call per request read, with the texts of the table. Tests (`tests/test_supervise.py`
      `RunControlTest` and `JobTest`, reading `<logs>/control-log.jsonl`): an after-task stop mid-task → "stopping
      after the current task"; a second one → `ignored` "a stop after the current task is already under way"; a
      stop now mid-task → "stopping now"; Resume while held → "resuming"; hold during the usage wait → "staying
      paused after the reset", the same hold again → `ignored` "already so"; a stop-by time → a request line with
      `source` `until` and its taken line.
      (2026-11-08, analyze I2) `Cycle` keeps the stop request it carries out as `self.stop_control`, for either
      mode (set where it takes the stop: with `self.operator` for an after-task stop, before `stop(proc)` for a
      stop now); `run_cycles` (`:673-679`) copies it to `args.stop_control` before it returns `before-task`
      (`parse_args` sets `args.stop_control = None`). In `run_job`'s `before-task` branch, the dirty-tree suspend
      that 15d adds first writes a second taken line for that request, `taken(logs, args.stop_control, "stopping
      now")`, when `args.stop_control` is set; the reader keeps the last taken line (data-model §8.3), so the card
      says what the supervisor finally did instead of the first line's "stopping before any task". Test (`JobTest`,
      the 15d dirty-tree case): two taken lines with the request's id, the second "stopping now"; the clean-tree
      case has one.
      (2026-11-08, C1) This item also adds `"job": JOB_ID` to the stop-by request in `Cycle.run`
      (`bin/sdd-supervise.py:331-333`), tested by the stop-by case just above: its request line has `run` = the run
      id. (2026-11-08, L5) When a stop under way meets the "no task is left in this run" branch (`:386-397`), a
      second taken line for the same request says `letting the run finish: no task left`; the reader keeps the last
      one (data-model §8.3). Test: `RunControlTest` without `--job`, an after-task stop on the last task → two taken
      lines, the second "letting the run finish: no task left".
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
      (2026-11-08, analyze U1) Placement: define `jobRequestsHtml` right after `jobSummaryHtml` (`:3021-3048`),
      before the comment above `jobCardHtml` (`:3050`), inside the "Runs view" slice that runs in node: from "//
      Runs view (009 FR-021, FR-022)." (`:2852`) to "// End of the Runs view helpers." (`:3245`), cut by
      `JobsViewTest` (`tests/test_sdd_dashboard.py:2545-2552`) and `JobBannerTest` (`:2828-2833`). `jobCardHtml`
      is called through node by existing tests (`:2597`, `:2605`, `:2664`, `:2669`, `:2688`); defined outside the
      slice, `jobRequestsHtml` would throw a ReferenceError in each of them. Those tests must keep passing.
      (2026-11-08, analyze U2) Reading the log never fails a view: `sdd-views.py` reads it through one helper that
      catches `OSError` from `sdd_tasks.state_dir(project)` (`bin/sdd-tasks.py:60-66`, which calls `mkdir`, as
      `get_status`, `:69-78`, already guards) and gives `{}`; `read_control_log` itself returns `{}` for any
      `OSError` on the read (missing file, a directory, no permission). So `requests` is `[]` and `/api/jobs`,
      `/api/jobs/<id>` and `/api/run/current` (through `open_job_view`/`last_job_view`, `bin/sdd-views.py:337-354`;
      `bin/sdd-dashboard.py:265-267`) still answer 200. Tests: with `state_dir` patched to raise `OSError`,
      `job_view` gives `requests` `[]` and `GET /api/jobs` answers 200; with `control-log.jsonl` made a directory,
      `read_control_log` gives `{}`.
      (2026-11-08, analyze A4) A taken line whose `run` is null (a plain supervisor without `--job` taking a
      request) is skipped by the reader, like a request line without a run: no card could show it. Test: such a
      line adds no key to the result.
      (2026-11-08, third analyze pass I2; decided, research R19) A request refused while a run is suspended is
      logged with `run` null, as any request without a live supervisor, and shows on no card. Accepted, not
      changed: the page has no control button for a suspended run, only the command line can send one, and it
      answers the refusal at once. `request()` keeps taking the run id from a live supervisor only. No test change.
    - **15g. Sending state on the banner** (FR-033; R21; data-model §8.5). `bin/sdd-dashboard.py`: `controlSending`
      and `lastRun` page variables; `loadRunState` (`:3679-3692`) keeps `lastRun`; `controlButton` (`:3717-3723`)
      disables while sending and labels the matching button "sending…"; the usage checkbox in `renderJobBanner`
      (`:3802-3804`) is disabled while sending; `postControl` (`:3929-3952`) with `AbortController`, the 10 000 ms
      timer and the texts of §8.5. Tests (`tests/ui/test_banner.py`, `page.route("**/api/run/stop", …)`): a route
      that holds the request → the pressed button reads "sending…" and every `button[data-control]` is disabled;
      after 10 s "request failed: no answer after 10 s" in `.run-banner-error` and the toast, the buttons enabled
      again, and the route saw one request; `route.abort()` → "request failed: …" and enabled; a 409 still shows
      its error.
      (2026-11-08, H2) Placement: declare `var controlSending = null;` and `var lastRun = null;` right after `var
      runBannerError = '';` (`:3696`), inside the slice `JobBannerTest` runs in node (`tests/test_sdd_dashboard.py:
      2834-2835`: from "// Run banner (data-model.md §12.4" at `:3694` to "// POST /api/run/<command>" at `:3927`).
      Declared anywhere else (beside `postControl`, say), `controlButton` and `renderJobBanner` throw a
      ReferenceError in that slice and every `JobBannerTest` case fails; `JobBannerTest` must keep passing.
      `loadRunState` (`:3679`) is outside the slice and only assigns `lastRun`. (2026-11-08, L3) The usage checkbox
      is drawn only by `renderJobBanner` (`renderRunBanner`, `:3842`, draws none).
      (2026-11-08, M5) In `renderJobBanner` (`stopQuestion`, `:3784`): while shown `preparing` and not `stopping`,
      the Stop now confirmation is the preparing text of data-model §8.5, not "its task is resumed from the journal
      on the next start", which FR-006's exception makes wrong before any task. Test (`JobBannerTest`): a preparing
      banner's Stop now confirmation is the new text; a working one keeps today's.
      (2026-11-08, analyze A1; decided, research R24) A resumed session (a Continue) reads `working`, not
      `preparing`, from its start: run.json still names the earlier session's workflow, whose journal has
      `started` lines (`_has_started_agent`, `bin/sdd-job.py:252-278`). Its Stop now confirmation therefore stays
      the working text, unchanged. Accepted, not reworded: the page cannot tell that moment apart from an agent at
      work, and both outcomes keep the person's work. With half-done work in the tree (the usual Continue) the
      run is suspended as the text says; with a clean tree it ends `stopped` "stopped before any task", and the
      card's reason and its Requests row ("stopping before any task") say so. No test changes beyond M5's.
      (2026-11-08, analyze A5) The timer's abort is told apart by a flag: `postControl` keeps `var timedOut =
      false`; the `setTimeout` callback sets `timedOut = true`, then calls `controller.abort()`; the `catch` gives
      "request failed: no answer after 10 s" when `timedOut` is true, else today's `'request failed: ' + e`. Not
      `e.name === 'AbortError'`: the flag says that this page's own timer aborted it. Test (`JobBannerTest`
      page-source test of 15g): the page holds `timedOut = true` before `controller.abort()`.
      (2026-11-08, third analyze pass B2) Fix a browser test that fails on `main` since T028 (`c09d2d0`):
      `WorkingJobBannerTest.test_working_job_names_the_job_and_offers_both_stops` (`tests/ui/test_banner.py:
      390-397`) writes a `running` status, but its class has `RUN_JSON = None` (`JobBannerTestCase`, `:364`), so
      `shown_state` finds no started agent and shows `preparing` (`bin/sdd-job.py:292-297`): "Preparing Toy run" !=
      "Working on Toy run". Move that test, its assertions unchanged, into a new class
      `WorkingJobWithAgentBannerTest(JobBannerTestCase)` placed right after `WorkingJobBannerTest`, whose `RUN_JSON`
      is `[{"type": "launched"}, {"type": "started", "key": "v2:01", "agentId": "a01", "label": "impl:T012",
      "phase": "Build"}]`, as `SuspendedJobBannerTest` has (`:479-480`); `JobBannerTestCase.setUpClass` then
      writes this run's own `.sdd/run.json` (`:377-380`). `WorkingJobBannerTest` keeps
      `test_working_without_a_supervisor_or_run_json_offers_land` (`:400-406`) with `RUN_JSON = None`, which that
      test needs (no run.json, so the run reads ended with landing pending), and gains
      `test_preparing_job_names_the_job_and_offers_both_stops`: a `running` status, banner text "Preparing " +
      `JOB_TITLE`, enabled labels "Stop after current task" and "Stop now" (FR-005, FR-032 in the browser), and no
      run id shown. The flip tests of `PausedJobBannerTest` (`:438-470`) are not touched.
    - **15h. Stop while preparing end to end** (SC-009; R22). `bin/sdd-supervise.py` `parse_args` (`:591`):
      `--claude` default `os.environ.get("SDD_CLAUDE") or "claude"`. No harness change (2026-11-08, L9): the
      browser test passes its environment the way `tests/ui/test_banner.py:85-86` already does, with
      `mock.patch.dict(os.environ, …)` around `harness.start_dashboard` (here started in `setUpClass` and stopped in
      `tearDownClass`, because the test also calls `launch_job` in-process and reads the state directory). New
      `tests/test_supervise.py` `StartWindowTest` (research R22 item 1: delayed spawn, after-task and now, and the
      preparing moment). New `tests/ui/test_stop_preparing.py` (R22 item 2: Start run in the page then Stop while
      "Preparing" once the session has started; the delayed spawn then Stop; each ended `stopped` "stopped before
      any task" within 15 s with the two control log lines). Test design (2026-11-08): **H3** the patched `_spawn`
      keeps the `Popen` it returns, and the test waits with `proc.wait(timeout=15)`, never by polling `os.kill(pid,
      0)` (an unwaited child stays a zombie and reads as alive; 15a's reaper thread waiting too is harmless,
      `Popen.wait` is safe from two threads). **A3** (third analyze pass) the delayed spawn's delay is a parameter,
      `delay(seconds)` (research R22 item 1): 3 s in `StartWindowTest`, 10 s in the browser test, where the page must
      load and take the click inside the window; the wait is `proc.wait(timeout=seconds + 15)`. **M4** the preparing
      case sends the stop only after the session has started (`<project>/.sdd/run.json` exists, written by the fake's
      `{"run": "s1"}` step) while `shown_state` is still `preparing`, and asserts the `Cycle.run` say line "stop
      requested before any task: stopping the run", not the startup one, so it tests the in-session path every time.
      README "Runs" (`## Runs` is `README.md:154`, not :138; 2026-11-08, L6): the control log file and the Requests
      list in one sentence each; and (M5) the cut-off bullet at `:163` says Stop now before any task has started, with
      a clean tree, ends the run **stopped** "stopped before any task" instead of suspending it. "### Stop and resume"
      (`README.md:131`; its "Stopping" bullet with Stop now is `:134`; plain supervised runs without a run record,
      which hold on Stop now) stays true and is not changed. A `[manual]` task runs the browser tests with
      `SDD_UI_TESTS=1` outside a run and records the result (R22 item 3).

    - **15i. No short report for a session without task entries** (2026-11-08, third analyze pass B1; spec
      Clarifications 2026-11-08 last answer, FR-017, `56c8ffb`; research R25). Today a session that wrote
      `.sdd/run.json` joins the record's `runs` (`run_job`, `bin/sdd-supervise.py:906-912`; `close_session`,
      `:804-809`); `end` (`bin/sdd-job.py:562-594`) calls `finalize` (`bin/sdd-history.py:356-370`), whose
      `write_session_reports` (`:313-341`) writes `.sdd/runs/<session>.md` for every session without a report, even
      one with no task entries. That file is not ignored (only `.sdd/runs/*/`), so it is committed as
      `chore(run): <id> history` and `land` lands it: a run stopped before any task lands `landed`, not `nothing`.
      Change: in `write_session_reports`, right after `mine` is filtered to the session's non-empty task lists
      (`:323`), `continue` when `mine` is empty. Update its docstring (`:314-315`): a short report is written only
      for a session with at least one task entry in this run. Nothing else changes: `finalize` already makes no
      commit when no file changed (`:368-369`), `land` already gives `nothing` for a branch with no commit the base
      lacks and still commits the run summary on the base (`:494-498`, `_with_summary` `:520-531`), and `summary`
      lists only reports that exist (`:281`) while `sessions` keeps the session id. A session with task entries
      still gets its report, as FR-017's first sentence requires (quickstart §8 step 10).
      Existing tests: none expects a short report for a session with no task entries. Checked 2026-11-08 by
      applying this change to a copy of `56c8ffb` and running `python3 -m unittest discover -s tests`: 765 tests,
      OK (1 skipped), as without it. The tests around it that must keep passing:
      `tests/test_sdd_history.py` `SummaryAndFinalizeTest.test_finalize_commits_histories_and_reports_only` (`:218`;
      its session has T012 and T013 entries, so its report is still written), `test_finalize_with_nothing_new_makes_
      no_commit` (`:236`), `test_existing_report_and_summary_are_kept` (`:243`); `tests/test_sdd_job.py`
      `test_nothing_to_land` (`:694`), `test_end_commits_histories_before_landing_and_the_summary_after` (`:776`);
      `tests/test_supervise.py` `JobTest.test_a_stop_before_any_agent_starts_ends_the_run_stopped_before_any_task`
      (`:1051`; its session never wrote run.json, `runs` is `[]`, so it never met the bug).
      New tests: `tests/test_sdd_history.py` `SummaryAndFinalizeTest`: a record whose `runs` holds a session with no
      task entries (no workflow listing for it) → `write_session_reports` returns `[]`, no `.sdd/runs/<session>.md`
      exists, and `finalize` returns `{"commit": None, "files": []}` with HEAD unchanged; a record with two sessions,
      one with entries (`self.workflow(…, self.retry_journal())`) and one without → exactly one report, the first's.
      `tests/test_supervise.py` `JobTest`, beside `:1051`: the same scenario with the fake's `{"run": "r1"}` step
      before the `{"control": {"command": "stop", "mode": "after-task"}}` step → the say line "stop requested before
      any task: stopping the run", ended `stopped` "stopped before any task", landing `nothing`, `record["runs"] ==
      ["r1"]`, no `.sdd/runs/r1.md`, and no subject containing `history` in `git log main`. Checked the same day on
      that copy: this test fails on `56c8ffb` ("'landed' != 'nothing'") and passes with the change.
      With 15i, the landing `nothing` that 15c (the clean Continue, whose fixture run.json must name a session with
      no task entries), 15d and 15h (the preparing case with `{"run": "s1"}`) assert holds, and the spec's "nothing
      landed" (FR-005, FR-006, FR-032, SC-009), the README sentence and the preparing Stop now text (data-model §8.5)
      are true as written. README "Run summary" (`README.md:172`): "A session that ended without its own report (a
      stop at a boundary, a Drop) gets a short one written from its journal" gains ", unless no task was started in
      it" (in T044, with the other README edits).

    Note for `/speckit-tasks` (rewritten 2026-11-08 after the second analyze pass, D1; task numbers are those of
    `tasks.md` since `2e205ce`). Split by file so each change ships with its test (Principle IX):
    - **T034** 15a, with M1's reaper thread and M2's carried `window`/`resets_at`.
    - **T035** 15b in `bin/sdd-control.py` and the dashboard's `_control`: `send_control` passes `job` on and
      logs the `until` line, tested by calling it directly (C1); L2's `mode or "after-task"`.
    - **T036** 15c, first half: `taken`, `startup_control`, the kept stop in `run_job` deciding on the tree alone
      (I1, with its run.json-and-clean-tree test), M3's malformed-file deletion, the taken-line keys.
    - **T037** 15c, second half: `abandon_start` (M1) and `restart_wait` (M2).
    - **T038** 15d, with L1's stop-by reason in the dirty-tree suspend.
    - **T039** 15e: the `"job": JOB_ID` on the supervisor's own stop-by request (`bin/sdd-supervise.py:331-333`)
      and its test (C1); `self.stop_control`, L5's second line, and I2's second line "stopping now" in
      `run_job`'s dirty-tree `before-task` branch with `args.stop_control`; L4's rewording (the implementer
      leaves the control-log assertions of T036–T038 alone).
    - **T040** 15f, with U1's placement, U2's `OSError` → `[]` and A4's skipped null-run taken line.
    - **T041** 15g, with H2's placement, M5's preparing confirmation, A5's `timedOut` flag; A1 needs no code.
    - **T042** 15h process test (`StartWindowTest`, `--claude` from `SDD_CLAUDE`), with H3 and M4.
    - **T043** 15h browser test (`tests/ui/test_stop_preparing.py`), with H3, M4 and L9.
    - **T044** 15h README: "Runs" (`:154`) and its cut-off bullet (`:163`, M5); "### Stop and resume" (`:131`)
      unchanged.
    - **T045** the `[manual]` browser run.
    (2026-11-08, after the third analyze pass) **15i becomes a new task placed before T036** (renumbering is
    `/speckit-tasks`' call), in `bin/sdd-history.py` with its tests, since T036, T038 and T042 assert landing
    `nothing` for a session that wrote `.sdd/run.json`. **T041** also takes B2 (the `WorkingJobBannerTest` move and
    the new preparing test, 15g); **T036** takes A4's check order (15c); **T038** takes A1 (its clean
    stop-now-before-any-task case has no `run` step); **T037** takes A2 (the project with `.sdd/config.toml` of
    `JobTest`, no record for `--job`, so `run_job` returns 2 through `abandon_start`; every in-process `main`,
    `run_job` and `restart_wait` call inside `contextlib.redirect_stdout`, so the suite output stays clean, R13);
    **T042** and **T043** take A3 (`delay(seconds)`, 3 s and 10 s, `proc.wait(timeout=seconds + 15)`); **T043** drops
    `FAKE_CONTROL` (A5: its scenario has no control step); **T044** takes the README "Run summary" sentence of 15i. I1
    is in the spec (`56c8ffb`); I2 needs no task (15f, R19).
    The second pass's test-design and wording findings that change no design (T1, T2, A2, A3, A6, C1–C3, K1) go
    straight into those tasks; research R24 lists where.

## Not in this pass

- A plain in-session `/sdd-run` (no supervisor) gets trailers and task histories but no automatic landing or
  boundary stop; it warns (FR-027). 008's `/sdd-stop` and hand-over build on this feature.
- Renaming `sdd-job.py`, `.sdd/jobs/` and `/api/jobs` (008 rebuilds the routes).
- Building task histories for work done before this feature (FR-026 "MAY").
- Deleting a landed run branch (FR-014 "MAY").
- Amendment 2026-11-07: rotating or trimming `control-log.jsonl` (a line is about 200 bytes); logging a malformed
  control POST (400); a Stop button on the run card and activity while preparing (moved to 013); the plain
  `launch` (no run record) gets no `starting` status. (2026-11-08) A malformed `control.json` met in the middle of a
  run (`Cycle.run`, `hold`, `wait_for_reset`) is still left in place as today; only startup deletes it (M3). A
  plain supervisor (no `--job`) restarted during a usage wait keeps today's `sleep_until`, reading no requests.

## Complexity Tracking

None: no constitution violation to justify.
