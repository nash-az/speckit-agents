# Tasks: Speckit-agents Redesign (v0.2)

## Phase 1: Spec & plan

- [x] T101 Design doc: `specs/002-redesign/design.md` (captured lessons from `logs/2026-10-14-language-app.md` and `CLAUDE.md`)
- [x] T102 Spec: `specs/002-redesign/spec.md` (covers all backlog items, traced by backlog item ID)
- [x] T103 Plan: `specs/002-redesign/plan.md`
- [x] T104 Tasks: `specs/002-redesign/tasks.md`

## Phase 2: Deterministic helpers (`bin/sdd-tasks.py`)

- [x] T201 Parse cross-task references (`after Tnnn`, `per Tnnn`, `needs Tnnn`, …) as `deps`
- [x] T202 Hold tasks with terminal BLOCKED.md statuses (`resolved`, `wontfix`, `open`)
- [x] T203 Propagate holds through dependencies (fixpoint iteration)
- [x] T204 Hold tasks superseded by converge (`superseded-by` → `per Tnnn (partial)`)
- [x] T205 Tests: dependency parsing, hold propagation, terminal-status holds, superseded holds

## Phase 3: Workflow (`workflows/sdd-run.js`)

- [x] T301 Event feed: `.sdd/runs/<run id>/events.jsonl` + usage windows to `status.json` every 5%
- [x] T302 Credit-aware pause: `budgetOK()` stops before starting a new agent near the limit
- [x] T303 Per-task dependency/download approval via `taskAllow` (`[dep:ok]`/`[download:ok]`)
- [x] T304 Worktree isolation: `[run] isolation = "worktree"` runs each task of a parallel group in its own worktree; bookkeeper merges onto run branch in task order
- [ ] T305 Tests: event feed, credit-aware pause, worktree merge — superseded-by T012 (specs/003-plugin-redesign: worktree and event feed removed; credit-pause tests exist)

## Phase 4: Agents

- [x] T401 Implementer: scratch dir usage, dep check, credit awareness, `[dep:ok]`, shell gotchas
- [x] T402 Verifier: runs project checks before approving; walks user flow end to end for `[ui]`/admin
- [x] T403 Evaluator: scratch dir, dep check, credit awareness
- [x] T404 Bookkeeper: credit awareness, attribute unclaimed changes to last task

## Phase 5: Skills

- [x] T501 `sdd-run`: `eventsDir` + `taskAllow` + `isolation` in workflow args; held tasks as "waiting on you" in confirmation and report; decision options/trade-offs; manual how-to steps
- [x] T502 `sdd-status`: list held tasks as "waiting on you" with questions; show `moved`/`product` state apart
- [x] T503 `sdd-unblock`: cover held tagged tasks; decisions register in `<feature>/decisions.md`
- [x] T504 `sdd-tag`: write how-to steps for `[manual]` tasks; options/trade-offs for `[decision]` tasks

## Phase 6: Open tasks from v0.1

- [x] T601 T009: e2e test script (`tests/e2e/make_toy.sh`, `tests/e2e/check_toy.sh`)
- [x] T602 T010: worktree isolation (implemented in T304)
- [x] T603 T011: browser tool decision for `[ui]` (Playwright outside the sandbox; held for person)

## Phase 7: Out of scope this cycle

- [ ] T701 Project dashboard UI backed by server (backlog item 11) — deferred; event feed (T301) is the foundation — superseded-by T047 (specs/003-plugin-redesign: dashboard built)
- [ ] T702 Phone notifications (backlog item 1/10) — deferred; event feed is the foundation

## Dependencies & Execution Order

Phase 2 depends on Phase 1. Phases 3–7 depend on Phase 2.
