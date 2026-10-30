# Contract: `[feedback]` in `.sdd/config.toml`

```toml
[feedback]
model = "claude-opus-5-5"   # the drafting session's model; an alias or id (as [agents] values)
profile = "cheap"            # optional: a [profiles.<name>] whose model and env the session uses (FR-025)
timeout_minutes = 10         # a session running longer is stopped; the note is `failed`
max_parallel = 2             # sessions at once; the others wait in `new`
```
- The whole section is optional. Defaults: `model` = the launch default, `timeout_minutes` 10, `max_parallel` 2.
- `model`: non-empty string. `profile`: must name an existing profile. `timeout_minutes` and `max_parallel`:
  integers; `timeout_minutes` 1 to 120, `max_parallel` 1 to 5. Any other key is an error.
- `config_errors()` in `bin/sdd-tasks.py` reports each problem as `feedback.<key>: <reason>`; the
  helper refuses to start a session while the section has errors, and the note shows `failed` with the
  first one.
- Env of a profile follows the 003 rule (no `KEY`, `TOKEN`, `SECRET`, `PASSWORD` names).
