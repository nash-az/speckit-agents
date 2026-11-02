# Contract: server API (008)

Base address: `http://127.0.0.1:<port>`, localhost only. Every response carries the header `X-SDD-Contract: 1.0`.

Every route that changes state, and `GET /api/fs/list`, applies the 003 request checks (003 data-model §4.7 and
§4.9): an allowed Host header, no foreign Origin, and a JSON body with `Content-Type: application/json` where a body
is expected. A failed check → 403, with nothing done.

## Server-wide

| Method and path | Body | Answer |
|---|---|---|
| GET `/api/server` | | data-model §11 |
| GET `/api/settings` | | `{"max_parallel"}` |
| POST `/api/settings` | `{"max_parallel": int ≥ 1}` | 200 `{"max_parallel"}`, or 400 |
| GET `/api/projects` | | `{"projects": [{"id", "path", "name", "missing", "state", "by", "job"}]}`; `state` and `by` from data-model §4 |
| POST `/api/projects/add` | `{"path"}` | 200 the entry, 200 `{"needs_setup", "plan"}`, or 422 (data-model §10) |
| POST `/api/projects/<id>/remove` | `{}` | 200 `{"ok": true}`; never touches the project |
| GET `/api/fs/list?path=&hidden=` | | data-model §10 |
| GET `/api/plugin` | | data-model §13 |
| POST `/api/plugin/install`, `/api/plugin/update` | `{}` or `{"accept_command"}` | 200 `{"ok", "output", "version"}`, or 409 |
| POST `/api/setup/detect`, `/api/setup/check`, `/api/setup/apply` | data-model §12 | 200, 403, 409, 422 |
| GET `/api/slots` | | `{"max_parallel", "holders": [{"project", "run_id", "since", "kind": "supervised|session"}], "queued": [...]}` |
| GET `/api/stream` | | `text/event-stream`. Each event is `data: {"project", "topic", "at"}`, plus `: ping` every 15 s |

## Per project: `/api/p/<id>/…`

Every route of the current dashboard (003 `contracts/dashboard-api.md`, 005 `contracts/jobs-api.md`) is kept, with
the same bodies and answers, under the prefix `/api/p/<id>`. For example, `GET /api/runs` becomes
`GET /api/p/<id>/runs`, and `POST /api/run/stop` becomes `POST /api/p/<id>/run/stop`.

- An unknown `<id>` → 404. A missing project → 410 `{"error": "project folder is missing"}`.
- `GET /api/projects` and `POST /api/project/select` of the old dashboard are dropped; the project list above
  replaces them.

Changes and additions under the prefix:

| Method and path | Body | Answer |
|---|---|---|
| GET `…/run/current` | | as before, plus `by`, `session_alive` and `attach` (`attach.json` or null); `controls` from data-model §5 |
| POST `…/run/adopt` | `{"confirm_closed"?: bool}` | 202 `{"pid"}`, or 409 `{"error"}` (data-model §7) |
| POST `…/run/start`, `…/jobs/start` | as before | also 409 `{"error": "cap reached: <holders>"}` (data-model §8) |
| any write route | as before | also 409 `{"error": "version mismatch: …"}` on a major mismatch (data-model §11) |

## Status codes used

200, 202, 400 (bad body), 403 (request checks, or outside home), 404, 409 (cannot now, with a reason), 410
(project missing), 422 (cannot be loaded), 503 (no engine copy).
