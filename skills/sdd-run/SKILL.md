---
name: sdd-run
description: Run the open tasks of the current Spec Kit feature unattended with subagents. Each task is implemented, verified and committed on an auto/ branch, and anything that needs a person goes to BLOCKED.md. Use when the user asks to build, implement or finish the feature without supervision.
argument-hint: "[--phases 7,8] [--no-converge] [--max-tasks N] [--profile NAME] [--yes]"
---

# /sdd-run

You start and finish an unattended run. Subagents in a workflow do the work.
Your jobs are the preflight checks, bringing `tasks.md` up to date with the code, one confirmation, starting the workflow, and closing the run.
This skill is the user's opt-in to the Workflow tool.

Paths: this skill's base directory is `<plugin>/skills/sdd-run`, so `<plugin>` is two levels up from it.
- Helper: `python3 <plugin>/bin/sdd-tasks.py`
- Workflow: the plugin's saved workflow `speckit-agents:sdd-run` (source: `<plugin>/workflows/sdd-run.js`). Call it by `name`, never by `scriptPath`: a session in the user's project cannot read the plugin directory, so the Workflow tool refuses the path.

Arguments (`$ARGUMENTS`): `--phases a,b` runs exactly those phase ids and skips step 2. `--no-converge` skips step 2. `--max-tasks N` caps how many tasks it attempts. `--profile NAME` names the `[profiles.NAME]` table of `.sdd/config.toml` the run was launched with (step 1 checks it; the report records it). `--yes` skips the confirmation.

## 1. Preflight. Stop and tell the user at the first failure.
1. `.sdd/config.toml` exists. If it does not, say to run `/sdd-init` first.
   Load the config: `python3 <helper> config`. If its `errors` list is not empty, stop: print each entry on its own line and say to fix `.sdd/config.toml`, then start `/sdd-run` again (data-model §3.8).
   With `--profile NAME`: if the config has no `profiles.NAME` table, stop and say `unknown profile NAME`. Otherwise only record the name for the report (step 7): an interactive session cannot switch its model or provider, so the profile's `model` and `env` take effect only when `bin/sdd-supervise.py --profile NAME` starts the run.
   The feature folder is `run.feature`. If that is empty, read `feature_directory` from `.specify/feature.json`.
   The tasks file is `<feature>/tasks.md`. The blocked file is `<feature>/BLOCKED.md`.
   If `.sdd/run.json` exists, an earlier run stopped without closing (a usage limit, the supervisor pausing it, a crash or a kill). Say so, with its `run_id`. Its commits stay on its branch, and its unticked tasks run again.
   - **Resume.** If it has `workflow_dir` and you are on its branch, first check that its tasks have not changed since it started: `python3 <helper> changed <tasks> --run-json .sdd/run.json`. If `changed` is not empty, stop, keep `.sdd/run.json` and the tree as they are, and say (data-model §11): "These tasks changed since run <its run id> started: <the ids in `changed`>. Their interrupted work is in the tree — review it, then stash it (`git stash push -u -m …`) and start again." Never guess which version of a changed task the interrupted work follows.
     Otherwise read what it finished: `python3 <helper> checkpoint <workflow_dir>/journal.jsonl`. If the result's `tasks` is not empty, this run resumes it: keep the tree exactly as it is (the interrupted work is in it; never stash it), keep the checkpoint for step 6, and use the earlier run's `phases` as `--phases`. Skip step 2 and items 4 and 5 below: the tree is not clean on purpose, and the checks passed when that run started. Say which tasks resume and at which step. Remove the file and go on with item 2.
   - Otherwise, if the tree is not clean and you are on that run's branch, the changes are its unfinished work: the guard was on, so only the run's agents wrote files. Stash them with `git stash push -u -m "sdd-run <its run id>: unfinished work"`, and say so in the run report. Then remove the file and go on.
   - If you are on another branch, stop and show the files: they may be the user's.
2. The tasks file from item 1 (`<feature>/tasks.md`) must exist.
3. `.gitignore` lists all four of these lines (data-model §7):
   ```
   .sdd/run.json
   .sdd/scratch/
   .sdd/runs/*/
   .sdd/plugin-feedback.jsonl
   ```
   If it lacks any of them, or does not exist, stop and say: "Run `/sdd-init` to update `.gitignore`, commit it, then start `/sdd-run` again." Never edit `.gitignore` yourself.
4. `git status --porcelain` is empty: no uncommitted or untracked files that are not ignored.
   A run needs a clean starting point, so that each commit holds one task's work and nothing else.
   If the tree is not clean, list the files and stop. Never stash or commit the user's work yourself.
5. Run every `checks.commands` entry. Run each command as its own Bash call, exactly as written: no pipes, `;`, `&&`, `cd`, loops, `$?` or redirects. The tool result shows the exit status and the output. Unattended permission modes refuse compound commands. If any fails, stop and show the failing output. If it fails with "Operation not permitted" on a path outside the project, such as a tool cache, the sandbox is the cause: name the `sandbox.filesystem.allowWrite` entry the user should add to `.claude/settings.json`.
   The run cannot tell a regression from a failure that was already there.

## 2. Bring tasks.md up to date with the code
Unticked boxes are not evidence that work is missing: code often lands without its boxes being ticked. Spec Kit's `/speckit-converge` checks the code against the spec, plan and every task, and appends only the remaining work as a new `## Phase N: Convergence`. Use it; do not judge task completion yourself.
Skip this step with `--phases` or `--no-converge`.
1. Parse the tasks file: `python3 <helper> parse <tasks>`, and plan the newest phase whose title starts with `Convergence`: `python3 <helper> plan <tasks> --blocked <blocked> --phases <its id>`. If it still has **runnable** tasks, do not converge again (it would add the same gaps twice): go to item 4. Open tasks that are only held (tagged for a person, or `wontfix` in BLOCKED.md) don't count: they may stay open for a long time, and the spec may have changed since that phase was written, so converge again.
2. Invoke the **Skill** `speckit-converge`. It writes only `tasks.md`.
   If the `speckit-converge` skill is not installed, say that Spec Kit is older than the converge command, and go on to step 3 without converging, running all open tasks.
3. If it reports converged and no Convergence phase has open tasks, report that the feature is built and stop.
4. The run's phases are every `Convergence` phase with open tasks. Older open tasks stay unticked; converge has already turned what they still need into the new tasks.
5. Tag the open tasks of those phases. Follow `<plugin>/skills/sdd-tag/SKILL.md` steps 2 and 4, applied to those tasks only, and skip its confirmation: the confirmation in step 5 covers it. Keep the table of tags and reasons for the confirmation and the run report.
The changed `tasks.md` stays uncommitted until the run branch exists (step 5).

## 3. Plan
1. Plan: `python3 <helper> plan <tasks> --blocked <blocked> --phases <the phases from step 2, or --phases> [--max-tasks …]`.
   Without `--phases` and without a Convergence phase (step 2 skipped), plan all open phases.
   If `runnable` is 0, report what is held and stop.
2. When step 2 did not tag: look for open tasks that are not tagged but plainly need a person: recording, listening, judging by ear or eye, logging in, buying, choosing between options the spec leaves open.
   If you find any, list them and recommend `/sdd-tag` first. Stop unless the user says to go on.

## 4. Phase dependencies
Read the tasks file's "Dependencies & Execution Order" section (or the nearest equivalent) and build `phase_deps`: a map from each planned phase id to the phase ids it depends on.
Use only phase ids that appear in the parse output.
If the section is missing or vague for a phase, make that phase depend on the Setup and Foundational phases only (usually `1` and `2`). A Convergence phase depends on no other phase: it holds what is left after the others.

## 5. Confirm once (skip with `--yes`)
Show the following in a few lines, then ask whether to start:
- what converge found (the new Convergence phase and its task count) and the tags you gave, with reasons;
- the number of runnable tasks and phases, the groups that will run in parallel, and the held tasks with their tags;
- **waiting on you:** every held task as one line—`T### [tag] question`—for `manual`, `decision`, `dep`/`download` (not allowed) and any terminal BLOCKED.md entry. These are not attempted; the user answers them with `/sdd-unblock`.
- the branch name, `auto/<run id>`, where the run id is `YYYYMMDD-HHMM` in local time;
- what is allowed (dependencies, downloads), the retry limit, and the checks;
- that it uses about 3 agents per task, and that the user can watch it with `/workflows`.

Remind the user that permission prompts stop an unattended run, and that a headless `claude -p` run needs `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` or its workflow is stopped after 10 minutes. They should start Claude Code in a permission mode that will not prompt for the commands the run allowlists (see the README).

## 6. Start
1. `git switch -c <branch_prefix><run id>`. If the current branch already starts with `<branch_prefix>`, a run is being continued: stay on it, and use its name as `branch` in `run.json`.
   If step 2 changed `tasks.md`, commit it on the new branch as `chore(sdd): converge and tag tasks`, with the commit trailer.
2. Write `.sdd/run.json` with the keys `run_id`, `branch`, `protected_branches` and `protected_paths`, all taken from the config, `phases`: the list of phase ids you planned, and `task_snapshot`: the JSON output of `python3 <helper> snapshot <tasks>`, taken after any converge-and-tag commit (data-model §3.6, §11). A later resume compares the tasks against it.
   From here on, the plugin's guard refuses pushes, branch switches, history rewrites, commits on protected branches and edits to protected paths, for every subagent.
3. Call **Workflow** with `name: "speckit-agents:sdd-run"` and these `args`. Pass them as a JSON object, never as a string, and never pass this skill's own arguments (`--yes`, …) as `args`:
   ```json
   { "run_id": "...", "root": "<absolute project root>", "tasks_file": "<absolute>", "blocked_file": "<absolute>",
     "helper": "<absolute path of sdd-tasks.py>", "plan": <plan output>, "phase_deps": { ... },
     "checks": [ ... ], "max_retries": <run.max_retries>, "allow": <allow table>,
     "commit_trailer": "<the attribution trailer your instructions give for commits, or empty>",
     "resume": <the checkpoint from step 1 when resuming, else null>,
     "agentModels": <the config's agents table, or {}>,
     "taskAllow": { "<task id>": { "dependencies": true|false, "downloads": true|false }, ... } }
   ```
   `taskAllow` gives per-task permission overrides (for example a `[dep:ok]` task may add a dependency even when `[allow] dependencies = false`). Build it from the tasks' tags: a task tagged `[dep:ok]` gets `{ dependencies: true }`, one tagged `[download:ok]` gets `{ downloads: true }`.
   The Workflow result names the run's `Transcript dir`. Add it to `.sdd/run.json` as `workflow_dir` right away: if the run is cut off, the next `/sdd-run` reads its journal there and resumes instead of starting the tasks over.
   At the same moment, record the directory in `.sdd/runs/<run id>/workflows.json`, `{"workflow_dirs": ["<absolute transcript dir>", …]}`, oldest first: create the file (and its directory) if it is missing, and when resuming, append the new directory to its list. `python3 <helper> events . <run id>` reads the run's events from these directories.
4. Tell the user in one line that the run started, on which branch, and that `/workflows` shows progress. Then end your turn. The workflow notifies you when it finishes.

## 7. When the workflow finishes
If `stopped` starts with `interrupted`, the run was cut off (usage limit or stop) and is meant to be resumed:
- Write the report as below, marked **interrupted**, with the `interrupted` and `resumed` lists, and commit only the report file (`git add -- .sdd/runs/<run id>.md`): the tree holds the interrupted work.
- Keep `.sdd/run.json`: the next `/sdd-run` (or `bin/sdd-supervise.py` after the reset) resumes from it.
- Notify `sdd run <id>: interrupted, <n> done; resumes on the next /sdd-run`, and tell the user the same. Then stop; skip the items below.

Otherwise:
1. Write `.sdd/runs/<run id>.md` with:
   - what converge appended, and the tags with their reasons;
   - the approved tasks and their commits (`git log --oneline <base>..HEAD`);
   - the blocked tasks with their questions;
   - **waiting on you:** every held task as `T### [tag] question` (manual, decision, dep/download not allowed, terminal BLOCKED.md entry);
   - for `[decision]` held tasks, the options and trade-offs (or link to the research file);
   - for `[manual]` held tasks, the how-to steps (where, commands, what done looks like);
   - the skipped tasks with their reasons, the held tasks, the phase states, and `stopped` if it is set;
   - the profile: the `--profile` name, or "none" (data-model §11);
   - the model per role (`implementer`, `verifier`, `evaluator`, `bookkeeper`): the config's `agents.<role>`, or "run's model" when it has none;
   - the run's events: `python3 <helper> events . <run id>`.
2. Commit the report as `chore(sdd): run report <run id>`, with the trailer.
3. Remove `.sdd/run.json`.
4. Send a push notification if the PushNotification tool is available: `sdd run <id>: <n> done, <m> blocked`.
5. Tell the user:
   - the counts;
   - the blocked questions, which they answer with `/sdd-unblock`;
   - how to review: `git log main..<branch>` and `git diff main...<branch>`.
   Merging is the user's call. Never merge, push or switch back to `main` yourself.

If the workflow fails or is stopped and gives no result, write the report with what is known, say clearly that the run did not finish, and keep `.sdd/run.json`: the next `/sdd-run` resumes from the journal.
