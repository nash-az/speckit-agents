---
name: sdd-run
description: Run the open tasks of the current Spec Kit feature unattended with subagents. Each task is implemented, verified and committed on an auto/ branch, and anything that needs a person goes to BLOCKED.md. Use when the user asks to build, implement or finish the feature without supervision.
argument-hint: "[--phases 7,8] [--max-tasks N] [--yes]"
---

# /sdd-run

You start and finish an unattended run. Subagents in a workflow do the work.
Your jobs are the preflight checks, one confirmation, starting the workflow, and closing the run.
This skill is the user's opt-in to the Workflow tool.

Paths: this skill's base directory is `<plugin>/skills/sdd-run`, so `<plugin>` is two levels up from it.
- Helper: `python3 <plugin>/bin/sdd-tasks.py`
- Workflow: the plugin's saved workflow `speckit-agents:sdd-run` (source: `<plugin>/workflows/sdd-run.js`). Call it by `name`, never by `scriptPath`: a session in the user's project cannot read the plugin directory, so the Workflow tool refuses the path.

Arguments (`$ARGUMENTS`): `--phases a,b` limits the run to those phase ids. `--max-tasks N` caps how many tasks it attempts. `--yes` skips the confirmation.

## 1. Preflight. Stop and tell the user at the first failure.
1. `.sdd/config.toml` exists. If it does not, say to run `/sdd-init` first.
2. Load the config: `python3 <helper> config`.
   The feature folder is `run.feature`. If that is empty, read `feature_directory` from `.specify/feature.json`.
   The tasks file is `<feature>/tasks.md` and must exist. The blocked file is `<feature>/BLOCKED.md`.
3. `git status --porcelain` is empty: no uncommitted or untracked files that are not ignored.
   A run needs a clean starting point, so that each commit holds one task's work and nothing else.
   If the tree is not clean, list the files and stop. Never stash or commit the user's work yourself.
4. Run every `checks.commands` entry. If any fails, stop and show the failing output. If it fails with "Operation not permitted" on a path outside the project, such as a tool cache, the sandbox is the cause: name the `sandbox.filesystem.allowWrite` entry the user should add to `.claude/settings.json`.
   The run cannot tell a regression from a failure that was already there.
5. Plan: `python3 <helper> plan <tasks> --blocked <blocked> [--phases …] [--max-tasks …]`.
   If `runnable` is 0, report what is held and stop.
6. Look for open tasks that are not tagged but plainly need a person: recording, listening, judging by ear or eye, logging in, buying, choosing between options the spec leaves open.
   If you find any, list them and recommend `/sdd-tag` first. Stop unless the user says to go on.

## 2. Phase dependencies
Read the tasks file's "Dependencies & Execution Order" section (or the nearest equivalent) and build `phase_deps`: a map from each planned phase id to the phase ids it depends on.
Use only phase ids that appear in the parse output.
If the section is missing or vague for a phase, make that phase depend on the Setup and Foundational phases only (usually `1` and `2`).

## 3. Confirm once (skip with `--yes`)
Show the following in a few lines, then ask whether to start:
- the number of runnable tasks and phases, the groups that will run in parallel, and the held tasks with their tags;
- the branch name, `auto/<run id>`, where the run id is `YYYYMMDD-HHMM` in local time;
- what is allowed (dependencies, downloads), the retry limit, and the checks;
- that it uses about 3 agents per task, and that the user can watch it with `/workflows`.

Remind the user that permission prompts stop an unattended run. They should start Claude Code in a permission mode that will not prompt for the commands the run allowlists (see the README).

## 4. Start
1. `git switch -c <branch_prefix><run id>`
2. Make sure `.gitignore` lists `.sdd/run.json`.
   If you added that line, commit the change on the new branch as `chore(sdd): ignore run marker`, with the commit trailer.
3. Write `.sdd/run.json` with the keys `run_id`, `branch`, `protected_branches` and `protected_paths`, all taken from the config.
   From here on, the plugin's guard refuses pushes, branch switches, history rewrites, commits on protected branches and edits to protected paths, for every subagent.
4. Call **Workflow** with `name: "speckit-agents:sdd-run"` and these `args`. Pass them as a JSON object, never as a string, and never pass this skill's own arguments (`--yes`, …) as `args`:
   ```json
   { "run_id": "...", "root": "<absolute project root>", "tasks_file": "<absolute>", "blocked_file": "<absolute>",
     "helper": "<absolute path of sdd-tasks.py>", "plan": <plan output>, "phase_deps": { ... },
     "checks": [ ... ], "max_retries": <run.max_retries>, "allow": <allow table>,
     "commit_trailer": "<the attribution trailer your instructions give for commits, or empty>" }
   ```
5. Tell the user in one line that the run started, on which branch, and that `/workflows` shows progress. Then end your turn. The workflow notifies you when it finishes.

## 5. When the workflow finishes
1. Write `.sdd/runs/<run id>.md` with:
   - the approved tasks and their commits (`git log --oneline <base>..HEAD`);
   - the blocked tasks with their questions;
   - the skipped tasks with their reasons, the held tasks, the phase states, and `stopped` if it is set.
2. Commit the report as `chore(sdd): run report <run id>`, with the trailer.
3. Remove `.sdd/run.json`.
4. Send a push notification if the PushNotification tool is available: `sdd run <id>: <n> done, <m> blocked`.
5. Tell the user:
   - the counts;
   - the blocked questions, which they answer with `/sdd-unblock`;
   - how to review: `git log main..<branch>` and `git diff main...<branch>`.
   Merging is the user's call. Never merge, push or switch back to `main` yourself.

If the workflow fails or is stopped, run step 5 anyway with what is known, and say clearly that the run did not finish.
To pick up where the run stopped, run Workflow again with `name: "speckit-agents:sdd-run"`, the same `args` and `resumeFromRunId`. Write a new `.sdd/run.json` first if you removed it.
