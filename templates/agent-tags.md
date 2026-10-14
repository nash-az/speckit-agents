## Agent tags

Unattended runs (`/sdd-run` from speckit-agents) read extra tags written after `[P]` and `[USn]`:
`- [ ] T057 [US1] [manual] Record …`. Add a tag only when it applies; most tasks have none.

- `[manual]` — a person must do it: record audio or video, use hardware, log in or pay, judge by ear or eye. The run never attempts it.
- `[decision]` — the task must settle a question the spec, plan and research leave open; name the question in the task. Held until answered.
- `[dep]` — adds a package not yet in the project's manifests. Held unless the run allows dependencies.
- `[download]` — needs a large model or dataset not yet on disk. Held unless the run allows downloads.
- `[eval]` — a measurement (accuracy check, benchmark, calibration): done only when it has been run on the real data and the numbers are recorded.
- `[ui]` — changes what the user sees; verified by build and tests during the run and listed for a person to look at.

A task that the run would otherwise have to guess at is better split into the code part (untagged) and the person's part (`[manual]` or `[decision]`).
