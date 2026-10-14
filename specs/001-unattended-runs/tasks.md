# Tasks: Unattended runs

## Phase 1: Setup

- [x] T001 Plugin manifest and marketplace in `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`
- [x] T002 Spec Kit init and constitution in `.specify/memory/constitution.md`

## Phase 2: Foundational

- [x] T003 [P] Task parser, planner, tick and block helpers in `bin/sdd-tasks.py` with tests in `tests/test_sdd_tasks.py` (FR-001–FR-003)
- [x] T004 [P] Run guard in `hooks/register.ts`, `hooks/rules.ts` with tests in `tests/guard.test.ts` (FR-007)
- [x] T005 [P] Templates in `templates/sdd-config.toml`, `templates/settings.json`, `templates/agent-tags.md`

## Phase 3: User Story 1 - Finish the open tasks without supervision (Priority: P1)

- [x] T006 [P] [US1] Agents in `agents/sdd-implementer.md`, `agents/sdd-verifier.md`, `agents/sdd-evaluator.md`, `agents/sdd-bookkeeper.md`
- [x] T007 [US1] Workflow in `workflows/sdd-run.js` (FR-004–FR-006, FR-009)
- [x] T008 [US1] Skill in `skills/sdd-run/SKILL.md` (FR-008, FR-010)
- [ ] T009 [US1] [eval] End-to-end check SC-001: build a toy project fixture in `tests/e2e/make_toy.sh`, run `/sdd-run --yes` headless, assert commits, BLOCKED.md and `main` in `tests/e2e/check_toy.sh`; record the result in `specs/001-unattended-runs/research.md`
- [ ] T010 [US1] Worktree isolation for parallel groups: when `[run] isolation = "worktree"`, run each task of a group in its own worktree (`isolation: 'worktree'`) and have the bookkeeper merge them onto the run branch in task order in `workflows/sdd-run.js`
- [ ] T011 [US1] [decision] Browser check for `[ui]` tasks: which tool (claude-in-chrome, Playwright) and when it may run unattended

## Phase 4: User Story 2 - Tag tasks (Priority: P1)

- [x] T012 [P] [US2] Skills in `skills/sdd-init/SKILL.md` and `skills/sdd-tag/SKILL.md`

## Phase 5: User Story 3 - Answer what blocked the run (Priority: P2)

- [x] T013 [US3] Skill in `skills/sdd-unblock/SKILL.md`

## Phase 6: User Story 4 - See where things stand (Priority: P3)

- [x] T014 [US4] Skill in `skills/sdd-status/SKILL.md`

## Dependencies & Execution Order

Phase 2 depends on Phase 1. Phases 3–6 depend on Phase 2. Phases 4–6 do not depend on Phase 3.
