# Manual steps: Runs (009)

Step-by-step instructions for the `[manual]` tasks in `tasks.md`. The dashboard shows each section on its task's
card under "Waiting on you".

## T033 Walk the quickstart

What it proves: what `quickstart.md` describes works on a real checkout, not only in the tests. Sections 1 to 5 of
the quickstart are also in the test suite, so here you run that suite once and then do sections 6 and 7 by hand.

1. Open a terminal in `~/private/speckit-agents`. Check that no run is going: `ls .sdd/run.json` must say "No such
   file or directory". If the file exists, wait until the run has ended.
2. Run `python3 -m unittest discover -s tests`. Copy its last two lines exactly (they look like "Ran 812 tests in
   250.1s" and "OK"). These tests cover quickstart sections 1 to 5: Stop after this task, cut-off with Continue and
   Drop, conflict with Merge and Land, histories and the run summary, and the carry-over of old Jobs.
3. Run `python3 bin/sdd-tasks.py parse specs/009-runs/tasks.md | grep -o '"summary": [^,]*' | head -3`, then
   `python3 bin/sdd-tasks.py parse specs/009-runs/tasks.md | grep -o '"summary": null' | wc -l`. Copy the three
   lines the first prints and the number the second prints (quickstart section 6, step 1).
4. Open the dashboard at <http://127.0.0.1:8765>. If it does not load, run
   `python3 bin/sdd-dashboard.py --project . --port 8765` in this repo and open the address again.
5. In the header's **Feature** picker, choose `009-runs`, then click **Tasks**. Open the card of T013. Copy the
   card's main text exactly, then click **Details** and copy the first ten words of the text it shows
   (quickstart section 6, step 2).
6. Wait three seconds with Details open. Write down whether it is still open.
7. Open a new Claude Code session in this repo (not through the dashboard) and type `/sdd-run --phases 15`. Before
   it does anything else it prints a warning. Copy the warning exactly, then press Escape to stop the session
   (quickstart section 6, step 3). Nothing is run: Phase 15 has only this manual task.
8. Back in the dashboard, click **Runs**, then **Start run**. Pick one small open phase of a real feature, or a
   single task, and press **Start run** (quickstart section 7).
9. While its first task is being worked on, press **Stop after current task** in the banner. Copy the banner text
   at that moment.
10. When the run has ended, copy the banner text again, and the run card's state line and its summary line (how
    long, tasks done, attempts, models, usage).
11. In the terminal, run `git log --oneline -3 main` and copy the three lines.
12. Paste everything you copied (steps 2, 3, 5, 6, 7, 9, 10 and 11) into this card's note box, each with its step
    number, then press **I did it**. It ticks T033 and saves your note under `## Validation` in `research.md`, in
    one commit. Claude reads the note to check each expected result.
    - **I did it** refuses while a run is going. Wait until the run from step 8 has ended.

Record: research.md ## Validation
