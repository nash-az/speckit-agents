## Agent tags

Unattended runs (`/sdd-run` from speckit-agents) read extra tags written after `[P]` and `[USn]`:
`- [ ] T057 [US1] [manual] Record …`. Add a tag only when it applies; most tasks have none.

- `[manual]` — a person must do it: record audio or video, use hardware, log in or pay, judge by ear or eye. The run never attempts it.
  Give every `[manual]` task a section in the feature's `manual.md` whose heading starts with the task id
  (`## T057 Record the learner audio`), with numbered steps a person can follow without reading code: where to
  go (a terminal, a dashboard view, a file), the exact commands or clicks, what to look for, where to record
  the result, and the last step "press I did it on the task's card" (it ticks the task). When the person's
  finding must be written down, end the section with `Record: <file>.md ## <heading>` (a file in the feature
  folder, such as `research.md ## Validation`): the note they type on the card is saved there, in the same commit.
  Ask the person only to do things and to paste or describe what they saw, never to draw a conclusion that the
  output already shows (an exit code, whether a value is set): the agent reading the record draws it.
  Ask for text, never a screenshot: the card's note box takes only text, so a step says "copy the message
  exactly" instead.
  The dashboard shows that section on the task's card under "Waiting on you". The task line itself stays one line.
- `[decision]` — the task must settle a question the spec, plan and research leave open; name the question in the task. Held until answered.
- `[dep]` — adds a package not yet in the project's manifests. Held unless the run allows dependencies.
- `[download]` — needs a large model or dataset not yet on disk. Held unless the run allows downloads.
- `[dep:ok]` / `[download:ok]` — added next to `[dep]` / `[download]` by a person to approve that one task even when the run does not allow dependencies / downloads.
- `[eval]` — a measurement (accuracy check, benchmark, calibration): done only when it has been run on the real data and the numbers are recorded.
- `[ui]` — changes what the user sees; verified by build and tests during the run and listed for a person to look at.

A task that the run would otherwise have to guess at is better split into the code part (untagged) and the person's part (`[manual]` or `[decision]`).
