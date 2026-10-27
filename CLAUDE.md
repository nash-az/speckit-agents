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
- **Redesign (2026-10-25): `specs/003-plugin-redesign/` is the current work and its source of truth.**
  - Constitution v1.1.0 adds Principle VII, "AI-free at the core".
  - 003 supersedes `specs/002-redesign/` (kept as reference only) and `001`'s open tasks T009–T011.
  - spec (28 FRs, 6 stories), plan, research, data-model, contracts, quickstart and tasks (42) are committed.
    `/speckit-analyze` ran; F1–F4 fixed, low findings accepted.
  - **Phase 2 (T003–T012) is implemented** (run `20261027-1352`, merged into `main` at `fa8236a`). Phases 3–9 are open.
  - `research.md` has the disposition of every backlog item (done / in 003 / deferred).
  - Tasks name exact files, functions and the data-model section to follow. Choosing a cheaper or local implementer model is a
    feature of the finished plugin for its users; the plugin itself is always built with the session's model (Opus 5.5).
  - Next feature after 003: the chat window (backlog item 40), on the action channel in `contracts/action-channel.md`.
- **State (end of 2026-10-27):** Phases 2 (T003–T012, T043–T044) and 3 (T013–T020, dashboard MVP) are implemented and
  merged; all approved on the first attempt on Opus 5.5. T001 is wontfix (BLOCKED.md); T002 and Phases 4–9 are open.
  Run logs: `logs/2026-10-27-phase2-pilot.md`. The event feed now comes from the engine journal (`sdd-tasks.py events`,
  data-model §3.9), because Workflow scripts can't `import()` or call `Date.now()`.
  - **Dashboard launch model (user decision):** a plugin command (`/speckit-agents:ui`) run from the project directory
    starts the API server and the UI on separate ports; one project per dashboard; the plugin stores nothing. Not in 003 yet.
  - **Paused before Phase 4 (user):** design research for the finished dashboard is done and awaiting review:
    `logs/2026-10-27-dashboard-design.html` (also https://claude.ai/artifact/26f22opZPWvBp1wP7gJmiJ). Proposal: Home
    (waiting on you / last run / features), Features (docs + traceability), Runs (task → attempts → log), chat drawer;
    UI as plain static files on its own port, no build step. Open decisions are listed at the end of that page.
  - **Next:** the user reviews the design; then fold it into 003 (rewrite T026's UI part, add tasks for the UI split,
    `/speckit-agents:ui`, removing the project picker, inbox, traceability, doc links), then run Phase 4.
  - Known gap: the implementer prompt says `.sdd/scratch/<run>/<task>/` is allowed by the guard, but that carve-out is
    T036 (Phase 7); the guard refused one scratch write in run 1431.
- **`.claude/settings.local.json.bu` and `.claude/settings.provider.example.json`:** the user's own files. Keep them untracked; never commit them.
- **Installed** at user scope as `speckit-agents@speckit-agents` from the marketplace `~/private/speckit-agents` (cache at
  commit `2f4f544`; the frozen copy in `/tmp` is no longer used). Claude Code runs the plugin from a cache pinned to a
  git commit (`~/.claude/plugins/cache/speckit-agents/…`), so edits take effect only after commit + `claude plugin uninstall`
  + `claude plugin marketplace update speckit-agents` + `claude plugin install`.

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
- **No `import()` in workflow scripts** (Claude Code 2.1.311): the Workflow tool refuses the script with
  `SyntaxError: import() is not available in workflow scripts`. Workflows can't touch the file system or spawn processes.
- **Preflight needs a clean tree, untracked files included.** Keep a user's own untracked files out with `.git/info/exclude`.
- **Tests must not hard-code `/tmp`:** the sandbox allows only `$TMPDIR` there, so they fail the run's checks. Use `os.tmpdir()`.
- **Start the operator session outside the repo** (e.g. `~/private`): the repo's sandbox settings also lock a session in it,
  and the nested `claude -p` then can't start its own sandbox (EPERM on the socket).
- **Testing kit:** stubs beneath the plugin answer `{ value }` for calls on `$`, `{ result }` for `tool.call`, and `{ deny }` to refuse.

## First real target
The user's language app (`~/private/language-app`). The app repo should not hold notes about this work.
State (2026-10-14): the first attempt failed before any agent started (uv cache under the sandbox, then the script path); both are fixed in the plugin. The app's sdd commits were reverted, so its tree matches `97970b5` again. Its 5 uncommitted imitation-check files are the user's, so leave them alone.
State (2026-10-25): paused until the 003 redesign is implemented (the user's decision: redesign first, then continue the app with the redesigned plugin).
- `tasks.md` is at Phase 18: 19 open tasks, several held.
- A stale `.sdd/run.json` (run `20261024-0014`, phase 16, no report) must be cleaned up first.
- T296's BLOCKED.md entry uses an ad-hoc `tag: failed` that the new model gives a proper state.
- Before its first run on the new plugin, run `/sdd-init` there so `.gitignore` gets the new lines (003 task T027).
