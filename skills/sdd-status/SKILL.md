---
name: sdd-status
description: Show where the current Spec Kit feature stands for unattended runs - open, held, blocked and runnable tasks, the active run, and the last run report. Use when the user asks for progress or what is left.
---

# /sdd-status

`<plugin>` is two levels above this skill's base directory. Report briefly.

1. Find the tasks file, using `.sdd/config.toml` or `.specify/feature.json`.
2. Run `python3 <plugin>/bin/sdd-tasks.py plan <tasks> --blocked <feature>/BLOCKED.md`, plus `parse` for the totals.
   Report the tasks done and open, the runnable tasks by phase, and the held tasks by tag.
3. Open entries in `BLOCKED.md`: give the id and the question, one line each.
4. If `.sdd/run.json` exists, a run is active or was interrupted. Give its branch and the number of commits on it so far (`git log --oneline <protected base>..<branch>`).
   If it has `workflow_dir`, run `python3 <plugin>/bin/sdd-tasks.py checkpoint <workflow_dir>/journal.jsonl` and say which tasks a resume would continue, and at which step.
   If `~/.local/state/speckit-agents/<slug>/status.json` exists (`<slug>` is the absolute project path with every run of characters other than letters and digits turned into `-`, without a leading `-`), the supervisor (`bin/sdd-supervise.py`) is handling it: give its `state`, the `usage` per window (utilization and `resets_at`, epoch seconds, in local time) against its `threshold` (per window, `*` for the others), and for `waiting` the reset it waits for.
   Otherwise point the user to `/workflows` for live progress, or to `/sdd-run` to resume an interrupted run.
5. Give the latest `.sdd/runs/*.md` report in one line: when it ran, the counts, and whether it stopped early.
6. Suggest the single most useful next step: `/sdd-unblock`, `/sdd-tag`, `/sdd-run`, or reviewing the `auto/` branch.
