# Blocked tasks

Tasks an unattended run could not finish. Answer the question under `- answer:` and set
`status: resolved`: the next `/sdd-run` runs the task again with your answer.
Set `status: wontfix` to drop the task, or `status: moved` if it became work outside this project.


## T009
- status: wontfix
- tag: 
- reason: 001 is superseded by 003; the toy-project end-to-end check lives in 003's tests/e2e and later runs.
- question: Close this leftover?
- answer: Yes (user, 2026-11-07).
