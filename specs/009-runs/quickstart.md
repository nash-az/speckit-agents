# Quickstart: validating runs (009)

Each section is a check with its expected result. The fake-`claude` checks (§1–§5) are also in the test suite
(`tests/test_supervise.py`, `tests/test_sdd_job.py`, `tests/test_carry_over.py`); run them by hand only to see a
run's behaviour step by step. §7 is a real run, done once. Shapes: `data-model.md`; commands and texts:
`contracts/runs-cli.md` and `contracts/dashboard-routes.md`.

## Prerequisites

- `python3 -m unittest discover -s tests`, `node --test tests/workflow.test.mjs` and `claude plugin test .` pass,
  and the unit suite prints nothing but the test runner's own lines.
- A toy repository: `git init` a temp folder with a commit on `main`, `specs/001-x/tasks.md` with
  `## Phase 1: Toy` and two open tasks `T001`, `T002`, each followed by a two-space summary line, `.gitignore` with
  the five lines `/sdd-init` writes (`.sdd/run.json`, `.sdd/scratch/`, `.sdd/runs/*/`, `.sdd/plugin-feedback.jsonl`,
  `.sdd/jobs/`).
- The fake `claude`: `export FAKE_DIR=$(mktemp -d)`, a `$FAKE_DIR/scenario.json` (format in the header of
  `tests/fake/claude`), and `FAKE_TASKS=<repo>/specs/001-x/tasks.md`. The supervisor takes it with the hidden
  `--claude <plugin>/tests/fake/claude` flag; `tests/test_supervise.py` has helpers that build these scenarios.

## 1. Stop after this task ends the run and lands (US1, SC-001, SC-005)

Scenario, one cycle: `{"run": "<session>"}`, a transcript, journal `started` `impl:T001`, `{"control":
{"command": "stop", "mode": "after-task"}}`, journal `started` `impl:T002` right after `commit:T001`'s result,
`{"write": …}` and `{"commit": "feat(T001): …"}`, `{"tick": "T001"}`, then a sleep (the supervisor stops it).

1. `python3 bin/sdd-launch.py <repo> specs/001-x --job --phases 1` (with the supervisor's `--claude` pointing at
   the fake, as the tests do).
2. Expected: the supervisor log says "task boundary: stopping the run"; `bin/sdd-job.py show <id> <repo>` gives
   `shown.state` `ended`, outcome `stopped`, landing `landed`; `git -C <repo> log main` has T001's commit and a
   `chore(run): <id> summary` commit; `.sdd/run.json` is gone and `.sdd/runs/<session>/run.json` exists; T002 is
   still `- [ ]`.
3. If T002 left an edit: `git -C <repo> stash list` shows `sdd-run <id>: stopped, stashed T002`, and
   `.sdd/tasks/001-x/T002.jsonl` ends with a `dropped` entry whose `diff` holds the edit (FR-005).
4. Start a second run on T002 straight away: it starts (no refusal).
5. Stop pressed before any agent starts: the run ends `stopped` with reason "stopped before any task" and landing
   `nothing`.

## 2. Cut off, Continue, Drop (US2, SC-002)

Scenario: T001 committed, then `started` `impl:T002`, a `{"write": …}` and an `{"exit": 1}` with `.sdd/run.json`
left (a cut-off).

1. Expected after the cycle: shown `suspended`, reason "the run was cut off"; `main` already has T001
   (fast-forward while suspended, FR-011); the edit is still in the tree; T002's history ends with `suspended`.
2. `python3 bin/sdd-launch.py <repo> specs/001-x --continue <id>`: same id and branch; T002's history gets
   `resumed`.
3. Repeat the cut-off, then `python3 bin/sdd-job.py drop <id> <repo>`: JSON with `"dropped": true`, `"tasks":
   ["T002"]`, a stash message; tree clean; T002 open; its history has `dropped` with the diff; the run is ended
   `dropped`, landing `landed`.
4. In the dashboard, Drop's confirm names T002 and the changed files (contracts "Page texts").
5. While suspended, `--job` again is refused with "a run is suspended: Continue or Drop it first".

## 3. Conflict, Merge and Land (US1 scenario 4, SC-004)

1. Commit to `main` a change to a file T001 also changes, then end a run on T001.
2. Expected: landing `needs-merge` with the file; `main` unchanged; the checkout on the run branch; the card
   offers Merge.
3. Resolve on `main`, then `python3 bin/sdd-job.py merge <id> <repo>`: same JSON as `land`; landing `landed`
   with a `merge: run <title>` commit, then the summary commit.
4. `land` again is refused with "this run has already ended its landing".

## 4. Task history and the run summary (US3, US4, SC-003)

1. After §1 or §2, delete the fake's workflow folder (the transcripts).
2. `python3 bin/sdd-history.py show <repo> 001-x T001`: every attempt, verdict and the commit are there.
3. `.sdd/runs/<id>.json` has `landing`, `duration_s`, `sessions`, `reports`, `usage_peak` (from the fake's
   `rate_limit_event`s), `tasks`, `attempts`, `approved_first_time`, `models`.
4. The Runs view shows the past run with its summary line and no button.

## 5. Carry-over (US5, SC-006)

`tests/test_carry_over.py` copies `tests/fixtures/carry-over/` (this repository's three merged Jobs and their
reports, plus a stopped Job with its `run.json` and a done Job not merged) into a temp repository. Expected: the
merged ones are past runs (ended, landed, no button); the stopped one is suspended with Continue and Drop; the done
one is ended with Land, and Land lands it in one call.

## 6. Task summaries (FR-028–FR-030, SC-007)

1. `python3 bin/sdd-tasks.py parse specs/009-runs/tasks.md`: every task has a non-null `summary`; no `text`
   contains a summary sentence; `deps` and `files` are the same as without the summary lines.
2. Open the dashboard on 009 (`python3 bin/sdd-dashboard.py --project . --port 8765`), Tasks: each card reads as a
   plain sentence; "Details" opens the exact task text. Start run lists the summaries.
3. `/sdd-run` typed into an interactive session without `SDD_SUPERVISED=1`: before any agent starts it says the
   run has no usage-limit protection and that runs are started with the launcher or Start run (SC-008).

## 7. Real run, once

Start a small run from the dashboard's Start run on a real feature, press Stop after this task during its first
task, and check §1's expectations on the real repository: the finished task is on the base branch with no click,
and the card shows the past run's summary.
