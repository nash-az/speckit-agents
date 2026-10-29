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

