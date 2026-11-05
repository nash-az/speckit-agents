# Manual steps: Jobs (005)

Step-by-step instructions for the `[manual]` tasks in `tasks.md`. The dashboard shows each section on its task's card
under "Waiting on you".

## T022 Real Job test

What it proves: a Job started from the dashboard runs by itself, the banner talks about the Job (never a run id), and
Merge brings its work into `main`.

1. Open the dashboard at <http://127.0.0.1:8765>. If it does not load, ask Claude to start it (or run
   `python3 bin/sdd-dashboard.py --project . --port 8765` in this repo).
2. In the header's **Feature** picker, choose `008-standalone-server`. That is the feature the Job works on.
3. Click **Jobs** in the left nav. Under **Start job**, choose one small open phase of 008 and press **Start job**.
   The Job skips `[manual]` tasks and runs the rest.
4. Leave it. Close the tab if you like. Come back after a while (an hour or more is fine).
5. Copy the banner at the top of the page, exactly as it reads, at two moments:
   - while the Job works. It reads like "Working on Phase 2: T008, verifying (3m)", or "Working on Phase 2" before
     the first task starts;
   - after it ends. It reads like "Phase 2 done: 8 tasks done; Merge to bring them into main, or Drop".
   Other texts you may see: "Waiting on you: 2 items", and "Paused until 18:00: usage limit; continues by itself".
6. When the Job is done, open it in the Jobs view and press **Merge**. Copy the message Merge shows, or the Job's
   new state line.
7. Back here, paste the two banner texts and the Merge message into this card's note box. Then press **I did it**.
   It ticks T022 and saves your note under `## Validation` in `research.md`, in one commit. Claude reads the note
   to check that the banner named the Job and never a run id (like `20261104-1602`).
   - **I did it** refuses while a run is going. Wait until the Job has finished.

Record: research.md ## Validation
