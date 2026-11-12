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
   `nothing`. (2026-11-09) In a run whose earlier session did tasks (a Continue, or the next session after a
   usage-limit wait), the reason is "stopped before its next task" and that earlier work lands (§8 step 11).

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
6. (2026-11-11, spec Clarifications "Session 2026-11-11", FR-007; research R28) A cut-off at a task boundary: the
   same scenario, but the session exits (`{"exit": 1}`, `.sdd/run.json` left) right after `commit:T001`'s result
   and `{"tick": "T001"}`, before `impl:T002` starts, so the tree is clean. Expected: shown `suspended`, reason
   "the run was cut off", Continue and Drop offered, `main` has T001; the run is **not** ended. Ending it cleanly
   is out of scope here (010 server core).

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

## 8. Stop while preparing (amendment 2026-11-07: FR-031, FR-032, SC-009)

Automated in `tests/test_supervise.py` `StartWindowTest` (always runs) and `tests/ui/test_stop_preparing.py`
(browser; strict with `SDD_UI_TESTS=1`); research R22. By hand, on the toy repository of the prerequisites, with
`SDD_CLAUDE=<plugin>/tests/fake/claude`, `FAKE_DIR`, `FAKE_TASKS` and a scenario whose one session is
`[{"transcript": true}, {"run": "s1"}, {"sleep": 30}]`:

1. `python3 bin/sdd-launch.py <repo> specs/001-x --job --phases 1`, then at once
   `python3 bin/sdd-job.py show <id> <repo>`: `shown.state` is `preparing`, and the state directory's
   `status.json` says `"state": "starting"` (or already `running`) with the run's id as `job`.
2. `python3 bin/sdd-control.py stop --project <repo>`: `{"contract": "1.0", "ok": true, "id": "<request id>"}`,
   not a refusal.
3. Within 15 s: `sdd-job.py show <id> <repo>` gives `shown.state` `ended`, outcome `stopped`, reason "stopped before
   any task", landing `nothing`; T001 and T002 are still `- [ ]`; `.sdd/run.json` is gone. (2026-11-08, B1,
   FR-017) `git log main` gains only `chore(run): <id> summary`; there is no `chore(run): <id> history` commit and
   no `.sdd/runs/<session>.md`, also in step 7, where the session wrote `.sdd/run.json`.
4. `<state dir>/control-log.jsonl` has the request line (`"source": "cli"`, `"outcome": "accepted"`, `"run": "<id>"`)
   and a taken line with the same `id` and `"taken": "stopping before any task"` (data-model §8.2).
5. Repeat with `stop --now`: the same ending (not suspended).
6. Leftover request: write `control.json` by hand with `"job": "20200101-0000"`, then launch: the supervisor log
   says "deleted a leftover control.json", the run goes on, and the control log has `"taken": "ignored"`, `"reason":
   "for another run"`, with the request's `"command": "stop"` and `"request_run": "20200101-0000"` (2026-11-08).
7. (2026-11-08) In the session: wait until `<repo>/.sdd/run.json` exists (the fake's `run` step) while `show`
   still says `preparing`, then `stop`: the same ending, and `launch.log` has "stop requested before any task:
   stopping the run" (the in-session path, not the startup one).
8. (2026-11-08) Unreadable request: write `not json` into the state directory's `control.json`, then launch: the
   file is gone, the control log has `"taken": "ignored"`, `"reason": "unreadable request"`, and a `stop` after
   that answers `ok`, not "a request is already pending".
9. (2026-11-08) A supervisor that cannot start: append `[agents]` and `nosuch = "x"` to `<repo>/.sdd/config.toml`
   and commit it (the launcher needs a clean tree; the supervisor then stops on "config error: agents.nosuch:
   unknown role"), and launch: within a few seconds `status.json` says `"state": "stopped"`, `"reason": "the
   supervisor could not start"`, `controls` offers no stop, and `show` no longer says `preparing`. In the
   dashboard the same, started with Start run (the dashboard is the parent that must reap it). Revert the commit.
10. (2026-11-08, I1; updated 2026-11-09) A Continue with a clean tree: take a run suspended at a task boundary
    with nothing uncommitted (Stop now pressed between two tasks, after an agent had started, so FR-007 suspends it
    whatever the tree holds; `.sdd/run.json` is kept), write `control.json` with `"command": "stop"` and `"job":
    "<its id>"`, then Continue it (`sdd-launch.py … --continue <id>` or the card's Continue): no `claude` starts,
    the run ends `stopped` with "stopped before its next task" (the earlier session did tasks; spec Clarifications
    2026-11-09), `.sdd/run.json` is archived under `.sdd/runs/<session>/`, and the landing is not refused for an
    active run. With an uncommitted file in the tree instead, the same steps leave it `suspended` "stopped by
    you". (2026-11-08, B1) The earlier session has task entries, so its short report and histories are committed
    (`chore(run): <id> history`, FR-017) when they are not committed yet, and the landing is then `landed` (or
    `nothing` when all of it was already on the base); the stopped session itself adds no commit.
    (2026-11-12, ninth analyze pass L9) By hand this needs a real `claude`, as step 11 does: the fake writes no
    `workflows.json`, so its sessions leave no task entries, and with the fake the run ends `stopped` with
    "stopped before any task" and landing `nothing` instead. Everything else in this step (no `claude` starts,
    `.sdd/run.json` archived, the landing not refused, the dirty-tree suspend) shows with the fake too.
    `tests/test_supervise.py` `JobTest` covers the reason with a committed history file standing in for the
    earlier session (plan 15j, case 2).
11. (2026-11-09, plan 15j) The next session after earlier work: a run with two sessions, the first doing T001
    (committed), the second stopped with `stop` after its `run` step and before any agent: the run ends `stopped`
    "stopped before its next task", T001 and the first session's report are on `main` (landing `landed`), there is
    no `.sdd/runs/<second session>.md`, and the control log's taken line still says "stopping before any task" (it
    is about the session). By hand this needs a real `claude`: the fake writes no `workflows.json`, so its
    sessions leave no task entries and the reason would read "stopped before any task". `tests/test_supervise.py`
    `JobTest` covers it with a committed history file standing in for the first session (plan 15j).

In the dashboard: Start run, and while the banner says "Preparing <title>" press Stop after current task; the run
ends as above, and its card in Runs shows a **Requests** list with one row: "<time> · Stop after current task ·
dashboard · accepted · stopping before any task". Pressing Stop now while preparing asks "Stop the run now? No task
has started yet, so the stop adds no work to the run; work finished earlier in this run still lands. …" (data-model
§8.5, 2026-11-09) and also ends the run `stopped`.

## 9. Sending state and the trace of a refusal (FR-031, FR-033, FR-034)

1. With no run going, `python3 bin/sdd-control.py stop --project <repo>` exits 1 with the refusal; the control log
   gets a request line with `"outcome": "refused"`, the same reason and `"run": null` (no card shows it).
2. In the browser test (`page.route` on `**/api/run/stop`, never answered): after the click the button reads
   "sending…" and the banner's control buttons are disabled; after 10 s the banner and a toast say "request failed:
   no answer after 10 s" and the buttons work again; the route saw exactly one request.
3. A past run's card still lists its requests; a run without requests shows no Requests list.
4. `[manual]`: run `SDD_UI_TESTS=1 python3 -m unittest tests.ui.test_stop_preparing tests.ui.test_banner
   tests.ui.test_jobs` once outside a run and record the output (a run's agents may not be able to start a browser,
   `.sdd/learnings.md`).
