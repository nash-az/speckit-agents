# Learnings

## 20261028-1741 (2026-10-28)
- The run sandbox cannot reach PyPI (403 on `pip install playwright`), so a task that needs a new Python package must be a `[manual]` install step.
- Chromium is already cached under `~/Library/Caches/ms-playwright`; only the Playwright Python package is missing for browser tests.
- A task that deletes files (T002 removed `package.json` and `package-lock.json`) makes the workflow report an "Irreversible Local Destruction" security warning even when the task asks for it and the verifier approves. Check the commit with `git show --stat` before closing the run; git can restore the files.
- `claude plugin test .` is required by the constitution before every commit and is now in `checks.commands` (it was missing, so earlier runs committed without it).
- A run cut off during the bookkeeper's commit (run 20261028-1005) cannot be resumed by the skill as written; the main session finished the commit by hand (see `plugin-feedback.jsonl`).
