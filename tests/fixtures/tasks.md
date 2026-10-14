# Tasks: Example

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [x] T001 Create project structure in `src/`
- [ ] T002 [P] Add lint config in `pyproject.toml`
- [ ] T003 [P] Add CI workflow in `.github/workflows/ci.yml`

## Phase 2: Foundational

- [ ] T004 [P] Model A in `src/models/a.py`
- [ ] T005 [P] Model B in `src/models/b.py` and `src/models/a.py`
- [ ] T006 Service in `src/services/s.py`

## Phase 3: User Story 1 - Do the thing (Priority: P1)

### Tests for User Story 1

- [ ] T007 [P] [US1] Contract test in `tests/contract/test_x.py`
- [ ] T008 [US1] [manual] Record the reference samples (protocol in `docs/PROTOCOL.md`)
- [ ] T009 [P] [US1] [eval] Measure accuracy with `svx eval accuracy` in `src/eval/acc.py`
- [ ] T010 [US1] [dep] Add yt-dlp fetching in `src/media/fetch.py`
- [ ] T011 [US1] Wire it up with no file named

## Phase 3a: User Story 2 - Other

- [ ] T012 [P, US2] Thing in `src/x.py` → `src/y.py`

## Dependencies & Execution Order

- [ ] T999 not a task: outside any phase
