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
   run.json of its own yet, or its `workflow_dir` is missing, or that `journal.jsonl` has no `started` line at all.
   Any `started` line counts, whatever its label: a resumed session's journal may start with `verify:` or
   `commit:`, and that run is past preparing.
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
(2026-11-08, third analyze pass B1, FR-017) A missing session report is written only for a session that has at
least one task-history entry in this run (`write_session_reports`, `bin/sdd-history.py:313-341`); a session with
none (a run stopped before any task, whether or not it wrote `.sdd/run.json`) gets no report, so `finalize` makes
no commit for it and the landing is `nothing`. The session id stays in the record's `runs` and in the summary's
`sessions`; the summary's `reports` lists only reports that exist (`:281`).

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

Counting rules: a task's `attempts` (and the run's `attempts`) = the number of its `started` entries in this run.
`per_task.duration_s` and `per_task.tokens` sum each agent once: entries are grouped by `transcript` (else by the
`key`'s agent id part), and of each group only the first entry carrying `duration_s` / `tokens` counts, since
several entries (for example `started` and `implemented`) come from the same transcript.

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

## §8. Run control: the start window, the request, the control log (amendment 2026-11-07)

FR-031 to FR-034, research R15–R22; updated 2026-11-08 for the analyze findings and the spec's Clarifications
"Session 2026-11-08" (research R23), and again for the second analyze pass (research R24). Every file here is in the supervisor's state directory (`sdd-tasks.py`
`state_dir(project)`, outside the project); nothing in the project tree changes.

### §8.1 `status.json` state `starting` (R16)

Written by `sdd-launch.py` `_start_supervisor` through `sdd-supervise.py` `write_starting_status(path, job_id,
pid)` right after the supervisor process is spawned for a run (`launch_job`, `continue_job`; not the plain
`launch`):

```json
{"state": "starting", "job": "20261107-1900", "pid": 48211,
 "contract": "1.0", "engine_version": "repo", "updated": 1794070800}
```

- `pid` is the spawned supervisor's (`proc.pid`), never the launcher's. Written to `status.json.tmp`, then
  `os.replace`. Not written when the current `status.json` already has `"pid": <that pid>` (read it first; a
  missing or unreadable file counts as "not yet").
- (2026-11-08, R16) When the status it replaces has `"state": "waiting"` (whatever its pid), its `window` and
  `resets_at` are copied in, so a restarted supervisor still waits for that reset:
  `{"state": "starting", "job": …, "pid": …, "window": "five_hour", "resets_at": 1794088800, …}`.
- The supervisor overwrites it with its own first `write_status` (`running` at the cycle start, `waiting` from
  `restart_wait`, or `done`/`stopped` when it ends or suspends at once).
- (2026-11-08, R16, R17) A supervisor that exits before its first session (no config, config errors, unknown
  profile, no record, record not `working`) replaces its own `starting` status (same `pid` as `os.getpid()`, only
  then) with `{"state": "stopped", "reason": "the supervisor could not start" | "the run is not working", …}`
  (`abandon_start`). The launcher reaps the process it spawned (a daemon thread on `proc.wait()`), so a dead
  supervisor never reads as alive.
- Readers: `controls()` treats `starting` as `running` (`stop` True, `reason` None); `current_state` maps it to
  `running`; `shown_state` gives `preparing` (live pid, no started agent; §3 rule 2 unchanged).

### §8.2 `control.json` and the control log lines

`control.json` (003 data-model §12.1) gains `job`:

```json
{"contract": "1.0", "id": "9f2c…", "command": "stop", "mode": "after-task", "requested_at": 1794070803,
 "job": "20261107-1900"}
```

`job` is the run id of the live supervisor when the request was accepted (`status.json` `job`, §8.1 or the
supervisor's own), and is left out when there is none. The stop-by request the supervisor sends itself has `"id":
"until"` and `"job": <its JOB_ID>`.

`<state dir>/control-log.jsonl`, append-only, one JSON object per line, written only through `sdd-control.py`
`log_control(logs, entry)` (never raises; returns True or False).

**Request line** (one per request, written by `request()` after the write or the refusal, and by `send_control`
after a successful write):

| Key | Type | Meaning |
|---|---|---|
| `ts` | int | epoch seconds |
| `id` | string \| null | the request id (`control.json` `id`); null for a refused request |
| `run` | string \| null | the run id (as `control.json` `job`); null when no run supervisor is live |
| `command` | `stop` \| `resume` \| `hold` \| `release` | |
| `mode` | `after-task` \| `now` \| null | `stop` only, and always set for a stop: `mode or "after-task"`, as `control.json` has it (R19, L2) |
| `source` | `dashboard` \| `cli` \| `until` | `POST /api/run/<command>`, `sdd-control.py` on the command line, the stop-by time |
| `outcome` | `accepted` \| `refused` | |
| `reason` | string \| null | refused only: the exact error the person was shown (`controls()` reason, "not available now", or "a request is already pending") |

**Taken line** (one per request the supervisor reads, written where it reads it):

| Key | Type | Meaning |
|---|---|---|
| `ts` | int | epoch seconds |
| `id` | string \| null | the `control.json` `id` it read |
| `run` | string \| null | `control.json` `job`, else the supervisor's `JOB_ID`, else null |
| `request_run` | string \| null | (2026-11-08) `control.json` `job` as read, null when the request carried none |
| `command` | string \| null | (2026-11-08) `control.json` `command` as read; null for an unreadable file |
| `mode` | string \| null | (2026-11-08) `control.json` `mode` as read, else null |
| `taken` | string | one of the texts below |
| `reason` | string \| null | why, for `ignored` only |

`taken` texts (exact) and where each is written. The ignored `startup_control` rows are checked in the order
shown, before the kept one, first match wins (2026-11-08, third analyze pass A4, research R17): a supervisor
without `--job` logs "left from before this supervisor started" even for a request that carries a `job`.

| Where (`bin/sdd-supervise.py`) | Request | `taken` | `reason` |
|---|---|---|---|
| `startup_control` | `stop` with this supervisor's `--job` | (kept; `run_job` writes the line) | |
| `startup_control` | (2026-11-08) a `control.json` that does not parse or is not an object; the file is unlinked | `ignored` | `unreadable request` |
| `startup_control` | anything, from a supervisor without `--job`; or anything without `job` | `ignored` | `left from before this supervisor started` |
| `startup_control` | anything with another `job` | `ignored` | `for another run` |
| `startup_control` | not `stop`, with this `--job` | `ignored` | `only stop is taken at startup` |
| `abandon_start` (2026-11-08; `main`'s early exits) | kept stop, the supervisor could not start | `ignored` | `the supervisor could not start` |
| `run_job` (kept stop) | clean tree (2026-11-08, I1: whether or not a Continue left `.sdd/run.json`; `close_session`, then ends the run) | `stopping before any task` | null |
| `run_job` (kept stop) | dirty tree: suspends again | `stopping now` | null |
| `run_job` (kept stop; through `abandon_start`) | returned before (no record, not working) | `ignored` | `the run is not working` |
| `Cycle.run` | `stop` `now`, no agent started | `stopping before any task` | null |
| `run_job` `before-task` branch (2026-11-08, I2; a second line for the request `Cycle.run` took before any agent, `args.stop_control`) | dirty tree: suspends instead of ending | `stopping now` | null |
| `Cycle.run` | `stop` `now` | `stopping now` | null |
| `Cycle.run` | `stop` `after-task`, first, no agent started | `stopping before any task` | null |
| `Cycle.run` | `stop` `after-task`, first | `stopping after the current task` | null |
| `Cycle.run` | `stop` `after-task`, a stop already under way | `ignored` | `a stop after the current task is already under way` |
| `Cycle.run` (2026-11-08, L5; `:386-397`) | the stop under way meets "no task is left in this run" (a second line for the same request) | `letting the run finish: no task left` | null |
| `Cycle.run` | `resume`, `hold`, `release` | `ignored` | `only stop is taken while the run is going` |
| `hold` | `resume` | `resuming` | null |
| `hold` | anything else | `ignored` | `only resume is taken while held` |
| `wait_for_reset` | `hold` that changes it | `staying paused after the reset` | null |
| `wait_for_reset` | `release` that changes it | `resuming after the reset` | null |
| `wait_for_reset` | `hold`/`release` that changes nothing | `ignored` | `already so` |
| `wait_for_reset` | `stop`, with `--job` | `stopping now` | null |
| `wait_for_reset` | `stop` without `--job`, or `resume` | `ignored` | `not taken during the usage wait` |

`wait_for_reset` rows also cover the restarted wait (`restart_wait`, 2026-11-08, R17), which calls it.

Helper in `bin/sdd-supervise.py`: `taken(logs, control, text, reason=None)` builds the taken line from the
request it read (`id`, `request_run`, `command`, `mode` from it; `control` may be `{}` for an unreadable file) and
calls `sdd_control.log_control`. `Cycle.run` takes `logs` from `self.log_path.parent`.

Example of an ignored leftover (2026-11-08):

```
{"ts": 1794070801, "id": "41ab…", "run": "20261107-1900", "request_run": null, "command": "stop", "mode": "after-task", "taken": "ignored", "reason": "left from before this supervisor started"}
```

Example after a Stop pressed while preparing:

```
{"ts": 1794070803, "id": "9f2c…", "run": "20261107-1900", "command": "stop", "mode": "after-task", "source": "dashboard", "outcome": "accepted", "reason": null}
{"ts": 1794070805, "id": "9f2c…", "run": "20261107-1900", "request_run": "20261107-1900", "command": "stop", "mode": "after-task", "taken": "stopping before any task", "reason": null}
```

### §8.3 Reading the log (`sdd-control.py` `read_control_log(logs) -> dict`)

Returns `{run id: [request, …]}`, each list in file order. A request:

```json
{"id": "9f2c…", "ts": 1794070803, "command": "stop", "mode": "after-task", "source": "dashboard",
 "outcome": "accepted", "reason": null, "taken": "stopping before any task", "taken_reason": null,
 "taken_ts": 1794070805}
```

- Built from the request lines with a non-null `run`. A taken line belongs to the latest request line before it
  with the same `id` and the same `run` (the stop-by id `until` repeats across runs, and can repeat in one run).
  `taken`, `taken_reason`, `taken_ts` come from the **last** taken line that belongs to the request (2026-11-08,
  L5: a stop that first said "stopping after the current task" and then "letting the run finish: no task left"
  shows the second), else null.
- A taken line with no request line before it is added as its own entry, with `command` and `mode` from the taken
  line (2026-11-08; null in lines written before) and `source`, `outcome` null (a request left from another run and
  ignored at startup still shows on that run's card).
- (2026-11-08, A4) A taken line whose `run` is null (a plain supervisor without `--job`) is skipped, like a
  request line without a run.
- Lines that do not parse as a JSON object are skipped. A missing file is `{}`, and so is any other `OSError` on
  the read (a directory, no permission; 2026-11-08, U2).

### §8.4 Run view `requests` (FR-034)

`sdd-views.py` `job_view` adds `"requests": [request, …]` (§8.3, that run's list, `[]` when none) for every run,
live or past. Nothing is added to the run record or the run summary (§6). (2026-11-08, U2) When the state
directory cannot be made (`state_dir` raises `OSError`) or the log cannot be read, `requests` is `[]`; the view
never fails because of the log.

### §8.5 Page texts (exact)

Requests list on a run's card (`jobRequestsHtml`), shown only when `requests` is not empty:

```html
<div class="job-requests"><div class="card-title">Requests</div>
  <div class="job-request">18:00:03 · Stop after current task · dashboard · accepted · stopping before any task</div>
</div>
```

- Row = `<time> · <command> · <source> · <outcome>[ · <taken>]`, parts joined by " · ".
- `<time>`: `ts` as local `HH:MM:SS`.
- `<command>`: `stop`+`after-task` "Stop after current task"; `stop`+`now` "Stop now"; `resume` "Resume"; `hold`
  "Keep paused"; `release` "Let it resume" (the banner's own labels); null (an unreadable request, or a taken-only entry without
  `command`) "Request". A taken-only entry with a `command` uses its label, e.g. "01:02:03 · Stop after current
  task · ignored: left from before this supervisor started" (2026-11-08).
- `<source>`: `dashboard` "dashboard"; `cli` "command line"; `until` "stop-by time"; null left out.
- `<outcome>`: "accepted"; "refused: <reason>"; null left out.
- `<taken>` (accepted or null outcome only): `taken`, plus ": <taken_reason>" when it has one; "not taken yet" when
  `taken` is null. A refused request has no taken part.

Banner while a request is out (FR-033, R21):

- The pressed button's label: `sending…` (with the single character U+2026). Every `button[data-control]` in
  `#run-banner` and `#run-banner-continue` are `disabled` until the answer.
- No answer after 10 000 ms: the fetch is aborted; `runBannerError` and the toast read
  `request failed: no answer after 10 s`.
- Other failures keep today's texts: a non-202 answer shows its `error` (else `request failed (<status>)`), a
  rejected fetch shows `request failed: <error>`.
- After any ending the buttons follow `controls` again; the page never sends the request a second time by itself.

Stop now confirmation (2026-11-08, M5, FR-006's exception; `renderJobBanner` `stopQuestion`):

- While shown `preparing` and not `stopping`: `Stop the run now? No task has started yet, so the run ends with
  nothing to land. If a resumed task's work is in the tree, the run is suspended instead.`
- Otherwise unchanged: `Stop the run now? The current agent is stopped and its task is resumed from the journal on
  the next start.`
- (2026-11-08, A1, research R21) A resumed session (Continue) is shown `working` from its start, so it gets the
  second text even before its first agent. Accepted: with half-done work in the tree it is suspended as the text
  says; with a clean tree it ends `stopped` "stopped before any task", and the card's reason and Requests row say
  so.

No answer after 10 s (2026-11-08, A5): `postControl`'s timer sets a local `timedOut = true` and then calls
`controller.abort()`; the rejection shows `request failed: no answer after 10 s` only when `timedOut` is true.

Supervisor `say` lines (in `launch.log`; tests compare them):

- `stop requested before the run started: stopping before any task` (`run_job`, kept stop, ending)
- `stop requested before the run started: suspending it again` (`run_job`, kept stop, dirty tree; 2026-11-08,
  I1: a clean tree ends the run with the line above, run.json or not)
- `stop now requested before any task: stopping the run` (`Cycle.run`, R18)
- `deleted a leftover control.json` (`startup_control`, every ignored request and an unreadable file; unchanged
  text)
- `an earlier supervisor was waiting for the <window> window: waiting until <HH:MM>` (`main`, unchanged text; now
  also from a `starting` status carrying `resets_at`)
- `stop requested after the current task, but no task is left in this run: letting it finish` (`Cycle.run`,
  unchanged text; now with its second taken line)
