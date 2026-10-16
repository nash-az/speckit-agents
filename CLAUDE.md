# speckit-agents

A Claude Code plugin that runs a Spec Kit project's open tasks unattended, with subagents. It works on top of Spec Kit and never forks it.
Read `README.md` for the user-facing description, and `.specify/memory/constitution.md` for the principles every change must keep.

## Why this exists
The user (Nash) wanted their Spec Kit projects to be developed by Claude subagents "safely without my intervention until the system is done".

- **Managed Agents ruled out.** We looked at Managed Agents multiagent orchestration (platform.claude.com/docs/en/managed-agents/multiagent-orchestration). Its agents share one cloud sandbox, and the user's projects depend on local models and data, so we chose Claude Code locally.
- **Nothing to reuse.** Spec Kit (v1.0.9) has `[P]` markers and a workflow engine with steps that run one after another and gates where a person approves, but nothing that hands work to subagents. A web search found no existing Spec Kit add-on for this.

## Decisions the user made (2026-10-14)
- **Repo:** a separate repo plus a Claude Code plugin, not a folder inside an app repo.
- **v1 scope:** the full scope, including agent tags (`[manual] [decision] [dep] [download] [eval] [ui]`) and a tasks-template override, so that `/speckit-tasks` writes the tags.
- **Isolation from app repos:** app repos know about this only as an installed plugin. Keep context, progress and notes about this work here, never in an app repo or its memory.
- **Commits:** ask before committing. v0.1 is committed on `main` as a single commit, with no remote.

## How it works
- **`bin/sdd-tasks.py`** (stdlib only): `parse`, `plan` (groups consecutive `[P]` tasks with disjoint files; holds tagged tasks and open BLOCKED.md entries), `tick`, `block`, `config`.
- **`workflows/sdd-run.js`**: the Workflow script, shipped as the plugin workflow `speckit-agents:sdd-run`. The main loop calls it by name and passes the plan as `args`.
  - Per task: implementer (or evaluator for `[eval]`), then the read-only verifier. A rejection goes back with feedback, up to `max_retries` retries.
  - Per group: the bookkeeper runs the project checks, then makes one commit per task with only its files.
  - When the checks fail: one repair, and if that fails, stash the work and record the tasks as blocked.
  - Phases: a dependent phase is skipped when its dependency has an attempted task that failed. Held tasks do not taint their phase.
- **`agents/`**: `sdd-implementer`, `sdd-verifier`, `sdd-evaluator`, `sdd-bookkeeper`. Their full names are `speckit-agents:<name>`, and the workflow refers to them by those names.
- **`skills/`**: `sdd-init`, `sdd-tag`, `sdd-run` (preflight, confirmation, branch, marker, Workflow call, report), `sdd-status`, `sdd-unblock`.
- **`hooks/register.ts` and `hooks/rules.ts`**: the run guard, active only while `.sdd/run.json` exists.
  - It refuses destructive git, commits on protected branches and writes to protected paths.
  - It fails closed.
  - The main loop (no `agentId`) may write `.sdd/`.
- **`templates/`**: `sdd-config.toml`, `settings.json` (sandbox and allow/deny rules), `agent-tags.md`.

## Progress
- **Done:** v0.1 is built. Tasks T001–T008 and T012–T014 are ticked in `specs/001-unattended-runs/tasks.md`.
- **Tests:** `python3 -m unittest discover -s tests` (parser, checkpoint and supervisor tests; the supervisor runs against `tests/fake/claude`), `node --test tests/workflow.test.mjs` (the workflow with stubbed agents), and `claude plugin test .` (11 guard tests).
  - Type-check: `tsc -p` against a tsconfig that includes the engine's `claude-code.d.ts`. The engine lays it in `.claude-plugin/types/` once the plugin loads.
  - `claude plugin validate .` passes, with warnings about the missing author field only.
- **End-to-end:** a headless `/sdd-run --yes` on a toy project passed SC-001. It produced 3 task commits and held 1 manual task, and left `main` untouched. Details, and the 5 problems found and fixed, are in `specs/001-unattended-runs/research.md`.
- **Open tasks:**
  - T009: script the end-to-end test in `tests/e2e/`.
  - T010: worktree isolation for parallel groups.
  - T011 `[decision]`: which browser tool to use for `[ui]` checks.
- **Installed** (2026-10-14) at user scope as `speckit-agents@speckit-agents` from the local marketplace `~/private/speckit-agents`. It reads from this directory, so edits here take effect in new sessions.

## Lessons from real runs
- **Unattended mode:** use `claude --permission-mode dontAsk`.
  - The project must be trusted, which means opened interactively once. Otherwise Claude Code ignores its `permissions.allow`.
  - `Workflow`, `Glob` and `Grep` must be on the allowlist.
  - Some compound shell commands (`for` loops) are refused, and agents fall back to simpler ones.
- **Headless runs:** set `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`. Otherwise `claude -p` stops background tasks 10 minutes after the main loop's last turn, which killed run #3 on the language app during T206. With it set, the process exits only when the workflow finishes and the report is written.
- **The guard's write check** must look only at real write targets. `2>/dev/null` and `2>&1` are not writes.
- **Workflow `args`** must be the JSON object, never the skill's own arguments.
- **Waiting on a headless run:** use its process ID (`wait $pid`, or `kill -0 $pid`), never `pgrep -f "<pattern>"`. The watcher's own command line matches the pattern, so the loop never ends.
- **Sandbox and tool caches:** the sandbox blocks writes outside the project, so `uv run` fails on `~/.cache/uv` (language app, 2026-10-14). The checks passed during `/sdd-init` only because the sandbox wasn't active yet. `/sdd-init` now adds tool caches to `sandbox.filesystem.allowWrite` and runs the checks again after writing the settings.
- **Call the workflow by name, not by path:** `scriptPath` must be a file the session can read, and a session in an app repo cannot read the plugin directory (language app, 2026-10-14). The toy test missed this. The plugin's `workflows/` scripts are saved workflows named `speckit-agents:<meta.name>`, and they resolve from any project.
- **Usage limits (2026-10-16):** headless stream-json carries `rate_limit_event` (`status` allowed / allowed_warning / rejected, `utilization` from 0.90 in 1 % steps, `resetsAt`, `unifiedWindows.five_hour|seven_day`), also while the workflow runs. At the limit `agent()` returns null; the workflow now calls that `interrupted`, stops, keeps `run.json`, and the next `/sdd-run` resumes from `sdd-tasks.py checkpoint <workflow_dir>/journal.jsonl`. `bin/sdd-supervise.py` pauses at a threshold after the next finished agent, sleeps until the reset and restarts. Interactive sessions pause workflows at the limit by themselves (at most twice per run); `claude -p` doesn't.
- **Testing kit:** stubs beneath the plugin answer `{ value }` for calls on `$`, `{ result }` for `tool.call`, and `{ deny }` to refuse.

## First real target
The user's language app (`~/private/language-app`). The app repo should not hold notes about this work.
State (2026-10-14): the first attempt failed before any agent started (uv cache under the sandbox, then the script path); both are fixed in the plugin. The app's sdd commits were reverted, so its tree matches `97970b5` again. Its 5 uncommitted imitation-check files are the user's, so leave them alone.
Next attempt:
1. Run `/sdd-init`.
2. Run `/speckit-converge`. `tasks.md` is behind the code, and converge appends only the work still missing, as a new `Phase N: Convergence`. Don't reinvent this in the plugin.
3. Run `/sdd-tag` on the new phase, then `/sdd-run --phases N`.
