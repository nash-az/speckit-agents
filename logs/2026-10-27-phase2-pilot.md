# Run log: 003 Phase 2 pilot on this repo, 2026-10-27

Target: this repo, feature `specs/003-plugin-redesign`, Phase 2 (T003–T012), base `main` at `128e209`.
Plugin run from the frozen copy `/tmp/speckit-agents-stable` (`4530dbe`, later patched to `5ab1a5a`), so the run
could not edit its own bookkeeping, guard or prompts. Main loop and all subagents on Opus 5.5
(`CLAUDE_CODE_SUBAGENT_MODEL=claude-opus-5-5`); the plugin itself is never built with a cheaper model.
Operator: a separate Claude Code session started in `~/private`, launching
`claude -p "/sdd-run --yes --no-converge --phases 2" --permission-mode dontAsk --add-dir /tmp/speckit-agents-stable`.

## Result
Run `20261027-1352`, branch `auto/20261027-1352`: **10 of 10 approved, 0 blocked, 0 held, 0 skipped**.
Every task passed the verifier on its first attempt; the checks never needed a repair. 30 agents, ~665k subagent
tokens, ~19 min (about 2 min per task), reported cost $5.22. One commit per task plus the run report.

Operator spot checks after the run: both suites pass on the branch (74 Python tests, 15 node tests, 0 skipped).
T006 matches data-model §3.4, including the direction (`superseded-by Tnnn` on X holds **X**) that an earlier
design pass had backwards, and the `[manual]` T020 / Convergence T090 case §3.4 asks for.

Reading: tasks written to the data-model's exact sections (functions, message strings, test cases) were enough for
first-pass approvals. This says nothing yet about a cheaper or local implementer; that's a feature of the finished
plugin to test on a user project.

## Launches before it ran (all stopped before any task)
1. **Launches 1–2: nested `claude -p` EPERM on the sandbox socket.** The repo's own `.claude/settings.json` (sandbox on,
   `failIfUnavailable`) also locks an operator session running *in* the repo. → Start the operator outside the repo.
2. **Launch 3: preflight refused the tree.** The clean-tree check counts untracked files, and the user's two
   `.claude/` files are untracked by design. → Listed them in `.git/info/exclude`. `/sdd-init` could offer this
   for files the user wants kept untracked.
3. **Launch 4: checks failed under the sandbox.** `tests/workflow.test.mjs` wrote to hard-coded `/tmp/...`; the
   sandbox allows only `$TMPDIR`. Also the worktree test leaked a `spawnSync git ENOENT` after it ended (outside the
   sandbox too). → `128e209`: `os.tmpdir()`, worktree test skipped (T012 then deleted it). Lesson: tests must never
   hard-code `/tmp`; preflight is the first place a sandbox-only failure shows.
4. **Launch 5: `SyntaxError: import() is not available in workflow scripts`.** The engine (Claude Code 2.1.311) no
   longer allows `await import('node:fs' | 'node:path' | 'node:child_process')` in Workflow scripts. → Patched the
   frozen copy only: `event()` a no-op, worktree code removed.

## Problems found
- **003 design gap (blocking for Phase 4 and the learnings step):** research.md D6 assumes the workflow writes
  `.sdd/runs/<id>/events.jsonl` itself. It can't any more. The repo's `workflows/sdd-run.js` still has the
  `fs`/`path` imports (lines 34–35), so **the repo's plugin cannot run a workflow today**. T021, T022, T026 and
  data-model §6 read that feed. Needs a new writer (main loop, bookkeeper, or a helper an agent runs) in 003
  before Phase 4.
- **Wrong guess in the run report:** it says the event feed was missing "likely the guard's `.sdd/**` protected
  path". The real cause is the pilot patch that turned `event()` into a no-op. The main loop didn't know about
  the patch. → A report should not guess causes as findings; it should say "unknown".
- **The installed plugin is a cache, pinned to a git sha** (`~/.claude/plugins/cache/speckit-agents/.../0.1.0`).
  Editing the marketplace folder changes nothing until commit + `plugin uninstall` + `marketplace update` +
  `plugin install`. The frozen-copy plan didn't account for this.
- **Headless `dontAsk` refused a compound command** in the main loop (`git switch -c … && mkdir -p … && cat > …`);
  it recovered by splitting the commands. Same as the 2026-10-14 log.

## Operator notes
- **Monitor:** started late (the user asked for it). Rule stands: start `logs/watch_wf.py` through Monitor at
  launch, filtered (no `start` lines, no healthy heartbeats).
- **`watch_wf.py` false alarms:** it flags any tool result containing "guard", so listing `tests/` (`guard.test.ts`)
  or reading `skills/sdd-run/SKILL.md` shows as a refusal (4 times this run), and one line was reported twice.
  → Match refusal markers only (`Permission`, `<tool_use_error>`, the guard's own message prefix), not the word.
- **zsh gotcha:** `echo ======` fails (`=` expansion), which hid a `cat` result in the operator's first command.
