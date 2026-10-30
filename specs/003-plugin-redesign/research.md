# Research: Plugin redesign

Decisions behind `plan.md`, and what reading the actual code showed. Revised 2026-10-25 after a
whole-project review pass; the first draft of this file had several claims the code contradicts —
they are corrected below and marked **(corrected)**.

## Decisions

### D1. Markdown rendering
**Decision**: `marked` + highlight.js (`@highlightjs/cdn-assets`) + DOMPurify, loaded as pinned
`<script>` tags from jsDelivr, rendering into the dashboard's existing `.markdown` container.
**Why**: the dashboard already has hand-tuned `.markdown` CSS; `zero-md` renders into shadow DOM,
which fights that styling and buys features (Mermaid, KaTeX) this project doesn't need. DOMPurify
because agents write these documents. The backend stays stdlib-only.
**Rejected**: `zero-md`; server-side rendering (would need a non-stdlib Markdown library).

### D2. Which views are rendered markdown
**Decision**: spec, plan, research, data-model, quickstart, decisions, constitution, learnings and
run reports render through D1. Tasks and BLOCKED keep their structured card views (with §1 badges);
their raw documents are viewable rendered behind a toggle.
**Why**: the structured views already exist and are the right UI for status; rendering tasks.md as
generic markdown and patching checkboxes afterwards would be fragile.

### D3. Status taxonomy
Ten keys, parked vs transient, exact colors and glyphs: `data-model.md` §1.

### D4. Model selection — two levels (corrected)
**Decision**: (a) **run profiles** (`[profiles.<name>]`, model + optional non-secret env) applied by
`bin/sdd-supervise.py`, which already starts every headless run as its own `claude -p` process with
its own environment (`sdd-supervise.py` `command()` and `Popen(..., env=env)`); (b) **role models**
(`[agents]`) passed as `agent()`'s documented `model` option inside the run.
**Why**: provider/base URL is per process in Claude Code, so per-run is the level where a cheaper
provider or a local-model gateway can be chosen at all; per-role is still useful inside a run (keep
the verifier strong). The architect works in the person's own interactive session.
**Validation (corrected)**: the first draft required model ids to match `^claude-…`, which rejects
Bedrock ARNs, Vertex version names and Foundry deployment names — the user keeps a Bedrock example
config in this repo. Model strings are now passed through unchecked; only structure is validated
(unknown roles/profiles, empty values, secrets in env). Silent platform fallback (org model
allowlist) cannot be detected from a plugin, so the run report lists the configured model per role.
**Caveat**: Anthropic does not support non-Claude backends through gateways; local models often lack
reliable tool calling. Documented as best-effort.

### D5. Icons
Inline SVG glyphs in one JS object, no icon library. Extends the dashboard's existing pill pattern.

### D6. Events come from the workflow journal (revised 2026-10-27)
First decision: the workflow writes `.sdd/runs/<run id>/events.jsonl` with `event()`. The Phase 2
pilot (`logs/2026-10-27-phase2-pilot.md`) found that impossible. The Workflow tool refuses a script
that uses `import()` (`SyntaxError: import() is not available in workflow scripts`, Claude Code
2.1.311), and the workflow-authoring reference says scripts have no file system or Node.js API and
that `Date.now()` throws. The workflow can neither write a file nor stamp a time.

Options:
- (a) The bookkeeper appends events after each group. Rejected: tokens for every event, nothing
  between group ends (no "implementing" / "verifying"), and the guard keeps subagents out of `.sdd/**`.
- (b) The main loop writes the events after the Workflow returns. Rejected: nothing live during a run.
- (c) **Chosen:** derive the events from the transcript directory the engine already writes live for
  every workflow: `journal.jsonl` (a `started` and a `result` or `failed` line per agent, with the label
  the workflow gave it) and `agent-<agentId>.jsonl` (every line has an ISO `timestamp`). Stdlib only
  (Principle VII), no tokens, live, and the event kinds the dashboard uses stay the same.
  `bin/sdd-tasks.py` already reads this journal to resume (`checkpoint()`, `LABEL_RE`).

Costs of (c):
- The transcript lives under `~/.claude/projects/…`, and Claude Code deletes it after `cleanupPeriodDays`
  (default 30). Older runs lose their timeline; the committed report stays.
- Run and phase events (`phase.started`, `phase.skipped`, `run.finished`, `task.resumed`,
  `task.interrupted`) are not in the journal. The report and `.sdd/run.json` carry those facts
  (data-model.md §3.9, §4.5).

The main loop records each workflow's transcript directory in `.sdd/runs/<run id>/workflows.json`
(a resumed run adds one). Keep the existing 3-second polling.

### D7. Live output
Per-agent transcripts are `agent-<id>.jsonl` in the workflow's transcript directory (confirmed in the
workflow-authoring reference). `workflow_dir` is in `.sdd/run.json` while a run is active. The
label → file mapping is **confirmed** (Phase 2 pilot, 2026-10-27): every journal `started` line
carries `agentId` and `label`, e.g.
`{"type":"started","key":"v2:6817…","agentId":"a4160c52caf1c510e","label":"impl:T003","phase":"Build"}`,
and that agent's transcript is `agent-a4160c52caf1c510e.jsonl` (its `.meta.json` repeats the label as
`description`). Use the mapping; no content search (`data-model.md` §4.6).

### D8. Localhost only
`main()` already binds `127.0.0.1` with no `--host` option. Added: a Host-header check against DNS
rebinding (a browser tab on a hostile site can otherwise reach a localhost server), and a comment
marking the bind as a security boundary.

### D9. Dashboard writes and the chat feature (corrected)
The first draft made the dashboard "structurally read-only, no write route ever" (FR-020), which
would force a backend redesign for the next feature (chat that writes specs). Now: the dashboard
never writes files or git itself; future actions are delegated to a Claude Code session working
under the guard. `contracts/action-channel.md` fixes the shape now; no action route ships in 003.

### D10. Worktree isolation removed (new)
`workflows/sdd-run.js` creates worktrees but never runs agents inside them: `runTask()` does not
receive the worktree path, so agents edit the main tree; `git add` in the worktree then adds
nothing, and `mergeWorktree()` calls `git checkout <worktree path> -- files`, passing a path where
git expects a commit. Every error is swallowed. Removed in 003 (user decision); rebuild later on
`agent({isolation: 'worktree'})`, minding backlog item 1 (it branches from the default branch).

### D11. `resolved` means "run again" (new)
`block()` writes into every BLOCKED.md: "set `status: resolved`, then run `/sdd-unblock` or the next
`/sdd-run` picks the task up again", but `TERMINAL_STATUSES` and `/sdd-unblock` hold resolved tasks
forever. The first draft of this file explained the code's behavior as intentional. User decision:
the header text is right — resolved tasks run again, with the answer in the prompt.

### D12. Learnings stay out of the app repo's history (new)
The repo's own rule: app repos hold no notes about developing the plugin. Project lessons belong
to the project (`.sdd/learnings.md`, committed). Plugin feedback is written in the project but
git-ignored, and harvested into this repo's `logs/` by `bin/sdd-harvest.py`. A run in an app repo
cannot write to the plugin directory (sandbox), which is why harvesting is a separate step.

## Corrections to the first draft and to the backlog

- **Superseded (FR-008) (corrected)**: the first draft said the gap was "nothing writes
  `superseded-by` on the original task". The code does the opposite of that design:
  `direct_holds()` treats `superseded-by Tnnn` written on task X as superseding **Tnnn**, and only
  while X is still open — once X is ticked, Tnnn becomes runnable again. Nothing uses the annotation
  today (checked in the language app). Redesigned in `data-model.md` §3.4, with a deterministic
  rule for converge and no edits to Spec Kit's commands.
- **Dependency of a held task (FR-009, item 20)**: already implemented by `propagate_holds()`.
  Regression test only, plus one change: a `superseded` dependency must not hold its dependents
  (otherwise every converge task that says "per T050" would be held by its own predecessor).
- **Converge blocked by held tasks (FR-010, item 27)**: already fixed in `skills/sdd-run/SKILL.md`
  step 2.1 ("tasks that are only held … don't count"). Regression test only.
- **Per-task approval (FR-014)**: two bugs — `plan()` holds `[dep] [dep:ok]` tasks because
  `hold_reason()` only checks the global allow flags, and the workflow never reads `taskAllow`.
- **Scratch dir (FR-013) (corrected)**: the guard carve-out is not enough. Nothing git-ignores
  `.sdd/scratch/`, though the agent prompts say it is; scratch files would show up as "unclaimed
  changes" and make the next preflight refuse a dirty tree.
- **Event directories (FR-026, new)**: `.sdd/runs/<id>/events.jsonl` (now `workflows.json`, D6) is neither committed nor
  ignored, so after any run the tree has an untracked file and the next `/sdd-run` preflight stops.
- **Dashboard picks an arbitrary feature (FR-027, new)**: `find_file()` returns the first match in
  `specs/*/` directory order; with several features (this repo has three) it can show the wrong
  feature's tasks.
- **Supervisor hard stop (FR-028, item 35)**: still floors every window at the global 0.99
  (`max(args.hard_stop, t + 0.01)`).
- **`templates/sdd-config.toml` protects `.sdd/**`** (seeded into every project);
  `bin/sdd-tasks.py`'s `DEFAULT_CONFIG` itemizes only `.sdd/config.toml` and `.sdd/run.json` and is
  used only before `/sdd-init`. The scratch carve-out works with either.

## Backlog disposition (every item, so nothing is dropped silently)

| Item | Topic | Disposition |
|---|---|---|
| 1 | Eval worktrees from HEAD | Deferred with worktree isolation (D10) |
| 2 | `model`/`maxTurns` per agent | `model`: in 003 (FR-011). `maxTurns`: deferred |
| 3 | Spawn depth / concurrency cap settings | Deferred |
| 4 | Phase-end code reviewer | Deferred |
| 5 | Retry on API error / partial result | Partly done (null agent → interrupted, resumable). Rest deferred |
| 6 | GPU-bound evals | Deferred with worktree isolation |
| 7 | PreToolUse hint for compound commands | Deferred |
| 8 | Independent review before committing | Process: this review pass and `/speckit-analyze` |
| 9 | Verifier runs project checks | Done (verifier prompt, check 5) |
| 10 | Phone notifications | Deferred |
| 11 | Dashboard | In 003 (US1, US2) |
| 12, 12a | Usage-limit pause/resume | Done (supervisor, checkpoint) |
| 13 | One "waiting on you" list | In 003 (FR-005) |
| 14 | Manual tasks vs pending amendments | Deferred |
| 15 | Decision evidence/trade-offs | Done (run report step 7) |
| 16 | zsh `"${B}:path"` rule | Deferred |
| 17 | One end state for dropped tasks | In 003 (FR-007, FR-023) |
| 18 | How-to steps for manual tasks | Done (run report step 7) |
| 19 | Decisions register | Done (`/sdd-unblock` step 2) |
| 20 | Dependencies on held tasks | Done; regression test in 003 |
| 21 | Monitoring | Event feed done; dashboard in 003; watchdog deferred |
| 22 | Task splitting / UI words | Deferred |
| 23 | Scratch folder | In 003 (FR-013, FR-026) |
| 24 | "Moved" state | In 003 (FR-007) |
| 25 | Unclaimed files | Done (bookkeeper prompt) |
| 26 | Superseded originals after converge | In 003 (FR-008) |
| 27 | Converge blocked by held tasks | Done; regression test in 003 |
| 28 | Flow checks for UI tasks | Deferred |
| 29 | Per-task dependency approval | In 003 (FR-014) |
| 30 | Operator monitoring cost | Addressed by the dashboard; non-AI CLI (37c) deferred |
| 31 | Per-window budgets | Done (supervisor); editing from the dashboard deferred |
| 32 | Relaunched workflows | Done |
| 33 | Resume after a spec change | In 003 (FR-018) |
| 34 | `/sdd-check <area>` | Deferred |
| 35 | Hard stop per window | In 003 (FR-028) |
| 36 | In-run feedback channel | Next feature (chat) |
| 37 | Registry / discovery | Narrowed in 003 (FR-015); registry deferred |
| 37a, 37b | Open-any-project, all-agents view | Deferred |
| 37c | Non-AI CLI client | Deferred |
| 37d | AI query layer | Next feature (chat) |
| 37e | MCP vs HTTP | Open; the action channel (D9) is HTTP-shaped and can be wrapped by MCP later |
| 38 | Learnings in the project | In 003 (FR-024) |
| 39 | Review of 002-redesign | Done (superseded by 003) |
| 40 | Chat window | Next feature, on D9's action channel |

## Phase roll-up

Decision (2026-10-29): (b)

## First browser run

T053, 2026-10-30, in the unattended run's sandbox (run on branch `auto/20261030-1515`), Playwright 1.64.1 for the system `python3`:

```
python3 -m unittest tests/ui/test_smoke.py -v
setUpClass (tests.ui.test_smoke.SmokeTest) ... skipped "Chromium cannot be launched (...)"
Ran 0 tests  OK (skipped=1)
```

The test did not actually run in a browser. These are environment results, not dashboard findings:

- The bundled Chromium is not installed (`chromium_headless_shell-1248` is missing under `~/Library/Caches/ms-playwright/`).
  The fallback, Google Chrome with `channel="chrome"`, fails inside the sandbox with "Failed to create a ProcessSingleton
  for your profile directory".
- The sandbox also refuses to bind a local port (`PermissionError: [Errno 1] Operation not permitted` from
  `ThreadingHTTPServer` in `bin/sdd-dashboard.py`), so the dashboard can't start here either.
- Harness finding for T065: `UITestCase.setUpClass` only skips when the browser is missing. When the browser launches but
  the dashboard can't bind its port, `Dashboard.start()` raises `RuntimeError`, so the class errors instead of skipping.
  - **Result (T065): fixed.** Test first: `test_ui_test_case_skips_when_dashboard_cannot_start` in
    `tests/test_ui_harness.py` (a faked browser, `Dashboard.start` raising the bind error) failed with the class in
    `errors`. Fix: `start_dashboard()` in `tests/ui/harness.py` turns that `RuntimeError` into a one-line skip
    ("the dashboard cannot start (... ; PermissionError: ...)"), or a failure under `SDD_UI_TESTS=1`
    (`skip_or_fail()`, shared with `require_browser()`; tested by `test_ui_test_case_fails_when_dashboard_cannot_start_and_strict`).
    `UITestCase` and the test classes with their own `setUpClass` (`test_views.py`, `test_banner.py`, `test_rollup.py`)
    all start their dashboard through it.

Dashboard findings (console errors, phases missing from the Tasks view): none yet. The smoke test still has to be run
outside the sandbox with `SDD_UI_TESTS=1 python3 -m unittest tests/ui/test_smoke.py -v`, and its failures listed here.

Retry, 2026-10-30, run on branch `auto/20261030-1545` (after the answer "continue"): same result, `Ran 0 tests OK (skipped=1)`.
`chromium_headless_shell-1248` is still missing and Google Chrome still fails with the ProcessSingleton error inside the
sandbox, so there are still no dashboard findings. No browser result had been recorded outside the sandbox either.

Real run, 2026-10-30, branch `auto/20261030-1854`, inside the run sandbox after the sandbox fix (answer 2:
`allowLocalBinding` in `.claude/settings.json`, and `launch_chromium()` retrying with `--single-process`, commit `b765e6e`).
The browser starts and the dashboard binds its port, so both tests ran:

```
python3 -m unittest tests/ui/test_smoke.py -v
test_page_opens_without_errors ... FAIL
test_tasks_view_lists_the_toy_phases ... FAIL
Ran 2 tests in 4.5s  FAILED (failures=2)
```

Failures (listed, not fixed, per FR-031):

- **Dashboard: the page loads Google Fonts from the network.** `build_html()` in `bin/sdd-dashboard.py` (line ~1213) has
  `<link href="https://fonts.googleapis.com/css2?family=Inter...&family=JetBrains+Mono...">`. This is not one of the four
  pinned CDN files, so the harness neither vendors it nor has a fallback for it. The offline test page records
  `offline: blocked request to https://fonts.googleapis.com/...` and the console error `Failed to load resource: net::ERR_FAILED`.
  Both tests fail on this one error. Possible fixes (not chosen here): drop the web fonts for a system font stack, or vendor
  the fonts and add them to the CDN fallback (FR-032).
  - **Result (T065): fixed by dropping the web fonts.** Vendoring them would be a download and a fifth CDN dependency the
    plan does not list (plan.md Technical Context names only `marked`, `@highlightjs/cdn-assets` and `dompurify`); the CSS
    already had system fallbacks after `'Inter'` and `'JetBrains Mono'`, so no style rule changed. Those two fonts are
    still used where they are installed locally. Test first: `DocumentsViewTest.test_no_other_external_urls` in
    `tests/test_sdd_dashboard.py` (the page's only `<script src>`/`<link href>` network loads are the four pinned files)
    failed on the fonts URL, and `tests/ui/test_smoke.py` failed 2 of 2 (with `test_status.py` 3 of 3). After removing the
    `<link>` from `build_html()`, both pass, and the `WEB_FONTS` empty-stylesheet workaround in `test_views.py`,
    `test_banner.py` and `test_rollup.py` (marked "remove with T065") is gone: `python3 -m unittest discover -s tests/ui`
    runs 24 tests, OK; `python3 -m unittest discover -s tests` runs 321, OK.
- **Test expectation, fixed in the test:** the first version expected the raw `## Phase 1: Setup` heading. The dashboard shows
  the parser's title (`Setup`, per `PHASE_RE` in `bin/sdd-tasks.py`), uppercased with CSS. The test now reads the title span's
  DOM text and compares it without case. With that change the Tasks view lists the toy's phase. The only failure left is the
  font error above.
  - **Result (T065):** nothing to fix in the dashboard; the smoke test passes with this expectation.
- Observation, not a failure: the Tasks view phase heading does not show the phase number ("SETUP", not "Phase 1: Setup").
  - **Result (T065):** left as it is. No test or requirement asks for the number, and whether to show it is a layout
    choice for the design review (T067 and the design fold-in), not a defect, so it is not in `BLOCKED.md` either.

Screenshots and HTML of both failures: `.sdd/scratch/ui/tests.ui.test_smoke.SmokeTest.*.{png,html}` (git-ignored).
`SDD_UI_TESTS=1` was not set for this run because the run's shell refuses `VAR=value` prefixes. It was not needed: the
browser launched, so nothing was skipped.
