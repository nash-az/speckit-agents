# speckit-agents

A Claude Code plugin that runs a Spec Kit project's open tasks unattended, with subagents. It works on top of Spec Kit and never forks it.
Read `README.md` for the user-facing description, and `.specify/memory/constitution.md` for the principles every change must keep.

## Why this exists
The user (Nash) wanted their Spec Kit projects to be developed by Claude subagents "safely without my intervention until the system is done".

- **Managed Agents ruled out.** We looked at Managed Agents multiagent orchestration (platform.claude.com/docs/en/managed-agents/multiagent-orchestration). Its agents share one cloud sandbox, and the user's projects depend on local models and data, so we chose Claude Code locally.
- **Nothing to reuse.** Spec Kit (v1.0.9) has `[P]` markers and a workflow engine with steps that run one after another and gates where a person approves, but nothing that hands work to subagents. A web search found no existing Spec Kit add-on for this.

## Decisions the user made (2026-10-14)
- **Repo:** a separate repo plus a Claude Code plugin, not a folder inside an app repo.
- **v1 scope:** the full scope, including agent tags (`[manual] [decision] [dep] [download] [eval] [ui]`) and a tasks-template override, so that `/speckit-tasks` writes the tags.
- **Isolation from app repos:** app repos know about this only as an installed plugin. Keep context, progress and notes about this work here, never in an app repo or its memory.
- **Commits:** ask before committing. v0.1 is committed on `main` as a single commit, with no remote.

## How it works
- **`bin/sdd-tasks.py`** (stdlib only): `parse`, `plan` (groups consecutive `[P]` tasks with disjoint files; holds tagged tasks and open BLOCKED.md entries), `tick`, `block`, `config`.
- **`workflows/sdd-run.js`**: the Workflow script, shipped as the plugin workflow `speckit-agents:sdd-run`. The main loop calls it by name and passes the plan as `args`.
  - Per task: implementer (or evaluator for `[eval]`), then the read-only verifier. A rejection goes back with feedback, up to `max_retries` retries.
  - Per group: the bookkeeper runs the project checks, then makes one commit per task with only its files.
  - When the checks fail: one repair, and if that fails, stash the work and record the tasks as blocked.
  - Phases: a dependent phase is skipped when its dependency has an attempted task that failed. Held tasks do not taint their phase.
- **`agents/`**: `sdd-implementer`, `sdd-verifier`, `sdd-evaluator`, `sdd-bookkeeper`. Their full names are `speckit-agents:<name>`, and the workflow refers to them by those names.
- **`skills/`**: `sdd-init`, `sdd-tag`, `sdd-run` (preflight, confirmation, branch, marker, Workflow call, report), `sdd-status`, `sdd-unblock`.
- **`hooks/register.ts` and `hooks/rules.ts`**: the run guard, active only while `.sdd/run.json` exists.
  - It refuses destructive git, commits on protected branches and writes to protected paths.
  - It fails closed.
  - The main loop (no `agentId`) may write `.sdd/`.
- **`templates/`**: `sdd-config.toml`, `settings.json` (sandbox and allow/deny rules), `agent-tags.md`.

## Progress
- **Handoff (2026-11-12) — start here.** Checkout on `main`, clean, no run. Code unchanged since `735714b`; only
  `specs/009-runs/` moved. Lessons: `logs/2026-11-11-lessons.md`.
  - **009 amendment, rounds 7–9** (real `/speckit-*` commands, headless; Fable 5.2 from round 8, the person's choice):
    FR-007's crash-at-a-boundary clause moved to Out of Scope for 010 (the person's answer, `f3e20d4`); plan
    `4883696`; tasks `7250d73`, `aa17d4c`, `e384e9b`. Analyze pass 9: no CRITICAL or HIGH, 1 MEDIUM, 10 LOW; the gate
    is open. Reports: `.sdd/scratch/analyze-009-7.md` to `-9.md`.
  - **Waiting on the person's choice:** (1) a last round: `/speckit-plan` for pass 9's M1 and L7–L10, `/speckit-tasks`
    for L1–L6, analyze pass 10, then stop analyzing unless a HIGH appears; (2) `/speckit-tasks` for L1–L6 only; or
    (3) start the run now.
  - **Then:** the person starts the run from the dashboard's Start run on 009 Phases 16–20 (supervisor only). After
    it: T045 and T033 (the person's), then `/speckit-specify` 010.
- **Handoff (2026-11-09; superseded by the one above).** Checkout on `main`, clean, no run. Code unchanged since `735714b`; only
  `specs/009-runs/` moved.
  - **009 amendment (Stop while preparing and the run-control trace):** FR-031–FR-034, SC-009, the FR-005/006/007/017
    changes and tasks T034–T046 (Phases 16–21; T045 `[manual]`). Every step ran as the real `/speckit-*` command,
    headless (`claude -p` in this repo, clarify questions relayed to the person; method and costs in
    `logs/2026-11-07-lessons.md`). Six analyze passes; the sixth has no CRITICAL or HIGH, so constitution VIII's
    gate is open. Reports: `.sdd/scratch/analyze-009-<n>.md`; raw command results in
    `.sdd/scratch/009-amendment-runs/` (both git-ignored).
  - **Before the run (optional, the person's call):** pass 6's M1 (FR-007's crash-at-a-boundary clause is a MUST with
    no task: drop it to Out of Scope through `/speckit-clarify`, or add a task) and M2 (a 0.3 s sleep in five Stop-now
    test fixtures, a race), plus L1–L5 wording, through `/speckit-plan`/`/speckit-tasks`, then analyze again.
  - **Then:** the person starts the run from the dashboard's Start run on 009 Phases 16–20 (supervisor only).
    After it: T045 and T033 (the person's), then `/speckit-specify` 010.
- **Handoff (2026-11-07, afternoon; superseded by the one above).** Build order: `.sdd/roadmap.toml`.
  - **State:** checkout on `main` (009-runs is at the same commit), clean, no remote. All `auto/*` run branches were
    merged and are deleted. The old background worktree's uncommitted v0.1 eval-lane edits are kept under the tag
    `archive/eval-lane-2026-10-14`; the worktree and its branch are gone.
  - **008 closed after T015** (`f1686fc`, user's option B): each open 008 task is held `wontfix` in its BLOCKED.md,
    naming its new home. The rest is re-specified against the post-009 code as small features: **010 server core**
    (008 US1, US2, US3, US5), **007 parallelism** (now also 008 US4, hand-over and slots), **012 setup and plugin
    install** (US7), **013 new UI** (US6, US8, FR-032 pictures, FR-036 Approve, and the 009 UI follow-ups: Stop on the
    run card, activity while preparing), **014 local models** (FR-034, FR-035). 005 is superseded by 009 (T022
    closed); the 001/002 leftovers are closed. 004 and 006 are checked against 009 when their turn comes.
  - **Constitution v1.3.0:** VIII (every spec through the Spec Kit flow, clean analyze before a run) and IX (tests
    for every change). Making both the plugin's default for every project is its own feature, not written yet.
  - **Next:** the 009 Stop-while-preparing bug (Stop pressed during preflight never reaches the supervisor: no
    `control.json`, no log line) as a 009 amendment through the full flow; then `/speckit-specify` 010. 009 T033
    `[manual]` is still the person's.
  - Plugin feedback up to 2026-11-06 is harvested into `logs/2026-11-07-speckit-agents-feedback.md`.
- **Handoff (2026-11-07, night; superseded by the one above).** Read `logs/2026-11-05-lessons.md` first, then the runs' learnings in
  `.sdd/learnings.md` and plugin feedback in `.sdd/plugin-feedback.jsonl` (harvest with `bin/sdd-harvest.py`).
  - **009 Runs (`specs/009-runs/`):** a Job is now a short-lived **run**; the task is the lasting record. T001–T032
    are done: T001 by hand, T002–T012 by an in-session `/sdd-run` (`auto/20261106-1204`), T013–T032 (the amendment
    after that run's analyze pass) by the supervised run `20261106-1631` on `auto/20261106-1631`, which builds on
    `auto/20261106-1204`. Neither branch is merged into `main` yet; merging is the person's call (ask first).
  - **Open: T033 `[manual]`, waiting on the person:** the unit suite once, then quickstart §6 (task summaries,
    Details, the warning without the supervisor) and §7 (one real run from Start run, stopped after its first task)
    by hand, per the "T033" section of `specs/009-runs/manual.md`; results go in the note box.
  - **What 009 changed:** Stop after current task waits for a task boundary, then ends the run (`stopped`) and lands
    its commits on the base (fast-forward, else `merge: run <title>`; a conflict → `needs-merge`, Merge retries via
    `sdd-job.py land`). A cut-off (Stop now, unwaited limit, dead supervisor, kept pause) → `suspended` with Continue
    and Drop; Drop stashes and saves the patch in the task's history; finished work is fast-forwarded onto the base
    while suspended. Task histories in `.sdd/tasks/<feature>/<id>.jsonl` and run summaries in `.sdd/runs/<id>.json`
    are committed (`bin/sdd-history.py`); task commits carry `Task`/`Run`/`Attempts`/`Model` trailers. The dashboard
    says "run" (`#runs`, Start run, banner per FR-022) and shows History on a task card. Internal names
    (`sdd-job.py`, `.sdd/jobs/`, `/api/jobs`, `--job`) stay; 008 renames them. README "Runs" describes it.
  - **The amendment (T013–T032):** a boundary stop with a later task's edits stashes them (history step `dropped`)
    and still ends and lands; a stop before any task ends `stopped` with "stopped before any task"; a live run with
    no task started shows **preparing**; Drop's confirm names the tasks and changed files; histories are committed
    before landing (`chore(run): <id> history`) and the run summary after landing (`chore(run): <id> summary`, with
    duration, per-task attempts/durations/tokens and the supervisor's peak usage), shown as one line on a past run's
    card; on the command line `merge` = `land`, and the dashboard's Merge/Drop refuse with the CLI's words; only the
    newest run restarts after a settle; task histories record suspended/resumed; tasks carry a plain-language
    summary line (template, parser, cards with Details); a carry-over test covers 005 records
    (`tests/test_carry_over.py`).
  - **FR-017 is met as amended:** histories go in the session's report commit and the run's history and summary
    commits; a task's own commit holds only its files.
  - **005 functions removed** from `bin/sdd-job.py` (`open_job`, `display_state`, `merge`, `drop`, `_closable`,
    `finish_if_complete`, `COMPLETE_REASON`, `CLOSED_STATES`); only the run rules remain.
  - **Runs start only through the supervisor** (FR-027): Start run in the dashboard or `bin/sdd-launch.py … --job`.
    `/sdd-run` typed alone warns that it has no usage-limit protection and goes on; it never starts a supervisor.
  - **Not in 009:** a plain `/sdd-run` gets trailers and histories but no auto-landing or boundary stop; histories for
    work before 009 (FR-026, a MAY) are not built.
  - Tests (fresh run after T032): 759 Python passing (1 skipped), 27 node workflow tests passing.
- **Handoff (2026-11-05, superseded by the one above).** Read `logs/2026-11-05-lessons.md` first. Build order: `.sdd/roadmap.toml`.
  - **State:** checkout on `main` (`916a0da`), clean, nothing pushed (no remote). `.specify/feature.json` points at
    008. No run or Job is going. The dashboard runs from the checkout on port 8765 (started by the operator session;
    restart with `python3 bin/sdd-dashboard.py --project . --port 8765` if it is gone). The plugin loads in place from
    this checkout (a directory marketplace, research R11 evidence), so edits apply at the next session start; the
    plugin-cache note in the 2026-11-02 handoff is stale.
  - **005:** only T022 is open. Both Jobs on 008 ran and were merged; the person pastes the banner texts and the
    Merge message into T022's note box and presses I did it (steps in `specs/005-jobs/manual.md`). Then check the
    note says the banner never showed a run id.
  - **008:** 73 tasks, 11 done (Phases 1–2: T001–T011). New since 2026-11-02: FR-032 to FR-035, R13, data-model
    §16–§17, T061–T073. Next manual task: **T070** (the person's terminal: `qwen3.6:27b-q8_0` does a small task in
    `claude -p` through Ollama; the model is downloaded). Next runs: Phase 3 onward, started by the person from Jobs.
  - **The person's decisions (2026-11-04/05):** the technical log goes under the Job, step events under the task, in
    008's UI (FR-033; the old dashboard only got the Job log panel). Model per Job for implementer and verifier, from
    the UI (FR-034). The implementer may be a local Ollama model, the verifier stays Claude (FR-035). Best result
    over speed: `qwen3.6:27b-q8_0`. `manual.md` and `research.md` stay read-only in the UI: Claude edits them, and the
    note box records results under `Record:` headings. Pictures in notes only in 008 (FR-032).
  - **Open bugs and on hold:** Stop after current task during start-up stops before any task; no Preparing state in
    the old dashboard (008 has it); `/sdd-run` resume deletes and rebuilds `run.json`.
  - **Later (ask first):** delete the 8 merged `auto/*` branches; the stale worktree
    `.claude/worktrees/agent-a8daf0fca12087451` has uncommitted edits (CLAUDE.md, README.md, agents/), so read them
    before removing it.
  - Tests: 589 Python, 24 node, all passing on `main`.
- **Handoff (evening of 2026-11-02, superseded by the one above).** Read `logs/2026-11-02-lessons.md` first. Build order:
  `.sdd/roadmap.toml` (005, 008, 006, 004, 007; 001/002 superseded, 003 done), not memory.
  - **State:** checkout on `main`, clean, nothing pushed (no remote). The plugin is reinstalled from `cc98e18`
    (cache `~/.claude/plugins/cache/speckit-agents/speckit-agents/0.1.0`, identical to the repo); open Claude sessions
    keep the old copy until restarted. `.specify/feature.json` points at **008** (the dashboard's own feature picker may
    still show 005; pick 008 there). No run is going.
  - **Done today:** `c11294d` (a dead supervisor's `status.json` no longer marks a live run paused); 005 T020 (`cf476fe`,
    run `20261102-1438`, closed by hand, report `ae3c161`); `a56b7fd` (Resume works for dashboard-started runs:
    `--max-hold-hours`, Resume cycles don't count, a stop after the last task lets the run finish); 008 spec, plan,
    research, data model §1–§15, contracts, quickstart and 60 one-line tasks (two analyze passes by a fresh reviewer),
    `07e6e44`, `83c7929`, `cc98e18`.
  - **008 standalone server:** one server per person, installed on its own (git clone), started from a terminal
    (`sdd-server`), opening any project (Load project, setup prompt, plugin install/update), the plugin as an almost
    stateless engine (`bin/` copied to a versioned engine folder), versions flagged (a major mismatch locks writes),
    hand-over of ended in-session runs, `/sdd-stop`, cap `max_parallel` 2, React + TypeScript UI, and the roadmap as a
    project's first view (US8). 34 of 60 tasks runnable; T001/T002 are the person's proofs; T033 `[dep]` gates the UI.
  - **Next, in the person's order:** T022 (005's real Job test) runs as a Job on 008 Phases 1–2 from the Jobs view (the
    person's choice, 1A); then 008 runs. The person starts runs from the dashboard, not the operator.
  - **Approved in principle, on hold until the person says go:** the appended task log (every agent of the task across
    the run's workflows; `run_events()` emits verify/commit starts); the Preparing state in 008; the banner saying what
    Resume does plus a Close run button (proposed for 008's UI); tightening `/sdd-run`'s resume (it deletes and
    rebuilds `run.json`). Open bug: Stop after current task during start-up stops before any task.
  - **Later:** delete merged `auto/` branches (ask); the language app (stale `run.json`, `/sdd-init` on the new plugin,
    then runs; not in the roadmap yet).
  - **Dashboard:** `python3 bin/sdd-dashboard.py --project . --port 8765` from the checkout (it was started from the
    operator session and may have ended with it).
  - Tests: 477 Python, 48 browser, 24 node, 17 plugin, all passing.
- **Handoff (morning of 2026-11-02, superseded by the one above).** Read `logs/2026-10-31-lessons.md` first (lessons up to 2026-11-02).
  - **Branch:** the checkout is on `auto/20261031-2039` (the 005 run branch), clean, NOT merged into `main` yet.
    `main` holds 003 (all done except T001, wontfix), the 005/006 specs and the responsive layout. Nothing is pushed.
  - **005 Jobs (`specs/005-jobs/`):** T001–T018 are implemented and ticked (runs `20261031-2039`, cut off at the
    weekly limit after T017, and `20261102-0009`, one task: T018). Open: T019 (banner/Overview from the open Job),
    T020 (README "Jobs"), T021 (quickstart §1–§5 with the fake `claude`), then T022 `[manual]`, the user's real Job
    test, which the dashboard shows as "Not yet" until T019–T021 are done. Next: run T019–T021 (no supervisor is
    running; `.sdd/run.json` is gone, so a new run starts), then tell the user T022 is ready, then merge into `main`
    (ask first).
    Launch: from `~/private`, `python3 speckit-agents/bin/sdd-supervise.py --project ~/private/speckit-agents
    "--run-args=--no-converge" --max-wait-hours 12 --max-cycles 10` in the background, output to a file.
  - **Fixed this session (committed on the run branch):** dashboard "Waiting on you"/"Needs you" lists only manual
    steps doable now (others show "Not yet"); neutral `[manual]` reason in `bin/sdd-tasks.py`; run detail lists
    tasks newest first; a run cut off before its report shows "stopped before its report", not "running".
  - **To fix next (not done):** "Stop after the current task" pressed during a run's start-up stops before any task
    (`bin/sdd-supervise.py`, the `idle = started == finished` safe point; see lessons). Add a browser test for the
    newest-first run detail order (none yet).
  - **Then:** 006 spec cycle (`specs/006-spec-cycle/`, clarified: `spec/<feature>` branches, Merge planning, only
    CRITICAL analyze findings block, Clarify starts by itself, Opus, `max_parallel` 2) needs plan, tasks, analyze;
    then 004; then 007 (parallelism, from 005 Q4). Housekeeping: make the tasks template and `/sdd-tag` mark more
    tasks `[P]`. The language app still waits (stale `run.json`, `/sdd-init`, then runs).
  - **Dashboard:** `python3 bin/sdd-dashboard.py --project . --port 8765` from the checkout; restart it after code
    changes (the page is served by the process that was started, old code until restarted).
  - **Plugin install** is at `fbbc8a3` (before 005's code); reinstall after merging (see "Installed" below).
  - Tests at handoff: 463 Python, 41 browser (`SDD_UI_TESTS=1 python3 -m unittest discover -s tests/ui`), 24 node,
    17 plugin, all passing.
- **Done:** v0.1 is built. Tasks T001–T008 and T012–T014 are ticked in `specs/001-unattended-runs/tasks.md`.
- **Tests:** `python3 -m unittest discover -s tests` (parser, checkpoint and supervisor tests; the supervisor runs against `tests/fake/claude`), `node --test tests/workflow.test.mjs` (the workflow with stubbed agents), and `claude plugin test .` (11 guard tests).
  - Type-check: `tsc -p` against a tsconfig that includes the engine's `claude-code.d.ts`. The engine lays it in `.claude-plugin/types/` once the plugin loads.
  - `claude plugin validate .` passes, with warnings about the missing author field only.
- **End-to-end:** a headless `/sdd-run --yes` on a toy project passed SC-001. It produced 3 task commits and held 1 manual task, and left `main` untouched. Details, and the 5 problems found and fixed, are in `specs/001-unattended-runs/research.md`.
- **Redesign (2026-10-25): `specs/003-plugin-redesign/` is the current work and its source of truth.**
  - Constitution v1.1.0 adds Principle VII, "AI-free at the core".
  - 003 supersedes `specs/002-redesign/` (kept as reference only) and `001`'s open tasks T009–T011.
  - spec (28 FRs, 6 stories), plan, research, data-model, contracts, quickstart and tasks (42) are committed.
    `/speckit-analyze` ran; F1–F4 fixed, low findings accepted.
  - **Phase 2 (T003–T012) is implemented** (run `20261027-1352`, merged into `main` at `fa8236a`). Phases 3–9 are open.
  - `research.md` has the disposition of every backlog item (done / in 003 / deferred).
  - Tasks name exact files, functions and the data-model section to follow. Choosing a cheaper or local implementer model is a
    feature of the finished plugin for its users; the plugin itself is always built with the session's model (Opus 5.5).
  - Next feature after 003: the chat window (backlog item 40), on the action channel in `contracts/action-channel.md`.
- **State (end of 2026-10-27):** Phases 2 (T003–T012, T043–T044) and 3 (T013–T020, dashboard MVP) are implemented and
  merged; all approved on the first attempt on Opus 5.5. T001 is wontfix (BLOCKED.md); T002 and Phases 4–9 are open.
  Run logs: `logs/2026-10-27-phase2-pilot.md`. The event feed now comes from the engine journal (`sdd-tasks.py events`,
  data-model §3.9), because Workflow scripts can't `import()` or call `Date.now()`.
  - **Dashboard launch model (user decision):** a plugin command (`/speckit-agents:ui`) run from the project directory
    starts the API server and the UI on separate ports; one project per dashboard; the plugin stores nothing. Not in 003 yet.
  - **Phase 4 (T021–T027) is implemented** (run `20261027-2024`, 7 task commits, all approved first time on Opus 5.5, no
    blocks, 184 Python tests pass; usage peaked at 30%). T026 was built from its original wording, not the redesigned Runs view.
    **Phase 5 (T028–T031) is implemented** (run `20261027-2149`, 4 task commits, no blocks; Python tests pass).
    **Phase 6 (T032–T035) is implemented** (run `20261028-0914`, 4 task commits, no blocks; 188 Python and 20 node tests pass).
    Profiles and role models are only tested against stubs and the fake `claude`; a real cheaper-model run is quickstart §4 (T042).
    **Phase 7 (T036–T037) is implemented** (run `20261028-0925`, 2 task commits, no blocks; 188 Python and 24 node tests pass;
    `tests/guard.test.ts` not run by me). The `.sdd/scratch` guard carve-out (T036) now exists.
    **Phase 8 (T038–T039) is implemented** (run `20261028-0947`, 2 task commits, no blocks; 193 Python and 24 node tests pass).
    T038 is skill text, only exercised by a real run (quickstart §5, T042). Phase 9 is next: T040–T041 first (`--phases 9 --max-tasks 2`),
    T042 (full validation) only after Phase 10 (stop/resume, T045–T048) is built.
    **Phase 9 T040–T041 are implemented** (run `20261028-0957`, 2 task commits, no blocks; 196 Python and 24 node tests pass).
    **Phase 10 (T045–T048, stop/resume/keep paused) is implemented** (run `20261028-1005`: paused by the user after T047 while T048's
    commit was pending, then resumed and finished; 231 Python and 24 node tests pass). The Stop/Resume banner has not been seen in a browser yet.
    Remaining: T042 (full validation, quickstart §1–§8), T002, merging `auto/20261027-2024`, and the design fold-in after the user's review.
    The dashboard runs as one process: `python3 bin/sdd-dashboard.py --project . --port 8777` (API and page on one port until the UI split).
  - **Branch:** every run since Phase 4 continued on `auto/20261027-2024` (the skill stays on a current `auto/` branch), so
    Phases 4–9 and the doc commits since `a575a64` are NOT on `main` yet. Merging is the user's call; nothing is pushed.
    A leftover branch `worktree-agent-a8daf0fca12087451` is checked out in some worktree; not created by these runs, left alone.
  - **Run launch that pauses at the usage limit and does not resume at the refresh:**
    `ANTHROPIC_MODEL=claude-opus-5-5 CLAUDE_CODE_SUBAGENT_MODEL=claude-opus-5-5 python3 bin/sdd-supervise.py --project <repo> --run-args "--no-converge --phases N" --max-wait-hours 0 --max-cycles 1`
    (run in the background with output to a file). The tree must be clean first, so commit doc edits before launching and make none during the run.
    `ANTHROPIC_MODEL` puts the orchestrating `claude -p` on Opus; without it that session runs on the default (Sonnet 5.5), and
    one such run stopped at step 6.2 on a permission denial. "the run finished" from the supervisor only means the process exited:
    check `tasks.md` ticks and `git log` before calling it done.
    Watch it with a Monitor that polls `pgrep -f "sdd-supervise.py.*--phases N"` and exits when the process is gone; never `tail -f`,
    which keeps the watch armed until its timeout (see memory `feedback-monitor-must-end`).
  - **Was paused before Phase 4 (user):** design research for the finished dashboard is done and awaiting review:
    `logs/2026-10-27-dashboard-design.html` (also https://claude.ai/artifact/26f22opZPWvBp1wP7gJmiJ). Proposal: Home
    (waiting on you / last run / features), Features (docs + traceability), Runs (task → attempts → log), chat drawer;
    UI as plain static files on its own port, no build step. Open decisions are listed at the end of that page.
  - **Stop/resume added to 003 (2026-10-27, user asked):** US7, FR-029/FR-030, data-model §4.9 and §12, tasks T045–T048 (Phase 10),
    quickstart §8. The dashboard writes only a `control.json` in the supervisor's state dir; the supervisor stops at a safe point
    and holds until Resume; during the usage-credit wait a "Keep paused" button (hold/release) makes it go to held at the reset instead of restarting. Spec edits only; nothing implemented, no run started.
  - **Next:** the user reviews the design; then fold it into 003 (rewrite T026's UI part, add tasks for the UI split,
    `/speckit-agents:ui`, removing the project picker, inbox, traceability, doc links), then run Phase 4.
  - Known gap: the implementer prompt says `.sdd/scratch/<run>/<task>/` is allowed by the guard, but that carve-out is
    T036 (Phase 7); the guard refused one scratch write in run 1431.
- **State (end of 2026-10-29):** 003 Phases 2–10 are on `main` (merged 2026-10-28, `e351492`); since then, on `main`:
  FR-031/032 (browser tests, CDN fallback), FR-033 (answer and apply decisions in the dashboard, `bin/sdd-decide.py`) and
  FR-034 (start a supervised run from the Runs view, `bin/sdd-launch.py`), with spec text and tasks T068–T071.
  Phase 11 (Convergence, T049–T071) is the open work: run `20261029-1003` (started from the dashboard with a 1-task cap)
  did T051 (UI test harness) on branch `auto/20261029-1003`, **not merged**. T049/T050 (Playwright, CDN files) were done by hand.
  T062 (roll-up) is answered (b); T063 applies it. T052–T061, T063–T066, T069, T071 are runnable; T067 is yours.
  - **Browser tests:** Playwright is installed for the system `python3` (via Artifactory, see `.sdd/learnings.md`); use
    `channel="chrome"`. `tests/ui/vendor/` holds the CDN copies. The dashboard check that works today is a throwaway Node
    script using the language app's Playwright (see learnings).
- **Handoff (end of 2026-10-30, older).** `main` holds everything (not pushed); run `20261030-1515`
  (003 Phase 11, T052/T053/T061) is merged into `main` (fast-forward). Read `logs/2026-10-30-lessons.md` first.
  - **Done this session:** dashboard: Docs tabs, spec/constitution editor (FR-035), Overview ("Running now", grouped "Needs
    you"), clicked task/run opens its log/events/report under itself, runs↔tasks linked both ways with commits, readable event
    feed, headline in words (no run ids), a just-blocked task shows "Blocked (not recorded yet)"; buttons for `[manual]` tasks
    (FR-036, `bin/sdd-manual.py`) and for run-blocked BLOCKED.md entries (FR-037, `bin/sdd-unblock.py`: I did it / Answer and
    retry / Won't fix). FR-037 buttons are unit- and route-tested but **not yet clicked in a browser**.
  - **004 feedback and chat:** spec (clarified), plan, research, data model, contracts, quickstart and tasks (33, 31 runnable)
    are committed in `specs/004-feedback-chat/`. Not started. Do not name a phase "Convergence" there (see lessons).
  - **005 Jobs:** `specs/005-jobs/spec.md` is a draft (user named it "Job"; a Job continues by itself after the person
    settles an item). Next: `/speckit-clarify`, then plan and tasks. User's order: Jobs before building 004.
  - **Open in 003 Phase 11:** T053 is blocked (browser cannot run in the sandbox); T054–T057 wait on it; T063–T066, T069, T071
    runnable. Planned fix the user chose: let runs do browser tasks — `"allowLocalBinding": true` under `sandbox.network` in
    `.claude/settings.json`, install the Playwright headless shell build the run wants (`-1248`; cache has `-1228`), prove it
    with a sandboxed `tests/ui/test_smoke.py` before relying on it; do not widen the sandbox further without asking. Then
    T053's step can be redone by a run, or by hand and settled with the new "I did it" button.
  - **Rule:** while `.sdd/run.json` exists, change nothing in the project (user, 2026-10-30); draft under `/tmp`.
  - Tests at handoff: 317 Python, 24 node, 17 plugin, all passing.
- **State (2026-10-30):** held `[manual]` tasks have **I did it** and **Skip** buttons in "Waiting on you" (FR-036, `bin/sdd-manual.py`; a `wontfix` entry now outranks the tag). **State (2026-10-29, later session):** `auto/20261029-1003` is merged into `main`. The planned dashboard improvements are done:
  `/api/run/current` says `running` for a live supervisor that is running or stopping (§4.5), "Waiting on you" skips tasks held only
  behind a dependency, task logs are readable text (§4.6), the 3 s refresh redraws only on change and keeps scroll, the sidebar
  title is fixed. Also new: the section is in the URL (`#tasks`), no project picker, design documents grouped as tabs under **Docs**
  (Spec, Plan, Research, Data model, Quickstart, Constitution, Learnings), a summary **Overview**, done tasks folded per phase with
  finished phases last, links inside documents work, and **FR-035** (edit and commit a spec or the constitution, `bin/sdd-edit.py`).
  Adding and removing specs is the chat window feature, not a button. 287 Python, 24 node and 17 plugin tests pass; the editor and
  views were checked in a browser with throwaway scripts, not committed UI tests. Not done: the Tasks view rethink (status-first list
  with filter chips and one-line rows was proposed, awaiting the user's go-ahead); Phase 11 tasks T052-T061, T063-T066, T069, T071.
- **`.claude/settings.local.json.bu` and `.claude/settings.provider.example.json`:** the user's own files. Keep them untracked; never commit them.
- **Installed** at user scope as `speckit-agents@speckit-agents` from the marketplace `~/private/speckit-agents` (cache
  reinstalled 2026-11-02 from `cc98e18`; the frozen copy in `/tmp` is no longer used). Claude Code runs the plugin from a cache pinned to a
  git commit (`~/.claude/plugins/cache/speckit-agents/…`), so edits take effect only after commit + `claude plugin uninstall`
  + `claude plugin marketplace update speckit-agents` + `claude plugin install`.

## Lessons from real runs
- **Unattended mode:** use `claude --permission-mode dontAsk`.
  - The project must be trusted, which means opened interactively once. Otherwise Claude Code ignores its `permissions.allow`.
  - `Workflow`, `Glob` and `Grep` must be on the allowlist.
  - Some compound shell commands (`for` loops) are refused, and agents fall back to simpler ones.
- **Headless runs:** set `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`. Otherwise `claude -p` stops background tasks 10 minutes after the main loop's last turn, which killed run #3 on the language app during T206. With it set, the process exits only when the workflow finishes and the report is written.
- **The guard's write check** must look only at real write targets. `2>/dev/null` and `2>&1` are not writes.
- **Workflow `args`** must be the JSON object, never the skill's own arguments.
- **Waiting on a headless run:** use its process ID (`wait $pid`, or `kill -0 $pid`), never `pgrep -f "<pattern>"`. The watcher's own command line matches the pattern, so the loop never ends.
- **Sandbox and tool caches:** the sandbox blocks writes outside the project, so `uv run` fails on `~/.cache/uv` (language app, 2026-10-14). The checks passed during `/sdd-init` only because the sandbox wasn't active yet. `/sdd-init` now adds tool caches to `sandbox.filesystem.allowWrite` and runs the checks again after writing the settings.
- **Call the workflow by name, not by path:** `scriptPath` must be a file the session can read, and a session in an app repo cannot read the plugin directory (language app, 2026-10-14). The toy test missed this. The plugin's `workflows/` scripts are saved workflows named `speckit-agents:<meta.name>`, and they resolve from any project.
- **Usage limits (2026-10-16):** headless stream-json carries `rate_limit_event` (`status` allowed / allowed_warning / rejected, `utilization` from 0.90 in 1 % steps, `resetsAt`, `unifiedWindows.five_hour|seven_day`), also while the workflow runs. At the limit `agent()` returns null; the workflow now calls that `interrupted`, stops, keeps `run.json`, and the next `/sdd-run` resumes from `sdd-tasks.py checkpoint <workflow_dir>/journal.jsonl`. `bin/sdd-supervise.py` pauses at a threshold after the next finished agent, sleeps until the reset and restarts. Interactive sessions pause workflows at the limit by themselves (at most twice per run); `claude -p` doesn't.
- **No `import()` in workflow scripts** (Claude Code 2.1.311): the Workflow tool refuses the script with
  `SyntaxError: import() is not available in workflow scripts`. Workflows can't touch the file system or spawn processes.
- **Preflight needs a clean tree, untracked files included.** Keep a user's own untracked files out with `.git/info/exclude`.
- **Tests must not hard-code `/tmp`:** the sandbox allows only `$TMPDIR` there, so they fail the run's checks. Use `os.tmpdir()`.
- **Start the operator session outside the repo** (e.g. `~/private`): the repo's sandbox settings also lock a session in it,
  and the nested `claude -p` then can't start its own sandbox (EPERM on the socket).
- **Testing kit:** stubs beneath the plugin answer `{ value }` for calls on `$`, `{ result }` for `tool.call`, and `{ deny }` to refuse.

## First real target
The user's language app (`~/private/language-app`). The app repo should not hold notes about this work.
State (2026-10-14): the first attempt failed before any agent started (uv cache under the sandbox, then the script path); both are fixed in the plugin. The app's sdd commits were reverted, so its tree matches `97970b5` again. Its 5 uncommitted imitation-check files are the user's, so leave them alone.
State (2026-10-25): paused until the 003 redesign is implemented (the user's decision: redesign first, then continue the app with the redesigned plugin).
- `tasks.md` is at Phase 18: 19 open tasks, several held.
- A stale `.sdd/run.json` (run `20261024-0014`, phase 16, no report) must be cleaned up first.
- T296's BLOCKED.md entry uses an ad-hoc `tag: failed` that the new model gives a proper state.
- Before its first run on the new plugin, run `/sdd-init` there so `.gitignore` gets the new lines (003 task T027).
