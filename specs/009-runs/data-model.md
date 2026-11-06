# Data model: Runs (009)

**Spec**: `spec.md` | **Plan**: `plan.md` | **Research**: `research.md`

Sections are numbered so tasks can cite them (`data-model §3`). "Today" marks what the code on
`auto/20261106-1204` already does; "New" marks what the amendment adds.

## §1. Run record (`.sdd/jobs/<id>.json`, git-ignored)

The 005 Job record is the run record. Written only through `sdd-job.py` `save` (atomic replace, unknown keys
kept, `contract` set).

| Key | Type | Meaning |
|---|---|---|
| `id` | `YYYYMMDD-HHMM[-n]` | the run id |
| `title` | string | the scope in words unless the person gave one |
| `feature` | string | `009-runs`, or a path with `/` |
| `scope` | `{"phases": [..]}` \| `{"tasks": [..]}` \| `{"all": true}` | what the person asked for |
| `scope_tasks` | [task id] | tasks the scope matched so far (grows when tasks are added in scoped phases) |
| `blocked_at_start` | [task id] | as 005 |
| `limits` | `{"max_tasks", "until", "max_runs"}` | null = no limit; `max_runs` counts sessions (default 20) |
| `branch` | `auto/<id>` | the run branch |
| `base` | `{"branch", "commit"}` | the base branch and its commit at the start |
| `runs` | [session id] | the run's sessions (`claude -p` processes), oldest first |
| `no_progress_runs` | int | sessions in a row without progress |
| `created`, `ended` | epoch seconds | `ended` only once ended |
| `state` | `working` \| `suspended` \| `ended` | stored state (§2 reads 005 states) |
| `outcome` | `done` \| `stopped` \| `limit` \| `waiting` \| `failed` \| `dropped` | ended only |
| `reason` | string \| null | in words |
| `landing` | §1.1 | ended only |
| `landed_commit` | sha | last commit fast-forwarded onto the base while suspended (FR-011) |
| `merge_commit` | sha | same as `landing.commit` once landed (kept for 005 readers) |
| `usage_peak` | `{"five_hour": float, "seven_day": float}` | **New**: highest utilization the supervisor saw per window (R9) |
| `contract` | `"1.0"` | engine contract |

### §1.1 Landing

`{"state", "commit", "files", "reason"?}`:

| `state` | Meaning | Card offers |
|---|---|---|
| `landed` | on the base; `commit` = the base's commit after the fast-forward or merge | nothing |
| `nothing` | the branch had no commit the base lacks | nothing |
| `needs-merge` | the merge conflicted; `files` lists them; branch kept, checkout back on it | Merge |
| `pending` | not tried yet or could not run; `reason` says why | Land |
| `not-landed` | a 005 Job that was dropped | nothing |

## §2. Reading any record as 009 (`normalize`, today)

`normalize(project, record)` returns a copy; nothing is written. "Its run.json" = `owns_run_json`: `.sdd/run.json`
is readable and its `job` equals the record's `id`, or the record has a `branch` and run.json's `branch` equals it.

| Stored state | Its run.json | Read as |
|---|---|---|
| `working`, `suspended` | – | kept |
| `ended` | – | kept; a missing or non-dict `landing` becomes `pending` |
| `merged` | – | `ended`, outcome from reason (default `done`), landing `landed` with `merge_commit` |
| `dropped` | – | `ended`, outcome `dropped`, landing `not-landed` |
| `done`, `stopped`, `failed`, `waiting`, `held` | yes | `suspended`, reason kept |
| `done`, `stopped`, `failed`, `waiting` | no | `ended`, outcome = state (reason may override), landing `pending` |
| `held` | no | `ended`, outcome `stopped` (reason may override), landing `pending` |
| anything else | – | `working` |

Outcome from a 005 reason: ends with " reached" → `limit`; "only items that need you are left" → `waiting`;
starts with "no progress" → `failed`; "stopped by you" or starts with "kept paused" → `stopped`.

## §3. Shown state (`shown_state`, today; `preparing` new)

Returns `{"state", "outcome", "reason", "landing"}` (+ `resets_at`, `keep_paused` for `paused`). First rule wins,
on the normalized record:

1. `working`, supervisor alive (`status.json` pid) and its state `waiting` → `paused`.
2. `working`, supervisor alive, no task started yet in the current session → `preparing` (**New**, FR-002): no
   run.json of its own yet, or its `workflow_dir` is missing, or that `journal.jsonl` has no `started` line whose
   `label` starts with `impl:` or `eval:`.
3. `working`, supervisor alive → `working`.
4. `working`, supervisor dead, its run.json → `suspended`, reason kept or "the run was cut off".
5. `working`, supervisor dead, no run.json of its own → `ended`, outcome `stopped`, reason "the supervisor ended
   without recording the run's end", landing `pending`.
6. `ended` → `ended` with outcome, reason, landing.
7. otherwise the normalized state.

**Open** (`is_open`, blocks a new run): shown `preparing`, `working`, `paused`, `suspended`, or `ended` with landing
`needs-merge` or `pending`. `open_run(project)` = the newest open record.

Banner and card words (FR-022): "Working on <title>: <task>, <step>", "Preparing <title>", "Paused until <time>:
usage limit", "Suspended during <task>: <reason>. Continue or Drop", "Stopping after <task>", "<title> ended: <n>
tasks landed on <base>".

## §4. Run lifecycle

```
start (launch_job) ──> working ──(no agent yet)── preparing (shown only)
working ──usage wait──> paused (shown only) ──reset──> working
working ──Stop after this task / limit / done / waiting / no progress──> [boundary]
   [boundary], tree clean ─────────────> close session, end(outcome) ──> land ──> summary
   [boundary], tree dirty (later task) ─> stash_and_end: patch to history, stash, close session, end, land, summary
   Stop pressed before any agent ───────> close session, end("stopped", "stopped before any task")
working ──Stop now / cut-off / kept paused / crash──> suspend: history `suspended`, land_ready
suspended ──Continue──> history `resumed`, working (same id, branch)
suspended ──Drop──> drop_suspended: patch to history `dropped`, stash, archive run.json, end("dropped"), land, summary
ended(needs-merge|pending) ──Merge/Land──> land ──> summary
```

`end(project, id, outcome, reason)` (**changed**, R2): store `ended`; `sdd-history.py` `finalize` commits the
task histories and missing session reports on the run branch (`chore(run): <id> history`); then `land`; when the
landing is `landed` or `nothing`, `land` writes and commits the summary (§6). Never raises.

## §5. Task history entry (`.sdd/tasks/<feature folder>/<task id>.jsonl`, committed)

One JSON object per line, append-only, oldest first. Written only by `sdd-history.py` (`append`, `harvest`,
`record_step`).

| Field | When | Meaning |
|---|---|---|
| `ts` | always | ISO local time (journal: transcript timestamp) |
| `run` | always | run id (the record's `id`) |
| `session` | when known | session id |
| `step` | always | `started`, `implemented`, `verified`, `blocked`, `failed`, `committed`, `checks failed`, `repair`, `suspended`, `resumed`, `dropped` |
| `attempt` | agent steps | 1-based |
| `role`, `model` | agent steps | `implementer` / `evaluator` / `verifier` / `bookkeeper`; the model from the agent's meta |
| `verdict`, `problems` | `verified` | `approve` / `reject`, list of strings |
| `commits` | `committed` | short shas |
| `reason`, `tag` | `blocked`, `failed`, `suspended`, `dropped` | in words |
| `diff` | `dropped` | the uncommitted patch, cut to 200 000 characters |
| `duration_s` | agent steps, **New** | seconds from the transcript's first to last timestamp, when it exists at harvest |
| `tokens` | agent steps, **New** | input + output tokens summed over the transcript's assistant lines, when it exists |
| `transcript` | agent steps | transcript path while it exists |
| `key` | always | unique: journal `<session>:<workflow>:<label>:<step>:<agent id>:<task>`; run steps `<run>:<step>:<ts>:<task>` |

Run steps (**New** for `suspended` and `resumed`, R10): `suspend` records `suspended` with `reason`;
`continue_job` records `resumed` when it resumes; `drop_suspended` records `dropped` with `diff`;
`stash_and_end` records `dropped` with `diff` and reason "started after the stop; its edits were stashed". Each
is recorded for every task in flight in run.json's workflow journal (`sdd-tasks.py checkpoint` `tasks`).

## §6. Run summary (`.sdd/runs/<run id>.json`, committed, written once)

Written by `sdd-history.py` `write_summary` when the landing is final (`landed` or `nothing`), committed on the
base as `chore(run): <id> summary`. Never rewritten (`write_summary` returns None when the file exists).

```json
{
  "contract": "1.0",
  "id": "20261106-1700", "title": "Phase 7", "feature": "009-runs",
  "scope": {"phases": ["7"]},
  "created": 1793454000, "ended": 1793461200, "duration_s": 7200,
  "outcome": "stopped", "reason": "stopped by you",
  "landing": {"state": "landed", "commit": "<sha>", "files": []},
  "sessions": ["20261106-1700", "20261106-1915"],
  "reports": [".sdd/runs/20261106-1700.md", ".sdd/runs/20261106-1915.md"],
  "tasks": {"done": ["T013"], "blocked": [], "dropped": ["T014"]},
  "attempts": 3, "approved_first_time": 1,
  "models": {"implementer": "claude-opus-5-5", "verifier": "claude-opus-5-5"},
  "per_task": {"T013": {"attempts": 2, "duration_s": 840, "tokens": 412000}},
  "usage_peak": {"five_hour": 0.93, "seven_day": 0.41}
}
```

New against T001: `landing`, `duration_s`, `reports`, `per_task`, `usage_peak` (null when the supervisor saw no
usage event). Missing data is `null`, never left out.

## §7. Task summary line (`tasks.md`, FR-028)

```
- [ ] T013 In `bin/sdd-tasks.py` `parse_tasks` read the line right below a task …
  Tasks can carry a plain-language summary that the dashboard shows.
```

- The line right below a task line, starting with exactly two spaces and then a character other than a space or
  `-`; not a task line itself. One sentence, no file paths, function names or task ids.
- Parsed into the task's `summary` (stripped); `summary: null` when absent. Never part of `text`, `files`,
  `deps` or `superseded_by`. `tick` and the other line edits leave it in place (they touch only the task line).
- `snapshot`/`changed` (resume's task check) ignore it: editing a summary does not count as changing a task.
