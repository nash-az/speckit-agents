# Contract: server API (008)

Base address: `http://127.0.0.1:<port>`, localhost only. Every response carries the header `X-SDD-Contract: 1.0`.

Request checks (data-model §10): every request needs an allowed Host (421 otherwise). Every POST needs an allowed
Origin (403) and `Content-Type: application/json` (415). `GET /api/fs/list` and `GET /api/plugin` reject a foreign
Origin or a cross-site `Sec-Fetch-Site` (403), and accept a missing Origin.

## Server-wide

| Method and path | Body | Answer |
|---|---|---|
| GET `/api/server` | | data-model §11 |
| GET `/api/settings` | | `{"max_parallel"}` |
| POST `/api/settings` | `{"max_parallel": int ≥ 1}` | 200 `{"max_parallel"}`, or 400 (also for any other key: `plugin_source` and `claude_command` are file-only) |
| GET `/api/projects` | | `{"projects": [{"id", "path", "name", "missing", "state", "origin", "job"}]}`; `state` and `origin` from data-model §4 |
| POST `/api/projects/add` | `{"path"}` | 200 the entry, 200 `{"needs_setup", "plan"}`, or 422 (data-model §10) |
| POST `/api/projects/<id>/remove` | `{}` | 200 `{"ok": true}`; never touches the project |
| GET `/api/fs/list?path=&hidden=` | | data-model §10 |
| GET `/api/plugin` | | data-model §13 |
| POST `/api/plugin/install`, `/api/plugin/update` | `{}` or `{"accept_command"}` | 200 `{"ok", "output", "version"}`, or 409 |
| POST `/api/setup/detect`, `/api/setup/check`, `/api/setup/apply`, `/api/setup/specify` | data-model §12 | 200, 403, 409, 422 |
| GET `/api/slots` | | `{"max_parallel", "holders": [{"project", "run_id", "since", "kind": "supervised|session"}], "queued": [...]}` |
| GET `/api/stream` | | `text/event-stream`. Each event is `data: {"project", "topic", "at"}`, plus `: ping` every 15 s |

## Per project: `/api/p/<id>/…`

Every route of the current dashboard (003 `contracts/dashboard-api.md`, 005 `contracts/jobs-api.md`) is kept, with
the same bodies and answers, under the prefix `/api/p/<id>`. For example, `GET /api/runs` becomes
`GET /api/p/<id>/runs`, and `POST /api/run/stop` becomes `POST /api/p/<id>/run/stop`.

- An unknown `<id>` → 404. A missing project → 410 `{"error": "project folder is missing"}`.
- `GET /api/projects` and `POST /api/project/select` of the old dashboard are dropped: the server-wide project list
  replaces the first, and `POST …/feature` below replaces the second.

Changes and additions under the prefix:

| Method and path | Body | Answer |
|---|---|---|
| GET `…/run/current` | | as before, plus `origin`, `session_alive` and `attach` (`attach.json` or null); `controls` from data-model §5 |
| POST `…/feature` | `{"feature": name \| null}` | 200; sets the project's feature in `projects.json` (replaces the old `/api/project/select`) |
| GET `…/roadmap`, `…/waiting-all`, `…/runs-all` | | data-model §15 |
| POST `…/roadmap` | `{"entries": [...]}` | 200 `{"commit"}`, or 400 / 409 |
| POST `…/feature/activate` | `{"feature"}` | 200, or 409 with the exact refusal text (data-model §15) |
| POST `…/run/adopt` | `{"confirm_closed"?: bool}` | 202 `{"pid"}`, or 409 `{"error"}` (data-model §7) |
| POST `…/run/start`, `…/jobs/start`, `…/jobs/<id>/continue` | as before | also 409 `{"error": "cap reached: <holders>"}` (data-model §8) |
| GET `…/models` | | `{"default": {"implementer", "verifier"}, "aliases": [...], "local": [...] \| null, "local_reason"}` (data-model §17) |
| POST `…/jobs/start`, `…/jobs/<id>/continue` | as before, plus `"models"?: {"implementer"?, "verifier"?}` (a name, or `null` for the project default) | as before; 400 with the refusal text for a bad name (data-model §17) |
| POST `…/manual/done` | as before, plus `"images"?: [{"type", "data"}]` (base64; at most 5, 10 MB each) | as before; 400 with the refusal text for a bad picture (data-model §16) |
| POST `…/task/approve` | `{"task"}` | 200 with the engine's result, or 409 `{"error"}` with the refusal text (data-model §18) |
| any write route | as before | also 409 `{"error": "version mismatch: …"}` on a major mismatch (data-model §11) |

## Status codes used

200, 202, 400 (bad body), 403 (Origin checks, or outside home), 404, 409 (cannot now, with a reason), 410 (project
missing), 415 (not JSON), 421 (bad Host), 422 (cannot be loaded), 503 (no engine copy).
