---
name: sdd-status
description: Show where the current Spec Kit feature stands for unattended runs - open, held, blocked and runnable tasks, the active run, and the last run report. Use when the user asks for progress or what is left.
---

# /sdd-status

`<plugin>` is two levels above this skill's base directory. Report briefly.

1. Find the tasks file, using `.sdd/config.toml` or `.specify/feature.json`.
2. Run `python3 <plugin>/bin/sdd-tasks.py plan <tasks> --blocked <feature>/BLOCKED.md`, plus `parse` for the totals.
   Report the tasks done and open, the runnable tasks by phase, and the held tasks by tag.
   **Waiting on you:** list every held task as one line—`T### [tag] question`—for `manual`, `decision`, `dep`/`download` (not allowed) and any terminal BLOCKED.md entry. Include the question from the held reason or from the BLOCKED.md entry, so the user can act on it without opening BLOCKED.md.
   Show tasks in a `moved`/`product` state apart from the others, since they are done but moved into product use rather than dropped.
3. Open entries in `BLOCKED.md`: give the id and the question, one line each.
4. Give the run state, using the same rules as the dashboard's `GET /api/run/current` (data-model §4.5, FR-017). The first rule that matches wins:
   1. no `.sdd/run.json` → `none` (no run; skip the rest of this step);
   2. `.sdd/runs/<run_id>.md` exists for `run.json`'s `run_id` → `paused` (an interrupted run waiting to resume; its report is written and `run.json` is kept);
   3. the supervisor's `status.json` (path below) exists, its `pid` is a live process and its `state` is not `"stopped"` → `paused`, except `running` or `stopping` → `running`; a dead supervisor's file (any `state`) is skipped;
   4. `run.json`'s `workflow_dir` is absolute, its name matches `^wf_[A-Za-z0-9_-]+$`, it is a directory containing `journal.jsonl`, and that `journal.jsonl` or any `agent-*.jsonl` in it was modified less than 30 minutes ago → `running`;
   5. otherwise → `stale`. Report it as the dashboard's banner does: "Run <id> left `.sdd/run.json` behind and is not running. Start `/sdd-run` to resume or clean it up."

   For any state other than `none`, give the state, the run's branch and the number of commits on it so far (`git log --oneline <protected base>..<branch>`).
   If it has `workflow_dir`, run `python3 <plugin>/bin/sdd-tasks.py checkpoint <workflow_dir>/journal.jsonl` and say which tasks a resume would continue, and at which step.
   If `~/.local/state/speckit-agents/<slug>/status.json` exists (`<slug>` is the absolute project path with every run of characters other than letters and digits turned into `-`, without a leading `-`), the supervisor (`bin/sdd-supervise.py`) is handling it: give its `state`, the `usage` per window (utilization and `resets_at`, epoch seconds, in local time) against its `threshold` (per window, `*` for the others), and for `waiting` the reset it waits for.
   **Stop and resume:** the dashboard's buttons ask the supervisor to stop or hold (data-model §12). For `stopping`, say the run stops after the current task (its `mode`, `after-task` unless `now`). For `held`, give its `reason`: `stopped by operator` (the person clicked Stop) or `kept paused; the usage window has reset` (the person clicked "Keep paused"); the supervisor waits for Resume from the dashboard, at most `--max-wait-hours`. For `waiting` with `keep_paused: true`, say it will go to held at the reset instead of restarting the run.
   Otherwise point the user to `/workflows` for live progress, or to `/sdd-run` to resume an interrupted run.
5. Give the latest `.sdd/runs/*.md` report in one line: when it ran, the counts, and whether it stopped early.
6. Suggest the single most useful next step: `/sdd-unblock`, `/sdd-tag`, `/sdd-run`, or reviewing the `auto/` branch.
