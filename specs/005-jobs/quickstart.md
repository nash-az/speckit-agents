# Quickstart: validating Jobs

Each section is a check with its expected result. The fake-`claude` checks run in the test suite; §6 is a real
run, done once by hand.

## Prerequisites

- `python3 -m unittest discover -s tests`, `node --test tests/workflow.test.mjs`, `claude plugin test .` pass.
- `.gitignore` has `.sdd/jobs/`.

## 1. A Job across three runs (SC-001, story 1)

Temp repo with a three-task phase; `tests/fake/claude` scenario: each run ticks one task and finishes.
`sdd-launch.py <repo> specs/001-x --job --phases 1`. Expected: branch `auto/<id>`, three run ids in the record,
state `done`, no person action. `sdd-job.py show <id>` lists the three runs.

## 2. Waiting on you, settle, continue by itself (story 3, FR-016)

Scenario: one task is `[decision]`. Expected after the first run: `waiting`, the banner "Waiting on you: 1
items". Apply the decision with `sdd-decide.py`; its JSON output has `"job": {"started": true}` and the next run
starts on `auto/<id>` without a click.

## 3. Usage limit with the checkbox (story 1 scenario 3–4, FR-004a)

Scenario: the fake emits a `rate_limit_event` over the threshold with `resetsAt` 60 s ahead. Expected: shown
`paused`, banner text "…continues by itself" with the box checked; at the reset the same run resumes. Again with
`/api/run/hold` posted three times and `/api/run/release` twice during the wait (last = hold): at the reset the
Job is `held` and the supervisor has exited; Continue resumes the same run id.

## 4. Limits and Continue (FR-005, FR-009)

`--max-tasks 1` on a three-task scope. Expected: `stopped`, reason "max_tasks 1 reached". Continue without a new
value: refused "the limit max_tasks is still reached; give a new value". Continue with `--max-tasks 3`: the Job
finishes `done`; `tasks_done` counts 3, not 2.

## 5. Merge, conflict, Drop (story 2, SC-002, SC-005)

- A `done` Job, base unchanged: Merge fast-forwards; checkout on base; state `merged`.
- Base moved with a conflicting edit: Merge refused naming the file; `git status` clean, checkout still on
  `auto/<id>`, base unchanged.
- A `failed` Job: Drop; checkout on base; `auto/<id>` still exists; Start job enabled.

## 6. Real run, once

On this repo, with a small open phase: Start job from the Jobs view, leave it, come back. Expected: the Jobs
view shows the Job with its runs; the banner never shows a run id (SC-003); Merge brings the commits into `main`.
