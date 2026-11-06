# speckit-agents Constitution

## Core Principles

### I. Spec Kit stays the source of truth
speckit-agents layers on Spec Kit; it never forks it. Specs, plans, tasks and the constitution are Spec Kit's files in Spec Kit's formats. Extensions go through supported hooks only (template overrides, extra bracket tags, Claude Code plugin parts). A project without the plugin must still work with plain Spec Kit.

### II. Safe without a person watching
Every unattended action is bounded by mechanisms that hold without the model's cooperation: sandbox and permission rules, the run guard hooks, an isolated `auto/` branch. Nothing pushes, rewrites history, touches protected branches or edits the constitution or specs during a run. Guards fail closed. Work is never deleted: failed work is stashed.

One exception, for the engine only: the plain-code engine (`bin/sdd-job.py`), never an agent, may land a run's approved, committed work on the run's base branch, even when the base is protected. It may do this when the run ends, and by fast-forward while the run is suspended. It lands by fast-forward or a plain merge commit, never by rebase or force. On a conflict it aborts the merge and leaves the base unchanged. After landing, the engine may also commit the ended run's summary and task-history files (only those files) on the base. Agents stay unable to touch protected branches during a run.

### III. Never guess on a person's behalf
Work that needs a person (manual steps, open decisions, new dependencies, large downloads) is parked in BLOCKED.md with one actionable question, never improvised. The run continues with what it can do and stops when nothing else is possible.

### IV. Verified, not reported
A task is done only when an independent read-only verifier approves it and the project checks pass. Implementers never mark their own work done. Skipped, weakened or stubbed tests are rejections. Measured numbers are reported as measured.

### V. Deterministic where possible
Parsing, planning, ticking and recording are done by plain code (`bin/sdd-tasks.py`, the workflow script), not by model judgement. Models do the work that needs judgement: implementing, verifying, tagging.

### VI. One task, one commit
Each approved task is one commit holding only its own files, so a run can be reviewed, reverted or cherry-picked task by task.

### VII. AI-free at the core
The orchestration engine — task parsing, planning, the run guard, status and state, and anything a dashboard or CLI reports — runs and is fully usable with no AI model involved anywhere in that path. An AI-backed surface, such as a chat interface or a free-form query layer, is an optional client of the same backend the non-AI dashboard and CLI use; it is never a dependency of the plugin's core function, and the plugin stays fully operable with it absent. This is distinct from Principle V: V says where model judgement belongs inside a run; this says the plugin's own architecture never requires a model to exist at all.

## Development

The framework is built with itself: Spec Kit for spec, plan and tasks; `/sdd-run` for implementation once the guard and agents are in place. Runs are started through the supervisor (the launcher or the dashboard's Start run), which owns the usage-limit pause; `/sdd-run` is the supervisor's single cycle and is not typed into a session to start a run. Tests: `python3 -m unittest discover -s tests` and `claude plugin test .`; both must pass before a commit.

**Version**: 1.2.1 | **Ratified**: 2026-10-14 | **Last Amended**: 2026-11-06
