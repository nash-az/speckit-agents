---
name: sdd-init
description: Set up a Spec Kit project for unattended /sdd-run. Writes the run config and project checks, adds the agent-tags section to the tasks template, and merges the safety settings. Use once per project, or to refresh the setup.
---

# /sdd-init

Set up this project for `/sdd-run`. Show every file you are about to write or change, and get the user's yes before writing.
Paths: `<plugin>` is two levels above this skill's base directory.

## 1. Check that Spec Kit is present
`.specify/` and `.specify/templates/tasks-template.md` must exist. If they do not, tell the user to run `specify init --here --ai claude` first, and stop.

## 2. Run config: `.sdd/config.toml`
Start from `<plugin>/templates/sdd-config.toml`.
- `[checks] commands`: these must be the project's real test, lint and type-check commands, each run from the project root and each exiting non-zero on failure.
  Find them in `pyproject.toml`, `package.json` scripts, `Makefile`, `justfile`, CI workflows, the README and `quickstart.md`.
  Prefer fast, non-interactive commands: for example `cd backend && uv run pytest -q`, or `pnpm -C frontend test --run`.
  Prefer a form that needs no `cd`, such as `uv run --directory backend pytest -q`, so it can be allowlisted as one rule.
  Run each one now. Run each command as its own Bash call, exactly as written: no pipes, `;`, `&&`, `cd`, loops, `$?` or redirects. The tool result shows the exit status and the output. Unattended permission modes refuse compound commands. Show the user which pass. A command that fails now must not be listed until the user fixes it or confirms it should be left out.
- `[run] feature`: leave it empty to follow `.specify/feature.json`.
- `[allow]`: keep both settings `false` unless the user says otherwise.
- `protected_paths`: keep the defaults, and add anything the user names.

## 3. Tasks template with agent tags
The override goes in `.specify/templates/overrides/tasks-template.md`, which Spec Kit reads ahead of the core template.
- If the override does not exist, copy the project's current `.specify/templates/tasks-template.md` and add the contents of `<plugin>/templates/agent-tags.md` after the "Format" section, or at the end if there is none.
- If the override exists and has no `## Agent tags` section, add the section the same way.
- If it already has the section, replace that section with the current one.
From now on, `/speckit-tasks` writes the tags.

## 4. Safety settings
Read plugin files with the Read tool; `ls` and `cat` outside the project are refused.
Claude Code protects `.claude/settings.json`: a headless or pre-approved session cannot write it, only a person approving the write. If the write is refused, print the complete merged file in a code block and tell the user to save it, so they need not assemble it.
Merge `<plugin>/templates/settings.json` into `.claude/settings.json`:
- union the `permissions.allow`, `permissions.deny` and `sandbox` lists;
- never remove what the user already has;
- show the merged diff before writing.
Add the allow rules for the commands you chose in step 2, for example `Bash(uv run pytest *)`.
The sandbox blocks writes outside the project, so add each check tool's cache to `sandbox.filesystem.allowWrite`. Ask the tool where its cache is, for example `uv cache dir` (usually `~/.cache/uv`), `pnpm store path` or `npm config get cache`, and write the path with `~` in place of the home directory.
Once the settings are written, run the checks from step 2 again, because the sandbox is active only from that point. If a check passed before and fails now, the sandbox is the cause: show the error, propose the `allowWrite` or `allowedDomains` entry that fixes it, and do not go on until every check passes.
Explain in two lines that the sandbox and the deny list hold even when nobody is watching, and that the plugin's guard adds a stricter set while a run is active.

## 5. Ignore files
Add these five lines to `.gitignore`, each only if it is not there yet:
```
.sdd/run.json
.sdd/scratch/
.sdd/runs/*/
.sdd/plugin-feedback.jsonl
.sdd/jobs/
```
`.sdd/runs/*/` ignores the per-run directories only. The config and the run reports in `.sdd/runs/` (`.sdd/runs/<id>.md`) stay tracked.
Ask the user to commit `.gitignore`: `/sdd-run` stops in preflight while any of the five lines is missing.

## 6. Next step
Say `/sdd-run` is ready. It runs `/speckit-converge` first, so it builds only what the code still lacks, and it tags the new tasks itself. `/sdd-tag` is only needed to tag existing tasks for a `--phases` run.
