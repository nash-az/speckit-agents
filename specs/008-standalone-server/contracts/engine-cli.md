# Contract: engine commands (008)

The commands the plugin's skills and the server call. Each prints one JSON object on stdout, with
`"contract": "1.0"` at the top level. A refusal exits with a non-zero code and prints `{"contract", "error"}`.
They are run as `python3 <engine>/bin/<script> …`. `<engine>` is the plugin root inside a session, and the engine
copy's `current` folder for the server.

| Command | Prints | Exit codes |
|---|---|---|
| `sdd-engine.py install --from <plugin root>` | `{"installed", "path", "plugin_version"}` | 0; 1 on a copy error |
| `sdd-engine.py path` | `{"path", "plugin_version"}` | 0; 2 when there is no copy |
| `sdd-tasks.py state --project <dir>` | data-model §4 | 0 |
| `sdd-tasks.py session --run-json <file>` | `{"session", "started_by"}` (or `session: null` with `error`) | 0 |
| `sdd-control.py controls --project <dir>` | the `controls` object (data-model §5) | 0 |
| `sdd-control.py stop --project <dir> [--now]` | `{"ok", "id"}` or `{"error"}` | 0 / 1 |
| `sdd-control.py resume\|hold\|release --project <dir>` | as `stop` | 0 / 1 |
| `sdd-launch.py adopt <project> --run-id <id> [--confirmed-closed]` | `{"pid"}` or `{"error"}` | 0 / 1 |
| `sdd-setup.py detect\|check\|apply --project <dir> …` | data-model §12 | 0 / 1 |
| `sdd-slots.py holders` | `{"max_parallel", "holders", "queued"}` | 0 |
| `sdd-supervise.py … --adopt <run id> [--confirmed-closed]` | log lines (not JSON) | 0 closed or finished; 3 timed out; 4 cap reached on a person's start |

The existing commands (`parse`, `plan`, `tick`, `block`, `config`, `checkpoint`, `changed`, `events`, and the
`sdd-decide`, `sdd-manual`, `sdd-unblock`, `sdd-edit`, `sdd-job`, `sdd-launch` commands) keep their arguments. Their
JSON output gains the `contract` field.

Environment:
- `SDD_SUPERVISED=1` is set by the supervisor on the `claude -p` it starts (data-model §3).
- `XDG_STATE_HOME` and `XDG_DATA_HOME` move the state and engine folders, as today for state.
