# Implementation Plan: Runs (009)

**Branch**: `009-runs` (built on `auto/20261106-1204`) | **Date**: 2026-11-06 (amended the same day) | **Spec**: `spec.md`

**Input**: `spec.md`, amended 2026-11-06 after run `20261106-1204` (report `.sdd/runs/20261106-1204.md`) and its
analyze pass; constitution v1.2.1.

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

## Technical Context

**Language/Version**: Python 3.11+ (stdlib only) for `bin/`; JavaScript for `workflows/sdd-run.js` and the
dashboard page (inline in `bin/sdd-dashboard.py`); Markdown skills.
**Primary Dependencies**: none new. git CLI; Claude Code (Workflow tool, `claude -p`).
**Storage**: files. Run records `.sdd/jobs/` (ignored), task histories `.sdd/tasks/<feature>/<id>.jsonl` and run
summaries `.sdd/runs/<id>.json` (committed), live session `.sdd/run.json` (ignored), supervisor state outside the
project.
**Testing**: `python3 -m unittest discover -s tests` (unit, supervisor against `tests/fake/claude`, dashboard
routes), `node --test tests/workflow.test.mjs`, `claude plugin test .`; browser tests in `tests/ui/` with
`SDD_UI_TESTS=1`.
**Target Platform**: macOS/Linux, local repository without a remote.
**Project Type**: Claude Code plugin (CLI helpers, skills, workflow, local dashboard).
**Performance Goals**: none beyond today (the dashboard polls every 3 s; history reads are per task).
**Constraints**: constitution v1.2.1 II (only the engine lands, and after landing may commit the run's summary and history files on the base; never force, push or rewrite), VI (one task, one commit),
VII (no model in the engine); the suite stays green on every task commit.
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

## Project Structure

### Documentation (this feature)

```text
specs/009-runs/
├── spec.md          # amended 2026-11-06
├── plan.md          # this file
├── research.md      # R1–R14
├── data-model.md    # §1 run record, §2 normalize, §3 shown state, §4 lifecycle, §5 task history, §6 run summary, §7 task summary line
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
```

**Structure Decision**: the existing plugin layout; no new module. One new test file and one fixture folder.

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

## Not in this pass

- A plain in-session `/sdd-run` (no supervisor) gets trailers and task histories but no automatic landing or
  boundary stop; it warns (FR-027). 008's `/sdd-stop` and hand-over build on this feature.
- Renaming `sdd-job.py`, `.sdd/jobs/` and `/api/jobs` (008 rebuilds the routes).
- Building task histories for work done before this feature (FR-026 "MAY").
- Deleting a landed run branch (FR-014 "MAY").

## Complexity Tracking

None: no constitution violation to justify.
