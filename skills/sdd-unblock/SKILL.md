---
name: sdd-unblock
description: Go through the open entries in BLOCKED.md with the user - record answers, mark tasks the user did by hand as done, and lift holds so the next /sdd-run picks the tasks up. Use after an unattended run, or when the user wants to answer blocked questions.
---

# /sdd-unblock

`<plugin>` is two levels above this skill's base directory. The helper is `python3 <plugin>/bin/sdd-tasks.py`.

Do not run this while `.sdd/run.json` exists. If it exists, ask the user whether a run is still active. If no run is active, remove the stale marker first.

1. List what is waiting on the user:
   - Open entries in `<feature>/BLOCKED.md`: give the id, tag, reason and question, one line each.
   - Held tagged tasks from `python3 <plugin>/bin/sdd-tasks.py plan <tasks> --blocked <feature>/BLOCKED.md`: tasks tagged `manual`, `decision`, `dep`/`download` (not allowed), or with a terminal BLOCKED.md entry (`resolved`/`wontfix`). Give the id, tag and reason.
   Ask the user to work through them one at a time. They can skip any.
   For each entry, show the task line, the reason and the question.
2. Act on the answer, depending on the entry's tag:
   - **`manual`, done by the user:** tick the task with the helper's `tick` command.
     If the user's work produced files, such as recordings, check they are where the task says.
   - **`decision`:** if the answer changes behaviour that the spec or plan describes, the spec must change first. Offer `/speckit-clarify`, or edit `spec.md` / `plan.md` with the user's yes, and add an Amendment-log line if the spec keeps one. Then remove the `[decision]` tag from the task.
     Write the decision into the feature's decisions register: `<feature>/decisions.md`, one line per decision with the task id, the question, the answer, the date and how to revisit it.
   - **`dep` / `download`, approved:** either remove the tag from that task, or set `[allow] dependencies` / `downloads` in `.sdd/config.toml` if the user wants that in general.
   - **`failed`:** discuss the verifier's reasons. Then either reword the task in `tasks.md` (with the user's yes), split it, or leave it for a person.
      If the run stashed the work, `git stash list` shows the entry, named `sdd-run <id>: …`.
   - **`resolved` / `wontfix`:** these hold the task forever (single source of truth: BLOCKED.md status and the task checkbox agree). To reopen it, change the BLOCKED.md entry back to `open` or remove it, and re-tag the task.
3. Write the answer under the entry's `- answer:` line and set `- status: resolved`, or set `- status: wontfix` if the user drops the task.
4. Commit `BLOCKED.md`, `tasks.md`, `decisions.md` and any spec edits as `chore(sdd): unblock <ids>`, with the commit trailer, on the current branch, but only after the user says yes.
5. End by saying how many tasks the next `/sdd-run` can now pick up. Use the helper's `plan` command to count them.
