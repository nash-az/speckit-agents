# Contract: Dashboard HTTP API

`bin/sdd-dashboard.py`. Binds `127.0.0.1` only; every request whose `Host` (without port) is not
`127.0.0.1`, `localhost` or `[::1]` gets `421` (FR-022). No route writes to disk (FR-020).
Implementation details: `data-model.md` §4.

| Method + path | Status in 003 | Response |
|---|---|---|
| `GET /` | unchanged | the dashboard page |
| `GET /api/projects` | changed (FR-015) | `[{"path", "name"}]` — only the project the server was started with |
| `POST /api/project/select` | extended (FR-027) | body `{"path"?, "feature"?}`; sets in-memory project/feature only; `{"ok": true, "path", "feature"}` |
| `GET /api/doc?name=<name>` | new (FR-001) | `name` ∈ `spec, plan, research, data-model, quickstart, tasks, blocked, decisions, constitution, learnings, config`; raw text (`text/markdown`, `config` as `text/plain`); missing file → `200 {"missing": true}`; other names → `404` |
| `GET /api/project/spec`, `/plan`, `/decisions`, `/config` | kept as aliases | same as `/api/doc` with that name |
| `GET /api/project/tasks` | changed (FR-002, FR-007) | `{"text", "phases": [{"id", "title", "status", "tasks": [Task]}]}` — Task per `data-model.md` §2; phase `status` is the §1 rollup of its tasks' parked/done states only; the page recomputes it with live transient states |
| `GET /api/project/blocked` | changed | `{"text", "entries": [{"id", "status", "tag", "reason", "question", "answer"}]}`, last entry per id |
| `GET /api/run/current` | new (FR-017) | `{"state": "none"\|"running"\|"paused"\|"stale", "run_id", "workflow_dir"}` |
| `GET /api/runs` | changed (FR-006) | runs from `.sdd/runs/<id>/` directories, newest id first; item shape unchanged (`id`, `events_count`, `approved`, `blocked`, `interrupted`, `finished`, `stopped`) |
| `GET /api/runs/<id>/events` | changed (FR-006) | events from `.sdd/runs/<id>/events.jsonl` |
| `GET /api/runs/<id>/report` | unchanged path, id validated | run report markdown |
| `GET /api/runs/<id>/tasks/<task>/log` | new (FR-021) | `{"available": true, "label", "lines": [...]}` or `{"available": false, "reason"}` — always 200 |
| `GET /api/status` | unchanged | supervisor `status.json` |

Per-feature routes return `409 {"error": "choose a feature", "features": [...]}` when no active
feature can be determined (FR-027). Ids in paths are validated: run id
`^[0-9]{8}-[0-9]{4}[a-z0-9-]*$`, task id `^T\d+[a-z]?$`; anything else → 404.
