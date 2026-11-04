# Learnings

## 20261028-1741 (2026-10-28)
- The run sandbox cannot reach PyPI (403 on `pip install playwright`), so a task that needs a new Python package must be a `[manual]` install step.
- Chromium is already cached under `~/Library/Caches/ms-playwright`; only the Playwright Python package is missing for browser tests.
- A task that deletes files (T002 removed `package.json` and `package-lock.json`) makes the workflow report an "Irreversible Local Destruction" security warning even when the task asks for it and the verifier approves. Check the commit with `git show --stat` before closing the run; git can restore the files.
- `claude plugin test .` is required by the constitution before every commit and is now in `checks.commands` (it was missing, so earlier runs committed without it).
- A run cut off during the bookkeeper's commit (run 20261028-1005) cannot be resumed by the skill as written; the main session finished the commit by hand (see `plugin-feedback.jsonl`).

## 20261029-1003 (2026-10-29)
- Dependencies are reached through Artifactory (`artifactory.workday.com`, user and password in the env file the user names; never print them). PyPI: repo `python-virtual`, index `https://<user>:<password>@artifactory.workday.com/artifactory/api/pypi/python-virtual/simple` passed as `PIP_INDEX_URL` for one command, not saved in a config file. Direct PyPI and npm return 403 from the sandbox.
- Playwright for Python (installed with `pip install --user`) cannot use the cached Chromium 1228 (that build is for Playwright 1.61.1); launch with `channel="chrome"` to use the installed Google Chrome, which needs no download.
- The language app's `frontend/node_modules` has Playwright 1.61.1 and matches the cached Chromium 1228. A throwaway Node script that requires it by absolute path gives a real browser look at the dashboard without installing anything in this repo.
- The dashboard's read-only guard test (`ReadOnlyTest`) rejects `subprocess`, `.unlink(` and the text `git ` anywhere in `bin/sdd-dashboard.py`, even in comments. Writes, git and process spawning live in helper scripts (`sdd-decide.py`, `sdd-launch.py`) that the dashboard imports.
- A dashboard "Save decision" or "Start run" needs a clean working tree. Commit before using them, and make no edits while a run is going.
- In `tasks.md`, only the first id after `after`/`needs` is parsed as a dependency: write one `After Tnnn` per task, and chain the rest.
- Dashboard Python tests load a slice of the page script from `escapeHtml(` to `loadProjects(`; a helper used by task cards (such as `inlineCode`) must be defined inside that slice or the node-based tests fail with a ReferenceError.

## 20261030-1515 (2026-10-30)
- A run's agents cannot bind a local port or launch Chromium/Chrome inside the sandbox, so browser tests in `tests/ui/` only skip during a run. A task that must see a real browser result (a "run it and record" step) has to be `[manual]`, or it blocks and every task after it is skipped.
- Phase 11's tasks chain through T053 and T054 ("After T053", "After T054"), so one blocked browser task skipped 13 of 16 tasks. Tasks that only write test code should not depend on a task that records a real browser run.

## 20261030-1545 (2026-10-30)
- Answering an environment block (no browser, no local port) with "continue" without changing the sandbox or doing the step by hand only reproduces the block: change `.claude/settings.json` or do the step outside the sandbox first.
- Inside the run sandbox, Google Chrome via `channel="chrome"` also fails (ProcessSingleton profile error), not just the bundled Chromium.

## 20261030-1854 (2026-10-30)
- Since `b765e6e` (`allowLocalBinding` plus the single-process Chromium fallback), browser tests run and pass inside the run sandbox: 31 UI tests ran in run agents with none skipped. A browser task no longer has to be `[manual]`.
- In `dontAsk` mode the agents' `SDD_UI_TESTS=1 python3 -m unittest …` (an env-var prefix) is refused. They run the same tests without the prefix and confirm by hand that none skipped. Allowlist the prefixed command if strict runs are wanted.
- The dashboard no longer loads Google Fonts (T065), so the UI tests have no network dependency besides the CDN files, which `tests/ui/vendor/` serves.


## 20261102-1150 (2026-11-02)
- The fake `claude` (`tests/fake/claude`) writes a `run.json` with only `run_id`, so a hand-run Continue on a held Job is refused unless `branch` and `job` are added as a real `/sdd-run` writes them.

## 20261104-1710 (2026-11-04)
- The verifier holds every engine command to `specs/008-standalone-server/contracts/engine-cli.md`: refusals as well as successes must print one JSON object with `contract` (refusal: `{"contract", "error"}`, exit 1). Plain-text `refused: …` lines get a task rejected (T008, attempt 1). Update the tests that assert old output rather than keeping the old output.
- `snapshot` output now carries a top-level `contract` key next to the task ids, so `task_snapshot` in `run.json` has one key that is not a task. `changed` ignores it.
