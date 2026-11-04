---
name: sdd-tag
description: Add agent tags ([manual], [decision], [dep], [download], [eval], [ui]) to the open tasks of an existing Spec Kit tasks.md, so /sdd-run knows which tasks to park and which to measure. Use after /speckit-tasks on a tasks.md written before /sdd-init, or when the user asks to tag tasks.
---

# /sdd-tag

Tag the open tasks in the current feature's `tasks.md`. The tag meanings are in `<plugin>/templates/agent-tags.md`, where `<plugin>` is two levels above this skill's base directory. Read that file first.

1. Find the tasks file: use `.sdd/config.toml` `run.feature`, or `.specify/feature.json`.
   Parse it with `python3 <plugin>/bin/sdd-tasks.py parse <tasks.md>`.
2. For each open task (`done: false`), decide which tags apply from its text, and from the plan or spec section it cites when the text is not clear.
   - Judge by what the task needs, not by its wording. "Record the learner part of the eval set" is `manual`. "Implement `eval imitation`" is `eval`.
   - `dep`: the task names a package that is not in the project's manifest or lockfile. Check them.
   - `download`: the task needs a model or dataset that is not already on disk. Check the data directories it names.
   - `decision`: the spec, plan or research leaves an open question that the task must answer. Name the question.
   - Leave a task untagged when it is plain code work.
3. Show a table of the tasks that get tags, with these columns: id, tags, and a one-line reason. Ask the user to confirm or change it.
4. Write each tag as a bracket after the task's existing leading tags (`[P]`, `[USn]`), keeping their order:
   `- [ ] T057 [US1] [manual] Record …`.
   Change nothing else on the line, and never touch tasks that are done.
5. For each `[manual]` task, add a section to the feature's `manual.md` (create it if missing) whose heading starts with the task id (`## T057 Record the learner audio`), with numbered steps a person can follow without reading code: where to go (a terminal, a dashboard view, a file), the exact commands or clicks, what to look for, where to record the result, and last "press I did it on the task's card". Keep the task line itself one line. The dashboard shows the section on the task's card.
6. For `[decision]` tasks, write the options and their trade-offs next to the question (or link to the research file that has them), so the person can decide without reading transcripts.
7. Run `parse` again and confirm that the tags were read. Say how many tasks are now held by default: those tagged `manual`, `decision`, `dep` or `download`, unless the config allows dependencies or downloads.
