# Feature Specification: Runs: short-lived groupings of tasks, with the task as the record

**Feature Branch**: `009-runs` | **Created**: 2026-11-06 | **Status**: Amended 2026-11-06 after run 20261106-1204 and its analyze pass (T001–T032 built); amended again 2026-11-07 (Stop while preparing and the run-control trace: FR-031 to FR-034, SC-009; clarified, plan and tasks written; clarified again 2026-11-08 after analyze: Stop now before any task, ignored requests logged, no empty session
report; clarified again 2026-11-09 after the fifth analyze pass: the reason when earlier sessions did work, FR-007
narrowed)

**Input**: On 2026-11-06 the person stopped a Phase 3 Job of 008 with "Stop after current task" and pressed Merge. Merge
refused with "a run is active": the stop had kept `.sdd/run.json` so the run could resume, and Merge refuses while that
file exists. T012–T014 were done and verified but could not reach `main` without a hand-made workaround, and T015 had
been started and abandoned. Behind this sit three ideas the person rejects: a Job that lives on after its work is
over until someone merges it; a merge gate that adds a click but no check (every task already passed the checks and
the verifier); and logs kept per run in Claude's transcript store, when the person thinks in tasks. The person:
"The run should be treated as ephemeral grouping that would live on beyond its lifetime as statistical/project
learnings/timestamps supporting data, not more. The logs should be tied to a task, not a run, since the tasks are
granular pieces of work that are mostly independent." Resume stays: "we implemented a whole feature where I can stop
the task and make it resume … I want that to stay in."

Changes `specs/005-jobs/` (the Job is folded into the run) and `specs/008-standalone-server/` (the views, routes and
hand-over that speak of Jobs). Builds on `specs/003-plugin-redesign/` (supervisor, launcher, workflow, run guard).

## Clarifications

### Session 2026-11-06
- Q: What is the thing the person starts called? → A: A **run** (user, 2026-11-06). "Job" is retired. The `claude -p`
  processes a run uses (for example before and after a usage-limit wait) are its **sessions**, an internal detail.
- Q: Does Stop end the run? → A: Yes (user). "Stop after this task" waits until the current task is committed or
  blocked, then ends the run. Its record lists only what it did: started with T1 and T2, stopped after T1, the record
  has T1, and T2 is open again for any later run.
- Q: Who merges? → A: The plugin, when a run ends cleanly (user agreed that a manual merge adds no check). Every task
  commit already passed the project checks and the verifier; the repository has no remote; a local merge is easy to
  undo.
- Q: What happens to work in progress when a run is cut off mid-task (Stop now, a usage limit that is not waited
  for, a crash, a closed session)? → A: It stays (user). The run is **suspended**: `.sdd/run.json` and the half-done
  work stay in the tree, which is dirty on purpose. The person then chooses **Continue** (resume where it stopped, as
  today), **Wait** (leave it), or **Drop** (throw away the half-done task, end the run, land its finished tasks).
- Q: Where do logs live? → A: With the task (user). A task's history (every attempt in every run, verdicts, commits,
  the reason it was blocked) is kept per task in the repository. A run afterwards is a summary: times, tasks done,
  usage, statistics.

### Session 2026-11-06 (amendment, after run 20261106-1204 and its analyze pass)
- Q: Constitution II forbids touching protected branches during a run, yet a run lands on `main`. Which gives? →
  A: Amend the constitution (user). v1.2.0 lets the plain-code engine, never an agent, land a run's committed work on
  its base by fast-forward or merge at the end, and by fast-forward while suspended.
- Q: At Stop after this task, a task that started after the boundary may have left edits. Suspend or end? → A: Stash
  and end (user). The edits are saved as a patch in that task's history and stashed, the run ends `stopped`, and its
  finished tasks land.
- Q: Which `waiting` run restarts when the person settles an item? → A: Only the newest run of the project, so a
  stale run never restarts by itself (default chosen in clarify; analyze flagged the risk).
- Q: Who enforces the usage-limit pause? → A: The supervisor only (user): concerns stay separate and the workflow
  stays simple. Every run is started through the supervisor (launcher or Start run). `/sdd-run` stays the
  supervisor's single cycle and never starts a supervisor itself; typed into a session without one, it warns that the
  run has no usage-limit protection. Run 20261106-1204 was started that way and hit the 5-hour limit mid-task.
- Q: Where does a task's history get committed? → A: With the session's report commit, or with the run's history
  and summary commits when the run ends or is dropped (as the plan chose and the code does), not in the task's own commit.
- Q: The task text is written for the implementer (paths, function names, exact strings). What does the person
  read? → A: A plain-language summary on an indented line below each task (user), written by `/speckit-tasks`. The
  task card shows the summary, with the exact text behind an expander. Backfill 009's `tasks.md` only; 005 and 008
  stay as they are.

### Session 2026-11-07 (amendment: Stop while preparing and the run-control trace)
- Q: Stop after this task pressed after Start run but before the new supervisor has written its state: refuse it,
  keep it, or hide the buttons? → A: Keep it (user). The request is accepted, tagged with the run id the launcher
  created; the supervisor carries it out at startup ("stopped before any task") and deletes only a request for a
  different run. (Superseded in part by 2026-11-08 Q2: a request with no run id is deleted too, and a deleted
  request is logged as ignored; FR-032.)
- Q: What does the banner show when a run-control request gets no answer (network failure, timeout, server error)?
  → A: An error beside the buttons, and pressing again is the retry; no automatic retry (user). Already built: a
  refused or failed request shows `request failed: <error>` on the banner and in a toast. New: the pressed button
  reads "sending…" with the banner's control buttons disabled until the answer, and a request unanswered after 10
  seconds counts as failed (FR-033).
- Q: Where can the person read the control log? → A: The file in the state directory, plus a Requests list on the
  run's card in the Runs view, live and past runs (user); not copied into the committed run summary (FR-034).

### Session 2026-11-08 (amendment, after its analyze pass)
- Q: When the person presses Stop now before any task of the run has started, should the run end, or be left
  suspended? → A: End it (user): `stopped`, "stopped before any task", nothing landed, whenever no agent has started
  yet (not only in FR-032's start window); if the tree holds uncommitted work (a resumed session's half-done task),
  the run stays `suspended` instead (FR-006, FR-007). (2026-11-09 Q1 refines "nothing landed" and the reason when
  earlier sessions of the run did work.)
- Q: When a new supervisor finds a request meant for an earlier run (or with no run id), should the control log keep
  what the request said, or only note that one was thrown away? → A: Keep it (user): the supervisor reads it, does
  not carry it out, deletes it, and logs it as ignored with its id, run id, command and why (FR-031, FR-032).
- Q: Does the clean-tree condition of the first answer also apply to Stop after this task pressed during `preparing`?
  → A: Yes, already decided by that answer (applied after the second analyze pass, D2): a clean tree ends the run
  "stopped before any task"; uncommitted work keeps it `suspended` (FR-005, User Story acceptance 2).
- Q: When a run is stopped before any task has started, should its session's short report still be committed and
  land on the base branch? → A: No (user, third analyze pass B1): the engine writes no session report for a session
  with no task entries, so such a run lands nothing and its landing is always `nothing to land` (FR-017). The run
  summary still records it. The clean-tree clause of the first answer is applied to FR-032 too (I1). (2026-11-09
  Q1: this holds for a run with no task entries in any session; earlier sessions' work still lands.)

### Session 2026-11-09 (after the fifth analyze pass)
- Q: When a run that already finished tasks in an earlier session (before a usage-limit wait or a Continue) is
  stopped before its next session starts any agent, what reason should the ended run show? → A: A different reason
  (user, analyze M1): "stopped before its next task" when an earlier session of the run has task entries;
  "stopped before any task" only when no session has. The stopped session adds no commit; the finished work of
  earlier sessions still lands (FR-005, FR-006, FR-017, FR-032).
- Applied without a question (analyze M2, L12): FR-007's boundary clause is narrowed to stops the supervisor detects
  before any agent of the session has started; Stop now after an agent started and FR-008's kept pause suspend
  whatever the tree holds. FR-032 says a request for its own run that is not a stop is ignored at startup.

## Terms

- **Run**: what the person starts (scope, limits, branch), from start until it ends. Short-lived. Afterwards it is a
  summary record, not something to act on.
- **Session**: one `claude -p` (or interactive `/sdd-run`) process inside a run.
- **Clean end**: a run ends at a task boundary with a clean tree; its finished tasks land on the base branch.
- **Suspended**: a run cut off mid-task, with its half-done work kept in the tree for Continue or Drop.
- **Task history**: the per-task, append-only record of everything that happened to one task, across runs.
- **Land**: bring a run's task commits into its base branch.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Stop after this task ends the run and lands its work (Priority: P1)

The person starts a run on T1 and T2 and presses **Stop after this task** while T1 is being implemented. T1 is
implemented, verified and committed; then the run ends, T1 lands on `main`, and T2 is open again. The person can
start another run straight away, with T2 or with other tasks.

**Why this priority**: it is the failure of 2026-11-06; without it, finished work is stuck behind a stopped Job.

**Independent Test**: on a toy repo with the fake `claude`, start a run on two tasks, request Stop after this task
during the first; check `main` has the first task's commit, `.sdd/run.json` is gone, the run's record lists one task,
the second task is open, and a new run on the second task starts without any other step.

**Acceptance Scenarios**:

1. **Given** a run working on a task, **When** the person presses Stop after this task, **Then** the run finishes that
   task (commit or blocked entry), starts no other task, ends, and its task commits land on the base branch with no
   click.
2. **Given** Stop after this task is pressed while the run is still preparing (before any task has started),
   **Then** with a clean tree the run ends at once, adds no commit, and says so ("stopped before any task", or
   "stopped before its next task" when an earlier session did work, which still lands); with uncommitted work in
   the tree (a resumed session's half-done task), it stays `suspended`.
3. **Given** a run ends because its scope is done, a limit is reached (task cap, stop-by time, no progress), or only
   items that need the person are left, **Then** it ends cleanly in the same way, with that reason.
4. **Given** the base branch moved while the run worked (the person committed to it), **When** the run ends,
   **Then** its commits land with one merge commit; on a conflict nothing changes, the run shows "needs merge"
   naming the files, and **Merge** retries once the person has resolved it.
5. **Given** a run has ended cleanly, **When** the person looks at the run list, **Then** it shows as a past run with
   its summary and no action other than opening it.

---

### User Story 2 - A run cut off mid-task waits for the person (Priority: P1)

A run is cut off in the middle of a task: the person pressed **Stop now**, a usage limit was reached and the run
was not set to wait for the reset (or the reset is too far), the session crashed, or the terminal of an in-session
run was closed. The half-done work stays in the tree. The person sees the run as **suspended**, with the task it was
on, and chooses: **Continue** resumes where it stopped; **Drop** discards the half-done task, ends the run and lands
its finished tasks; doing nothing leaves it suspended.

**Why this priority**: the person built resume and wants to keep it; it must not block landing finished work.

**Independent Test**: on a toy repo, cut a run off during its second task (kill the fake `claude`); check the run is
suspended with the half-done files in the tree; Continue finishes the task in the same run; repeat and Drop instead:
the tree is clean, the first task is on `main`, the second is open, and its history holds the discarded diff.

**Acceptance Scenarios**:

1. **Given** a run cut off mid-task, **Then** `.sdd/run.json` and the half-done changes are kept, the run shows
   "Suspended during <task>: <reason>", and it offers Continue and Drop.
2. **Given** a suspended run, **When** the person presses Continue, **Then** it resumes from its checkpoint in the
   same run (same run id, same branch), as resuming does today.
3. **Given** a suspended run, **When** the person presses Drop (with a confirm naming the task), **Then** the half-done
   changes are removed from the tree, saved in that task's history, the run ends, and its finished tasks land.
4. **Given** a suspended run whose finished tasks are not on the base branch, **When** the base branch has not moved,
   **Then** the finished tasks land at once by fast-forward, without touching the tree or the half-done work.
5. **Given** a usage limit is reached and the run is set to continue when it resets, **Then** the run is not
   suspended: it pauses and carries on in the same run after the reset (003/005 behaviour kept).
6. **Given** a suspended run, **When** the person starts a new run, **Then** it is refused with "a run is suspended:
   Continue or Drop it first".

---

### User Story 3 - Every task keeps its own history (Priority: P1)

The person opens T008 in Tasks and sees everything that happened to it, oldest first: implemented by the
implementer in run 20261104-1710, rejected by the verifier with its reason, implemented again, approved, committed
as 81e4f43. If T015 was dropped from a suspended run, its history says so and holds the discarded diff. This history
is still there months later and on another machine with a clone of the repository.

**Why this priority**: the person thinks in tasks; logs per run are scattered across sessions and can expire.

**Independent Test**: run a task that is rejected once and then approved; delete the Claude transcript folders of
that run; the task's history still shows both attempts, the rejection reason, the verdict and the commit.

**Acceptance Scenarios**:

1. **Given** any task step (started, implemented, verified with verdict and problems, retried, committed, blocked,
   suspended, resumed, dropped), **Then** an entry is added to that task's history with its time, run, session,
   attempt, agent role and model.
2. **Given** a task is committed, **Then** its commit message carries the task id, the run id, the attempt count and
   the implementer's model as trailers, and its history is committed with the session's report or the run's history and summary commits.
3. **Given** Claude's transcript of an attempt still exists, **Then** the task's history links to it for the full
   output; when it has been deleted, the history still reads complete.
4. **Given** a task worked on in several runs, **Then** its history shows all of them in one list.

---

### User Story 4 - A past run is a summary (Priority: P2)

The person opens the run list and sees past runs as summaries: when, how long, the scope asked for, the tasks done
(each a link to the task), tasks dropped or blocked, attempts and approval rate, models, usage, and how it ended.
Across runs they can compare, for example, how often a local implementer's work is approved compared with Claude's.

**Why this priority**: the run keeps value as statistics; it should cost nothing to keep.

**Independent Test**: finish two runs with different implementer models; the run list shows both summaries and a
per-model approval rate computed from the task histories.

**Acceptance Scenarios**:

1. **Given** a run has ended, **Then** its summary is written to the repository once and never changes afterwards.
2. **Given** a past run, **When** the person opens it, **Then** each task in it links to that task's history.
3. **Given** several past runs, **Then** totals per feature and per model (tasks, attempts, approval rate, time,
   usage) can be read from the summaries and task histories without the transcripts.

---

### User Story 5 - Existing Jobs and runs carry over (Priority: P3)

After upgrading, nothing the person already has is lost or stuck. Merged and dropped Jobs and their runs show as past
runs; an open Job shows as a run (suspended if its `.sdd/run.json` is there, otherwise ended and offered to land).

**Why this priority**: one-time, but the person's open work must not be stranded.

**Independent Test**: with records from 005 (one merged Job, one stopped Job with `run.json`, one done Job not
merged), upgrade; check each shows as described and the two open ones can be continued, dropped or landed.

**Acceptance Scenarios**:

1. **Given** a merged or dropped Job, **Then** it shows as a past run with its runs' tasks.
2. **Given** an open Job with `.sdd/run.json`, **Then** it shows as a suspended run with Continue and Drop.
3. **Given** an open Job without `.sdd/run.json` and not merged, **Then** it shows as ended with **Land** (one press).

### Edge Cases

- Stop after this task during a retry: the task finishes its retries (approved, or blocked after `max_retries`).
- Stop after this task while a group's bookkeeper is committing several tasks: the group's commits finish first.
- A run cut off between a task's commit and the next task's start by a crash, a closed session or a usage limit it
  does not wait for is not suspended: it ends cleanly. Stop now and a kept pause at the reset suspend it, whatever
  the tree holds (FR-007).
- A suspended run whose half-done work was changed or removed by hand: Continue resumes from the checkpoint as
  today; Drop removes whatever uncommitted changes are in the tree, after a confirm that lists them.
- Two suspended runs cannot exist: a new run is refused while one is suspended (US2 scenario 6).
- The person commits to the base branch while a run is suspended: finished tasks land when the run ends (merge
  commit), not by fast-forward.
- A run with no task committed ends cleanly with nothing to land and still leaves a summary.
- A plain `/sdd-run` typed into a session (no supervisor) is not how runs start (FR-027). It still runs, records
  task histories and commit trailers, and warns at the start that it has no usage-limit protection; it does not
  stop at task boundaries or land by itself.
- Stop after this task when a task that started after the boundary has left edits: they are saved as a patch in that
  task's history and stashed, and the run ends cleanly (FR-005).
- Stop pressed in the second or two after Start run, before the new supervisor has written its state: the request is
  accepted and tagged with the new run's id; the supervisor carries it out as soon as it starts, so the run ends
  `stopped` with "stopped before any task" (FR-005, FR-032). A request left from an earlier run (another run id, or
  none), or a request for its own run that is not a stop, is deleted at startup without being carried out, and
  logged as ignored with its id, command and why (FR-031).
- Stop now before any agent has started: the run ends `stopped`, "stopped before any task", like Stop after this
  task; but when a resumed session finds half-done work in the tree, the run stays `suspended` (FR-006).
- A Continue, or a session after a usage-limit wait, stopped before its first agent when an earlier session of the
  run did tasks: the run ends `stopped` with "stopped before its next task"; the stopped session adds no commit, and
  the earlier sessions' finished work lands as usual (`landed`) (FR-006, FR-017).

## Requirements *(mandatory)*

### Functional Requirements

**The run**
- **FR-001**: The thing the person starts MUST be a run: id (`YYYYMMDD-HHMM`), title, feature, scope (phases, tasks,
  or all open), limits, branch `auto/<run id>`, base branch and its commit at the start. A run MUST be started from
  the UI (**Start run**) or the command line with the refusals of 005 FR-003, plus "a run is suspended".
- **FR-002**: A run's stored state MUST be one of `working`, `suspended`, `ended`; the state the person sees MUST be
  one of `preparing` (working, no task started yet), `working`, `paused` (usage wait), `suspended`, `ended`. An
  ended run MUST record how it ended: `done` (scope finished), `stopped` (by the person), `limit` (which one),
  `waiting` (only the person's items left), `failed` (no progress, with the reason), `dropped` (ended by Drop), and
  its landing: `landed` (with the commit), `nothing to land`, or `needs merge` (with the files).
- **FR-003**: A run MAY use several sessions (a usage-limit wait, a crash followed by Continue); they share the run
  id, branch, scope, limits and record. Sessions MUST NOT be shown as separate runs: the run list has one entry per
  run id, and a run's sessions are listed inside it.
- **FR-004**: Limits (task cap, stop-by time, maximum sessions, default 20) MUST count over the whole run. Reaching
  one MUST stop the run after the current task (clean end, reason `limit`).

**Stopping and ending**
- **FR-005**: **Stop after this task** MUST let the current task finish (committed, or blocked after its retries)
  and its group's commits complete, start nothing else, and end the run cleanly. Pressed during `preparing` with a
  clean tree, it MUST end the run before any task starts, with the reason "stopped before any task" ("stopped
  before its next task" when an earlier session of the run has task entries); with uncommitted work in the tree (a
  resumed session's half-done task), the run stays `suspended` (FR-006, FR-007). When a task that started after
  the boundary has left uncommitted edits, they MUST be saved as a patch in that task's history and stashed, and the
  run MUST still end cleanly; that task is open again.
- **FR-006**: **Stop now** MUST stop at once and leave the run `suspended`, keeping `.sdd/run.json` and the tree.
  Exception: pressed while no agent of the run's current session has started (during `preparing`, in FR-032's start
  window, or before a resumed session's first agent) and with a clean tree, it MUST end the run `stopped` as Stop
  after this task does (FR-005): the reason is "stopped before any task", or "stopped before its next task" when an
  earlier session of the run has task entries; the stopped session adds no commit, and earlier sessions' finished
  work still lands. With uncommitted work in the tree, the run stays `suspended`.
- **FR-007**: A run MUST become `suspended`, not ended, whenever it is cut off with a task started but not committed
  or blocked: Stop now, a usage limit it does not wait for, a session that crashed, or an in-session run whose
  session closed. Cut off at a task boundary with a clean tree by a crash, a closed session or a usage limit it does
  not wait for, it MUST end cleanly instead. A stop the supervisor detects before any agent of the session has
  started follows FR-005 and FR-006 (ends with a clean tree). Stop now after an agent of the session has started, and
  FR-008's kept pause at the reset, MUST suspend the run whatever the tree holds. Stop after this task is not a
  cut-off (FR-005).
- **FR-008**: At a usage limit the run MUST pause and continue after the reset, as 005 FR-004/FR-004a do today
  ("Continue when the limit resets"); left unchecked at the reset, the run becomes `suspended`.

**Suspended runs**
- **FR-009**: A suspended run MUST offer **Continue** (resume from its checkpoint in the same run) and **Drop**. Wait
  needs no button: the run stays suspended until the person acts.
- **FR-010**: Drop MUST ask for confirmation naming the half-done task and the changed files, save the uncommitted
  diff into that task's history, restore the tree to the run's last commit (untracked files the run created
  included), remove `.sdd/run.json`, end the run (`dropped`) and land its finished tasks. The half-done task MUST
  become open again.
- **FR-011**: While a run is suspended and the base branch is still an ancestor of the run's last task commit, the
  finished tasks MUST land by fast-forwarding the base branch, without checking it out or touching the tree.
- **FR-012**: While a run is suspended, starting another run MUST be refused with "a run is suspended: Continue or
  Drop it first".

**Landing**
- **FR-013**: When a run ends, its task commits MUST land on its base branch with no click: fast-forward when
  possible, else one merge commit `merge: run <title>`. Never force, push or rewrite history. On a conflict the merge
  MUST be aborted, nothing changed, the run recorded `needs merge` with the files, and **Merge** offered to retry.
- **FR-014**: After landing, the checkout MUST be on the base branch. The run branch MAY be deleted once landed
  (its commits are on the base branch); a run that ended `needs merge` keeps its branch.
- **FR-015**: The dashboard's own commits (decisions, edits, manual tasks, approvals, feedback) MUST go to the
  checked-out branch: the run branch while a run is working or suspended, else the base branch.

**Task history**
- **FR-016**: Every task MUST have an append-only history in the repository, one file per task, written only by the
  plugin's helpers. Each entry: time, run id, session, attempt, step (started, implemented, verdict with problems,
  retried, committed with hash, blocked with reason, suspended, resumed, dropped with diff), agent role, model,
  duration and tokens when known, and the transcript path when one exists.
- **FR-017**: Task histories MUST be committed with the session's report commit, and with the run's history and
  summary commits when the run ends or is dropped, so every history is in the repository once its run has ended. A task's own commit
  holds only the task's files (constitution VI). The engine MUST NOT write a session report for a session with no
  task entries, so a session stopped before any task adds no commit on the run branch. A run none of whose sessions
  has task entries therefore lands `nothing to land`; a run whose earlier sessions have task entries still lands
  their commits, reports and histories (FR-005, FR-006, FR-032).
- **FR-018**: Task commits MUST carry trailers `Task: <id>`, `Run: <run id>`, `Attempts: <n>`, `Model: <implementer>`.
- **FR-019**: The UI MUST show a task's history on the task (008 FR-033's step events read from it), newest last,
  with its links to full transcripts while they exist. Nothing the person needs MUST depend on Claude's transcripts.

**Run summary**
- **FR-020**: When a run ends, its summary MUST be written to the repository once, after landing: times and
  duration, sessions, scope asked for, how it ended and its landing, tasks done, dropped and blocked (each with its
  id), attempts, approvals, models per role, the highest usage the supervisor saw per window, and a link to the
  plain-words report the run writes today.
- **FR-021**: The run list MUST show past runs from their summaries, newest first, each opening to its tasks (links
  to their histories). It MUST NOT offer actions on ended runs other than **Merge** for `needs merge` and **Land**
  for a landing still `pending` (US5, FR-025).

**Words, views and commands**
- **FR-022**: The UI MUST say "run" wherever 005 and 008 say "Job": the Jobs view becomes **Runs** (`#runs`; `#jobs`
  redirects), Start job becomes **Start run**, and the banner speaks of the run in words, never its id:
  "Working on <title>: <task>, <step>", "Paused until <time>: usage limit", "Suspended during <task>: <reason>.
  Continue or Drop", "Stopping after <task>", and after the end "<title> ended: <n> tasks landed on <base>".
- **FR-023**: Every action MUST also exist on the command line with the same refusals: start, stop after task, stop
  now, continue, drop, merge and land. On the command line, merge of a 009 run MUST do what Land does.
- **FR-024**: When a run ended `waiting` and the person settles an item that makes a task in that run's scope
  runnable, a new run with the same scope and limits MUST start by itself (005 FR-016 kept, now as a new run), but
  only when that run is the newest run of the project; an older `waiting` run never restarts.

**Carry-over**
- **FR-025**: Existing records MUST carry over without loss (US5): merged and dropped Jobs become past runs (their
  runs' tasks and reports joined), an open Job with `.sdd/run.json` becomes a suspended run, an open Job without it
  becomes an ended run offered **Land**. Old run reports stay where they are and are linked from the past run.
- **FR-026**: Task histories for tasks done before this feature MAY be built once from the run reports, commits and
  any transcripts still present; tasks without such data show "no history before <date>".

**Starting runs**
- **FR-027**: Runs MUST be started through the supervisor (`bin/sdd-launch.py`, the dashboard's Start run or the
  command line); the usage-limit pause lives only there. `/sdd-run` MUST NOT start a supervisor. When `/sdd-run` runs
  without `SDD_SUPERVISED=1`, it MUST say before the run starts that the run has no usage-limit protection and that
  runs are started with the launcher or Start run, then go on.

**Task summaries**
- **FR-028**: A task in `tasks.md` MAY have a plain-language summary on the indented line right below it (one
  sentence, no file paths or function names). `/speckit-tasks`, through the plugin's tasks template, MUST write one
  for every task. `bin/sdd-tasks.py` MUST parse it as the task's `summary` and MUST NOT treat it as part of the task
  text, its files or its dependencies; a task without one has `summary: null`. Plain Spec Kit still reads the file.
- **FR-029**: The task card MUST show the summary as the task's main text and the exact task text behind an
  expander ("Details"), and the Start run form's task list MUST show the summary; a task without a summary shows its
  exact text as today.
- **FR-030**: 009's `tasks.md` MUST have a summary for every task, done ones included. 005's and 008's MUST NOT be
  changed for this.

**Run-control trace**
- **FR-031**: Every run-control request (stop after this task, stop now, resume, hold, release), from the dashboard
  or the command line, MUST leave one line in a control log in the supervisor's state directory: the time, the run id
  (when there is a run), the command, the stop mode, where it came from, and the outcome: accepted (with the request's id) or refused (with the
  reason the person was shown). When the supervisor takes a request it MUST add a line with the same id saying what it
  did (stopping after the current task, stopping now, stopping before any task, or ignored, with why). Nothing in the
  project tree changes, and a failing log write MUST NOT stop the request itself.
- **FR-032**: From Start run until the new supervisor has written its state, Stop after this task and Stop now MUST
  be accepted, not refused: the request carries the run id the launcher created. At startup the supervisor MUST carry
  out a stop request with its own run id (with a clean tree the run ends `stopped` with FR-006's reason and the
  stopped session adds no commit; with uncommitted work in the tree the run stays `suspended`, FR-006) and MUST
  delete, without carrying it out, any request with another run id or none, and any request for its own run that is
  not a stop, logging it as ignored with its id, run id (or none), command and why (another run, no run id, or only
  stop is taken at startup) (FR-031).
- **FR-033**: While a run-control request from the banner is unanswered, the pressed button MUST read "sending…" and
  the banner's control buttons MUST be disabled. A request with no answer after 10 seconds MUST be abandoned and
  shown like a failed one (the existing `request failed: <error>` on the banner and in a toast, here with "no answer
  after 10 s"), and the buttons work again; the browser MUST NOT retry by itself. A request the server never received
  is not in the control log (FR-031), so this banner error is its only trace.
- **FR-034**: A run's card in the Runs view, live or past, MUST show a **Requests** list read from the control log:
  one line per request of that run, oldest first, with its time, command (and stop mode), where it came from,
  accepted or refused (with the reason), and what the supervisor did with it, or "not taken yet". A run with no
  requests shows no list. The control log file stays the only store; nothing is added to the committed run summary.

### Key Entities

- **Run**: id, title, feature, scope, limits, branch, base (branch and commit), state, sessions, how it ended,
  landing, created and ended times. Live state in `.sdd/run.json` (only while working, paused or suspended); the
  summary in the repository after the end.
- **Session**: one process within a run; start and end times, why it ended (usage limit, crash, closed, finished).
- **Task history**: one append-only file per task in the repository; entries as FR-016.
- **Task summary**: the optional indented line below a task in `tasks.md` (FR-028).
- **Run summary**: one file per ended run in the repository; fields as FR-020.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After Stop after this task, the finished tasks are on the base branch without any click, and a new run
  can start at once; covered by an end-to-end test with the fake `claude`.
- **SC-002**: After Drop, the tree is clean, the finished tasks are on the base branch, the half-done task is open,
  and its history holds the discarded diff; covered by tests.
- **SC-003**: Every step of every task in a run is in that task's history; after the run's transcripts are deleted,
  the task view still shows all attempts, verdicts and commits; covered by a test.
- **SC-004**: Landing never changes anything on a conflict or refusal; covered by tests (005 SC-005 kept).
- **SC-005**: The 2026-11-06 situation (stopped after a task, Merge refused "a run is active") cannot happen: a run
  that stopped at a task boundary is ended and landed; one that did not is suspended with Continue and Drop.
- **SC-006**: Upgrading with the person's existing `.sdd/jobs/` and `.sdd/runs/` loses no record and strands no
  work (US5 scenarios pass on a copy of this repository's `.sdd/`).
- **SC-007**: Every task card of 009 reads as a plain sentence, with the exact task text one click away.
- **SC-008**: A `/sdd-run` typed into a session warns that it has no usage-limit protection before any agent starts.
- **SC-009**: Pressing Stop after this task on the banner while a run shows Preparing ends that run as `stopped` with
  "stopped before any task", nothing landed and no task ticked, within 15 seconds, and the control log holds the
  accepted request and the supervisor's line for it; the same holds when it is pressed right after Start run, before
  the supervisor has written its state (FR-032); covered by a browser test with a real dashboard, launcher and
  supervisor and the fake `claude`.

## Out of Scope

- Running several runs at once on one project (007 parallelism).
- Rebasing run commits onto a moved base branch (history is never rewritten; a merge commit is used).
- Pushing to a remote.
- Changing how a task is implemented or verified (003's workflow stays; only what it records changes).

## Assumptions

- The checkout is shared: while a run is working or suspended it is on the run branch, as today (005 FR-008).
- Task commits stay one per task, made by the bookkeeper (003).
- Claude's transcripts may be deleted by Claude Code after its retention period; nothing durable depends on them.
- The person accepts that a run's commits land without review; they can still undo a landing with git.

## Effect on other features

- **005-jobs**: FR-001 to FR-017 are replaced by this feature's run (FR-001 to FR-015, FR-022 to FR-025). Merge and
  Drop of a Job become landing and Drop of a suspended run. T022 (the person's real Job test) is superseded by this
  feature's validation.
- **008-standalone-server**: views and routes that say Job are renamed (FR-022): FR-005, FR-007, FR-009, FR-033,
  FR-034 (Start run), T040 (`views/Jobs.tsx` → `views/Runs.tsx`), T065–T069 (`--job` flags). US4 hand-over continues
  a suspended in-session run (this feature's FR-007, FR-009) instead of resuming a run that is merely left over. 008
  Phases 5 and 6 build on run state and should follow this feature; build 009 before them.
- **003-plugin-redesign**: the workflow's `interrupted` result and the supervisor's stop request gain the
  task-boundary rule (FR-005, FR-007); `/sdd-run`'s resume is kept for Continue.
