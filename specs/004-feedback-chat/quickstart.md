# Quickstart: validating feedback and chat

Prerequisites: a project with `specs/<feature>/spec.md` and `tasks.md`, a clean git tree, the dashboard
running (`python3 bin/sdd-dashboard.py --project .`). Steps 1-6 use the fake `claude`
(`tests/fake/claude`, `FAKE_DIR` with a drafting scenario) and need no usage; step 7 uses a real session once.

1. **Submit and draft.** `bin/sdd-feedback.py submit <project> "make the done tasks foldable"`. Expect note
   `N001` in `new`, then `drafting`, then `proposed`; `bin/sdd-feedback.py get` shows the proposal. The project's
   tree is unchanged (`git status` clean).
2. **Questions.** Use the fake scenario `ask`. Expect `waiting` with at most three questions; answer them;
   expect `drafting` again and then `proposed`. A third question round is refused (the fake tries one).
3. **Apply.** Apply the proposal from the dashboard or `bin/sdd-apply.py`. Expect one commit
   `chore(feedback): N001 …` whose files are exactly those the proposal named plus `feedback-log.md`.
4. **Refusals.** With `.sdd/run.json` present, Apply refuses and changes nothing; after editing `tasks.md`
   by hand so a change's `old` text is gone, Apply names that change and changes nothing.
5. **Queue and crash.** Send three notes with `max_parallel = 2`: two `drafting`, one `new`. Kill a worker;
   `GET /api/feedback` shows that note `failed` ("interrupted"), and the queued one starts.
6. **No claude.** With `claude` off the PATH, `submit` still stores the note; the note becomes `failed`
   ("the claude command was not found"); every other dashboard view works (Principle VII).
7. **Real session (once, by hand).** Send a vague note on a real project. Check in the note's log that only
   `Read`, `Grep` and `Glob` were used, that a result of kind `questions` or `proposal` came back, and that
   `git status` stayed clean. Record the shape of the final stream event in `research.md` D1.

Dashboard checks (Playwright harness): the `#feedback` URL survives a refresh; the proposal card keeps typed
edits through the 3 s refresh; Remove on a change shows it as left out after Apply; Apply is disabled with its
reason during a run.
