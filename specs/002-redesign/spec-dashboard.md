# Feature Specification: Project Dashboard UI (v0.2)

**Feature Branch**: `002-redesign` | **Created**: 2026-10-24 | **Status**: proposed

## User Scenarios

### User Story 1 - Watch an unattended run live (P1)

A developer started `/sdd-run` (or `sdd-supervise.py`) and wants to see what's happening without reading agent transcripts or tailing log files.

**Acceptance**:
1. Open `http://localhost:8765` and see a dashboard listing all runs for the current project.
2. For each run: status (running / paused / done / stopped), branch, number of approved tasks, blocked tasks, held tasks, and whether it stopped early.
3. Click a run to see its event feed (task started / implemented / verified / approved / blocked / skipped / interrupted) in real time.
4. See usage windows (five-hour, seven-day) with utilization % and reset times when a supervisor is managing the project.
5. Refresh auto-updates (polling) so the dashboard stays live while a run is in progress.

### User Story 2 - See what waits on you (P1)

The user wants to know what needs their attention across all features/runs.

**Acceptance**:
1. The dashboard lists held tasks across all runs as "waiting on you" with their tags and questions.
2. Blocked tasks are listed with their questions.
3. Decisions that need answers are highlighted.

## Requirements

- **FR-D-001** A Python stdlib HTTP server (`bin/sdd-dashboard.py`) serving on a configurable port (default 8765).
- **FR-D-002** The server reads `.sdd/runs/` inside the project for event feeds and reports.
- **FR-D-003** The server reads `status.json` from the supervisor state dir (`~/.local/state/speckit-agents/<slug>/`).
- **FR-D-004** API endpoints: `GET /api/runs`, `GET /api/runs/<id>/events`, `GET /api/status`, `GET /api/runs/<id>/report`.
- **FR-D-005** A single-page HTML/JS frontend that polls the API and shows runs, events, usage, and held/blocked tasks.
- **FR-D-006** No external dependencies: stdlib only, consistent with the rest of the plugin.
- **FR-D-007** The server is read-only: it never writes to the project or state dirs.

## Success Criteria

- **SC-D-001** `bin/sdd-dashboard.py --port 8765` starts and serves `http://localhost:8765/`.
- **SC-D-002** The dashboard lists runs from `.sdd/runs/` and shows their events from `events.jsonl`.
- **SC-D-003** The dashboard shows usage windows from `status.json` when present.

## Out of Scope

- WebSockets or server-sent events: polling is sufficient.
- Multi-project support: one server instance serves one project (the CWD or `--project`).
- Authentication: the dashboard runs on localhost only.
