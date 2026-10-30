# Contract: feedback routes

Every `POST` takes the 003 FR-030 checks (allowed Origin, `Content-Type: application/json`, localhost Host)
and answers errors as `{"error": "<one line>"}`: `400` bad body or id, `404` unknown note, `409` the
helper refused (nothing changed), `503` the feedback helpers or the `claude` command are unavailable (every
other route keeps working, Principle VII). Note ids match `^N\d{3,}$`. `{note}` below is the Note of
`data-model.md` §1 without `worker`; `{proposal}` is the Proposal of §3.

## Reads
- `GET /api/feedback` → `{"notes": [{note}...], "limits": {"max_parallel": 2, "timeout_minutes": 10}, "run_active": bool}`.
  Calls `kick()` first, so orphans are marked `failed` and queued notes start. Open notes first, newest first.
- `GET /api/feedback/<id>` → `{note}` plus `"log": [readable lines, last 200]` while `drafting` or `failed`.
- `GET /api/feedback/<id>/preview` → the `preview()` of `data-model.md` §7 for the stored proposal:
  `{"ok", "problems": [{"id","reason"}], "files": [...], "stale": bool}`.

## Writes
| Route | Body | Does | Answer |
|---|---|---|---|
| `POST /api/feedback` | `{"text", "source"?: "feedback"\|"chat", "context"?: {task?, heading?, view?}}` | `submit` | `202 {note}` |
| `POST /api/feedback/<id>/answer` | `{"answers": [{"id": "q1", "answer": "…"}], "best_effort"?: bool}` | `answer` | `202 {note}` |
| `POST /api/feedback/<id>/reply` | `{"text", "kind": "answer"\|"change"}` | `reply` | `202 {note}` |
| `POST /api/feedback/<id>/redraft` | `{}` | `redraft` | `202 {note}` |
| `POST /api/feedback/<id>/retry` | `{}` | `retry` | `202 {note}` |
| `POST /api/feedback/<id>/discard` | `{}` | `discard` | `200 {note}` |
| `POST /api/feedback/<id>/apply` | `{"proposal": {proposal}, "confirm"?: ["c6"]}` | `sdd-apply.apply`, then `mark_applied` | `200 {"note", "commit", "files", "applied", "left_out"}` |

`apply` sends the whole edited proposal; the server validates it with the same rules as a session result
and uses the stored proposal only to compute `left_out`. A route never trusts the page: ids, lengths and
targets are checked again. While `.sdd/run.json` exists, `answer`, `reply`, `redraft`, `retry` and `submit`
still work (drafting only reads), and `apply` answers `409 {"error": "a run is active (.sdd/run.json exists)…"}`.

## Not routes
No route runs `claude`, `git` or writes a project file itself. The dashboard source calls the two helpers; the
`ReadOnlyTest` of 003 §4.8 still passes.
