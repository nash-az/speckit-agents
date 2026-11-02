# Feature Specification: Standalone server: one dashboard for all projects, the plugin as an almost stateless engine

**Feature Branch**: `008-standalone-server` | **Created**: 2026-11-02 | **Status**: Draft (clarified 2026-11-02; not yet reviewed)

**Input**: The dashboard ships inside the plugin, is started for one project at a time, and sees only part of what
the plugin does. On 2026-11-02 a run started with `/sdd-run` in a Claude session showed **Paused** while it was
working: a finished supervisor's leftover `status.json` decided the state, and the banner told the person to run a
terminal command. The Stop and Resume buttons only work for runs the supervisor started. A server started from a
sandboxed session inside the repo passes the sandbox on to the `claude -p` it launches, and the nested sandbox then
fails (EPERM, `CLAUDE.md` lessons). The person wants plugin commands and the UI to stay in sync, and one place that
orchestrates: "the UI and server independent from the plugin. The plugin will run almost stateless but the UI and
server will handle the orchestration of those operations."

Builds on `specs/003-plugin-redesign/` (dashboard, helpers, run guard, supervisor, state directory) and
`specs/005-jobs/` (Jobs). The state hot-fix of 2026-11-02 (`c11294d`: a dead supervisor's `status.json` is ignored)
is the first piece of FR-006 and is already on `main`. `specs/004-feedback-chat/` and `specs/006-spec-cycle/` add
views and routes to the dashboard; once this feature lands they are built on the new server and UI, not the old page.

## Clarifications

### Session 2026-11-02 (decisions made while designing)
- Q: Should `/sdd-run` in a session always hand off to a supervisor? → A: No. `/sdd-run` stays granular: it runs in
  the session, with no supervisor (user). The server shows such a run like any other and can attach to it.
- Q: Where do the UI and server live? → A: They are independent of the plugin (user). The plugin keeps what must
  live inside Claude Code: agents, the workflow, the run guard and the skills. The server does the orchestration.
- Q: One server per project, or one for all projects? → A: One server for the person. It opens any Spec Kit project
  set up for the plugin, and the **Load project** button comes back (user). This reverses the 2026-10-27 decision
  to launch one dashboard per project with no project picker.
- Q: Does the plugin keep a UI command (`/speckit-agents:ui`)? → A: No (user). The server is started on its own,
  outside any Claude session.
- Q: How does the server use the plugin's code without the two drifting apart? → A: The plugin installation copies
  its Python engine to a fixed, versioned place, and the server uses that copy (user). There is no separately
  maintained shared library: the code lives once, in the plugin repo.
- Q: What happens when versions do not match? → A: The mismatch is flagged (user): a visible warning that names both
  versions. On a major-version mismatch the actions that write are disabled as well and reads stay on (proposed
  while designing; the person gave the go-ahead with it in the proposal).
- Q: What is attaching a supervisor to an in-session run for? → A: Most of all, hand-over after a cut-off (user): when
  the session's run is cut off (usage limit, the terminal closed), the server finishes it without the person
  starting it again. A cooperative "stop after the current task" for in-session runs comes later.
- Q: Can a run be stopped from a plugin command? → A: Yes: a `/sdd-stop` command (user), which does exactly what the
  matching dashboard button does.
- Q: Which languages? → A: The UI is rebuilt in TypeScript with Node tooling, to be nicer and more responsive (user).
  The server and engine stay Python: the plugin's skills already run the Python engine inside sessions, and a second
  implementation in another language would drift. Porting the server later stays possible.
- Q: How is the server started? → A: One command only; no start at login (user). The person starts it after a
  reboot.

### Session 2026-11-02 (clarify)
- Q: If the server stops or restarts, what happens to the runs and Jobs it started? → A: They keep going on their
  own; when the server starts again it finds them and shows and controls them as before (user).
- Q: Can runs go in several projects at the same time? → A: Yes, up to a cap across all projects, default 2,
  changeable in the server's settings (user). A start the person asks for beyond the cap is refused with the
  reason; work that starts by itself (a Job's next run, a hand-over) waits for a free place.
- Q: How does the person pick a folder for Load project? → A: A folder browser served by the server, starting in
  the home folder, that marks folders set up for the plugin, plus a path box (user).
- Q: Is the server delivered with the plugin, or installed on its own? → A: Installed on its own (user). It gets its
  own install and update, and it can install the plugin, or update it, from a button.
- Q: What happens when a folder that is not set up for the plugin is opened? → A: The server offers to set it up,
  and does it when the person agrees (user). The plugin installation is checked as well.
- Q: Where does 008 go in the build order? → A: Next, after 005's T020 and T022; then 006, then 004, then 007
  (user). 006 and 004 have no code yet, so their views are written once, on the new server and UI.

## Terms

- **Server**: the standalone program that serves the UI and its API, keeps the project list, and starts, watches and
  controls runs and Jobs for every project it has open. It never runs inside a Claude session.
- **Engine**: the plugin's deterministic Python code that reads and writes the project files the plugin defines
  (`tasks.md` tags, `BLOCKED.md`, `.sdd/run.json`, the journal, decisions, Jobs). Today `bin/sdd-tasks.py` and the
  `bin/` helpers.
- **Engine copy**: the copy of the engine that the plugin installation places in a fixed folder outside any project,
  with a file naming its version. The server uses only this copy.
- **Project list**: the projects the server has opened, kept in the server's own state folder.
- **In-session run**: a run started with `/sdd-run` in a Claude session. It has no supervisor.
- **Supervised run**: a run the server starts (through the supervisor) with a headless `claude -p`.
- **Hand-over**: the server taking over an in-session run that was cut off, and finishing it as a supervised run.
- **Contract version**: the version of the formats the plugin and the server exchange (`run.json`, journal labels,
  the engine's JSON output, the control request, the API). Written as `MAJOR.MINOR`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One dashboard for all my projects (Priority: P1)

The person starts the server once. It opens with the projects it has seen before. They click **Load project**, pick
a folder, and the project appears in the list; they switch between projects without restarting anything. A run
going in one project shows in the list while they look at another.

**Why this priority**: it is the base everything else sits on: one place for every project, outside any session.

**Independent Test**: start the server with an empty project list, load two toy projects, switch between them, and
restart the server: both are still listed and each shows its own tasks.

**Acceptance Scenarios**:

1. **Given** a running server with no projects, **When** the person clicks Load project, browses from the home folder
   (folders set up for the plugin are marked) and picks one, or pastes its path, **Then** the project is added to the list, opens, and stays listed after the server restarts.
2. **Given** a git repository that is not set up for the plugin, **When** the person picks it, **Then** the server offers
   **Set up this project** (US7) instead of refusing, and the project is added once setup is done. A folder that is
   not a git repository is refused with the reason.
3. **Given** two loaded projects with a run going in the second, **When** the person views the first, **Then** the
   project list shows the second as working.
4. **Given** a listed project whose folder was moved or deleted, **When** the server starts, **Then** it stays listed,
   marked missing, with **Remove from list**; nothing else breaks.
5. **Given** the server is already running, **When** a second one is started, **Then** the second says the first is
   running, gives its address, and exits.

---

### User Story 2 - Every run looks the same however it was started (Priority: P1)

Whether a run was started by `/sdd-run` in a Claude session, by Start run in the UI, or as part of a Job, the
banner, the Overview, the task chips and `/sdd-status` agree with what the run is doing.

**Why this priority**: the 2026-11-02 bug: a working run showed Paused. Wrong state makes every other screen useless.

**Independent Test**: start an in-session run on a toy project that has a leftover `status.json` from a finished
supervised run; the UI shows it working, and `/sdd-status` says the same.

**Acceptance Scenarios**:

1. **Given** an in-session run whose journal is being written, **When** the UI shows the project, **Then** it reads
   Working, and the running task reads Running, whatever an earlier supervisor left behind.
2. **Given** an in-session run, **When** the banner is shown, **Then** it says "Running in a Claude session", offers
   **Supervise this run**, and shows no control that cannot act. No screen ever tells the person to run a terminal
   command.
3. **Given** any run, **When** `/sdd-status` is called in a session, **Then** it reports the same state as the UI.

---

### User Story 3 - The engine comes with the plugin, and mismatches are flagged (Priority: P1)

When the person installs or updates the plugin, the server picks up the matching engine without its own update
step. If the server, the engine copy and the project's runs disagree on versions, the person is told plainly.

**Why this priority**: one engine, used by both sides, is what keeps plugin commands and the UI in sync.

**Independent Test**: update the plugin's version on a toy setup; the engine copy follows after the next session
start, and the server shows the new version. Fake an older major version: a warning shows and the write buttons
are disabled.

**Acceptance Scenarios**:

1. **Given** the plugin is installed or updated, **When** the next Claude session starts, **Then** the engine copy
   matches the installed plugin's version.
2. **Given** an engine copy whose contract version differs from the server's only in the minor number, **When** the
   UI loads, **Then** a warning names both versions and everything still works.
3. **Given** a major-version difference between the server and the engine copy, or between them and a project's run,
   **When** the UI loads, **Then** the warning shows, reads keep working, and every action that writes is disabled
   with the reason.
4. **Given** no engine copy at all, **When** the server starts, **Then** it says the plugin is not installed or has not
   run yet, and how to fix it; it does not fall back to any other copy of the code.

---

### User Story 4 - Hand-over after a cut-off (Priority: P2)

The person starts `/sdd-run` in a session and clicks **Supervise this run** in the UI. Later the session's run is cut
off: a usage limit, or the terminal closed. The server notices, waits for the usage window if needed, and finishes
the run from where it stopped. If the run instead closes normally, the server does nothing.

**Why this priority**: the person's priority for attaching; it saves the work that cut-off runs lose today.

**Independent Test**: on a toy project with the fake `claude`, start an in-session run, attach, end the session's
process; the server resumes the run once from its checkpoint and finishes it.

**Acceptance Scenarios**:

1. **Given** an attached in-session run whose session is still alive, **When** the server watches it, **Then** it never
   starts a second run on that project.
2. **Given** the session's process is gone and `.sdd/run.json` is still there, **When** the server sees it, **Then** it
   resumes the run from its checkpoint (after the usage reset if a limit caused the cut-off), and the banner says
   "Taken over after a cut-off" with the normal supervised controls.
3. **Given** the run closed normally (`.sdd/run.json` removed), **When** the server sees it, **Then** it stops watching
   and starts nothing.
4. **Given** a run whose session cannot be identified (started before this feature), **When** it has gone quiet, **Then**
   the UI offers **Take over** only after the person confirms that the session is closed.

---

### User Story 5 - Stop a run from a command (Priority: P2)

In a Claude session, the person types `/sdd-stop` (after the current task) or `/sdd-stop --now`. The run stops as if
they had clicked the matching button.

**Why this priority**: commands and buttons must be two doors to the same action.

**Independent Test**: on a supervised toy run, `/sdd-stop` produces the same stop request, and the same refusals,
as the UI's Stop after current task.

**Acceptance Scenarios**:

1. **Given** a supervised run, **When** the person runs `/sdd-stop` or `/sdd-stop --now`, **Then** the run stops exactly
   as with the matching button, and the UI shows it within 5 seconds.
2. **Given** an in-session run, **When** the person runs `/sdd-stop --now` in the session that started it, **Then** the
   workflow stops and `.sdd/run.json` stays, so it can be resumed; without `--now` the command says that "after the
   current task" is not available for in-session runs yet.
3. **Given** an in-session run started in another session, **When** the person runs `/sdd-stop`, **Then** it says which
   session owns the run and does nothing.
4. **Given** the server is not running, **When** the person runs `/sdd-stop` on a supervised run, **Then** it still
   works: the request goes through the engine, not through the server.

---

### User Story 6 - A nicer, faster UI (Priority: P2)

The person uses the rebuilt UI for everything the current dashboard does. Changes appear as they happen, without
a visible reload, and the page stays usable at phone width.

**Why this priority**: the person asked for it, and it is the face of the new server; parity comes first, polish second.

**Independent Test**: the current browser tests, ported, pass against the new UI; a run's events appear in the
UI within 2 seconds of being written.

**Acceptance Scenarios**:

1. **Given** the current dashboard's views (Overview, Tasks, Runs, Jobs, Blocked and Waiting on you, Decisions, Docs
   with the editor), **When** the new UI is used, **Then** each one does at least what it does today.
2. **Given** a run is going, **When** a task changes state or an event is written, **Then** the UI shows it within 2
   seconds, without reloading the page or losing the person's scroll position or open panels.
3. **Given** a phone-width window, **When** any view is opened, **Then** it has no horizontal scrolling and every action
   can be reached.

---

### User Story 7 - The server sets up projects and keeps the plugin installed (Priority: P2)

The person opens a folder that has never used the plugin. The server shows what setup will do and asks; on yes it
sets the project up, the way `/sdd-init` does. The person picks the project's check commands from what the server
found, and sees each one pass. If the plugin itself is missing from Claude Code, or too old for the server, the
server offers to install or update it.

**Why this priority**: it removes the last terminal steps from getting a project going (the "everything from the
UI" rule).

**Independent Test**: on a toy git repository with Spec Kit but no `.sdd/`, open it in the server, accept setup
with one detected check command, and get a project that `/sdd-run` accepts in preflight.

**Acceptance Scenarios**:

1. **Given** a git repository with Spec Kit and no `.sdd/config.toml`, **When** it is opened, **Then** a prompt lists
   what setup will write (the run config, the tasks template section, the safety settings, the ignore lines) and the
   check commands it found, each with a checkbox and an edit box.
2. **Given** the prompt, **When** the person clicks Run checks, **Then** each chosen command runs and shows pass or fail
   with its output. A failing command cannot be kept unless the person ticks "keep anyway".
3. **Given** the person confirms, **When** setup runs, **Then** the files are written as `/sdd-init` would write them,
   the changes are shown as a diff first, and they are committed in one commit if the person left "Commit the setup"
   ticked. The project then opens.
4. **Given** a git repository without Spec Kit, **When** it is opened, **Then** the prompt offers to run Spec Kit's own
   setup first when the `specify` command is available, and otherwise says how to install it.
5. **Given** the plugin is not installed in Claude Code, **When** the server starts or a project is opened, **Then**
   **Install plugin** is offered; after a click and a confirmation, the plugin is installed and the engine copy is
   refreshed.
6. **Given** the installed plugin is older than the server needs, **When** the person clicks **Update plugin** and
   confirms, **Then** it is updated and the engine copy refreshed. While any run is going, both buttons are
   disabled with the reason. Afterwards the page says that open Claude sessions keep the old version until they are
   restarted.
7. **Given** the server is the older side of a mismatch, **When** the warning shows, **Then** it says to update the
   server, and does not offer to change the plugin.

### Edge Cases

- The server is not running: plugin commands keep working from the files (`/sdd-status` reads them, `/sdd-stop`
  writes its request through the engine); nothing in the plugin needs the server.
- The plugin is updated while the server runs: the server notices the new engine copy and uses it for new work;
  a run already going keeps the version it started with, and the UI says so.
- The same project is loaded twice, or through a symlink: one entry, by resolved path.
- A project set up by an older plugin (no contract version in its `run.json` or config): treated as major version 0
  and flagged.
- Two runs would start on one project (an in-session run while the server wants to resume): the server never starts
  a run while another run on that project is alive.
- A session's process id is reused by another program after the session ended: the session is identified by more
  than the bare process id (for example its start time), so a reused id is not taken for the session.
- The port the server wants is taken by another program: the server says so and uses another free port, or exits
  if one was set explicitly.
- The cap is reached by in-session runs alone: nothing the server starts can go until one ends; the UI says so
  and lists them.
- The server is quit or crashes while runs are going: the runs carry on; on its next start the server shows them
  as working, with their controls, and an attachment it was holding is watched again.
- The engine copy is half-written when the server reads it (an update in progress): the copy is replaced in one
  step, so the server sees either the old or the new version, never a mix.

## Requirements *(mandatory)*

### Functional Requirements

**Server and project list**
- **FR-001**: The server MUST be a program of its own, started outside any Claude session, and never by the plugin.
  The plugin has no UI command. It MUST listen on localhost only.
- **FR-002**: At most one server MUST run per person. A second start MUST report the running one's address and exit.
- **FR-003**: The server MUST keep a project list in its own state folder, outside every project, and MUST let the
  person add a project (Load project: pick a folder), switch between projects and remove one from the list.
  Removing a project from the list MUST NOT touch the project's files.
- **FR-003a**: Load project MUST open a folder browser served by the server. It starts in the person's home folder,
  lists only folders (names, never file contents), marks those set up for the plugin and those already listed, and
  has a path box that accepts a typed or pasted path. It MUST stay within the home folder, and its requests MUST
  pass the same checks as the other routes that change state (003 FR-030), so another website cannot list folders.
- **FR-004**: Load project MUST accept a git repository. One set up for the plugin (`.sdd/config.toml`) is added
  directly; one that is not leads to the setup prompt (FR-025); a folder that is not a git repository is refused
  with the reason. A project is identified by its resolved path.
- **FR-005**: The server MUST show, for every listed project, whether a run or Job is going, so the person sees work
  in other projects without opening them.

**One view of every run**
- **FR-006**: Run state MUST come from what every way of starting a run writes: `.sdd/run.json` and the run's
  journal. A supervisor's status counts only while that supervisor is alive. This holds for the UI and for
  `/sdd-status` alike (the rule is in the engine, used by both).
- **FR-007**: The UI MUST say how a run was started (in a session, by the server, or as part of a Job), and MUST show
  only controls that can act on that run. It MUST never instruct the person to run a terminal command.
- **FR-008**: `/sdd-run` MUST record in `.sdd/run.json` which session runs it, in a way another process can check
  is still alive and that survives the reuse of a process id. If no reliable way exists, the feature falls back to
  the person confirming that a session is closed (US4 acceptance 4).

**Orchestration in the server**
- **FR-009**: The server MUST own starting, stopping, resuming and holding supervised runs and Jobs for every listed
  project, with the behaviour 003 and 005 define today (Start run, Stop after current task, Stop now, Resume, Keep
  paused, Jobs and their limits), and at most one active run per project.
- **FR-009a**: The server MUST run at most `max_parallel` runs at once across all listed projects (default 2, set in
  the server's settings, at least 1). In-session runs count toward the cap but are never stopped or refused by it.
  When the cap is reached: Start run and Start job are refused, naming the runs that hold the places; a Job's next
  run and a hand-over wait, shown as "Waiting for a free place", and start in the order they began waiting once a
  place frees.
- **FR-009b**: Runs and Jobs the server started MUST NOT depend on the server process: they keep going when the
  server stops, crashes or restarts. On start, the server MUST find every live supervisor, attachment and Job of
  every listed project from their state files, and show and control them as before. Stop, Resume and Keep paused
  requests already written MUST still be carried out.
- **FR-010**: The server MUST let the person attach to an in-session run (**Supervise this run**). While attached it
  MUST: wait while the session is alive; take over only when the session has ended and `.sdd/run.json` is still
  there; then resume the run from its checkpoint, after the usage reset when a limit caused the cut-off; and stop
  watching, starting nothing, when the run closed normally. Silence in the journal alone MUST never trigger a
  take-over.
- **FR-011**: Everything the server starts (supervisors, `claude -p`) MUST run outside any Claude session's sandbox,
  so a headless run can set up its own sandbox.

**Plugin: almost stateless**
- **FR-012**: The plugin MUST keep no state of its own. What it writes is project state a run needs (`.sdd/run.json`,
  the journal, task ticks, `BLOCKED.md`, reports, commits) or the engine copy.
- **FR-013**: `/sdd-run` MUST keep working exactly as today in a session, whether or not a server is running.
- **FR-014**: A `/sdd-stop [--now]` command MUST stop a run (US5). For a supervised run it MUST make the same request,
  with the same refusals and messages, as the matching UI button, through the same engine code, and work with no
  server running.
- **FR-015**: `/sdd-status` MUST work with no server running, from the project's files through the engine.

**Engine copy and versions**
- **FR-016**: The plugin installation MUST place a copy of the engine in a fixed folder outside every project, with a
  file naming the plugin version and the contract version, and MUST refresh it whenever the installed plugin's
  version differs from the copy's. The copy MUST be replaced in one step.
- **FR-017**: The server MUST use only the engine copy for reading and writing project files the plugin defines. It
  MUST contain no second implementation of those formats.
- **FR-018**: Every exchanged format MUST carry the contract version: the engine copy, `.sdd/run.json`, the control
  request, the engine's JSON output and the server's API.
- **FR-019**: A version mismatch MUST be flagged where the person looks (a warning in the UI naming both versions, and
  a line in `/sdd-status`). A minor mismatch only warns. A major mismatch also disables every action that writes;
  reads stay available. Data without a version counts as major version 0.

**UI**
- **FR-020**: The UI MUST be rebuilt in TypeScript and served by the server as built files; the person needs no
  build tools to use it, only to develop it.
- **FR-021**: The new UI MUST reach parity with the current dashboard's views and actions before the current page
  is removed, shown by the ported browser tests passing against it.
- **FR-022**: The UI MUST get changes from the server as they happen (pushed, not polled every few seconds), and
  MUST update in place without losing scroll position or open panels.
- **FR-023**: Every view MUST work at phone width with no horizontal scrolling.

**Setting up projects and the plugin**
- **FR-025**: Opening a git repository that has no `.sdd/config.toml` MUST offer to set it up. Setup MUST do what
  `/sdd-init` does today, through one engine command that the `/sdd-init` skill also uses:
  - write the run config;
  - add the tasks template section;
  - merge the safety settings;
  - add the ignore lines;
  - offer the commit.
  The person MUST confirm, after seeing the diff of every file it writes. No AI is involved: the person chooses the
  check commands from candidates the server finds by fixed rules, or types their own, and each chosen command is
  run before it is saved.
- **FR-026**: The server MUST detect whether the plugin is installed in Claude Code and at which version. It MUST
  offer Install plugin and Update plugin as buttons that need a confirmation, never as an automatic step, and never
  while a run is going in any listed project. After either one it MUST refresh the engine copy from the installed
  plugin.
- **FR-027**: The server MUST be installed and updated on its own, independently of the plugin. When the server is
  the older side of a mismatch, it MUST say to update the server.

**How the server is started**
- **FR-024**: The person MUST be able to start the server with one command, from any folder. The server does not
  start at login; the person starts it after a reboot.

### Key Entities

- **Project entry**: resolved path, display name, when it was added, last seen state (present or missing).
- **Engine copy**: folder, plugin version, contract version, when it was copied.
- **Run record** (`.sdd/run.json`, extended): run id, branch, phases, workflow folder, contract version, and the
  session that runs it (FR-008) when started in a session.
- **Attachment**: project, run id, the session being watched, state (watching, taken over, closed, waiting for
  confirmation).
- **Version warning**: what disagrees (server, engine copy, a project's run), both versions, minor or major.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A run started in any of the three ways shows the same state in the UI and in `/sdd-status`, and the
  2026-11-02 case (a working in-session run after a finished supervised one) reads Working.
- **SC-002**: The person can open, switch between and remove at least 5 projects in one server, and a run in one is
  visible from another, with no restart.
- **SC-003**: After a plugin update, the server uses the matching engine by the next session start, with no step of
  the person's own; a faked major mismatch disables every write action and nothing writes.
- **SC-004**: An attached in-session run cut off by ending its session is finished by the server with no task
  started twice and no committed work lost; an attached run that closes normally leads to no new run.
- **SC-005**: `/sdd-stop` and the matching button produce the same request, refusals and messages in every
  run state the controls table lists.
- **SC-006**: All current browser tests, ported, pass against the new UI before the old page is removed; a written
  event reaches the UI within 2 seconds.
- **SC-007a**: Restarting the server while a supervised run, a Job and an attachment are going loses none of them:
  each is shown with its controls within 10 seconds of the start, and the run finishes as if nothing had happened.
- **SC-007**: No headless run started by the server fails to start its own sandbox.

## Out of Scope

- Access from other machines, several people, or any authentication beyond localhost.
- Starting the server at login (a per-user background service).
- Rewriting the engine or the server in another language.
- A cooperative "stop after the current task" for in-session runs (needs proof that something inside the run can
  see the request).
- A usage meter for in-session runs (only an interactive session's status line knows the usage; backlog item 12).
- The chat window (004) and the spec cycle (006) themselves; they are built on top of this feature afterwards.
- An MCP interface to the server (backlog 37e); the HTTP API is shaped so one can be added later.

## Assumptions

- Python 3 is available wherever the plugin runs; the engine stays standard-library only.
- npm packages for the UI's build tools can be installed through Artifactory (`npm-virtual`). This has not been
  tried yet and is the first thing to prove; direct npm is refused on this machine.
- The `claude` command can install and update plugins without a terminal (`claude plugin install … --json`,
  `claude plugin update …`, `claude plugin marketplace add|update`). The commands exist; running them from the server
  with no terminal attached has not been tried yet.
- The sandbox check in today's `/sdd-init` (run the checks again once the sandbox is on) cannot be done by the
  server, which is not a Claude session. The first run's preflight already runs the checks under the sandbox and
  names the setting to add when one fails, so that check moves there.
- A plugin can run a hook when a Claude session starts, which refreshes the engine copy. Not yet checked; if it
  cannot, `/sdd-init` and `/sdd-run` refresh it instead, and the server flags a stale copy.
- The built UI files ship with the server, so using the UI needs no Node installation; developing it does.
- The current dashboard keeps working until the new UI reaches parity (FR-021), and is then removed with its tests.
- Build order (clarified): 005's T020 and T022, then 008, then 006, then 004, then 007. 006 and 004 have no code
  yet, so building them on the new server costs nothing extra; their specs get a note pointing here.
