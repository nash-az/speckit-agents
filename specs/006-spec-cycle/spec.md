# Feature Specification: Spec cycle: specify, clarify, plan and tasks from the dashboard

**Feature Branch**: `006-spec-cycle` | **Created**: 2026-10-31 | **Status**: Draft (four defaults assumed by the assistant, see Clarifications; not yet clarified or reviewed)

**Input**: The dashboard and runs only see the open tasks of one feature's `tasks.md`. Everything before that,
writing the spec, answering clarify questions, the plan and the tasks, happens in a chat session at a terminal,
so it is invisible in the dashboard and needs the person there. The person wants to start and steer Spec Kit's
own commands from the dashboard, in Spec Kit's recommended order: specify, clarify, plan, (checklist), tasks,
analyze, then build. Research and a working proof: `logs/2026-10-31-spec-cycle-research.md`.

Builds on `specs/003-plugin-redesign/` (dashboard, helpers, run guard, state directory) and
`specs/005-jobs/` (the Build stage starts a Job). Replaces `specs/004-feedback-chat/` User Story 5 (new specs
through a proposal) with the Specify stage; shares 004's question cards and helper pattern.

## Clarifications

### Defaults assumed 2026-10-31 (by the assistant; the person has not decided these yet)
- Q: Where do a stage's commits go? → Assumed: the current branch, one commit per approved stage, as the 003
  FR-035 editor does. (Alternative: a `spec/<feature>` branch merged once the tasks are approved.)
- Q: Does Clarify start by itself after Specify is approved? → Assumed: yes, and the person can skip it.
- Q: Do Analyze findings block Start job? → Assumed: CRITICAL findings block it until fixed or accepted one by
  one; other findings only warn.
- Q: Which model runs the stage sessions? → Assumed: the same model as runs (Opus 5.5) unless `[spec]` in
  `.sdd/config.toml` names another (the probe cost about $0.5 per answered question on Opus).

### Settled by the research (2026-10-31)
- Q: Use Spec Kit's workflow engine (`specify workflow run`) to drive the cycle? → A: Not now. A gate only
  approves or rejects between steps; nothing can answer a question inside a step, which `/speckit-clarify`
  needs. Revisit when the engine supports it; the stage state machine here is small enough to hand over.
- Q: Can a headless session run the real `/speckit-clarify` and take answers? → A: Yes, proven: `claude -p
  "/speckit-clarify" --output-format json` ends its turn at one question in Spec Kit's format and changes no
  file; `claude -p "<answer>" --resume <session id>` writes the answer into `spec.md` and asks the next one.

## Terms

- **Stage**: one Spec Kit command for one feature: Specify, Clarify, Plan, Checklist, Tasks, Analyze, Build.
- **Stage session**: the headless Claude Code session that runs one stage's command, continued once per answer
  or note. It ends when the command finishes.
- **Turn**: one start or continuation of a stage session, and its reply.
- **Cycle**: a feature's stages and their states; it is computed, not stored as a separate truth.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start a new feature from a description (Priority: P1)

The person presses **New feature**, types what they want, and presses Start. Specify runs. If it needs to know
something it shows up to three questions as cards; the person answers. The spec appears for review with what
changed, and the person approves it.

**Why this priority**: it is the entry point of the cycle; without it a feature still starts in a terminal.

**Independent Test**: with the fake `claude` answering one turn with a question and the next with a finished
spec, start a feature, answer the card, approve; check `specs/<NNN>-<name>/spec.md` exists, one commit
`spec(<NNN>): specify` holds only that folder and `.specify/feature.json`, and the feature is in the list.

**Acceptance Scenarios**:

1. **Given** no run is active and the tree is clean, **When** the person starts a feature with a description,
   **Then** the Specify stage is `working` and the dashboard shows the session's progress.
2. **Given** the session's reply asks questions, **When** the turn ends, **Then** the stage is `needs you`, each
   question is a card (options as buttons, the recommended one preselected, a free-text box), and it counts in
   "Waiting on you".
3. **Given** the person answers, **When** they send it, **Then** one continuation of the same session starts with
   the answer, and the stage is `working` again.
4. **Given** the command finished, **When** the turn ends, **Then** the stage is `review` and shows the reply and
   the changed files as a readable diff, with Approve, Request changes and Discard.
5. **Given** a stage in `review`, **When** the person presses Approve, **Then** the helper commits only that
   stage's files, the stage is `approved`, and the next stage is offered (Clarify starts by itself).

---

### User Story 2 - Clarify one question at a time (Priority: P1)

After Specify, Clarify runs Spec Kit's `/speckit-clarify`. It asks up to five questions, one at a time. Each is
a card; the person picks an option or types a short answer, and the next question follows. At the end the
person reviews what changed in the spec and approves it.

**Why this priority**: it is the step that most needs the person and the reason a terminal is needed today.

**Independent Test**: with the fake `claude` giving two questions then a finished reply, run Clarify; check two
cards appear one after the other, each answer starts one continuation with that answer as its prompt, and the
review shows the spec diff.

**Acceptance Scenarios**:

1. **Given** a reply in Spec Kit's question format (`**Question:**`, `**Recommended:**`, an options table),
   **When** it is shown, **Then** the options are buttons and the recommended one is marked and preselected.
2. **Given** a reply the helper cannot parse as a question, **When** it is shown, **Then** the reply appears as
   rendered text with a free-text answer box, never hidden.
3. **Given** a question card, **When** the person presses Skip clarify, **Then** the session is ended, any spec
   edits made so far go to review, and Plan is offered.

---

### User Story 3 - Plan, tasks and analyze, with a review at each step (Priority: P1)

The person moves the feature through Plan, Tasks and Analyze. Each stage runs its Spec Kit command, may ask, and
ends in review. After Tasks, the plugin's own plan check runs. Analyze lists its findings; CRITICAL ones must be
fixed or accepted before Start job.

**Why this priority**: it completes the front half; the result is a `tasks.md` a Job can run.

**Independent Test**: with the fake `claude`, take a feature from an approved spec to approved tasks; check one
commit per stage, the plan check result shown after Tasks, and Start job disabled while a CRITICAL finding is
open and enabled after it is accepted.

**Acceptance Scenarios**:

1. **Given** an approved spec, **When** the person starts Plan, **Then** `/speckit-plan` runs and its files (plan,
   research, data model, contracts, quickstart) are in the review.
2. **Given** approved tasks, **When** the plan check finds every open task held (for example by a phase that holds
   earlier ones), **Then** the review shows that as a warning with the reason.
3. **Given** Analyze finished, **When** findings exist, **Then** each is listed with its severity, and each
   CRITICAL one has **Fix** (Request changes on the stage it names, with the finding as the note) and **Accept**
   (recorded with the person's reason).
4. **Given** no open CRITICAL finding and approved tasks, **When** the person presses Start job, **Then** a 005 Job
   starts for the feature.

---

### User Story 4 - Request changes or discard a stage (Priority: P2)

In review the person does not approve: they write what to change and send it, and the same session continues
with that note; or they discard the stage, and its files go back to how they were.

**Why this priority**: without it a wrong draft means starting over or editing by hand.

**Independent Test**: request changes once, then discard; check the note started a continuation, and after
Discard the tree equals the last commit for that stage's files.

**Acceptance Scenarios**:

1. **Given** a stage in `review`, **When** the person sends a note, **Then** one continuation starts with the
   note, and the stage returns to `review` when it ends.
2. **Given** a stage in `review` or `needs you`, **When** the person presses Discard, **Then** the session is
   ended, the stage's files are restored to the last commit (a new feature folder is removed), and the stage is
   `not started` (or `approved` again if it was a re-run).

---

### User Story 5 - Out of date after an earlier change (Priority: P2)

The person edits the spec (in the 003 editor, through 004 feedback, or by re-running Specify) after the plan was
approved. The plan, tasks and analyze stages now show **out of date**, with Re-run and Keep.

**Why this priority**: this is the cycle: a change upstream must be visible downstream.

**Independent Test**: approve all stages in a temp project, commit a change to `spec.md`, check Plan, Tasks and
Analyze read `out of date`; press Keep on Plan and check it reads `approved` until the spec changes again.

**Acceptance Scenarios**:

1. **Given** a stage approved at commit C, **When** a file of an earlier stage changed after C, **Then** that stage
   and every later one read `out of date`.
2. **Given** an `out of date` stage, **When** the person presses Keep, **Then** it reads `approved` as of the
   current commit; **When** they press Re-run, **Then** a new stage session starts.

---

### User Story 6 - All features at a glance (Priority: P2)

The dashboard lists every feature folder under `specs/` with its current stage in words and what it waits on.
Opening one shows its stage stepper and the current card. The feature a run or Job uses is marked.

**Why this priority**: today the dashboard shows only one feature, so the front half of other features is
invisible.

**Independent Test**: in a temp project with three features at different stages, open the Features view; check
each row's stage, and that opening one does not change the feature runs use until the person says so.

**Acceptance Scenarios**:

1. **Given** features at different stages, **When** the Features view opens, **Then** each row shows its name, its
   current stage and state in words, and a count of what waits on the person.
2. **Given** a feature folder made by hand without stage records, **When** it is listed, **Then** its stages are
   derived from the files present (spec, plan, tasks) and shown as `approved` when committed.

### Edge Cases

- A run or Job is active in the same checkout: starting or continuing a stage is refused with the reason (the
  2026-10-30 rule); a stage already `needs you` stays so and can be answered after the run.
- The session exceeds its time limit, crashes or is stopped by a usage limit: the stage is `failed` with the
  reason; Retry continues the same session when it can, else starts a new one.
- The dashboard or machine stops while a stage is `working`: at the next start that stage reads `interrupted`
  with Retry.
- The session tries to write outside the feature folder, edit code, commit or push: the guard refuses it; the
  refusal is shown in the turn's reply.
- Two stages of different features waiting at once: allowed (each has its own session); only one stage session
  runs at a time per project.
- The command produces no file change (for example Clarify finds nothing to ask): the stage ends `approved`
  without a commit, and says so.
- The tree has unrelated uncommitted changes: a stage can run, but Approve commits only the stage's files and is
  refused if one of those files had changes before the stage started.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A helper (`bin/sdd-spec.py`), called by the dashboard server and usable from the shell, MUST start,
  continue, stop, approve and discard stages. The dashboard server MUST NOT spawn processes or write project
  files for this feature (003 FR-020, FR-033 pattern).
- **FR-002**: A stage session MUST run Spec Kit's own command, unchanged, as the project's integration installs it
  (`/speckit-specify`, `/speckit-clarify`, `/speckit-plan`, `/speckit-checklist`, `/speckit-tasks`,
  `/speckit-analyze`), in a headless Claude Code session in the project directory with the project's settings and
  sandbox, JSON output, and the question tool disabled so questions arrive as text.
- **FR-003**: Each answer or note MUST continue the same session (resume by its session id) as one turn. The
  helper MUST record per turn: the prompt, the reply, the files changed, the start and end time, the cost, and
  the outcome, in the state directory (outside the tracked project files).
- **FR-004**: A stage's state MUST be one of `not started`, `working`, `needs you`, `review`, `approved`,
  `out of date`, `failed`, `interrupted`, computed by plain code from the stage records, the files and git
  history. The order is Specify → Clarify → Plan → Checklist (optional) → Tasks → Analyze → Build.
- **FR-005**: A turn's reply MUST be classified by plain code: a question (Spec Kit's `**Question:**` /
  `## Question N` format, with options parsed from its table and the recommended option from `**Recommended:**`),
  or finished. A reply that is neither MUST be shown as rendered text with a free-text box.
- **FR-006**: Approve MUST commit only the stage's own files (the feature folder's files the stage wrote, plus
  `.specify/feature.json` for Specify) as `spec(<NNN>): <stage>`, on the current branch, and record the commit in
  the stage record. It MUST refuse while a run is active, or when one of those files had uncommitted changes from
  before the stage started.
- **FR-007**: Request changes MUST continue the session with the person's note (up to 4000 characters). Discard
  MUST end the session and restore the stage's files to the last commit; a feature folder created by the stage is
  removed.
- **FR-008**: While a stage session runs, a guard mode MUST allow writes only to `specs/<feature>/**` and
  `.specify/feature.json` and MUST refuse code edits, git commits, pushes and branch changes, failing closed like
  the 003 run guard.
- **FR-009**: Starting or continuing a stage MUST be refused while `.sdd/run.json` exists or a Job is working;
  at most one stage session runs at a time per project.
- **FR-010**: A stage approved at commit C MUST read `out of date` when a file of an earlier stage changed after
  C; every later stage then also reads `out of date`. Keep MUST record the current commit as accepted; Re-run
  MUST start a new session.
- **FR-011**: After Tasks is approved, the helper MUST run `bin/sdd-tasks.py plan` on the new `tasks.md` and show
  runnable, held and blocked counts; when every open task is held it MUST show a warning with the hold reasons.
- **FR-012**: Analyze findings MUST be listed with severity. Each CRITICAL finding MUST be fixed (Request changes
  on a named stage with the finding as the note) or accepted (with a reason, recorded) before Start job is
  enabled; other findings only warn.
- **FR-013**: Clarify MUST start by itself when Specify is approved, and MUST offer Skip, which ends the session
  and sends any edits so far to review.
- **FR-014**: The Build stage MUST start a 005 Job for the feature (its open tasks as scope) and show the Job's
  state; until 005 exists it starts a supervised run as 003 FR-034 does.
- **FR-015**: The dashboard MUST have a Features view listing every `specs/*` folder with its current stage and
  state in words and what waits on the person; a feature's page shows the stage stepper, the current card, each
  stage's turns, and the diff under review. Open questions and reviews count in "Waiting on you". Selecting a
  feature in this view MUST NOT change the feature runs use (`.specify/feature.json`) unless the person presses
  "Use for runs".
- **FR-016**: The model and the maximum time per turn (default 10 minutes) MUST be configurable in a `[spec]`
  section of `.sdd/config.toml`, reusing the 003 run profiles; the default model is the one runs use. A turn over
  the time limit is stopped and the stage is `failed`.
- **FR-017**: Everything a person can do here MUST also work from the shell through the helper, and the
  dashboard and CLI MUST stay fully usable (listing features and stages, approving, discarding, keeping) with no
  model available (Constitution VII); only starting or continuing a session needs a model.
- **FR-018**: A feature folder created by hand, without stage records, MUST be shown with stages derived from its
  files (spec, plan, tasks present and committed → `approved`).

### Key Entities

- **Stage record**: feature, stage, state, session id, turns, the commit it was approved or kept at, the files it
  wrote, Analyze findings with their decisions, failure reason. Kept in the state directory.
- **Turn**: number, kind (start, answer, note, retry), prompt, reply, parsed question (if any), files changed,
  started, ended, cost, outcome.
- **Feature**: a `specs/<NNN>-<name>/` folder; its cycle is the list of its stages.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A new feature goes from a typed description to an approved `tasks.md` and a started Job with no
  terminal step; checked end to end with the fake `claude`, and once for real on one feature.
- **SC-002**: Every question a Spec Kit command asks reaches the person as a card; none is answered for them
  (Constitution III). A test feeds each of Spec Kit's question formats and checks the card.
- **SC-003**: Each approved stage is exactly one commit holding only that stage's files; a test compares each
  commit's file list with the stage record.
- **SC-004**: A stage session never changes a file outside its feature folder and `.specify/feature.json`; guard
  tests cover writes, edits, commits and pushes.
- **SC-005**: With no model available, the Features view, stage states, Approve, Discard and Keep all work; a test
  runs them with `claude` absent from `PATH`.
- **SC-006**: An earlier change shows later stages `out of date` within one dashboard refresh (3 s).

## Out of Scope

- Spec Kit's workflow engine (`specify workflow run`) as the driver, for now (see Clarifications).
- Editing code or running tests from a stage; implementing is the Job's work.
- `/speckit-constitution` and `/speckit-taskstoissues` as stages (the constitution has the 003 editor).
- Several stage sessions at once, worktrees for stages, and multi-person use.

## Assumptions

- The project uses Spec Kit's Claude integration with skills (`/speckit-<cmd>`), as this repo does
  (`.specify/init-options.json`); other integrations are not supported yet.
- No Spec Kit git extension switches branches during Specify (true here); if one is installed, Specify is refused
  with that reason.
- The 003 helpers, guard and state directory are extended, not replaced; 005 Jobs exists before the Build stage
  starts Jobs.
- One project per dashboard, one person.
