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
  Run each one now. Show the user which pass. A command that fails now must not be listed until the user fixes it or confirms it should be left out.
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
Merge `<plugin>/templates/settings.json` into `.claude/settings.json`:
- union the `permissions.allow`, `permissions.deny` and `sandbox` lists;
- never remove what the user already has;
- show the merged diff before writing.
Add the allow rules for the commands you chose in step 2, for example `Bash(uv run pytest *)`.
The sandbox blocks writes outside the project, so add each check tool's cache to `sandbox.filesystem.allowWrite`. Ask the tool where its cache is, for example `uv cache dir` (usually `~/.cache/uv`), `pnpm store path` or `npm config get cache`, and write the path with `~` in place of the home directory.
Once the settings are written, run the checks from step 2 again, because the sandbox is active only from that point. If a check passed before and fails now, the sandbox is the cause: show the error, propose the `allowWrite` or `allowedDomains` entry that fixes it, and do not go on until every check passes.
Explain in two lines that the sandbox and the deny list hold even when nobody is watching, and that the plugin's guard adds a stricter set while a run is active.

## 5. Ignore files
Add `.sdd/run.json` to `.gitignore`. The config and the run reports in `.sdd/runs/` stay tracked.

## 6. Next step
If `tasks.md` exists and has open tasks without tags, offer `/sdd-tag`. Otherwise say `/sdd-run` is ready.
