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
4. If `.sdd/run.json` exists, a run is active. Give its branch and the number of commits on it so far (`git log --oneline <protected base>..<branch>`), and point the user to `/workflows` for live progress.
5. Give the latest `.sdd/runs/*.md` report in one line: when it ran, the counts, and whether it stopped early.
6. Suggest the single most useful next step: `/sdd-unblock`, `/sdd-tag`, `/sdd-run`, or reviewing the `auto/` branch.
