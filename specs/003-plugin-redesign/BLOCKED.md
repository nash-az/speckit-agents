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

## T053
- status: resolved
- tag: manual
- reason: The run's sandbox blocks local port binding and can't launch Chromium or Chrome, so the smoke test can only skip here. T053 asks for a real first browser run with its failures recorded for T065.
- question: Outside the sandbox, in /Users/nashwan.abouzidan/private/speckit-agents, run `SDD_UI_TESTS=1 python3 -m unittest tests/ui/test_smoke.py -v`. Then replace 'Dashboard findings: none yet' under `## First browser run` in specs/003-plugin-redesign/research.md with the result and any failures, and mark T053 done.
- answer: continue (2026-10-30)

## T053
- status: resolved
- tag: manual
- reason: The test needs a working browser and a local port to run the dashboard on. In this sandbox the bundled Chromium is missing, Google Chrome fails with the ProcessSingleton profile error, and an earlier run found that binding a local port is refused. The answer "continue" gave no browser result to record.
- question: Either run `SDD_UI_TESTS=1 python3 -m unittest tests/ui/test_smoke.py -v` outside the sandbox in /Users/nashwan.abouzidan/private/speckit-agents, record the result and any failures under `## First browser run` in specs/003-plugin-redesign/research.md, and settle T053 with "I did it". Or apply the planned sandbox fix so a run can do it: add `"allowLocalBinding": true` under `sandbox.network` in `.claude/settings.json`, and install the Playwright headless shell build `chromium_headless_shell-1248`.
- answer: 2 (2026-10-30)
