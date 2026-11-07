# Contract: dashboard routes for runs and task histories

State-changing routes follow 003 data-model §4.9 (allowed Origin, `Content-Type: application/json`). A refusal is
`409 {"error": "<reason>"}` with the same text as the command line (`runs-cli.md`), changing nothing. An id that is
not a run id, or an unknown run, is `404 {"error": "not found"}`. Route names keep 005's `jobs`; the page says
"run" (FR-022).

| Method | Path | Body | Answer |
|---|---|---|---|
| GET | `/api/jobs` | — | `{"jobs": [view, …]}`: open runs first, then newest |
| GET | `/api/jobs/<id>` | — | `view + {"runs": [session as /api/runs gives it]}` |
| GET | `/api/jobs/plan` | — | `{"phases": [{"id","title","runnable","task_list": [{"id","text","summary"}]}], "runnable", "held", "can_start", "reason"}` |
| POST | `/api/jobs/start` | `{"phases"?, "tasks"?, "max_tasks"?, "until"?, "max_runs"?, "title"?}` | `202 {"ok": true, "job"}` |
| POST | `/api/jobs/<id>/continue` | `{"max_tasks"?, "until"?, "max_runs"?}` (number or null) | `202 {"ok": true}` |
| POST | `/api/jobs/<id>/land` | `{}` | `200 {"ok": true, "landed", "commit", "fast_forward", "landing"}` |
| POST | `/api/jobs/<id>/merge` | `{}` | same as land (calls `land`; the 005 `merge` is gone) |
| POST | `/api/jobs/<id>/drop` | `{}` | `200 {"ok": true, "tasks", "stash"}` (calls `drop_suspended` for any run; refused unless suspended) |
| GET | `/api/project/tasks` | — | as today; each task also has `summary` (string or null) |
| GET | `/api/project/task-history?id=<task id>` | — | `{"task", "feature", "entries": [entry + {"run_title", "transcript": bool}]}`; 400 bad id, 409 no feature |
| GET | `/api/project/task-history?id=<task id>&transcript=<index>` | — | the entry's transcript as text; 404 "the transcript is gone" |

## Run view (`sdd-views.py` `job_view`)

```json
{ "...record": "...",
  "shown": {"state": "preparing|working|paused|suspended|ended", "outcome", "reason", "landing",
            "resets_at"?, "keep_paused"?},
  "progress": {"done", "total", "waiting_on_you", "left"} | null,
  "in_flight": ["T014"],
  "changed_files": ["bin/x.py", "tests/test_x.py"],
  "tasks_done": 3 | null,
  "summary": { "...": "the run summary, data-model §6" } | null }
```

New: `changed_files` (suspended runs: `git status --porcelain` paths outside `.sdd/`, at most 20, else `[]`) for
Drop's confirm, and `summary` (ended runs: `.sdd/runs/<id>.json` when it exists, else null) for the past-run card.

## Page texts (exact)

- Drop confirm (FR-010): "Drop <title>? The half-done work on <tasks> is stashed and saved in the task's history:
  <files>. Finished tasks land on <base>." (`<tasks>` = `in_flight` joined by ", ", or "its last task";
  `<files>` = `changed_files` joined by ", ", or "no changed files").
- Task card (FR-029): the summary as the main text; the exact text inside `<details class="task-details">` with
  `<summary>Details</summary>`. A task without a summary shows its exact text as today, without Details.
- Start run task list: `summary`, else the exact text cut to 110 characters, as today.
- Past-run card line (FR-021, US4): "<duration> · <n> done, <d> dropped, <b> blocked · <a> attempts, <f> approved
  first time · <models> · peak usage <five_hour>% (5 h), <seven_day>% (7 d)"; parts with no data are left out.

## Amendment 2026-11-07: Stop while preparing, sending state, Requests list

FR-031 to FR-034; research R16–R21; exact shapes and texts in data-model §8.

| Method | Path | Change |
|---|---|---|
| POST | `/api/run/stop`, `/api/run/resume`, `/api/run/hold`, `/api/run/release` | Same body and answers (`202 {"ok": true, "id"}`, `409 {"error"}`, `400`). `_control` calls `sdd_control.request(project, command, mode, "dashboard")`, so every 202 and 409 leaves a request line in the control log; a 400 does not. While the new run's status is `starting` (data-model §8.1) `stop` answers 202, not 409 |
| GET | `/api/run/current` | Same shape. While the status is `starting`: `controls.stop` true, `controls.reason` null, `job.shown.state` `preparing` |
| POST | `/api/jobs/start` | Same answer; the 202 is sent only after the `starting` status is written, so the next `/api/run/current` already offers Stop |
| GET | `/api/jobs`, `/api/jobs/<id>` | Each run view also has `requests` (data-model §8.4): `[{"id", "ts", "command", "mode", "source", "outcome", "reason", "taken", "taken_reason", "taken_ts"}, …]`, oldest first, `[]` when none |

Page behaviour:

- **Run card** (`jobCardHtml`): after the summary line and before the buttons, `jobRequestsHtml(job)` (data-model
  §8.5): nothing when `requests` is empty, else the `Requests` list, on live and past runs alike.
- **Banner Stop while preparing**: both Stop buttons are enabled from the first poll after Start run (they follow
  `controls.stop`, which the `starting` status makes true).
- **Sending** (`postControl`, `controlButton`): while a banner request is out, the pressed button reads `sending…`
  and every banner control button and the usage checkbox are disabled; after 10 s without an answer the request is
  aborted and the banner shows `request failed: no answer after 10 s` beside the buttons and in a toast. No
  automatic retry: pressing again is the retry.
