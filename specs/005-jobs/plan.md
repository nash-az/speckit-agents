# Implementation Plan: Jobs

**Branch**: `main` (no feature branch; this project works on `main`, runs use `auto/` branches) | **Date**: 2026-10-31 | **Spec**: [spec.md](./spec.md)

**Input**: `specs/005-jobs/spec.md` (clarified 2026-10-30 and 2026-10-31)

**Who implements this**: agents in unattended runs. `data-model.md` gives the exact shapes, state moves and
function names; `contracts/jobs-api.md` gives the CLI, routes and refusal texts. Where a decision is listed in
`research.md`, a task must not reopen it.

## Summary

A Job is what the person starts, watches and merges: a feature, a scope, limits and one `auto/<job id>` branch.
The supervisor carries it out as a series of runs, waiting through usage limits (with the "Continue when the
limit resets" checkbox), stopping at limits, and ending `waiting`, `done`, `stopped` or `failed`. Settling an
item in the dashboard continues a waiting Job by itself. Merge and Drop end it. The Runs view becomes Jobs, and
the headline speaks in words.

Approach: extend the 003 pieces, add one helper.
- `bin/sdd-job.py` (new, stdlib): the Job record, shown state, progress, Merge, Drop, `after_settle`.
- `bin/sdd-supervise.py`: `--job <id>` loop over runs; no maximum wait; held/stopped exit.
- `bin/sdd-launch.py`: `launch_job`, `continue_job`; no more `--max-wait-hours 0`.
- `bin/sdd-tasks.py`: `plan --tasks`; `skills/sdd-run/SKILL.md`: `--tasks`, `--job`, `job` in run files.
- `bin/sdd-decide.py`, `bin/sdd-manual.py`, `bin/sdd-unblock.py`: call `after_settle` after their commit.
- `bin/sdd-dashboard.py`: `/api/jobs*` routes, the Jobs view, banner texts, the usage checkbox, the task chip.

## Technical Context

**Language/Version**: Python 3.11+ stdlib (`bin/*.py`); plain JavaScript in the dashboard page script;
Markdown skill text.
**Primary Dependencies**: none new. `git` for branches and merges; the `claude` CLI only inside runs.
**Storage**: `.sdd/jobs/<id>.json` (git-ignored, research R1); `status.json` and `control.json` in the
supervisor's state directory as in 003.
**Testing**: `python3 -m unittest discover -s tests` (helpers on temp git repos; the supervisor against
`tests/fake/claude` with new `tick`/`finish` steps; routes through the handler), the browser tests in
`tests/ui/` for the Jobs view and banner, `node --test tests/workflow.test.mjs`, `claude plugin test .`.
**Target Platform**: macOS/Linux developer machine, single user, localhost.
**Project Type**: Claude Code plugin with a local web dashboard (as 003).
**Performance Goals**: Start job, Continue, Merge and Drop answer in under 2 s (the supervisor starts
detached); the view refreshes on the existing 3 s poll.
**Constraints**: the dashboard source spawns no process and writes no project file (003 FR-020; the routes call
helpers); everything works without a supervisor and without Jobs (Principle VII, FR-015); never push, never
delete a branch, never rewrite history; one open Job per project.
**Scale/Scope**: one project, tens of Jobs, up to 20 runs per Job.

## Constitution Check

| Principle | Check | Status |
|---|---|---|
| I. Spec Kit is the source of truth | Jobs read `tasks.md`/`BLOCKED.md` through `sdd-tasks.py`; no Spec Kit file format changes; the record is a plugin file under `.sdd/`. | PASS |
| II. Safe without a person watching | Every run of a Job runs on `auto/<id>` under the existing guard; the supervisor refuses to start a run off the Job's branch; Merge and Drop are a person's click, refuse during a run, never force, push or delete. | PASS |
| III. Never guess for a person | A Job with only person items left is `waiting`; it continues only after the person settles an item. Limits are raised only by the person (FR-009). | PASS |
| IV. Verified, not reported | Done counts come from ticks in `tasks.md`, which only the bookkeeper makes after a verifier approves (unchanged). | PASS |
| V. Deterministic where possible | State moves, progress, limits, merge and drop are plain code (research R3, R4, R9). | PASS |
| VI. One task, one commit | Unchanged: runs commit per task; Merge adds at most one merge commit. | PASS |
| VII. AI-free at the core | The record, states, Jobs view, Merge and Drop need no model; a plain `/sdd-run` still works and shows under Earlier runs (FR-015). | PASS |

Post-design re-check: PASS. The point to watch is the supervisor's new loop: a bug there could start runs
repeatedly; FR-006 (fail after two runs without progress) and `max_runs` (default 20) bound it, and the tests
cover both.

## Project Structure

### Documentation (this feature)

```text
specs/005-jobs/
├── spec.md
├── plan.md              # this file
├── research.md          # decisions R1–R13
├── data-model.md        # record, states, functions, banner texts
├── quickstart.md        # validation scenarios
├── contracts/
│   └── jobs-api.md      # CLI, routes, refusal texts
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
bin/
├── sdd-job.py           # new: record, shown state, progress, merge, drop, after_settle
├── sdd-supervise.py     # --job loop, no max wait for Jobs, held/stopped exit
├── sdd-launch.py        # launch_job, continue_job
├── sdd-tasks.py         # plan --tasks
├── sdd-decide.py        # after_settle
├── sdd-manual.py        # after_settle
├── sdd-unblock.py       # after_settle
└── sdd-dashboard.py     # /api/jobs*, Jobs view, banner, checkbox, chip
skills/
├── sdd-run/SKILL.md     # --tasks, --job, job in run.json and workflows.json
└── sdd-init/SKILL.md    # .sdd/jobs/ ignore line
tests/
├── test_sdd_job.py      # new
├── test_supervise.py    # Job scenarios
├── test_sdd_launch.py   # launch_job, continue_job
├── test_sdd_tasks.py    # plan --tasks
├── test_sdd_dashboard.py# routes, texts
├── fake/claude          # tick and finish steps
└── ui/test_jobs.py      # new: Jobs view, banner, checkbox
```

**Structure Decision**: same layout as 003; one new helper and its test file, one new browser test file.

## Complexity Tracking

None: no constitution gate is violated.
