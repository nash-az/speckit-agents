# speckit-agents

Unattended [Spec Kit](https://github.com/github/spec-kit) implementation with Claude Code subagents.

Spec Kit takes you from spec to plan to `tasks.md`. Its `/speckit-implement` then works through the tasks in a single context, and it is built for a person who watches.
speckit-agents adds a run that needs no supervision. It works through the open tasks with separate subagents:

```
tasks.md ──► sdd-tasks.py plan ──► for each phase, in dependency order
                                     for each group (parallel [P] tasks with disjoint files)
                                       implementer ─► verifier ─┐  (retry with feedback, max N)
                                       evaluator   ─► verifier ─┘  for [eval] tasks
                                     bookkeeper: project checks ─► tick + one commit per task
                                     (checks red → one repair → else stash + BLOCKED.md)
anything needing a person ──► BLOCKED.md with one question each ──► /sdd-unblock
```

The run commits only to a new `auto/<run id>` branch. It never pushes and never touches `main`. It stops when every task is done, held or blocked. You review the branch and merge it yourself.

## Install

```
/plugin marketplace add ~/private/speckit-agents
/plugin install speckit-agents@speckit-agents
```

For development, run `claude --plugin-dir ~/private/speckit-agents` instead.

## Use

| Command | What it does |
|---|---|
| `/sdd-init` | Once per project. Writes `.sdd/config.toml` (the project checks and limits), adds the agent-tags section to the tasks template (`.specify/templates/overrides/`), and merges the safety settings into `.claude/settings.json`. |
| `/sdd-tag` | Adds agent tags to the open tasks of an existing `tasks.md`. |
| `/sdd-run [--phases 7,8] [--max-tasks N] [--yes]` | Preflight (clean tree, checks pass), one confirmation, then the unattended run. |
| `/sdd-status` | Lists open, held, blocked and runnable tasks, plus the active run and the last report. |
| `/sdd-unblock` | Goes through `BLOCKED.md` with you. It records your answers, ticks tasks you did by hand and lifts holds. |

When a feature is new, the order is: `/speckit-specify` → `/speckit-plan` → `/speckit-tasks` (which now writes the tags) → `/speckit-analyze` → `/sdd-run` → `/sdd-unblock` → `/sdd-run` …

### Running it unattended

Permission prompts stall a run. Start the session like this:

```
claude --permission-mode dontAsk
```

In `dontAsk` mode, anything not on the allowlist is refused instead of waiting for you. A refused action shows up as a blocked task, not as a hung run.
With the sandbox from `/sdd-init`, sandboxed shell commands run without prompts. Their writes stay in the project, and network access is limited to the domains you allow.
Add the domains your project needs, such as model hubs or APIs, to `sandbox.network.allowedDomains`.

Two things the run relies on:
- **`Workflow` must be on the allowlist.** `/sdd-init` adds it. Without it, `dontAsk` refuses the run itself.
- **The project must be trusted.** Claude Code ignores a project's `permissions.allow` until you have opened it interactively once and accepted the trust prompt.

## Agent tags

Agent tags are written after `[P]` and `[USn]`, as in `- [ ] T057 [US1] [manual] Record …`:

| Tag | Effect |
|---|---|
| `[manual]` | A person's work. Never attempted. |
| `[decision]` | Settles an open question. Held until answered. |
| `[dep]` | Adds a package. Held unless `[allow] dependencies = true`. |
| `[download]` | Needs a large model or dataset. Held unless `[allow] downloads = true`. |
| `[eval]` | A measurement. The evaluator implements it, runs it on the real data and records the numbers. |
| `[ui]` | Verified by build and tests, and listed for you to look at. |

Held tasks do not count against their phase. A later phase that depends on it still runs. A task that was attempted and failed does count: phases that depend on its phase are skipped.

## Safety model

There are three layers, and each holds without the others:

1. **The sandbox and permission rules** (`.claude/settings.json`, from `templates/settings.json`):
   - writes stay in the project, and network access is limited to an allowlist;
   - `git push`, `reset --hard`, `rebase`, `clean` and `sudo` are denied;
   - `.env` files cannot be read.
2. **The run guard**: hooks in `hooks/register.ts`, active only while `.sdd/run.json` exists. It refuses the following for every subagent:
   - pushes, including `git -C x push`, which permission rules miss;
   - branch switches, history rewrites and `--no-verify`;
   - commits on a protected branch;
   - edits to protected paths: the constitution, `spec.md`, the settings and `.sdd/`.
   The guard fails closed: if the guard itself errors, the call is refused.
3. **The workflow's own rules**:
   - implementers never commit;
   - the read-only verifier rejects stubs, skipped tests and weakened tests;
   - the bookkeeper stages only each task's own files, and commits only when the project checks pass;
   - when the checks still fail after one repair, the work is stashed (`git stash list`), not deleted.

## Layout

```
.claude-plugin/   plugin.json, marketplace.json
agents/           sdd-implementer, sdd-verifier, sdd-evaluator, sdd-bookkeeper
skills/           sdd-init, sdd-tag, sdd-run, sdd-status, sdd-unblock
workflows/        sdd-run.js (the Workflow script /sdd-run starts)
bin/              sdd-tasks.py: parse / plan / tick / block / config (stdlib only)
hooks/            run guard (register.ts, rules.ts)
templates/        sdd-config.toml, settings.json, agent-tags.md
tests/            python3 -m unittest discover -s tests; claude plugin test .
```

## Limits

- **The phase order comes from the main session.** It reads the dependencies from `tasks.md` prose. When that prose is vague, a phase depends only on Setup and Foundational.
- **Parallel tasks share one working tree.** Their files are disjoint by construction, but the verifier's test runs can see a sibling task that is half written. Isolation in separate worktrees is not built yet.
- **The checks have to be fast enough** to run after every group.
- **`[ui]` work is not checked in a browser** during a run.
