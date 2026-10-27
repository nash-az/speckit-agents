# Blocked tasks

Tasks an unattended run could not finish. Answer the question under `- answer:` and set
`status: resolved`: the next `/sdd-run` runs the task again with your answer.
Set `status: wontfix` to drop the task, or `status: moved` if it became work outside this project.

## T001
- status: wontfix
- tag: decision
- reason: The baseline it asks for (tests on the unchanged tree) no longer exists: Phases 2 and 3 were implemented before Phase 1 ran. The pre-pilot test results are in logs/2026-10-27-phase2-pilot.md (36 Python, 15 node passing, 1 skipped).
- question: Drop T001?
- answer: Yes, drop it (user, 2026-10-27).
