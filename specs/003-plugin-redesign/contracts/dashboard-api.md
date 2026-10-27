# Contract: Dashboard HTTP API

`bin/sdd-dashboard.py`. Binds `127.0.0.1` only; every request whose `Host` (without port) is not
`127.0.0.1`, `localhost` or `[::1]` gets `421` (FR-022). No route writes to the project (FR-020); the only write is the supervisor control
file behind `/api/run/stop` and `/api/run/resume` (FR-029). Every `POST` also needs an allowed
`Origin` and a JSON content type (FR-030, `data-model.md` §4.9).
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
| `GET /api/run/current` | new (FR-017), extended (FR-029) | `{"state": "none"\|"running"\|"paused"\|"stale", "run_id", "workflow_dir", "by"?: "operator", "stopping"?: true, "controls": {"stop": bool, "resume": bool, "hold": bool, "release": bool, "reason": str\|null}}` — `data-model.md` §12.3 |
| `POST /api/run/stop` | new (FR-029) | body `{"mode"?: "after-task"\|"now"}`; `202 {"ok": true, "id"}`; `409 {"error"}` when not available (no live supervisor, waiting, already stopping, request pending); `400` bad mode; `403`/`415` per §4.9 |
| `POST /api/run/resume` | new (FR-029) | no body fields; `202 {"ok": true, "id"}` when the supervisor is `held`, else `409 {"error"}` |
| `POST /api/run/hold` | new (FR-029) | no body fields; `202 {"ok": true, "id"}` only while the supervisor waits for a usage window and `keep_paused` is false, else `409 {"error"}`; the supervisor then ends the wait `held` instead of restarting |
| `POST /api/run/release` | new (FR-029) | no body fields; `202` only while waiting with `keep_paused` true, else `409`; cancels the hold |
| `GET /api/runs` | changed (FR-006) | runs from `.sdd/runs/<id>/` directories holding `workflows.json`, newest id first; item shape unchanged (`id`, `events_count`, `approved`, `blocked`, `interrupted`, `finished`, `stopped`) |
| `GET /api/runs/<id>/events` | changed (FR-006) | `run_events()` (data-model.md §3.9): events derived from the workflow journals named in `.sdd/runs/<id>/workflows.json` |
| `GET /api/runs/<id>/report` | unchanged path, id validated | run report markdown |
| `GET /api/runs/<id>/tasks/<task>/log` | new (FR-021) | `{"available": true, "label", "lines": [...]}` or `{"available": false, "reason"}` — always 200 |
| `GET /api/status` | unchanged | supervisor `status.json` |

Per-feature routes return `409 {"error": "choose a feature", "features": [...]}` when no active
feature can be determined (FR-027). Ids in paths are validated: run id
`^[0-9]{8}-[0-9]{4}[a-z0-9-]*$`, task id `^T\d+[a-z]?$`; anything else → 404.
