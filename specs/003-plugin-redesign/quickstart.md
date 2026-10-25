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
