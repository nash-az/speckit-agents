# Research: Unattended runs

## 2026-10-14: first end-to-end run (SC-001), by hand

**Setup**
- Toy project: two `[P]` tasks (add, sub), one `[manual]` task (a demo video) and one dependent task (a CLI). The project check was `python3 -m unittest discover -s tests -t .`.
- Command: `claude -p --plugin-dir . --permission-mode dontAsk --settings <template + unittest allow> "/speckit-agents:sdd-run --yes"`.

**Result: SC-001 met.**
- **Commits on `auto/20261014-1657`:** `chore(sdd): ignore run marker`, then `feat(T002)`, `feat(T003)`, `feat(T005)`, then `chore(sdd): run report`. Each task commit held only that task's files plus its tick in `tasks.md`.
- **The manual task:** T004 was held and never attempted.
- **State afterwards:** `main` unchanged, all 16 tests passing, CLI behaviour as specified.

**Problems found, and what changed**
1. **Untrusted project folder.** Claude Code ignores a project's `permissions.allow` until the project is trusted, and `dontAsk` then refused the Workflow tool itself. Fix: `Workflow` added to the template allowlist, and the README says to trust the project once.
2. **The guard refused plain reads.** Reads such as `cat … constitution.md 2>/dev/null` were treated as writes to a protected path. Fix: the guard now checks only real write targets (redirection targets, and the file arguments of `tee`, `rm`, `mv`, `cp`, `truncate`, `sed -i`, …). There are tests for both directions.
3. **`dontAsk` denied listing.** `Glob` and `Grep` were not allowed. Fix: both added to the template.
4. **Wrong Workflow args.** The main loop's first call passed the skill argument `--yes` as `args`, and the workflow failed at once. The second call was correct. Fix: the skill now says this explicitly.
5. **Agents and compound commands.** `dontAsk` refused a few compound shell commands (`for` loops) that the sandbox does not allow automatically. The agents fell back to simpler commands. No change made.

T009 stays open: the script for this check (`tests/e2e/`) is not written yet.
