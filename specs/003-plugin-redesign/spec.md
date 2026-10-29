# Feature Specification: Plugin redesign

**Feature Branch**: `003-plugin-redesign` | **Created**: 2026-10-25 | **Status**: Draft (revised after review pass)

**Supersedes**: `specs/002-redesign/` (kept on disk as historical/reference material; its spec, plan,
design and dashboard sub-spec drifted from what was actually built and from decisions made since —
see `research.md`).

## Clarifications

### Session 2026-10-28
- Q: The dashboard has API and node-function tests but no test that loads the page. Is browser
  testing a new feature, or verification of what this spec already requires? → A: Verification of
  FR-001 to FR-005, FR-029 and SC-002, as tasks in a Convergence phase. Only the test harness rules
  (FR-031) and the missing-CDN fallback (FR-032) are new requirements.
- Q: Should the dashboard let the person make decisions, not just see them? → A: Yes (FR-033), with a button that
  applies the decision and commits it (user choice, 2026-10-29), so no terminal step is needed. This
  is the one exception to FR-020 besides the control file.
- Q: Can a person start a run from the dashboard, not only stop and resume one? → A: Yes (FR-034, user
  request 2026-10-29). The dashboard starts a supervised run through `bin/sdd-launch.py`, so the Stop,
  Resume and Keep paused controls then work on it. This is a further exception to FR-020.

### Session 2026-10-25
- Q: Should the unified task terminal-state model (FR-007) cover only parked/waiting-on-a-person
  states, or also transient run states (running, retrying, paused-by-usage-limit)? → A: Parked
  states only (held, blocked, wontfix, moved, superseded); transient run states stay derived live
  from the event feed — with the added condition that a running task's live log/output must stay
  visible in the dashboard, so a stalled or erroring task doesn't read as healthy just because its
  badge says "Running."
- Q: Should the dashboard require authentication, or stay open with no auth? → A: No
  authentication; the server refuses to bind to any address other than localhost, enforced in code.
- Q: For resume safety (FR-018), should "changed since the run began" compare a task's full raw
  text, or only the fields that affect correctness? → A: Only correctness-relevant fields
  (description, tags, dependencies, named files); checkbox state and formatting are ignored.

### Review pass 2026-10-25 (whole-project re-evaluation)
- Q: How should "a cheap or local model does the implementation" work? → A: Two levels. A **run
  profile** picks the model and provider for a whole headless run (started by the supervisor as its
  own process), which can point at a cheaper model or at a gateway in front of a local model
  (best-effort: Anthropic does not support non-Claude backends). Inside a run, each **agent role**
  can still use a different model. Architect work (spec, plan, tasks) stays in the person's
  interactive session on a strong model.
- Q: FR-020 forbade any dashboard write; the next feature (chat window) must create specs and make
  amendments from the UI. → A: The dashboard never writes project files or git itself. Actions are
  **delegated** to a Claude Code session, which writes under the normal run guard. This feature
  defines the shape of that action channel but ships only read routes.
- Q: Should the dashboard be able to stop and resume a run (Stop/Resume buttons), given FR-020? → A: Yes
  (2026-10-27, user). Process control is not a project write, so it needs no Claude session: the
  dashboard hands a request to the supervisor through one small control file in the supervisor's
  state directory (outside the project), and the supervisor acts on it. The dashboard itself starts
  and kills nothing. Without a supervisor (a plain `/sdd-run` in a session) the buttons are
  disabled and show the command to run instead. `run.pause` in the action channel contract is this
  feature's `POST /api/run/stop`; the rest of the action channel stays with the chat feature.
- Q: While the supervisor waits for usage credits, can the person stop it from restarting the run when
  they come back? → A: Yes (2026-10-27, user): a "Keep paused" button. The supervisor still waits for
  the window, but at the reset it goes to `held` instead of starting `/sdd-run`, and waits for
  Resume. "Let it resume" cancels it before the reset.
- Q: Worktree isolation is broken today (agents never work inside the worktree). → A: Remove it in
  this feature; parallel groups keep running in one tree, as in v0.1. Rebuild later on the
  Workflow's native per-agent worktree isolation.
- Q: What should `status: resolved` in BLOCKED.md mean? → A: The question is answered and the next
  `/sdd-run` picks the task up again, with the answer in the implementer's prompt. Dropped tasks
  use `wontfix`; tasks a person did by hand are ticked.

## User Scenarios

### User Story 1 - A trustworthy dashboard instead of raw files (P1)
A person running an unattended `/sdd-run` wants to see a project's real state — specs, tasks,
BLOCKED.md, decisions, learnings, run history — without opening raw markdown in an editor or
decoding status from CLI output. They open the dashboard and see each document rendered, every
task/run/phase status shown as an icon+color+label matching live state, and one place listing
everything currently waiting on them.

**Acceptance**:
1. Given a project with `.sdd/` configured, when the dashboard opens, then the active feature's
   spec.md, plan.md, research.md, data-model.md, quickstart.md, tasks.md, BLOCKED.md and
   decisions.md, plus the constitution, `.sdd/learnings.md` and every run report, are reachable and
   render as formatted documents (tables, code blocks, headings, an in-page table of contents for
   long ones) — never as raw text.
2. Given tasks.md has tasks in different states (done, held, blocked, running, superseded, wontfix,
   moved), each task's status renders as an icon + color + short label, and a phase's rollup badge
   reflects its most urgent child status.
3. Given a task is held by a tag or parked in BLOCKED.md, both appear together in one "waiting on
   you" view, each with its question.
4. Given a run is in progress, the dashboard reflects new events without a manual page refresh, and
   a running task's live output is viewable.
5. Given the project has several feature folders under `specs/`, the dashboard shows the active
   feature (the one `/sdd-run` would use), not an arbitrary one.

### User Story 2 - A dashboard and event feed that actually agree (P1)
The person watching a run needs the dashboard's event timeline to be real, not empty, and needs the
next run to start cleanly after this one.

**Acceptance**:
1. Given a run in progress or finished, the dashboard reads the exact event file path the workflow
   writes, and lists every event for that run.
2. Given a finished or abandoned run with no matching run report (an orphaned `run.json`), the
   dashboard and `/sdd-status` both flag it as stale rather than treating it as resumable.
3. Given a run has finished and left its run files and scratch files, when the next `/sdd-run`
   starts, its clean-tree preflight passes (runtime files never show up as uncommitted changes).

### User Story 3 - One true model for "needs a person" or "done with" (P1)
Today a task can be held (by tag), blocked (rejected, in BLOCKED.md), resolved, wontfix, or moved to
product use — tracked in overlapping places whose user-facing text and code disagree. A person
managing the project needs one consistent model.

**Acceptance**:
1. Given a task is parked for any reason (held tag, repeated rejection, wontfix, moved, superseded),
   its state is recorded once, in one schema, and every surface (CLI, dashboard, run report) reads
   that same value.
2. Given a person answers a BLOCKED.md question and sets `status: resolved`, the next `/sdd-run`
   attempts the task again and the implementer sees the answer.
3. Given a task superseded by a later convergence task, the original is marked superseded (not left
   open) and is excluded from future runs.
4. Given a task depends on a held task, it is held for that reason through the same mechanism.
5. Given the newest Convergence phase has only held tasks left, `/sdd-run` converges again instead
   of stopping.

### User Story 4 - Strong model for the architect, cheaper or local model for the developer (P2)
A person writes specs, plans and tasks with a strong model in their own session, then has the
mechanical implementation done by a cheaper (or local) model — controlling cost without losing
quality, because the plan and tasks are written precisely enough for a less capable model.

**Acceptance**:
1. Given a run profile in the project configuration names a model (and optionally a gateway), when
   the supervisor starts a headless run with that profile, the whole run uses that model and
   endpoint.
2. Given the configuration sets a model for an agent role (implementer, verifier, evaluator,
   bookkeeper), that role's agents use it inside the run; roles without a setting use the run's
   model.
3. Given no profile and no role setting, every agent behaves as today (the session's model).
4. Given a configuration names an unknown profile, an unknown role, or an empty model value, the run
   refuses to start and names the bad entry.
5. Given a run finishes, its report lists the model each role was configured with, so a silent
   platform-side substitution is at least visible.

### User Story 5 - Scratch space and per-task approvals actually work (P2)
An implementer or evaluator agent needs to write throwaway analysis files and, for a task explicitly
allowed to add a dependency or large download, do so — both already promised by its own prompt,
neither working today.

**Acceptance**:
1. Given an implementer or evaluator writes to its task's scratch directory, the guard allows it and
   the file never appears as an uncommitted change.
2. Given a task tagged with a per-task approval for a new dependency or download, the task is not
   held, and its implementer is told it is allowed (today the task is held before it ever runs).

### User Story 6 - The plugin keeps learning from every run (P2)
The person wants every run to leave behind what it learned: gotchas about this project, so the next
run avoids them, and issues with the plugin itself, so the plugin keeps improving — without notes
about the plugin landing in the app repo's history.

**Acceptance**:
1. Given a run finishes, project-specific lessons it found (environment quirks, check commands,
   recurring refusals) are appended to the project's `.sdd/learnings.md` and committed with the run
   report.
2. Given a run hits a plugin problem (a refused command the plugin itself issued, a crash, a
   contradiction between a prompt and the guard), it is written to a local, git-ignored plugin
   feedback file in the project, never committed there.
3. Given the person runs the harvest command from the plugin repo against a project, new plugin
   feedback entries are appended to the plugin's own `logs/` and are not harvested twice.

### User Story 7 - Stop and resume a run from the dashboard (P2)
The person watching a supervised run wants to stop it, and later continue it, without a terminal.

**Acceptance**:
1. Given a supervised run is in progress, when the person clicks "Stop after current task", the page
   shows "Stopping after T0xx", the run stops at the next safe point (the running agent finishes
   first, so nothing is lost), and the page then shows "Stopped by you" with a Resume button.
2. Given a supervised run is in progress, when the person clicks "Stop now" and confirms, the run
   stops at once; at most the step in flight is redone on resume.
3. Given the run is stopped by the person, when they click Resume, the supervisor starts `/sdd-run`
   again, which reads the journal and continues.
4. Given the run is not supervised (started by `/sdd-run` in a session), the buttons are disabled
   and say why, and show the command that starts a supervised one.
5a. Given the supervisor is waiting for a usage window to reset, Stop and Resume are disabled and
   "Keep paused" is offered. After the person clicks it the page shows "Will stay paused after the
   reset"; when the window resets the supervisor does not start `/sdd-run` but goes to "Stopped by
   you", and Resume continues. Clicking "Let it resume" before the reset cancels the hold.
5. Given a request to a control route from another website (a browser tab on a different origin) or
   without a JSON body, it is rejected and the run is unaffected.

### User Story 8 - Start a run from the dashboard (P2)

A person looks at what is runnable and held, picks the phases and clicks Start. The run starts under the
supervisor, and the Stop, Resume and Keep paused buttons control it. No terminal is needed to start,
pause, answer a decision or resume.

**Acceptance**:
1. Given a clean tree and runnable tasks, the Runs view shows a Start run panel with each runnable phase
   and its task count, a "skip converge" option (on by default), an optional task cap and the number of
   held tasks.
2. Clicking Start asks for confirmation (phases and task count), then starts one supervised run and shows
   the run banner within 5 seconds.
3. Given a supervisor already running, a dirty working tree (no interrupted run to resume), a project
   that is not a git repository, or nothing runnable, the panel says why and Start does nothing.
4. Given an interrupted run (`.sdd/run.json`, no live supervisor), the panel says which run it resumes,
   and starting it does not require a clean tree.
5. The run the dashboard starts is the same as one started from a terminal with `bin/sdd-supervise.py`:
   it uses the session's model settings, commits only on its `auto/` branch and never pushes.

### Edge Cases
- A Stop is requested while the supervisor is in the usage-limit wait, or a Resume while a run is
  going: the route answers `409` with the reason and writes nothing.
- The supervisor died with `status.json` left behind: its recorded process id no longer exists, so
  both buttons are disabled with "no supervisor is running".
- A markdown file with malformed syntax (unclosed code fence, broken table) still renders the rest
  of the document.
- A role's model is blocked by the account's model allowlist: the platform may substitute a fallback
  model; the plugin cannot prevent this, but the run report shows the configured model per role
  (US4 acceptance 5).
- A local-model gateway lacks tool calling: the run's first agent fails; the run stops as
  interrupted and the report names the profile — it never marks work done.
- A task re-run gets a fresh scratch directory (scoped per run id and task id).
- A `.sdd/run.json` whose run id has no run report is treated as stale (US2 acceptance 2).
- A resolved task is rejected again: a new open BLOCKED.md entry is appended and the task is held.
- A configuration still sets `isolation = "worktree"`: the run refuses to start and says the option
  was removed.

## Requirements

### Functional Requirements
- **FR-001**: The dashboard MUST make reachable, and render as formatted output, every document in
  User Story 1 acceptance 1 — tables, fenced code with syntax highlighting, headings, an in-page
  table of contents for long documents — never raw text. It MUST only serve files from that fixed
  set of locations (no arbitrary path reads).
- **FR-002**: Task checkboxes shown anywhere in the UI MUST reflect the task's parsed status from
  the same code `/sdd-run` uses, not the raw `- [ ]`/`- [x]` text.
- **FR-003**: Every task, phase, run and agent-step status shown in the UI MUST be an icon, a color
  and a short text label together, from one shared status vocabulary.
- **FR-004**: A phase or run status MUST roll up to the most urgent status among its children.
- **FR-005**: Held tasks and blocked tasks MUST appear together in one view of everything waiting on
  a person, each with its question.
- **FR-006**: The dashboard MUST read a run's events from the run's own record of what its agents
  did, not from a separate copy that can drift from it, and MUST show new events without a manual
  page reload.
- **FR-007**: A task's parked state (held, blocked, wontfix, moved, superseded) MUST be represented
  in one schema with one source of truth, read the same way by the CLI, the dashboard and run
  reports. The user-facing text written into BLOCKED.md MUST describe the same behavior the code
  implements.
- **FR-008**: A task superseded by later work MUST be held as superseded and excluded from future
  runs, without changing Spec Kit's own commands: (a) every open task in a non-Convergence phase
  that comes before the newest Convergence phase is superseded by that phase (converge already
  carried its remaining work); (b) a task whose own line says `superseded-by Tnnn` is superseded by
  Tnnn. A superseded task stays superseded after its successor is done, and never holds the tasks
  that depend on it.
- **FR-009**: A task whose dependency is held MUST itself be held, through the FR-007 mechanism.
- **FR-010**: `/sdd-run` MUST converge again when the newest Convergence phase has only held tasks
  left.
- **FR-011**: Each of the four run agent roles MUST support an independently configurable model;
  roles without a setting use the run's model.
- **FR-012**: The run MUST refuse to start when the configuration names an unknown profile, an
  unknown role, or an empty or non-string model value, naming the bad entry. Model identifiers
  themselves are passed through unvalidated (they differ per provider), and the run report MUST
  list the configured model per role.
- **FR-013**: An implementer or evaluator agent MUST be able to write to its own task's scratch
  directory without the run guard refusing it, while every other protected path stays refused.
- **FR-014**: A task carrying a per-task approval for a new dependency or a large download MUST NOT
  be held for that tag, and its implementer MUST be told it is allowed.
- **FR-015**: Dashboard project discovery MUST NOT scan directories; it MUST use the project it was
  started with (current directory or an explicit path).
- **FR-016**: Worktree isolation MUST be removed (the option, its code and its docs). A
  configuration that still requests it MUST make the run refuse to start with a message saying so.
- **FR-017**: `/sdd-status` and the dashboard MUST flag a `.sdd/run.json` with no corresponding run
  report as stale rather than resumable.
- **FR-018**: `/sdd-run` MUST refuse to resume a task whose `tasks.md` entry changed since the run
  began in a correctness-relevant field (description, tags, dependencies, named files); checkbox or
  formatting changes alone MUST NOT block resume.
- **FR-019**: The dashboard, the CLI, task parsing/planning and status reporting MUST work with no
  AI model available (constitution Principle VII).
- **FR-020**: The dashboard server MUST NOT write, delete or modify project files or git history
  itself. Any future action (creating a spec, amending a task) MUST be delegated to a Claude Code
  session that performs it under the run guard. This feature documents that action channel's shape
  and ships no other action routes. The only exceptions are FR-029 (one control request file in the
  supervisor's state directory, outside the project), FR-033 (saving and applying a decision),
  FR-034 (starting a supervised run) and FR-035 (editing and committing a spec or the constitution).
- **FR-021**: A running task's dashboard view MUST show its live agent output (a tail of its
  transcript), or a clear "not available yet" state — never an error.
- **FR-022**: The dashboard server MUST bind only to localhost, and MUST reject requests whose Host
  header is not a localhost name (protection against DNS rebinding from a browser tab). It has no
  authentication.
- **FR-023**: A BLOCKED.md entry with `status: resolved` MUST make its task runnable again on the
  next `/sdd-run`, with the entry's answer included in the implementer's prompt. `open`, `wontfix`
  and `moved` hold the task.
- **FR-024**: At the end of every run, project-specific lessons MUST be appended to the project's
  `.sdd/learnings.md` (committed with the run report), and plugin-level problems MUST be written to
  a git-ignored plugin feedback file in the project. A harvest command in the plugin repo MUST copy
  new feedback entries into the plugin's `logs/`, idempotently.
- **FR-025**: The project configuration MUST support named run profiles (model, and optionally
  non-secret environment settings such as a gateway URL). The supervisor MUST accept a profile name
  and start the headless run with it. Secrets MUST NOT be stored in the configuration.
- **FR-026**: Plugin runtime files in the project — the scratch directories and the per-run event
  directories under `.sdd/runs/` — MUST be git-ignored, set up by `/sdd-init` and enforced by
  `/sdd-run` before it starts. Run reports (`.sdd/runs/<id>.md`) stay tracked.
- **FR-027**: The dashboard MUST show the active feature: `run.feature` from the configuration, else
  `.specify/feature.json`, else the only feature folder; with several folders and neither set, it
  MUST ask the person to choose rather than pick one.
- **FR-028**: The supervisor's hard stop MUST be per usage window: each window stops at its own
  threshold plus one point unless the person sets a hard stop explicitly (backlog item 35).
- **FR-029**: The dashboard MUST offer Stop (after the current task, or now) and Resume for a
  supervised run. It MUST do so only by writing a control request (`control.json`) into the
  supervisor's state directory; it MUST NOT start, signal or kill any process itself. The supervisor
  MUST act on the request: stop at the next safe point (or at once), then hold until Resume or
  `--max-wait-hours`. Controls MUST be disabled, with the reason shown, when no live supervisor
  exists. While the supervisor waits for a usage window to reset, Stop and Resume MUST be disabled
  and the dashboard MUST offer "Keep paused": the supervisor then ends that wait in the held state
  instead of restarting the run. Once set, "Let it resume" MUST cancel it.
- **FR-030**: Every state-changing dashboard route (the control routes, and `/api/project/select`)
  MUST reject a request whose `Origin` header is missing or not an allowed origin (`403`), and one
  whose `Content-Type` is not `application/json` (`415`), so a web page on another origin cannot
  drive them (FR-022 covers DNS rebinding; this covers cross-site requests).

- **FR-031**: The dashboard's views MUST be verified by tests that load the page in a real browser
  (Chromium through Playwright for Python) against a temporary toy project, and fail on any console
  error, uncaught exception or failed same-origin request. These tests MUST skip, not fail, when
  Playwright or its Chromium is missing (`SDD_UI_TESTS=1` turns the skip into a failure), MUST serve
  the CDN scripts from pinned local copies so a run is offline, MUST save a screenshot and the page
  HTML under `.sdd/scratch/ui/` when one fails, and MUST NOT be part of the default
  `checks.commands`.
- **FR-032**: When `marked`, `highlight.js` or `DOMPurify` cannot be loaded, the Documents view MUST
  show the document's raw text with a one-line notice instead of an empty pane, and every other view
  MUST keep working.
- **FR-033**: The dashboard MUST let a person settle an open `[decision]` task without a terminal. A
  Decisions view lists every open decision task, formatted as its question, its options and what to
  record, with a box for the answer. `POST /api/decisions/answer` (with the FR-030 checks) saves the
  answer as `answers/<task id>.json` in the supervisor's state directory and then applies it through
  `bin/sdd-decide.py`, the one place the dashboard changes the project: it appends the decision to the
  feature's `decisions.md`, adds it under the heading the task names in `research.md`, ticks the task,
  and commits exactly those files. It MUST refuse, and change nothing, when a run is active
  (`.sdd/run.json`), when the working tree has any other change, when the project is not a git
  repository, or when the task is not an open decision; the saved answer then stays and the view shows
  the reason and an "Apply now" button. If the commit fails the files are restored. `/sdd-unblock`
  applies any answer file still waiting.
- **FR-034**: The dashboard MUST let a person start a supervised run. `GET /api/run/plan` returns what a
  run would do (runnable phases with their task counts, the number of held tasks, whether it can start
  and why not, and the run it would resume); `POST /api/run/start` (with the FR-030 checks) with
  `{phases?, no_converge?, max_tasks?}` starts `bin/sdd-supervise.py` detached through
  `bin/sdd-launch.py` and answers `202 {pid}`, or `409 {error}` when it cannot start. The launcher MUST
  refuse, and start nothing, when a supervisor is already running, when the working tree has changes
  and no interrupted run is being resumed, when the project is not a git repository, or when no task is
  runnable; it MUST reject a phase with no runnable task and a task cap outside 1 to 500. It sets the
  orchestrating session's model as the documented run launch does, and writes its output to
  `launch.log` in the supervisor's state directory. The dashboard source itself contains no process
  spawning; the helper does it.
- **FR-035**: The dashboard MUST let a person edit a feature's `spec.md` and the project constitution, and
  commit the edit, without a terminal. The Docs view groups the design documents as tabs (Spec, Plan,
  Research, Data model, Quickstart, Constitution, Learnings); the Spec tab has a picker for the feature, so
  each spec is edited on its own. `GET /api/doc/state?name=spec|constitution` returns the file's hash,
  whether it has uncommitted edits and why editing is off; `POST /api/doc/save` `{name, text, base}` (with
  the FR-030 checks) replaces the file through `bin/sdd-edit.py`; `POST /api/doc/commit` `{name, message}`
  commits just that file with the message as typed (no model rewrites it). No other document is editable.
  The helper MUST refuse, and change nothing, while a run is active (`.sdd/run.json`), when `base` is not
  the hash of the file's current text (it changed since the person opened it), when the text or the commit
  message is empty, when the file has no uncommitted change (commit), or when the project is not a git
  repository (commit); the view shows the reason and keeps the person's text. Adding and removing specs
  is NOT part of this requirement: it is done by an AI session in the chat window feature (the next
  feature, `contracts/action-channel.md`), which creates and deletes feature folders under the run guard.
  Links inside a document MUST work: a `#heading` link scrolls, a link to another document's file opens
  that document, and the section in the URL (`#spec`) survives a refresh.

### Key Entities
- **Run control request**: one command (`stop` with a mode, `resume`, `hold` or `release`), an id and a time, written by
  the dashboard and consumed and deleted by the supervisor.
- **Task status**: a **parked state** (held+tag, blocked, wontfix, moved, superseded+by) persisted
  and read the same way everywhere (FR-007), and a **transient run state** (queued, running+step,
  retrying+count, paused, done) derived live from the event feed and never persisted.
- **BLOCKED.md entry**: task id, status (open, resolved, wontfix, moved), tag, reason, question,
  answer. Several entries for one task id are allowed; the last one wins.
- **Run event**: one step an agent of the run took (started, implemented, verified, committed, blocked),
  derived from the engine's journal of the run.
- **Run profile**: name, model, optional non-secret environment settings.
- **Role model setting**: role (implementer, verifier, evaluator, bookkeeper) → model.
- **Learning entry**: date, run id, kind (project or plugin), text.

## Success Criteria

### Measurable Outcomes
- **SC-001**: A person can read a project's full current state (specs, tasks, blocked items,
  decisions, learnings, latest run) entirely inside the dashboard, without a terminal or an editor.
- **SC-002**: Every status shown in the dashboard has an icon, a color and a label; no two statuses
  share both icon and color.
- **SC-003**: The existing toy-project end-to-end test still passes, and a new one covering held →
  blocked → resolved (runnable again, with the answer in its implementer prompt) → superseded
  passes.
- **SC-004**: A real run's dashboard view shows its actual event timeline (not empty), and the run
  after it starts without a dirty-tree refusal.
- **SC-005**: A run started with a profile, and an implementer role set to a different model,
  completes a task; its report lists the configured model per role.
- **SC-006**: After two runs and one harvest, the project's `.sdd/learnings.md` has entries from
  both runs, and the plugin's `logs/` has each plugin feedback entry exactly once.
- **SC-007**: Removing the rendering code of any one dashboard view (for example the status badge on
  the task card) makes at least one browser test fail and name the view.
- **SC-008**: From a clean tree with runnable tasks, a person starts a run, answers a decision, stops it
  and resumes it using only the dashboard, and the run's commits appear on its `auto/` branch.

## Out of Scope
- Chat window for free-form questions, spec authoring and amendments (backlog item 40): the next
  feature, built on the action channel this feature documents (FR-020).
- In-run feedback/redirect of a running agent (item 36): part of the chat feature.
- Starting a run from the dashboard (the dashboard only stops and resumes a run a supervisor already
  owns; launching stays with `/sdd-run` and `bin/sdd-supervise.py`).
- Cross-project registry and "all running agents" view (items 37a/37b).
- Worktree isolation (rebuilt later on the Workflow's native per-agent isolation; items 1, 6).
- Official support for non-Claude backends: run profiles make a gateway possible, but Anthropic
  does not support it and tool calling may fail.
- Every other backlog item has an explicit disposition (done, in this feature, or deferred) in
  `research.md`'s backlog table.

## Assumptions
- `specs/002-redesign/` is retained on disk as reference material only.
- The dashboard's backend stays dependency-free (standard library only); rendering libraries load
  in the browser.
- "Commercial-product quality" means consistent, accessible status representation and fully
  rendered documents — not a visual rebrand.
- Writing learnings is judgement work done by the run's main loop model at the end of a run; this
  is allowed by Principle VII because nothing in the engine's core depends on it.
- The plugin repo is reachable from the machine where the harvest runs; harvesting is a manual
  command, not automatic.
