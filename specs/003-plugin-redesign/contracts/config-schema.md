# Contract: `.sdd/config.toml` changes

Existing `[run]`, `[checks]`, `[allow]` keys are unchanged except: `run.isolation` is **removed**
(a value of `"worktree"` makes the run refuse to start, FR-016).

## `[agents]` — model per role (FR-011)
```toml
[agents]
implementer = "haiku"
verifier = "opus"
```
- Optional; a missing role uses the run's model (the session's, or the profile's).
- Keys: `implementer`, `verifier`, `evaluator`, `bookkeeper`. Any other key is an error.
- Values: any non-empty string — an alias (`haiku`, `sonnet`, `opus`, `fable`), `"inherit"`, a
  Claude model id, or a provider-specific id (Bedrock ARN, Vertex name). Not validated further.

## `[profiles.<name>]` — run profiles (FR-025)
```toml
[profiles.cheap]
model = "haiku"

[profiles.local]
model = "claude-sonnet-5-5"
env = { ANTHROPIC_BASE_URL = "http://localhost:4000" }
```
- Used only by `bin/sdd-supervise.py --profile <name>` (headless runs).
- `model` required, non-empty string. `env` optional, string → string.
- Env names containing `KEY`, `TOKEN`, `SECRET` or `PASSWORD` are refused: secrets come from the
  person's environment, never from this file.
- A gateway to a non-Claude or local model is best-effort and not supported by Anthropic.

## Errors
`python3 bin/sdd-tasks.py config` prints the merged config plus `"errors": [...]`; `/sdd-run` and the
supervisor refuse to start while it is non-empty. Exact messages: `data-model.md` §3.8.

## Template
`templates/sdd-config.toml` gains both tables commented out, with one-line explanations, and drops
`isolation` if present.
