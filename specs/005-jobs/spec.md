# Feature Specification: Jobs: start what you want done, let it run across as many runs as it needs

**Feature Branch**: `005-jobs` | **Created**: 2026-10-30 | **Status**: Draft (two questions settled 2026-10-30; not yet clarified or reviewed)

**Input**: The person thinks in terms of what they asked for ("do Phase 11", "build the 004 MVP"), not in
terms of runs. A run is one `/sdd-run` session; it ends when usage runs out, when it crashes, or when it hits
its task cap, and today every start is its own run on its own `auto/` branch that the person merges by hand.
This feature adds a **Job**: the thing the person starts, watches and merges. A Job is carried out by one or
more runs, on one branch, and the runs become its history.

Builds on `specs/003-plugin-redesign/` (supervisor, launcher, run guard, dashboard routes and the Runs view)
and is used by `specs/004-feedback-chat/`, which will be built as a Job.

## Clarifications

### Session 2026-10-30
- Q: Is a Job a new feature or part of 004? → A: Its own feature, 005 (user, 2026-10-30). It changes the
  supervisor, the launcher and the Runs view; 004 then runs as a Job.
- Q: What is it called? → A: "Job" (user, 2026-10-30). "Run" stays the name for one `/sdd-run` session.
- Q: After the person settles a waiting item, does the Job continue by itself or wait for Continue? → A: By
  itself (user, 2026-10-30): when a settled item makes a task in scope runnable and no run is active, the next
  run starts without a click. Continue stays for `interrupted` and `stopped` Jobs.
- Q: Why? → A: Long work is carried out in pieces (usage windows, crashes, caps). Tools that do this keep
  one stable object the person starts and watches and put the executions under it as history: Temporal
  (one Workflow ID, several Run IDs), GitHub Actions (a workflow run with re-run attempts), Airflow (a DAG
  run with task tries), background coding agents (one task, one branch or PR, hidden sessions).

## Terms

- **Job**: what the person started: a feature, a scope of tasks, limits, and one branch. It has a title and
  a state, and it ends done, stopped or failed. It is merged once.
- **Run**: one `/sdd-run` session working on a Job (as today: `.sdd/run.json`, a run id, a report). A Job's
  runs are its history. A run interrupted by a usage limit and resumed keeps its run id, as today.
- **Scope**: the tasks a Job covers: all open tasks of the feature, a list of phases, or a list of tasks.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start a Job and let it finish across runs (Priority: P1)

The person picks a scope (for example Phase 11) and presses Start job. The supervisor runs `/sdd-run` on the
Job's branch. When a run ends with work left in the scope, for a usage limit, a crash or the end of the
run, the supervisor starts the next run on the same branch, until the scope is done, only items that need the
person are left, or a limit is reached.

**Why this priority**: it is the point of the feature: one start for one piece of work.

**Independent Test**: with the fake `claude`, start a Job whose scope needs three runs (the fake ends each run
after one task); check that three runs happen on one branch without a person, and the Job ends `done`.

**Acceptance Scenarios**:

1. **Given** a scope with runnable tasks and a clean tree, **When** the person starts a Job, **Then** a branch
   `auto/<job id>` is made from the current branch, the Job is `working`, and its first run starts.
2. **Given** a run ends and the scope still has runnable tasks, **When** no limit is reached, **Then** the next
   run starts on the same branch without the person, and the Job shows both runs in its history.
3. **Given** a run is paused for a usage window, **When** the window resets, **Then** the same run resumes (as
   today) and the Job shows `paused until <time>` meanwhile.
4. **Given** every task in the scope is done, wontfix or moved, **When** the last run ends, **Then** the Job is
   `done`.
5. **Given** only tasks that need the person are left (held, blocked, decisions), **When** the last run ends,
   **Then** the Job is `waiting on you` and the dashboard lists those items.

---

### User Story 2 - Merge a finished Job into its base branch (Priority: P1)

A Job's commits sit on its branch. When the Job is done (or stopped), the person presses **Merge** and the
Job's branch is merged into the branch it started from, with no terminal step.

**Why this priority**: today every run is merged by hand; one Job, one merge removes that work.

**Independent Test**: finish a Job in a temp repository, press Merge, check the base branch has every Job commit,
the checkout is back on the base branch, and the Job shows `merged` with the merge commit.

**Acceptance Scenarios**:

1. **Given** a `done` or `stopped` Job and no active run, **When** the person presses Merge, **Then** the base
   branch is fast-forwarded when possible, else gets one merge commit `merge: job <title>`, the checkout
   switches to the base branch, and the Job is `merged`.
2. **Given** the merge would conflict, **When** the person presses Merge, **Then** nothing changes (the merge is
   aborted), the Job stays as it was, and the dashboard names the conflicting files.
3. **Given** a run is active or the tree has uncommitted changes, **When** the person presses Merge, **Then** it
   is refused with the reason.

---

### User Story 3 - Continue after you answer (Priority: P2)

The Job is `waiting on you`. The person answers a decision, marks a manual task done or resolves a blocked
entry in the dashboard. The Job can then continue on its branch.

**Why this priority**: without it, answering means starting a new Job and a new branch.

**Independent Test**: a Job ends `waiting on you` with one decision; apply the decision; check the commit lands
on the Job's branch and that Continue starts the next run on that branch.

**Acceptance Scenarios**:

1. **Given** a Job `waiting on you`, **When** the person settles an item in the dashboard, **Then** the commit
   goes to the Job's branch (the checkout stays on it while the Job is open).
2. **Given** the settled item makes a task in scope runnable, **When** no run is active, **Then** the next run
   starts on the Job's branch by itself, and the Job shows `working` again.
3. **Given** a Job `waiting on you`, **When** the person presses Stop instead, **Then** the Job is `stopped`
   and can be merged as it is.

---

### User Story 4 - A Jobs view instead of a Runs view (Priority: P2)

The Runs view becomes **Jobs**. Each Job is a card with its title, state in words, a progress bar over its
scope, counts (done, waiting on you, left), its branch and commits, and its buttons (Stop, Continue, Merge).
Opening a card shows the tasks it covered (with outcome and commit) and its runs as history; opening a run
shows its tasks, events and report, as the Runs view does today.

**Why this priority**: the person watches Jobs, so the dashboard should show Jobs.

**Independent Test**: with a Job of two runs, open the Jobs view, open the Job, check both runs are inside it
and a task row opens the task.

**Acceptance Scenarios**:

1. **Given** Jobs exist, **When** the person opens `#jobs`, **Then** open Jobs come first, then finished ones,
   newest first.
2. **Given** runs from before this feature (no Job), **When** the view loads, **Then** they are listed under
   "Earlier runs", each as it is shown today.
3. **Given** a task card, **When** a Job touched the task, **Then** its chip names the Job ("done in job Phase
   11") and opens that Job; the run is one click deeper.

---

### User Story 5 - Plain words in the headline (Priority: P2)

The run banner and the Overview say what is happening, not which id is running.

**Why this priority**: "Run 20261030-1515 is running" says nothing the person can use (user, 2026-10-30).

**Independent Test**: for each Job state, check the banner text against the table in FR-012.

**Acceptance Scenarios**:

1. **Given** a working Job, **When** the Overview shows, **Then** the banner reads like "Working on Phase 11:
   T052, verifying (7 min)" with Stop after this task and Stop now, and no run id.
2. **Given** a paused Job, **Then** it reads "Paused until 18:05: usage limit; continues by itself".

### Edge Cases

- The supervisor is killed or the machine sleeps mid-Job: the next dashboard start finds the Job `working`
  with no live supervisor and shows it `interrupted` with Continue; the run resumes from `.sdd/run.json` as today.
- A run makes no progress (no task committed, nothing newly blocked) twice in a row: the Job is `failed` with
  the reason, instead of looping.
- The person edits `tasks.md` (adds tasks inside the Job's phases) while the Job waits: the scope is
  re-read at the start of each run; new open tasks in the scoped phases join the Job.
- The person switches branch by hand while a Job is open: the next run refuses to start ("the checkout is not
  on the Job's branch"), the Job shows that, and nothing is changed.
- A plain `/sdd-run` started in a Claude session (no supervisor): it is a run with no Job, shown under
  "Earlier runs" or, when the dashboard started it, under its Job.
- Two Jobs: only one Job may be open per project at a time; Start job is disabled with the reason while one is
  open.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The plugin MUST keep one record per Job in `.sdd/jobs/<job id>.json` (git-ignored like the rest of
  `.sdd/`, written only by the plugin's helpers and the supervisor, never by agents). The Job id has the run id
  form (`YYYYMMDD-HHMM`). Fields: id, title, feature, scope, limits, branch, base branch and its commit at the
  start, state, runs (run ids, oldest first), created and ended times, merge commit, and the last reason.
- **FR-002**: A Job's state MUST be one of `working`, `paused` (usage window), `waiting` (only items that need
  the person are left in scope), `interrupted` (no live supervisor while `working`), `stopped` (by the person,
  or a limit), `failed`, `done`, `merged`. Only `done`, `stopped` and `failed` Jobs can be merged; `merged` is final.
- **FR-003**: Starting a Job (dashboard Start job, or `bin/sdd-launch.py --job`) MUST create the record and the
  branch `auto/<job id>` from the current branch, and start the supervisor with the Job id. It MUST refuse when
  another Job is open, the tree is dirty, the project is not a git repository, or the scope has no runnable task.
- **FR-004**: The supervisor MUST, when a run ends, re-read the scope (`bin/sdd-tasks.py plan` with the Job's
  phases or tasks) and: start the next run on the Job's branch when runnable tasks remain and no limit is reached;
  set `waiting` when only person items remain; set `done` when nothing open remains in scope. Usage-window pauses
  and resumes stay as today and set `paused` in between.
- **FR-005**: Limits are per Job, not per run: a task cap (tasks committed in the Job), an optional stop-by time,
  and a maximum number of runs (default 20). Reaching one sets `stopped` with the limit named.
- **FR-006**: Two runs in a row with no progress (no task committed and no new BLOCKED.md entry) MUST set
  `failed` with "no progress in two runs".
- **FR-007**: Every run of a Job MUST run on the Job's branch; `/sdd-run` already stays on an `auto/` branch it
  is started on (SKILL step 1). The run's `workflows.json` MUST record `"job": "<job id>"` beside `feature`.
- **FR-008**: While a Job is open (not `done`, `stopped`, `failed` or `merged`), the checkout stays on its
  branch, and the dashboard's own commits (decisions FR-033, edits FR-035, manual tasks FR-036, 004 feedback)
  land there. A run MUST refuse to start when the checkout is on another branch.
- **FR-009**: Continue (dashboard, or `bin/sdd-launch.py --continue <job id>`) MUST start the next run of a
  `waiting`, `interrupted` or `stopped` Job on its branch, with the same refusals as FR-003 except "another Job
  is open".
- **FR-010**: Merge (dashboard, through a helper `bin/sdd-job.py merge <job id>`, with the 003 FR-030 checks)
  MUST merge the Job's branch into its base branch: fast-forward when possible, else one merge commit
  `merge: job <title>`; never force, never push, never rewrite history. It MUST refuse when a run is active, the
  tree is dirty, or the Job cannot be merged (FR-002); on a conflict it MUST abort the merge, change nothing and
  name the files. After a merge the checkout is on the base branch and the Job branch is kept.
- **FR-011**: The Runs view MUST become a Jobs view (`#jobs`, with `#runs` redirecting to it): one card per Job as
  in story 4, open Jobs first; inside a Job, the tasks it covered (from its runs, as `run_tasks()` gives them) and
  its runs, each opening as the run detail does today; runs with no Job under "Earlier runs".
- **FR-012**: The banner and the Overview MUST describe the open Job in words and never show a run id:
  `working` → "Working on <title>: <task>, <step> (<time>)"; `paused` → "Paused until <time>: usage limit;
  continues by itself"; `waiting` → "Waiting on you: <n> items"; `interrupted` → "Stopped unexpectedly:
  Continue to resume"; `stopped`/`done`/`failed` → "<title> <state>: <n> tasks done; Merge to bring them into
  <base>"; no open Job → "No job running" and Start job. Run ids stay in the Jobs view's history.
- **FR-013**: A task card's run chip (003 data-model §4.4 addition) MUST name the Job when the run had one
  ("done in job <title>") and open that Job.
- **FR-014**: The Job title MUST default to the scope in words ("Phase 11", "Phases 3–5", "T061, T063", "All open
  tasks") and MAY be typed by the person at start.
- **FR-015**: Without a supervisor (a plain `/sdd-run` in a session), everything keeps working: such a run has
  no Job and shows under "Earlier runs" (Principle VII; the core does not need Jobs).
- **FR-016**: When a Job is `waiting` and the person settles an item (FR-033, FR-036, a resolved BLOCKED.md entry, a
  004 feedback apply) so that a task in its scope becomes runnable, the next run MUST start by itself, with the
  FR-003 refusals except "another Job is open". The helper that made the commit triggers it, not the dashboard.

### Key Entities

- **Job**: id, title, feature, scope (`{"phases": [...]} | {"tasks": [...]} | {"all": true}`), limits
  (`max_tasks`, `until`, `max_runs`), branch, base (`{branch, commit}`), state, runs, created, ended, merge
  commit, reason.
- **Run**: as in 003, plus `job` in its `workflows.json`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A scope that needs several runs finishes with one Start job and, if nothing needs the person, no
  other action until Merge.
- **SC-002**: All of a Job's commits are on one branch, and one Merge brings them into the base branch; a test
  compares the base branch's history after the merge with the Job's commits.
- **SC-003**: No headline text (banner, Overview progress pill) contains a run id; checked by a page test for
  every Job state.
- **SC-004**: A Job that cannot progress stops (`failed` or `waiting`) within two runs, never loops; covered by a
  test with the fake `claude`.
- **SC-005**: Merge never changes anything on a conflict or refusal; covered by tests.

## Out of Scope

- Several open Jobs at once, or Jobs in parallel worktrees.
- Pushing, pull requests, or deleting branches.
- Scheduling a Job for later.

## Assumptions

- The 003 supervisor, launcher and run guard stay as they are, extended rather than replaced.
- The person merges into the branch the Job started from (normally `main`).
- One project per dashboard, one open Job per project.
