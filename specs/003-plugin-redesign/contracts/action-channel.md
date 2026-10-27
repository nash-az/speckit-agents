# Contract: Action channel (designed in 003, implemented by the chat feature)

**Not implemented in 003.** This fixes the shape so the next feature (chat window, backlog item 40)
does not need to redesign the dashboard backend (FR-020).

## Rule
The dashboard server never writes project files or git history. An action a person asks for in the
UI (create a spec, amend a task, answer a BLOCKED.md question, pause a run) is handed to a Claude
Code session, which performs it with its normal tools under the run guard and the project's
permissions. The dashboard only relays the request and shows the result.

## Already done in 003: run control
`run.pause` is not an action through this channel. Stopping and resuming a supervised run is process
control, not a project write, so 003 ships it directly as `POST /api/run/stop` and `POST /api/run/resume`
(FR-029, `data-model.md` §12): the dashboard writes one control file in the supervisor's state
directory and the supervisor acts on it. The chat feature need not implement `run.pause`.

## Shape (for the chat feature to implement)
- `POST /api/actions` — body `{"kind": "<kind>", "payload": {...}}`; returns `202 {"action_id"}`.
- `GET /api/actions/<action_id>` — `{"state": "queued"|"running"|"done"|"failed", "summary", "log": [...]}`.
- Kinds the chat feature is expected to start with: `chat.message` (free text to the session),
  `blocked.answer` (`{"task", "answer"}`), `task.amend` (`{"task", "text"}`), `spec.create`
  (`{"description"}`).
- The session is started with the Claude Agent SDK or `claude -p` from the dashboard's process, in
  the project directory, with the project's own settings — never with broader permissions than an
  interactive session in that project would have.
- While `.sdd/run.json` exists, actions that edit `tasks.md`, `BLOCKED.md` or specs are refused by
  the dashboard with `409` — the same rule the guard enforces for agents.

## Why HTTP
It matches the existing routes; an MCP server (backlog item 37e) can wrap these two routes later
without changing them.
