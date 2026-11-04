# Manual steps: Jobs (005)

Step-by-step instructions for the `[manual]` tasks in `tasks.md`. The dashboard shows each section on its task's card
under "Waiting on you".

## T022 Real Job test

What it proves: a Job started from the dashboard runs by itself, the banner talks about the Job (never a run id), and
Merge brings its work into `main`.

1. Open the dashboard at <http://127.0.0.1:8765>. If it does not load, ask Claude to start it (or run
   `python3 bin/sdd-dashboard.py --project . --port 8765` in this repo).
2. In the header's **Feature** picker, choose `008-standalone-server`. That is the feature the Job works on.
3. Click **Jobs** in the left nav. Under **Start job**, choose **Phases 1–2** of 008 and press **Start job**.
   - T001 and T002 are manual, so the Job skips them. It runs T003, then T004–T011.
4. Leave it. Close the tab if you like. Come back after a while (an hour or more is fine).
5. Check the banner at the top of every view, while the Job runs and after it ends. It should describe the Job
   (for example "Job running: Phases 1–2 of 008") and should **never** show a run id like `20261104-1602`. If you
   see a run id, take a screenshot.
6. When the Job is **done** (the Jobs view says so), open it in the Jobs view and press **Merge**. Expected: its
   commits land on `main` and the Job shows as merged. If Merge reports a conflict, take a screenshot and stop.
7. Back here, in this card's note box, write what you saw: "banner never showed a run id; Merge worked", or what went
   wrong. Then press **I did it**. It ticks T022 and saves your note in `manual-notes.md`. Ask Claude to copy the note
   under `## Validation` in `specs/005-jobs/research.md`.
   - **I did it** refuses while a run is going. Wait until the Job has finished.
