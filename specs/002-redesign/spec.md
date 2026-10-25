# Feature Specification: Speckit-agents Redesign (v0.2)

**Feature Branch**: `002-redesign` | **Created**: 2026-10-24 | **Status**: proposed

## Source of Backlog Items

Every requirement below is traced to a backlog item recorded in `logs/2026-10-14-language-app.md`
(language-app runs T203, T209–T210, T213, T239, T240, T243–T247, T249–T251, T260, T262, T264–T265).

## User Scenarios

### User Story 1 - Run unattended with no lost work at usage limits (P1)

A developer starts `/sdd-run` and walks away. When usage limits approach, the run pauses at a safe
point and resumes after the reset, losing no work. Every task is implemented, verified and committed
on an `auto/` branch; anything needing a person is listed with one question.

**Acceptance**:
1. Given a clean tree with passing checks, when `/sdd-run` runs, then each approved task is one commit
   on `auto/<run id>` and its box is ticked.
2. Given a usage-limit pause mid-agent, when the window resets, then `/sdd-run` resumes from the
   checkpoint without re-doing finished steps.
3. Given a task tagged `[manual]`, `[decision]`, `[dep]` (not allowed) or `[download]` (not allowed),
   the run never attempts it and lists it as "waiting on you" with its question.
4. Given a task whose dependency (`per Tnnn` / `after Tnnn`) is held, the dependent task is held too.
5. Given a group whose tasks pass alone but break the project checks together, one repair is tried;
   if still failing, the work is stashed and the tasks are blocked.
6. The run never pushes, switches branches, rewrites history, commits on a protected branch or edits
   a protected path.

### User Story 2 - See what waits on you, in one place (P1)

After a run, the user knows exactly what needs their attention: held tasks, blocked tasks, and
decisions — each with its one actionable question.

**Acceptance**:
1. The run report and `/sdd-status` list every held task as "waiting on you" with its question.
2. `/sdd-unblock` covers held tagged tasks, not only BLOCKED.md entries.
3. Decisions are recorded in the feature's `decisions.md` register.

### User Story 3 - Converge only what still needs building (P2)

After `/speckit-converge`, the original tasks stay unticked but converge does not re-add the same gaps.
Only runnable tasks count for the converge-again rule.

**Acceptance**:
1. Converge runs again when the newest Convergence phase has open tasks but none runnable.
2. Original tasks superseded by a Convergence task (`per Tnnn (partial)`) are skipped by `plan`.

## Requirements

| ID | Requirement | Backlog Item |
|----|-------------|--------------|
| FR-001 | Parse Spec Kit `tasks.md`, including cross-task references (`after Tnnn`, `per Tnnn`, `needs Tnnn`, …) as dependencies. | 15, 20 |
| FR-002 | Hold a task when tagged for a person, when its BLOCKED.md entry has a terminal status, or when a dependency is held. | 16, 17 |
| FR-003 | List every held task as "waiting on you" in the run report, `/sdd-status` and `/sdd-unblock`. | 16 |
| FR-004 | Write an event feed (`.sdd/runs/<run id>/events.jsonl`) for monitoring; usage windows written to `status.json` every 5%. | 1, 10, 21, 30 |
| FR-005 | Pause before starting a new agent when a usage window nears its threshold; resume from the checkpoint after the reset. | 12, 12a |
| FR-006 | Grant dependencies/downloads to an individual task via `[dep:ok]`/`[download:ok]` without turning the global `[allow]` on. | 13, 29 |
| FR-007 | Give each agent a git-ignored scratch dir `.sdd/scratch/<run id>/<task id>/` for throw-away scripts. | 23 |
| FR-008 | The verifier runs the project's lint/type-check/test commands before approving. | 9 |
| FR-009 | Attribute unclaimed changes after a task's commit to that task when the verifier's diff covered them. | 25 |
| FR-010 | When `[run] isolation = "worktree"`, run each task of a parallel group in its own worktree; the bookkeeper merges them onto the run branch in task order. | 14 |
| FR-011 | `[ui]` tasks are verified by build and tests (Playwright outside the sandbox) and listed for a person to look at; browser-bound work is held for a person. | 6 |
| FR-012 | `[manual]` tasks get "how-to" steps (where to do it, commands, what "done" looks like) written by converge/sdd-tag or the run report. | 18 |
| FR-013 | Flag `[manual]` validation tasks invalidated by pending spec changes; order them after pending decisions/amendments. | 2 |
| FR-014 | `[decision]` task lines need options and trade-offs, not just the question; evidence linked next to the question. | 4 |
| FR-015 | Numbers in reports/summaries computed from artifacts (not memory). | 3 |
| FR-016 | Keep user-visible requirements in the task that owns its page; name the page file in backend tasks. | 22 |
| FR-017 | Spec Kit tasks gain a `moved`/`product` state (apart from `wontfix`) for work moved into product use; shown apart in report and `/sdd-status`. | 24 |
| FR-018 | For `[ui]` and admin tasks, the verifier (or phase-end reviewer) walks the user flow end to end ("can the admin actually do X from here?"). | 28 |
| FR-019 | Shell gotchas included in agent rules (e.g. zsh `$B:c…` modifier read as zsh modifier; use `"${B}:path"`). | 5 |

## Success Criteria

- **SC-001** On a toy project with two parallel tasks, one manual task and one dependent task, a headless
  run commits the code tasks, holds the manual one, and leaves `main` untouched (automated in `tests/e2e/`).
- **SC-002** Guard tests cover every refused command class and the main-loop exemption.

## Out of Scope (for now)

- Project dashboard UI backed by a server (backlog item 11): deferred to a separate spec→plan→tasks cycle.
  The event feed (FR-004) is the monitoring foundation; a dashboard can consume it later.
- Phone notifications (backlog item 1/10): deferred; the event feed and `/sdd-status` are the notification foundation.
