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
| `/sdd-init` | Once per project. Writes `.sdd/config.toml` (the project checks and limits), adds the agent-tags section to the tasks template (`.specify/templates/overrides/`), merges the safety settings into `.claude/settings.json`, and adds the plugin's runtime files to `.gitignore` (below). |
| `/sdd-tag` | Adds agent tags to the open tasks of an existing `tasks.md`. |
| `/sdd-run [--phases 7,8] [--no-converge] [--max-tasks N] [--profile NAME] [--yes]` | Preflight (`.gitignore` lines, clean tree, valid config, checks pass), one confirmation, then the unattended run. |
| `/sdd-status` | Lists open, held, blocked and runnable tasks, plus the active run (or a stale one) and the last report. |
| `/sdd-unblock` | Goes through `BLOCKED.md` with you. It records your answers, ticks tasks you did by hand, and marks entries `resolved`, `wontfix` or `moved`. |

When a feature is new, the order is: `/speckit-specify` → `/speckit-plan` → `/speckit-tasks` (which now writes the tags) → `/speckit-analyze` → `/sdd-run` → `/sdd-unblock` → `/sdd-run` …

### Blocked tasks and your answers

Each entry in `BLOCKED.md` has a `status:` line, and it decides what the next `/sdd-run` does with the task:

| Status | Meaning |
|---|---|
| `open` | Waiting on you. The task is held. |
| `resolved` | You answered the question under `- answer:`. The task runs again on the next `/sdd-run`, and the implementer gets the question and your answer in its prompt. If it fails again, a new `open` entry is added. |
| `wontfix` | Dropped. The task is held for good. |
| `moved` | The work left this project (for example, it became product or admin work). The task is held for good. |

A task you did by hand is ticked in `tasks.md` instead. A task whose line says `superseded-by Tnnn` (or `replaced-by Tnnn`), and every open task in a phase before the newest Convergence phase, is held as `superseded`; that hold never holds the tasks that depend on it.

### Git-ignored runtime files

`/sdd-init` adds these five lines to `.gitignore`, and `/sdd-run` refuses to start until they are there. Commit `.gitignore` after `/sdd-init`.

```
.sdd/run.json
.sdd/scratch/
.sdd/runs/*/
.sdd/plugin-feedback.jsonl
.sdd/jobs/
```

`.sdd/config.toml`, the run reports (`.sdd/runs/<run id>.md`) and `.sdd/learnings.md` stay tracked.

### Models and run profiles

Each of the four agent roles can have its own model in `.sdd/config.toml`. A role without a setting uses the run's model:

```toml
[agents]
implementer = "haiku"   # an alias (haiku, sonnet, opus), "inherit" or a model id
verifier = "opus"
```

A **run profile** names the model the whole run starts with, and optionally non-secret environment settings such as a gateway URL:

```toml
[profiles.local]
model = "claude-sonnet-5-5"
env = { ANTHROPIC_BASE_URL = "http://localhost:4000" }
```

Profiles take effect only in headless runs: `bin/sdd-supervise.py --profile local` starts `claude` with `--model` and the profile's `env`. An interactive `/sdd-run --profile NAME` cannot switch model or provider; it only checks that the profile exists and records it in the report. The report lists the configured model per role.
The run refuses to start when the config names an unknown role or profile, an empty model, or an env name containing `KEY`, `TOKEN`, `SECRET` or `PASSWORD`. Secrets never go in the config; set them in your environment.

**Local and non-Claude models:** a profile that points at a gateway for a non-Claude or local model is best-effort and unsupported by Anthropic. Such models often lack the tool calling the agents need, so expect more rejected and blocked tasks.

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

To run it headless, for example from a script or overnight:

```
CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 claude -p "/sdd-run --yes" --permission-mode dontAsk
```

Without that variable, `claude -p` stops background work 10 minutes after the main loop's last turn, and the workflow dies mid-task. The process exits when the run report is written.

### Across usage limits

A run that reaches the claude.ai usage limit is **interrupted**, not failed: the workflow stops without recording anything in `BLOCKED.md`, keeps `.sdd/run.json`, and the next `/sdd-run` **resumes** it on the same branch. It reads the run's workflow journal (`sdd-tasks.py checkpoint`) and skips what already finished: an approved task goes straight to its commit, an implemented one only to the verifier, a rejected one to its next attempt with the feedback, and an attempt that was cut off continues from the edits it left in the tree.

To run unattended across several limit windows, let the supervisor start and restart the run:

```
python3 <plugin>/bin/sdd-supervise.py --project . [--run-args "--phases 15"] [--threshold five_hour=0.95,seven_day=0.99] [--profile NAME]
```

It starts `/sdd-run` headless and reads the usage that Claude Code reports in its stream (`rate_limit_event`: the five-hour and seven-day windows, from 90 % up). When a window reaches its threshold (by default 95 % for the five-hour window and 99 % for the seven-day one; one number sets every window), it waits for the next agent to finish, stops the run there, sleeps until the window resets, and starts `/sdd-run` again, which resumes. If the limit is hit before such a safe point, or a window reaches its hard stop (by default its threshold plus one point, at most 100 %: 96 % and 100 %; `--hard-stop` sets one value for every window), it stops at once, and only the step in flight is redone. It ends when a run finishes, when a reset is more than `--max-wait-hours` away (12 h; the seven-day window), or after `--max-cycles`. Its progress lines go to stdout, its logs and `status.json` to `~/.local/state/speckit-agents/<project>/`.

The simple alternative, for runs that hit the limit at most twice: start `/sdd-run` in an **interactive** session (for example in `tmux`). There, Claude Code itself pauses a workflow at the limit and continues it after the reset (claude.ai subscription, Claude Code 2.1.271 or later, `autoContinueAtUsageLimit` on). It waits at most twice per run, the session must stay open, and after a long sleep of the computer it may need an Enter. Under `claude -p` there is no such wait; that is what the supervisor is for.

A resume refuses to start when a task's description, tags, dependencies or named files changed in `tasks.md` since the run began, and lists those task ids. Ticking boxes or reformatting does not count.

### Stop and resume

You can stop a run and continue it later from the dashboard, without a terminal. This works for **supervised runs only**: start the run with `python3 <plugin>/bin/sdd-supervise.py --project .` (as above), then start the dashboard on the same project. For a run started with `/sdd-run` in a session, the buttons are disabled and show the supervisor command instead; they are also disabled when the supervisor that wrote `status.json` is no longer alive.
- **Stopping**: you clicked "Stop after current task". The running agent finishes first, so nothing is lost, and the run stops at that safe point. "Stop now" (with a confirm) stops at once; only the step in flight is redone on resume.
- **Held** ("Stopped by you"): the supervisor waits for Resume, at most `--max-hold-hours` (default 24), and then ends. Resume starts `/sdd-run` again, which reads the journal and continues; a cycle started by Resume does not count toward `--max-cycles`. Stop after current task when no task is left in the run lets the run finish and write its report instead of holding.
- **Waiting** for a usage window to reset: Stop and Resume are disabled. "Keep paused" makes the supervisor go to held at the reset instead of restarting the run; "Let it resume" cancels that before the reset.

The dashboard never starts, signals or kills a process. Each button writes only one file, `control.json`, in the supervisor's state directory (`~/.local/state/speckit-agents/<project>/`, outside the project); the supervisor reads it, acts on it and deletes it. These routes refuse requests from another origin or without a JSON body.

### Learnings and plugin feedback

At the end of every run, interrupted or not, the main session writes down two kinds of notes:
- **Project lessons**, facts about this codebase that change how the next run goes (a check that needs a flag, a path the sandbox refuses, a flaky test), as bullets under `## <run id> (<date>)` in `.sdd/learnings.md`. The file is committed with the run report.
- **Plugin feedback**, problems with speckit-agents itself (a command its own prompt told an agent to run that the guard refused, a schema error, a crash), as JSON lines in `.sdd/plugin-feedback.jsonl`. That file is git-ignored and never committed in the project.

The report says how many of each were written. To bring the plugin feedback into this repo, run from the plugin repo:

```
python3 bin/sdd-harvest.py <project dir> [--log logs/<file>.md]
```

It appends the entries it has not seen before to `logs/<date>-<project>-feedback.md` (or `--log`), under `## Harvested <date>`, and remembers their ids in `logs/.harvested.json`, so a second run adds nothing. Bad lines are skipped.

## Runs

A **run** is what you start when you want a piece of work done: a feature, a scope of tasks (all open tasks, some phases, or a list of tasks), optional limits, and one branch, `auto/<run id>`, cut from a base branch. A run is short-lived: it ends when its scope is done, when you stop it, or when a limit is reached, and its finished work then lands on the base branch by itself. The lasting record is the **task**, not the run: each task keeps its own history in the repository. A run may use several `/sdd-run` sessions (a usage-limit wait, a crash followed by Continue); they share the run's id, branch, scope and limits and are not shown as separate runs. A project has at most one open run at a time.

A run is **working**, **paused** (waiting for a usage window to reset), **suspended** (cut off with a task half done; it waits for you) or **ended**. An ended run says how it ended (done, stopped by you, a limit, waiting on you, failed, or dropped) and what happened to its work: landed (with the commit), nothing to land, or needs merge (with the files).

- **Start run.** In the dashboard's **Runs** view (`#runs`; the old `#jobs` link redirects there), open **Start run**, pick the scope (phases or tasks), set limits if you want any, and optionally a title (by default the scope in words: "Phase 11", "Phases 3–5", "T061, T063", "All open tasks"). It refuses when the working tree has changes, the project is not a git repository, no task in the scope is runnable, or another run is open: "another run is going: <title>", "a run is suspended: Continue or Drop it first", or "a run needs merge: <title>". Otherwise it records the run in `.sdd/jobs/<id>.json` (git-ignored; the internal names `sdd-job.py`, `.sdd/jobs/`, `/api/jobs` and `--job` stay), creates the branch and starts the supervisor (`bin/sdd-supervise.py --job <id>`) in the background. Without the dashboard: `python3 <plugin>/bin/sdd-launch.py . specs/<feature> --job [--phases 7,8 | --tasks T061,T063] [--max-tasks N] [--until EPOCH] [--max-runs N] [--title TEXT]`.
- **Stop after current task ends the run and lands it.** The run waits for a **task boundary**: the task in flight (and the other tasks of its group) finishes, committed or blocked after its retries, and nothing new starts. Then, with a clean tree, the run ends **stopped** ("stopped by you") and its task commits land on the base branch; the tasks it did not reach stay open for the next run. Pressed before any task has started, it ends the run at once. A stop-by time (`until`) ends the run the same way, as **limit**.
- **A cut-off suspends the run.** Stop now, a usage limit the run does not wait for, a crashed supervisor, or a pause you kept at the reset leaves the run **suspended**: `.sdd/run.json` and the half-done work stay in the tree, and the checkout stays on the run branch. A suspended run offers two buttons and waits for you as long as it takes:
  - **Continue** resumes it from its checkpoint in the same run (the same resume rules as `/sdd-run`).
  - **Drop** (with a confirm naming the half-done task) saves the uncommitted work, untracked files included, as a patch in that task's history, stashes it (`git stash list`: "sdd-run <id>: dropped <tasks>"), archives `run.json`, ends the run **dropped** and lands its finished tasks. The half-done task is open again.
  While a run is suspended and the base branch has not moved on, its finished tasks are already fast-forwarded onto the base branch, without checking it out or touching the tree.
- **Finished work lands by itself.** Whenever a run ends (done, stopped, a limit, waiting on you, failed, dropped), its task commits land on the base branch with no click: a fast-forward when possible, else one merge commit "merge: run <title>". It never forces, pushes or rewrites history. The checkout is then on the base branch, and dashboard commits go there. On a conflict the merge is aborted, nothing changes, and the run ends **needs merge** with the conflicting files; the run branch is kept, and **Merge** tries the landing again. A run whose landing could not be tried yet (for example a dirty tree at the end) shows **Land**. Until a needs-merge or unlanded run is landed, it blocks Start run. Without the dashboard: `python3 <plugin>/bin/sdd-job.py list|show <id>|land <id>|drop <id> [project]` (`land` is both Land and Merge; `drop` of a suspended run is Drop).
- **Run after run.** Between sessions the supervisor plans the scope again: runnable tasks left starts the next session; only items that need you ends the run **waiting**; nothing open in scope ends it **done**. Two sessions in a row without progress (no new ticked task and no new `BLOCKED.md` entry) end it **failed**. When a run ended waiting and you settle an item in the dashboard ("Waiting on you") that makes a task in its scope runnable, a **new run** with the same scope, limits and title starts by itself, with no click.
- **The usage checkbox.** When a session reaches the usage limit, the run is shown **paused** and waits for the reset, however long, the weekly limit included. The banner shows a checkbox "Continue when the limit resets (<HH:MM>)", checked by default. Checked, the run resumes at the reset; unchecked, it is suspended at the reset and waits for Continue or Drop.
- **Limits.** A number of tasks (`max_tasks`), a stop-by time (`until`) and a number of sessions (`max_runs`, 20 by default), counted over the whole run. Reaching one ends the run after the current task, as **limit**, with the limit named.
- **Task history.** Every task has an append-only history in `.sdd/tasks/<feature folder>/<task id>.jsonl`, committed with the session's report commit, and with the run summary (`chore(run): <id> history and summary`) when the run ends or is dropped. One line per step: started, implemented, the verifier's verdict and problems, retried, committed with its hash, blocked with the reason, suspended, resumed, dropped with the saved diff; each with its run, session, attempt, role and model. Open a task in the dashboard to read it under **History** ("Implemented (attempt 2)", "Rejected: …", "Approved", "Committed <sha>"), with a "full output" link while Claude's transcript still exists. Each task commit also carries the trailers `Task: <id>`, `Run: <run id>`, `Attempts: <n>` and `Model: <implementer>`. Without the dashboard: `python3 <plugin>/bin/sdd-history.py show . <feature folder> <task id>`.
- **Run summary.** When a run ends, a summary is committed once to `.sdd/runs/<run id>.json`: times, sessions, scope, how it ended and its landing, the tasks done, blocked and dropped, attempts, first-time approvals and models per role. A session that ended without its own report (a stop at a boundary, a Drop) gets a short one written from its journal.

The banner and the Overview say in words what the run is doing, never its id: "Working on <title>: <task>, <step>", "Paused until <time>: usage limit", "Suspended during <task>: <reason>. Continue or Drop", "Stopping after <task>", and after the end "<title> ended: <n> tasks landed on <base>". Open runs come first in the Runs view; a card shows Continue and Drop when suspended, Merge when it needs merge, Land when its landing is still to do, and no action otherwise.

Records from 005 (Jobs) carry over without being rewritten: a merged or dropped Job reads as an ended run, an open Job whose `.sdd/run.json` is still there as a suspended run, and an open Job without it as an ended run offered **Land**.

**A plain `/sdd-run` still works.** `/sdd-run` and `bin/sdd-supervise.py` without `--job` behave as before (see [Stop and resume](#stop-and-resume)): they get the commit trailers and task histories, but no automatic landing or task-boundary stop, and their runs show under "Earlier runs" in the Runs view.

## Dashboard

```
python3 <plugin>/bin/sdd-dashboard.py [--project DIR] [--port 8765]
```

A read-only view of one project, the one it was started with (the current directory by default); it never scans for others. It works without any AI model. Its only write is the run control file of a supervised run (see [Stop and resume](#stop-and-resume)), never in the project.
- **Documents** of the active feature (`run.feature` in the config, else `.specify/feature.json`, else the only folder under `specs/`; with several and neither set, it asks): spec, plan, research, data model, quickstart, tasks, `BLOCKED.md` and `decisions.md`, plus the constitution, `.sdd/learnings.md`, `.sdd/config.toml` and the run reports. Markdown is rendered with tables, highlighted code and a table of contents for long documents. It serves only these fixed files, never an arbitrary path. The renderers load from jsDelivr; offline, it falls back to plain text.
- **Tasks and phases** with one status vocabulary (icon, color and label), a "Waiting on you" list of held and blocked tasks with their questions, the runs and their events (read from the run's own workflow journal), a running task's live agent output, and a red banner for a stale `.sdd/run.json`.
- **Localhost only.** It binds to `127.0.0.1`, has no authentication, and answers 421 to any request whose `Host` is not `127.0.0.1`, `localhost` or `[::1]` (against DNS rebinding). There is deliberately no option to listen on another address.

### Browser tests

The dashboard's views are tested in a real browser (`tests/ui/`, FR-031). Each test starts the dashboard on a temporary toy project, opens it in Chromium through Playwright for Python, and fails on any console error, uncaught exception or failed same-origin request. The CDN scripts are served from pinned copies in `tests/ui/vendor/`, so the tests run offline. When a test fails, its screenshot and page HTML are saved under `.sdd/scratch/ui/`.

Install Playwright and its Chromium once, for the `python3` you run the tests with:

```
python3 -m pip install playwright
python3 -m playwright install chromium
```

If the bundled Chromium is missing or will not start, the tests use an installed Google Chrome instead.

Run only the UI tests:

```
SDD_UI_TESTS=1 python3 -m unittest discover -s tests/ui
```

- **They skip when the browser is missing.** Without Playwright, or when neither Chromium nor Google Chrome launches, each test skips with a one-line reason that names the install command. `SDD_UI_TESTS=1` turns that skip into a failure, so a missing browser cannot pass for a green run.
- **They are not in `checks.commands` by default.** `/sdd-init` does not add them, and `python3 -m unittest discover -s tests` does not collect `tests/ui/`. A run's checks therefore do not need a browser. Add the command above to `checks.commands` in `.sdd/config.toml` yourself if your runs can start a browser. Inside the run sandbox that also needs `"allowLocalBinding": true` under `sandbox.network`, so the dashboard can bind its port.

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
| `[dep:ok]` / `[download:ok]` | Added by you next to `[dep]` / `[download]` to approve that one task when the run does not allow it. Its implementer is told it is allowed. |

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
   - edits to protected paths: the constitution, `spec.md`, the settings and `.sdd/`, except `.sdd/scratch/` (a task's throw-away files go in `.sdd/scratch/<run id>/<task id>/`).
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
bin/              sdd-tasks.py: parse / plan / tick / block / config / checkpoint / snapshot / changed / events;
                  sdd-supervise.py: pause before the usage limit, resume after; run profiles;
                  sdd-job.py: run records, landing, Drop; sdd-history.py: task histories, run summaries;
                  sdd-dashboard.py: the read-only dashboard;
                  sdd-harvest.py: copy plugin feedback into logs/ (all stdlib only)
hooks/            run guard (register.ts, rules.ts)
templates/        sdd-config.toml, settings.json, agent-tags.md
logs/             run logs and harvested plugin feedback
tests/            python3 -m unittest discover -s tests; node --test tests/workflow.test.mjs;
                  claude plugin test .
```

## Limits

- **The phase order comes from the main session.** It reads the dependencies from `tasks.md` prose. When that prose is vague, a phase depends only on Setup and Foundational.
- **Parallel tasks share one working tree.** Their files are disjoint by construction, but the verifier's test runs can see a sibling task that is half written. There is no worktree isolation (a config that still sets `isolation = "worktree"` is refused); it is to be rebuilt later on the Workflow's native per-agent isolation.
- **The checks have to be fast enough** to run after every group.
- **`[ui]` work is not checked in a browser** during a run.
