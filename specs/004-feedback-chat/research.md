# Research: Feedback and chat

Decisions made while planning. Format: decision, why, alternatives rejected.

## D1. The drafting session is read-only because it has no write tool
Start it with `claude -p <prompt> --model <m> --tools Read,Grep,Glob --allowedTools Read,Grep,Glob
--permission-mode dontAsk --json-schema <schema> --output-format stream-json --verbose`, cwd = the project.
`--tools` removes every other built-in tool (no Write, Edit, Bash, no web), `dontAsk` denies anything not
allowed instead of asking, and `--json-schema` makes the session return the result as structured output
instead of writing a file. **Why**: stronger than the spec's first wording (a result file plus a guard):
the session cannot write anything, so there is nothing for a guard to catch; the run guard still applies as a
second layer while a run is active. **Rejected**: a `Write(<result path>)` rule (a path-pattern bug would
widen it); a Bash tool with rules (a shell is too wide); running in a copy of the project (slow, needs
cleanup). **To verify**: one real session confirms the result's place in the stream (`result` event) and that
a write attempt is denied. Only `extract_result()` depends on that.

## D2. Two helpers: drafting and applying
`bin/sdd-feedback.py` (notes, queue, sessions) and `bin/sdd-apply.py` (edits and commit). **Why**:
applying must stay model-free and exhaustively testable (Principle V); drafting needs processes and
time. The dashboard imports both with `importlib` like the 003 helpers, and lazily, so a broken feedback
file cannot take the dashboard down. **Rejected**: one file (mixes the part that spends usage with the part
that commits).

## D3. Notes live in the state directory, JSON, one file each
`<state>/feedback/<id>.json` (the same `state_dir(project)` as `answers/`), written to a temp file and moved
into place. The session's stream goes to `<id>.log` beside it. **Why**: FR-003, and a tracked note would dirty
the tree, which blocks `sdd-launch.py`. **Rejected**: SQLite (a new moving part for tens of rows); the
project tree (clean-tree rule).

## D4. Note ids are `N001`, `N002`, …
Next id = highest existing + 1, three digits, never reused (a discarded note keeps its id). **Why**: short,
readable in commit messages and in the log line, no clock needed.

## D5. Who starts queued sessions: whoever changes the queue
`sdd-feedback.py` has `kick(project)`: reap finished or dead sessions, mark orphans `failed`, and start the
oldest `new` notes while fewer than `max_parallel` are `drafting`. It is called by every POST route, by the
GET list route, and by the worker as it exits. **Why**: no daemon; a restart after a crash picks the
queue up on the first request. Marking orphans: a `drafting` note whose pid is not alive (or is not the
recorded worker) becomes `failed` with `error: "interrupted"` (FR-002 edge case), never restarted by
itself. **Rejected**: a polling thread in the dashboard (spawning in the dashboard process); cron.

## D6. The prompt carries paths and the note, not file contents
The session gets: the note text, its context, the feature folder path, the list of files to read first
(`spec.md`, `plan.md`, `tasks.md`, `decisions.md`, `BLOCKED.md`), earlier questions and answers, and the
rules (what a proposal may contain, ask at most three questions, no guessing). It reads files itself.
**Why**: `tasks.md` and the code are large; reading on demand costs less and stays current. The note text is
fenced and labelled as data. **Rejected**: inlining files (stale and big).

## D7. The result is validated by a schema in code, not trusted
`validate_result(obj, feature_dir)` checks shape, types, lengths (text ≤ 4000 chars, ≤ 40 changes), that every
change's targets are allowed (FR-017), that task ids in `amended_tasks`/`dropped_tasks` exist and are open,
that questions number ≤ 3 and the note is not past round 2 (FR-008). Anything else makes the note `failed`
with the reason. `--json-schema` is a first filter only; the code check is the authority.

## D8. A proposal change is a typed record with old text
Spec edit: `{heading, old, new}`; amended task: `{task, old, new}`; dropped task: `{task, old, reason}`. Apply
requires each `old` to match the file exactly once. **Why**: FR-007's stale check needs no hashes and no
line numbers (lines move). Hashes of `spec.md` and `tasks.md` at draft time are kept only to tell the person
"the project changed since this was drafted".

## D9. Apply is all or nothing
`sdd-apply.py` builds every new file text in memory first; if any check fails it raises `Refused` naming
the change and writes nothing. Only then does it write, `git add` the touched files, and commit; a failed
commit restores the files (the pattern of `sdd-decide.py`). The person removes or redrafts the bad change.

## D10. New task ids and placement
Next id = highest `T###` found anywhere in the feature's `tasks.md` + 1 (three digits). A new task goes at
the end of the phase it names (`phase` is the phase title, matched exactly); an unknown phase is created at the
end as `## Phase N: <title>`. Line format follows `.specify/templates/tasks-template.md`:
`- [ ] T### [P] [US#] [tag] text in `file``. After editing, `sdd-tasks.parse_tasks` re-reads the file and the
new ids must be present, else Refused (Principle IV).

## D11. A dropped task becomes a `wontfix` entry, not a deleted line
Same as a skipped manual task (FR-036): `BLOCKED.md` gets `## T###` / `status: wontfix` / reason. **Why**:
the line and its history stay (constitution II: work is not deleted), and `hold_reason` already makes a
`wontfix` entry outrank everything. Spec amended: `BLOCKED.md` is an allowed target (FR-017).

## D12. New and removed specs come last (story 5)
New: `specs/<next 3-digit number>-<slug>/spec.md` from `.specify/templates/spec-template.md` with the
title and "Input" filled from the note; the number is highest existing + 1 across `specs/`; the slug comes from
the proposal's name, lowercased, `[a-z0-9-]` only. Removed: `git rm -r` of the folder, refused when it has
open tasks, a live run, or is the feature a run uses, unless the proposal carries `confirm: true` that the
person set with a separate click. **Why last**: highest risk; the plain flow must work first.

## D13. The chat is a panel over the same routes
A docked panel (every view) with a composer and the last notes' states; a message calls `POST /api/feedback`
with `source: "chat"`; Reply calls `POST /api/feedback/<id>/reply`. No new backend. **Why**: FR-011 says one
machine; and the panel is optional UI (Principle VII).

## D14. Usage cost is shown, not capped (deferred from clarify)
The stream's final event carries token and cost numbers when present; the note keeps them and the card shows
them. No budget limit in this feature; `max_parallel` and the 10 minute limit bound the worst case. A cap
can be added later in `[feedback]` without changing the note format.
