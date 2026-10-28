# Quickstart: validating the plugin redesign

Run after implementation. Build fixtures from `tests/e2e/make_toy.sh` (extend it; don't start from
scratch). Each section names the success criterion it proves.

## 1. Task state (SC-003, FR-007/008/009/014/023)
Extend the toy project's `tasks.md` and `BLOCKED.md` with:
- T010 `[manual]` → held; T011 "after T010" → held (dependency).
- T012 `[dep] [dep:ok]` with `[allow] dependencies = false` → runnable.
- T013 with a BLOCKED.md entry `status: resolved`, answer "use option B" → runnable; `plan` output
  carries the answer.
- T014 with `status: moved` → held as moved.
- An older phase with open T020 `[manual]` before a `## Phase 9: Convergence` phase whose T090 says
  "per T020" → T020 superseded (by Phase 9, not held as manual), T090 runnable (a superseded
  dependency does not hold it).
Run `python3 bin/sdd-tasks.py plan tasks.md --blocked BLOCKED.md --config .sdd/config.toml` and
assert each id's runnable/held state and tag. Then run the existing toy e2e: it must still pass.

## 2. Clean tree between runs (SC-004, FR-013/026)
On a toy project after `/sdd-init`: confirm `.gitignore` has the four lines (data-model §7). Run a
one-task `/sdd-run` in which the implementer writes a file under `.sdd/scratch/`. Pass: the guard
allowed the write, the task commit holds no scratch or event files, and a second `/sdd-run` passes
its clean-tree preflight. Also: with a `.gitignore` missing the lines, `/sdd-run` stops and names
`/sdd-init`.

## 3. Dashboard (SC-001, SC-002, SC-004)
`python3 bin/sdd-dashboard.py --project <toy> --port 8799`, open `http://127.0.0.1:8799`.
- Every document in spec US1 acceptance 1 opens and renders (headings, a table, a highlighted code
  block); no raw markdown syntax visible.
- Tasks view: every fixture task shows icon + color + label; phases show their rollup.
- Waiting-on-you lists the held and blocked tasks with questions.
- During a real run: events appear without reload; a running task's live output shows (or "not
  available yet").
- With two feature folders and no `.specify/feature.json`: the picker appears instead of a guess.
- `curl -H 'Host: evil.example' http://127.0.0.1:8799/api/projects` → 421.
- Kill a run mid-task and delete its report-less state as a crash would leave it: the dashboard
  shows the stale-run banner after 30 minutes without events (or set the events file's mtime back).

## 4. Models (SC-005, FR-011/012/025)
- `[agents] implementer = "haiku"` + `python3 bin/sdd-supervise.py --project <toy> --profile cheap`
  (profile model `sonnet`): the run completes a task; the report lists implementer = haiku, other
  roles = the run's model, profile = cheap.
- `[agents] coder = "haiku"` → the run refuses to start: `agents.coder: unknown role`.
- `[profiles.x] env = { ANTHROPIC_API_KEY = "…" }` → refused as a secret.
- `run.isolation = "worktree"` → refused: worktree isolation was removed.

## 5. Learnings (SC-006, FR-024)
Run two toy runs that hit a known quirk (e.g. a check command that needs a flag). Pass:
`.sdd/learnings.md` has a section per run, committed with the report; `.sdd/plugin-feedback.jsonl`
exists and is untracked. Run `python3 bin/sdd-harvest.py <toy>` twice from the plugin repo: the
first run appends entries to `logs/`, the second reports `harvested: 0`.

## 6. Resume safety (FR-018)
Interrupt a run during T030, edit T030's description, start `/sdd-run`: it stops and names T030.
Interrupt again, only tick an unrelated box: it resumes.

## 7. Supervisor hard stop (FR-028)
`python3 bin/sdd-supervise.py --threshold five_hour=0.95,seven_day=0.99 …` (dry run or unit test):
hard stops are 0.96 and 1.0; with `--hard-stop 0.97` both are 0.97 (capped at 1.0).

## 8. Stop and resume from the dashboard (FR-029, FR-030)
Toy project, run through `python3 bin/sdd-supervise.py --project <toy> --poll 1 --max-wait-hours 1`
(fake `claude` from `tests/fake/`, or a real two-task toy), dashboard started on the same project:
- `GET /api/run/current` shows `controls.stop: true`; `POST /api/run/stop` (`Origin` = the dashboard's
  own, JSON body) → `202`; state goes `stopping`, then `held` after the running agent finishes, with
  no task lost; `controls.resume: true`.
- `POST /api/run/resume` → `202`; the supervisor starts a new cycle and the run completes.
- `{"mode": "now"}` stops at once; the next resume continues from the journal.
- No supervisor running (kill it, leave `status.json`): `controls` both false with "no supervisor is
  running…"; both POSTs → `409`, no `control.json` appears.
- During the usage-window wait (`state: "waiting"`, fake `claude` reporting a reset a few seconds
  ahead, no `--no-sleep`): stop and resume → `409`, `controls.hold: true`. `POST /api/run/hold` → `202`,
  `keep_paused: true`; at the reset the state goes `held` ("kept paused…") and no new cycle starts;
  `POST /api/run/resume` → the run continues. Repeat with `POST /api/run/release` before the reset:
  the cycle starts at the reset by itself.
- A POST with `Origin: http://evil.example`, with no `Origin`, or with `Content-Type: text/plain` →
  `403`/`403`/`415`; the run is unaffected.
- `git status` in the toy is clean throughout (the dashboard wrote nothing in the project).

## Validation run

Run 2026-10-28 (task T042), unattended from inside a run, with the toy built by
`tests/e2e/make_toy.sh` under `.sdd/scratch/`. Nothing was patched. Supervisor, dashboard and toy checks ran live with the fake `claude` from
`tests/fake/`; parts that need real model calls or a browser are marked not run.
Baseline: `python3 -m unittest discover -s tests` 231 pass; `node --test tests/workflow.test.mjs`
24 pass; `claude plugin test .` 17 pass.

| § | Result | Evidence |
|---|--------|----------|
| 1 Task state | pass (toy e2e part not run) | `tests/e2e/check_toy.sh <toy> --state-only` prints "task state: all fixture ids as expected": T010 held:manual, T011 held, T012 runnable, T013 runnable with "use option B", T014 held:moved, T020 held:superseded by Phase 9, T090 runnable. The full toy e2e needs a real `/sdd-run`; not run. |
| 2 Clean tree | pass (real-run parts not run) | Guard tests for `.sdd/scratch/` pass (Edit and shell writes allowed, `..` escape refused). On a git toy with the four `.gitignore` lines, files under `.sdd/scratch/r1/T1/`, `.sdd/run.json`, `.sdd/runs/r1/workflows.json` and `.sdd/plugin-feedback.jsonl` leave `git status --porcelain` empty (a second preflight would pass). With only `.sdd/run.json` in `.gitignore`, the same files show as `?? .sdd/plugin-feedback.jsonl`, `?? .sdd/runs/`, `?? .sdd/scratch/` (the state the `.gitignore` check exists to stop). The stop-and-name-`/sdd-init` message is `/sdd-run` SKILL step 3 text and needs a real model; not run. The task commit contents of a real one-task run: not run (needs a real model). |
| 3 Dashboard | partial | Live on port 8799 against this repo: all 7 documents of `/api/doc` return 200; `/api/project/tasks` returns phases with statuses and rollups; `Host: evil.example` gives 421; the current run's events appeared in `/api/runs/<id>/events`; no supervisor gives `controls` all false with the "no supervisor is running" reason. On a toy with two features and no `feature.json`: 409 `choose a feature` with both names. Stale banner covered by 3 unit tests only. Not checked: the rendered page (headings, table, code highlighting, icons, live output) needs a browser. |
| 4 Models | pass (report part not run) | Toy config with `[agents] implementer = "haiku"` and `[profiles.cheap] model = "sonnet"`: `sdd-supervise.py --profile cheap` (fake `claude`) logs "profile cheap: model sonnet", runs `claude -p "/sdd-run --yes --profile cheap" --model sonnet` with the profile env (`CHEAP_FLAG=1`) set, and exits 0. `sdd-tasks.py config` gives `agents.implementer = haiku`; `workflow.test.mjs` ("agentModels ..." tests) shows the implementer calls (and repair) carry `model: haiku` while verifier, bookkeeper and evaluator calls without an entry carry none (they inherit the run's model). Refusals: `agents.coder` gives "agents.coder: unknown role"; `run.isolation = "worktree"` gives "worktree isolation was removed; delete this line"; `profiles.x.env.ANTHROPIC_API_KEY` gives "secrets must come from your environment" (and `sdd-supervise.py --profile x` exits 2). `/sdd-run` stops on a non-empty `errors` list (SKILL step 2). Note: `sdd-supervise.py` itself does not refuse the first two (it exits 4 with no error); only `/sdd-run` does. Not run: the report text listing implementer = haiku, others = the run's model, profile = cheap (the report is written by the `/sdd-run` skill, which needs a real model). |
| 5 Learnings | partial | `sdd-harvest.py` on a toy feedback file (from a copy of the plugin, so `logs/` here stayed untouched): first run `harvested: 2`, second run `harvested: 0`. Not run: two real toy runs writing `.sdd/learnings.md` and `plugin-feedback.jsonl` (T038 is skill text). |
| 6 Resume safety | pass (real interrupt not run) | On the toy, `run.json` carrying `task_snapshot` from `sdd-tasks.py snapshot`: unchanged `tasks.md` gives `changed: []`; ticking only T002 gives `changed: []` (resumes); editing T001's description gives `changed: ["T001"]` (the skill stops and names it). Unit tests `test_edited_description_is_listed` and `test_cli_snapshot_and_changed` pass. An interrupted real run and the skill's stop message: not run (need a real model). |
| 7 Supervisor hard stop | pass | `HardStopTest`: defaults are threshold + 0.01 per window (0.96 / 1.0), `--hard-stop 0.97` applies to both, capped at 1.0. All pass. |
| 8 Stop and resume | pass (no browser) | Live: `sdd-supervise.py --project <toy git repo> --poll 1 --max-wait-hours 1` (fake `claude`) plus `sdd-dashboard.py --project <toy> --port 8798` on the same project, state dir shared via `XDG_STATE_HOME`. (a) Running: `GET /api/run/current` gives `controls.stop: true`. `POST /api/run/stop` (own Origin, JSON `{}`) gives `202`; state `stopping` (mode after-task) then `held` ("stopped by operator") 6.1 s later, once the running agent finished, journal intact (3 entries); `controls.resume: true`; `POST /api/run/resume` gives `202`; cycle 2 started and the supervisor exited 0 (2 cycles). (b) `{"mode":"now"}` gives `202`, `held` after 1.0 s while the agent was still sleeping; resume gives `202`, cycle 2 ran, exit 0. (c) Supervisor gone, `status.json` left: `controls` all false with "no supervisor is running; start one with ..."; stop, resume, hold and release all give `409`; no `control.json` appeared. (d) Waiting (fake `claude` reporting a reset 25 s ahead): `controls.hold: true`; stop and resume give `409`; `POST /api/run/hold` gives `202`, `keep_paused: true`; at the reset state `held` ("kept paused; the usage window has reset"), still 1 cycle started 3 s later; `POST /api/run/resume` gives `202`, cycle 2 ran, exit 0. (e) Waiting again: hold `202`, `controls.release: true`, `POST /api/run/release` gives `202`, `keep_paused: false`, the cycle started at the reset by itself (2 cycles, exit 0). (f) During a run: `Origin: http://evil.example` gives `403`, no `Origin` gives `403`, `Content-Type: text/plain` gives `415`; no `control.json` written, run state stayed `running` and finished (exit 0). `git status --porcelain` in the toy was empty after every scenario. Also `RunControlTest` (6 tests) passes. Not run: the Stop/Resume banner in a browser. |

Findings to report (not patched here):
- No failures found in what was run. Everything that needs a real model (a real `/sdd-run`, the report text, the skill's stop messages) or a browser is marked not run above.
- §4 wording "the run refuses to start" is true of `/sdd-run`, not of `sdd-supervise.py` (see above).
- Nothing was run in a browser, so the Stop/Resume banner and document rendering remain unseen.
