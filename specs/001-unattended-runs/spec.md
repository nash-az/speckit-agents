# Feature Specification: Unattended runs

**Feature Branch**: `001-unattended-runs` | **Created**: 2026-10-14 | **Status**: v0.1 implemented

## User Scenarios

### User Story 1 - Finish the open tasks without supervision (P1)
A developer with a Spec Kit `tasks.md` starts one command and walks away. When they come back, every task that could be done is implemented, verified and committed on an `auto/` branch, and every task that needs them is listed with one question.

**Acceptance**:
1. Given a clean tree with passing checks, when `/sdd-run` runs, then each approved task is one commit on `auto/<run id>` and its box is ticked.
2. Given a task tagged `[manual]`, the run never attempts it and the run report lists it as held.
3. Given a task the verifier rejects more than `max_retries` times, it is recorded in BLOCKED.md with the verifier's reasons, and later tasks of its phase that are not `[P]` are skipped.
4. Given a group whose tasks pass alone but break the project checks together, one repair is tried; if the checks still fail, the work is stashed and the tasks are blocked.
5. The run never pushes, switches branches, rewrites history, commits on a protected branch or edits a protected path.

### User Story 2 - Tag tasks so the run knows what needs a person (P1)
`/speckit-tasks` writes agent tags; `/sdd-tag` adds them to an existing tasks.md.

### User Story 3 - Answer what blocked the run (P2)
`/sdd-unblock` walks BLOCKED.md, records answers, ticks manual tasks the user did, lifts holds, and sends spec-changing decisions through the spec first.

### User Story 4 - See where things stand (P3)
`/sdd-status` reports open, held, blocked and runnable tasks, the active run and the last report.

## Requirements

- **FR-001** Parse Spec Kit `tasks.md` (phases `## Phase N:`, tasks `- [ ] T### [P] [USn] [tags] …`, backticked file paths) deterministically.
- **FR-002** Group consecutive `[P]` tasks with disjoint, known files to run in parallel; everything else runs alone, in file order.
- **FR-003** Hold tasks tagged `manual`, `decision`, and `dep`/`download` unless allowed; hold tasks with an open BLOCKED.md entry.
- **FR-004** Per task: implementer (or evaluator for `[eval]`) → read-only verifier, retried with the verifier's feedback up to `max_retries`.
- **FR-005** Per group: run the project checks; commit each approved task alone with only its files; record blocked tasks via the helper.
- **FR-006** Phases run in order; a phase whose dependency phase has an attempted-and-failed task is skipped. Held tasks do not taint a phase.
- **FR-007** While `.sdd/run.json` exists, a hook guard refuses destructive git, commits on protected branches and writes to protected paths for every subagent; the main loop may write `.sdd/`.
- **FR-008** Preflight refuses to start on a dirty tree or red checks, and warns about untagged tasks that need a person.
- **FR-009** The run stops early when the token budget runs low and can resume from the workflow journal.
- **FR-010** Every run writes `.sdd/runs/<run id>.md` and notifies the user.

## Success Criteria

- **SC-001** On a toy project with two parallel tasks, one manual task and one dependent task, a headless run commits three tasks, holds the manual one, and leaves `main` untouched.
- **SC-002** Guard tests cover every refused command class and the main-loop exemption.
