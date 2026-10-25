# Tasks: Project Dashboard UI (T701)

## Phase 1: Server (`bin/sdd-dashboard.py`)

- [ ] D101 Python stdlib HTTP server on configurable port (default 8765)
- [ ] D102 GET /api/runs: list runs from `.sdd/runs/` with status, branch, counts
- [ ] D103 GET /api/runs/<id>/events: read `events.jsonl` for a run
- [ ] D104 GET /api/status: read `status.json` from supervisor state dir
- [ ] D105 GET /api/runs/<id>/report: read `.sdd/runs/<run id>.md`
- [ ] D106 Read-only: no writes to project or state dirs

## Phase 2: Frontend

- [ ] D201 Single-page HTML/JS dashboard at `/`
- [ ] D202 List runs with status, counts, stopped-early flag
- [ ] D203 Click a run to see its event feed
- [ ] D204 Show usage windows (utilization %, reset times)
- [ ] D205 Show held/blocked tasks ("waiting on you")
- [ ] D206 Auto-refresh polling

## Phase 3: Tests

- [ ] D301 Unit tests: server starts, endpoints return correct data
- [ ] D302 E2E: dashboard shows a run's events

## Dependencies

Depends on T301 (event feed) being implemented in `workflows/sdd-run.js`.
