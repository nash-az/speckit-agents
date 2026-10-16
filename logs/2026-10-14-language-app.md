# Run log: language app, 2026-10-14

Target: `~/private/language-app`, feature `specs/001-fluency-trainer-mvp`, base `main` at `723a9a1` (same tree as `97970b5`).
The user's uncommitted imitation-check WIP is stashed as `stash@{0}`; restore with `git stash pop` on `main`.
Raw headless streams (stream-json) are kept in the session scratchpad, `run1/`.

Flow: `/sdd-init` → `/speckit-converge` → `/sdd-tag` → `/sdd-run --phases <Convergence> --yes`, each as a headless `claude -p` in the app.

## Steps

### 1. `/sdd-init` (headless, `acceptEdits`, 109 s, $0.53)
Wrote `.sdd/config.toml`, the tasks-template override and the `.gitignore` entry. All five checks pass before the sandbox is on: 241 backend tests, 20 frontend tests, ruff and mypy clean, typecheck clean.
Checks changed from `cd backend && uv run …` to `uv run --directory backend …`, which needs no `cd &&` and fits allow rules.

Problems:
- **`.claude/settings.json` cannot be written headless.** Claude Code protects it even under `acceptEdits` and an explicit pre-approval, and the operator's auto-mode classifier refuses it too (self-modification). It needs a person: approve the write in an interactive `/sdd-init`, or copy the prepared file. → The plugin must say so up front, and `/sdd-init` should print the exact file, not just describe it.
- **Compound shell commands refused:** 7 denials, for `for` loops, `${pipestatus[1]}`, `( cd … && … )`, `;` chains with `echo $?` and `> /tmp/x`. The agent fell back to one command per call, and that worked. → Skills should say: run each check as its own Bash call, with no pipes, `;`, `cd` or redirects. Exit status comes from the tool result.
- **`ls` outside the project is blocked**, but `Read` of the plugin's templates works. → Skills should `Read` plugin files, never `ls` or `cat` them.
- Init still runs the checks only before the sandbox is on: the re-run under the sandbox (added earlier today) cannot happen while the settings file is unwritten.

Plugin change made during this step (user request): `/sdd-run` now runs `/speckit-converge` and tags the new Convergence phase itself, so the user flow is `/sdd-init` → `/sdd-run`. `--no-converge` and `--phases` skip it.

### 2. `/sdd-run --yes` (headless, `dontAsk`, `--allowedTools Skill`), started 23:16
Before running it, the operator copied the prepared `.claude/settings.json` into the app and committed the setup there as `3011365`.
- **Preflight:** all five checks passed under the sandbox, so the uv and pnpm `allowWrite` entries work. One refusal: a `for` loop over the checks (this run loaded the skill before the single-command rule was added). The main loop recovered by running one command per call.
- **Converge** (about 6 min, 2 Explore agents): appended `## Phase 15: Convergence`, 52 tasks (T202–T253), each tagged `(missing)` or `(partial)` with its source ref.
  - Harmless error: `sqlite3 data/svenska_flyt.sqlite` after `cd backend/src/svenska_flyt` (relative path). Another case of "don't `cd`".
- **Tagging:** 7 held (`[manual]` T239, T245, T246; `[decision]` T244, T251; `[dep] [download]` T240, T243), 4 `[eval]`, and `[ui]` on the pages.
- **Branch** `auto/20261014-2322`. Converge and tags committed as `f953c50`, and `.sdd/run.json` written.
  - One refusal: a chained `RID=$(date …) && git switch -c … && git add … && git commit …`. Split into single commands, which worked.
- **Workflow** `speckit-agents:sdd-run` launched by name (the fix works). Args: 45 runnable, 7 held, phases `['15']`, `phase_deps {'15': []}`. Run `wf_cb9f2768-583`.
  Transcripts: `~/.claude/projects/-Users-nashwan-abouzidan-private-language-app/c669e02d-…/subagents/workflows/wf_cb9f2768-583/`.

### 3. First task, and a run stopped by the operator (23:23–23:31)
- `impl:T202` done in about 2 min, and `verify:T202` approved with no problems. The implementer and verifier freely used `cd …; cmd | tail` chains, which run inside the sandbox.
- **Bug: `commit:T202` made one Bash call, a `for` loop over the checks, which `dontAsk` refused.** The bookkeeper gave up and reported `checks_passed: false` ("the checks did not run").
  The workflow treated that as failing checks and started `repair:T202`. The path ahead was a second bookkeeper (same refusal), then discard (stash T202, record it blocked), then every later phase-15 task skipped as "an earlier task in this phase did not pass". So one refused command would have ended the whole run.
- **Operator action:** killed the headless run (pid 30084) during the repair. The repair had changed nothing harmful. Ran the five checks by hand (253 passed; ruff, mypy, typecheck and 20 frontend tests clean), ticked T202 and committed it as `5f0786e`, as the bookkeeper would have. Removed the stale `.sdd/run.json`.
- **Plugin fix:**
  - The bookkeeper schema gains `checks_ran`. When it is false, the workflow retries the bookkeeper once, with the refusal and the single-command rule. If that fails too, the run stops with the work uncommitted. A refusal never leads to a repair or a stash.
  - The bookkeeper prompt and agent now say to run each command as its own Bash call, and that a refusal is not a failing check.
- Main loop cost to this point: $5.34, 446 s, 37 turns. 3 refusals, all compound commands.

### 4. Run #2 (23:29–23:37): one task, then everything skipped
- New branch `auto/20261014-2329`, created from the first run's branch, so the auto branches stack. → Fix: `/sdd-run` now stays on the current branch when it is already an `auto/` branch.
- Converge correctly skipped, because phase 15 had open tasks. 44 runnable.
- `eval:T203` (5 min): built `svflyt eval voice-match` and ran it on RixVox (76 speakers, dev/test split by speaker, selection rule fixed before looking at test). **SC-013 cannot be met:** test recall reaches 84.4% at best, and at 0.90 every new voice gets a false proposal. Results recorded in research.md R23, and the task reported `blocked` (decision). Exactly the behaviour wanted.
  - One refusal: `HF_HUB_OFFLINE=1 uv run … python - <<EOF` (an environment prefix plus a heredoc). One StructuredOutput call failed (invalid JSON) and the retry worked.
- `commit:T203`: `checks_ran: true`, so the fix works. BLOCKED.md committed (`30ae7f0`).
- **Bug: blocked partial work left uncommitted.** The workflow did not pass blocked tasks' files to the bookkeeper, so it reported them as "unclaimed changes". → Fixed: blocked entries carry `files`. Tasks the verifier rejected (`stash: true`) now get their files stashed, so the tree is clean after every group.
- **Bug: one blocked decision skipped the other 42 tasks.** A phase that is not `[P]` is "tainted" by any task that does not pass, which suits ordered phases. → Fixed: in a Convergence phase, a task that does not pass holds back only later tasks whose text names it. Other phases are unchanged.
- Operator: committed T203's partial work as `e539e97`, after the bookkeeper had run the checks with it in the tree and they passed.

### 5. Run #3 (23:38–23:50): 2 commits, then the headless process stopped the workflow
- Stayed on `auto/20261014-2329` (fix works). 43 runnable, 8 held, including T203 (open in BLOCKED.md).
- T204: implement 2 min, approve, commit `63fd886`. **This was the first task to go through with nobody stepping in.**
- T205: one refusal (`cd backend && python3 - <<EOF` editing a file). It recovered, was approved, and was committed as `183daea`.
- **Bug (environment): `claude -p` stopped the workflow during T206**: "Background tasks still running 10m after the last turn …; stopping them. Set CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 to wait indefinitely." The earlier lesson that `claude -p` waits for the workflow held only for runs shorter than 10 minutes.
  The tree was clean, because T206 had just started, but `.sdd/run.json` was left behind and no run report was written.
  → Documented in README, CLAUDE.md and the sdd-run skill. The operator removed the stale marker.
- Plugin fix: all four agents now say to edit with Edit/Write and run single commands from the project root. Every refusal so far has been a compound or inline-script command.

### 6. Run #4 (23:50–23:57): 1 commit, then the account's usage limit
- `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` set; the process now waits for the workflow.
- T206: two failed Edit matches (a stale `old_string`) recovered by re-reading. Approved, committed as `c5d7d86`.
- **T207: `impl` and then `commit` failed at 23:57 because the claude.ai session limit was reached** ("You've hit your session limit · resets 2:10am"). The workflow behaved safely: the bookkeeper returned nothing, so the run stopped, with nothing marked blocked and a clean tree. Left a stale `.sdd/run.json` and wrote no report.
  → Fix: `/sdd-run` preflight now notices a stale `run.json`, says which run stopped, and removes it when the tree is clean.
- **Usage note:** this session plus 4 runs used up the session limit in about 2.5 hours, with about 5 tasks committed. A full 45-task phase will span several limit windows. Each window ends the run, and the next `/sdd-run` picks up from the unticked tasks.

### 7. Run #5 (from 02:11)
- The stale `run.json` from run #4 was detected and removed by preflight (fix works). Stayed on `auto/20261014-2329`.
- T207 (`SpeakerCheck`, ui) committed as `c9b0e30`, and T208 (line review, ui) as `1507ecb`, each in about 5 to 7 minutes with no refusals that mattered.
- T209 (`[eval]`, kb-whisper large vs medium on a 30-minute episode): the evaluator wrote `svflyt bench import`, started it in the background and polls the log with `for …; sleep 10` loops, which run because they are inside the sandbox. At 03:12 the large model had run for 39 minutes at about 200% CPU, with medium still to come. That is real measurement, as wanted, but it holds up the whole sequential run.
  - 3 refusals of about 30 commands: a `for` loop with `find`, a `sqlite3` chain and `cat >> file <<EOF`. The single-command rule in the agent prompts reduces refusals but does not stop them.
- **Ideas (not done):**
  1. Let `[eval]` tasks run alongside code tasks: they mostly add measurement code and results.
  2. A per-task time budget, after which a long measurement is parked for a person to run.
  3. A PreToolUse hint that rewrites or explains refused compound commands, since prompt rules alone don't stop them.
- T209 finished at 04:35 after 2 h 10 min, **blocked (decision)**. It built `svflyt bench import` (runs the real import job in a throw-away copy of the app) and assembled a 30-minute episode from local RixVox, since downloads were not allowed. Large: 67.2 min. Medium: 42.5 min. SC-011 asks for 15, so both miss. Recorded in research.md R21.
  - **Finding: the sandbox blocks the Mac GPU (MPS)**, so every model-heavy `[eval]` inside a run measures CPU only, which is not the real setup. Ollama was not running either, so the grammar stage went untimed.
    → Open design question: GPU-bound `[eval]` tasks either need a sandbox exception for the evaluator, or should be held for a person (`[manual]`-like) with the measurement code built and the run itself left to the user. Not changed yet; the user decides.
- **Bug: the blocked task's partial work broke the checks for everyone after it.** T209's code fails mypy (2 errors). `commit:T209` correctly committed nothing, but that also meant no BLOCKED.md entry, and the failing files stayed in the tree while T210 started. Every later group would then fail its checks, leading to repairs and possibly stashed good work.
  → Operator: stopped run #5 at 04:37 (T210 had changed nothing yet). Stashed T209's files (`stash@{0}`, "partial T209"), recorded T209 blocked with the stash name and the mypy errors in its reason, and committed as `449309a`. The user's own WIP stash is now `stash@{1}`.
  → Fix: new bookkeeper mode `park`. When no task in a group was approved and the checks fail, the blocked tasks' files are stashed and recorded, and the checks are run again. If they still fail, the run stops.

### 8. Run #6 (04:37–05:42): T210 cut off by the usage limit again
- T210 (`[eval]`, imitation check with regional validation) ran `svflyt eval imitation --pairs 100 --split dev` for about an hour on CPU. At 05:42 the session limit was hit ("resets 7:10am"), and the eval and commit agents both failed. The run stopped safely, but left T210's unfinished files in the tree and a stale `run.json`.
- **Gap:** the next preflight would refuse the dirty tree, so a person would be needed after every usage-limit stop.
  → Fix: when a stale `run.json` exists and the tree is dirty **on that run's branch**, the changes are the run's own (the guard was on). Preflight stashes them as "sdd-run <id>: unfinished work", notes it in the report, and goes on. On any other branch it still stops.
- Two limit windows have produced 6 committed tasks and 2 blocked decisions. The model-heavy `[eval]` tasks (T209 took 2 h 10 min, T210 over 1 h unfinished) use most of the time. This strengthens the case for holding GPU-bound evals for a person.

### 9. User decisions (2026-10-15, about 10:30), to apply when the run is between tasks or done
- **T209:** kb-whisper **large** stays the default import model. Slow Admin imports are acceptable. GPU benchmark run by the operator in `~/private/language-app-gpu`: large took about 35 min for 30 min of audio; medium's result is pending.
- **SC-011** to be amended to: within **2 hours**, as an **unattended background job** with a **visible progress bar**. `spec.md` is protected during runs, so this waits; show the user the exact edit first.
- New task to add: one overall progress bar per import, with time remaining. Today's bar shows per-stage progress only (`Sources.tsx` `ProgressBar`, reset by `JobContext.stage`).
- Answer T209 in BLOCKED.md, add the GPU numbers to research.md R21, and commit the fixed benchmark code from the GPU worktree.
- **GPU benchmark result (09:21–10:32, `~/private/language-app-gpu`, MPS, Ollama running so grammar was timed):** 30.0 min of RixVox question time (53 speeches, 4 debates).
  - Large: 33.8 min in total (1.13× real time). Transcribe 20.2, grammar 11.9 (390 lines), align and speakers 1.7. 522 lines, 72 speakers.
  - Medium: 32.7 min (1.09×). Transcribe 17.0, grammar 13.2 (436 lines), align and speakers 2.5, plus 4.0 min first load. 514 lines, 59 speakers. Word disagreement vs large: 15.6%.
  - On the GPU the gap between the models nearly vanishes. Large confirmed as the default, and both fit the amended 2-hour SC-011. Raw JSON: scratchpad `gpu-bench.json`.

### 10. Run #7 (07:12–11:14): T210 cut off by the usage limit a second time
- T210 ran for 4 h on CPU in the sandbox. Dev split done (targets met), test split about 90% done when the session limit hit at 11:14. **On CPU, a long eval cannot finish inside one usage window, so a restart would loop.**
- Operator actions (12:10–12:20, no run active):
  - **T209 closed:** the GPU numbers and the learner's decision added to research.md R21, the BLOCKED entry resolved, T209 ticked, the benchmark code committed with its mypy fixes. `f6032fa` (cherry-picked from the GPU worktree's `63440e0`); 312 + 29 tests pass.
  - **T210 and T211 held** as `[manual]` in BLOCKED.md (`345370e`): the operator measures them on the GPU in parallel. T210's unfinished code is in stash "sdd-run 20261015-0712: unfinished T210 (operator finishes on GPU)".
  - Stale `run.json` removed.
- This is option 1 from the user, done by hand. The permanent version is the eval lane in the plugin backlog.

### 11. User decision (2026-10-15, about 12:40): the admin attests that material is native
- "Rely on the admin to upload proper materials… it should fall upon the admin to make sure of that." No per-speaker Native/Not sure check, no automatic non-native detection.
- Consequences: T203 (voice-match threshold) is **superseded**, and SC-013 should go. FR-059–FR-063 (US9) are to be amended: native attestation at upload, region per source or speaker, name lookup optional. Ground truth for accuracy checks stays NST natives (FR-035b).
- Already built and affected: T202, T204 (voice matching), T207, T208 (SpeakerCheck). Open and affected: T230 (obsolete), T248 (mostly unneeded). The current run builds them anyway (its plan is fixed and they are small).
- Plan, after this run: amend the spec through Spec Kit (`/speckit-specify` for US9 and FR-059–063, drop SC-013, SC-011 to 2 h), get the user's review, then `/speckit-converge` and the next `/sdd-run`. Mark T203 superseded in BLOCKED.md at the next break between tasks.

### Plugin ideas noted during the run (not done)
- The verifier should run the project checks before approving: T213 was approved, then failed mypy at commit, and the repair fixed it.
- **Phone notifications** (user request, 2026-10-15): run events to the user's phone, through the Claude app or an equivalent AI app (finished, blocked and needs a decision, stopped). Not researched. Backlog item 10 in memory.
- **Project dashboard UI** (user request, 2026-10-15): a local web server showing a run's live tasks and agent steps, plus the whole Spec Kit project (features, specs, plans, tasks, BLOCKED.md, research, run reports), with a commercial-app look and feel. Not researched; to be built later through Spec Kit. Backlog item 11 in memory.

### 12. Run #8 (12:12–13:57): 16 tasks with nobody stepping in, then paused at the user's request
- T212–T227 all committed. T213 and T226 each needed one automatic repair, for mypy errors the verifier missed (backlog item 9). T214 and T222 were each rejected once by the verifier for real bugs (an arrow shown twice; review cards in one skill only) and fixed on the second attempt. Refusals were routine compound commands only.
- Paused at 13:57:31 right after `commit:T227` (`2b93f50`). A watcher stopped the run process as soon as the commit was in the journal. `impl:T228` had just started and left no files. The tree is clean, and the stale `run.json` is left for the next preflight.
- Phase 15 status: 23 of 52 done. Next up: T228.
- **T210 finished by the operator on MPS** (12:13–14:00, 1 h 48 min; the CPU attempts in the sandbox were 4+ h and unfinished): test split, **all targets met**. Central 99% close, no-match 100%, accent agreement 0.74. All 8 regional varieties validated, Finland excluded. Recorded in research.md R17, BLOCKED entry resolved, committed `297a584` on the paused run branch. Checks: 465 backend tests, mypy, ruff and typecheck pass.
- **T211 run by the operator on MPS (14:03, 24 s):** same-accent 83/109 pass (76.1%, 41 unclear), below 85%. **0 different-accent pairs**, because NST on disk has no accent minimal pairs (known from R2). SC-002 can be only half measured. Recorded as `[decision]` in BLOCKED.md with options (record natives via T236/T237, find a corpus, or narrow SC-002), committed `cb0bb27`.
- **T211, learner chose (b):** after research (no open corpus of accent minimal pairs; SweDia is restricted to research and education), the operator added T254 (mine accent homographs from RixVox and NST, with LLM sense disambiguation and lexicon accent), T255 `[eval]` (spot-check, dev/test tuning, accent-compare; held for the operator on the GPU) and T256 `[download]` (Common Voice, only if needed). T211 answered in BLOCKED.md and stays open until T255. Commit on the paused branch.
- **Forvo route (user's choice):** the user bought the non-commercial API plan (500 requests a day) and stored the key in the Keychain (service `svenska-flyt`, account `forvo`); the operator confirmed the entry without reading it. API terms: "It is not allowed to cache audio pronunciations"; audio links expire; every audio play counts as a request; default terms are attribution (BY), non-commercial (NC), share-alike (SA). Added T257 `[manual]` (the user posts 30 phrase requests from `forvo-requests.md`), T258 (loader: budget of 400 a day, no audio cache, only measurements kept, key redacted, tests on recorded responses) and T259 `[eval]` (held: the operator fetches once outside the sandbox). Commit `e52eca2`. T254/T255 (RixVox/NST mining) stay in parallel.
- T257 done: the user posted the 30 phrases on Forvo (pending pronunciation). T259 marked low priority, for later: the user will say when recordings are in. Resuming the run.

### 13. Run #9 (15:38–15:50): T228 cut off by the usage limit
- The user asked for a pause after T228, then cancelled it ("ignore the pause, continue"); the stop watcher was cancelled.
- T228 (VAD chunking): rejected twice by the verifier (a chunk could exceed 30 s; then 3 contract tests failed, with this verifier running the full suite). Attempt 3 was done when the session limit hit at 15:50, during verify, so nothing was committed. Its work is in the tree; the next preflight stashes it as unfinished work and T228 starts over.

### 14. Run #10 (18:12–19:23): 13 tasks, then the usage limit
- T228 (fresh attempt, approved first time) through T241 committed: T228–T238, T241 (13 tasks). T229 and T232 were each rejected once and fixed. The bookkeeper flagged a 50-vs-40 count note on T238's seed tests, to check at review. The session limit hit at 19:23 as T242 started; nothing was written and the tree was clean.
- User request: watch the usage limit while unattended, so less work is lost. Start smaller work, or stop, when there isn't enough left. → backlog item 12.

### 15. Run #11 (23:12–23:59): finished; every runnable task is done
- T242, T247 (with an operator follow-up commit `f1ec319` for the missing CLI command), T248, T249, T250, T252, T253, T254, T258: all committed, and the run report is committed (`2ba6681`). T247 was rejected once (origin evidence) and fixed. The run report flags that T249 changed behaviour: an unparseable LLM grammar reply now raises an error instead of passing the line.
- **T255 step 1 by the operator on MPS:** `svflyt eval accent-pairs build` turned 6,687 on-disk sentences into only 11 tokens, with no word in both accents, so there are no pairs yet. Waits for Forvo (T259) or a native recording; Common Voice is the fallback. Recorded in BLOCKED.md together with T203 marked superseded.
- Phase 15: 46 of 58 done. Open: T211, T255, T259 (accent data); T239, T245, T246 (`[manual]`); T244, T251 (`[decision]`); T240, T243, T256 (`[dep]`/`[download]`). T203 resolved as superseded.

### 16. Reviewing what waits on the user (2026-10-16)
User decision: **the plugin redesign starts after the language app is finished.** Until then, keep recording every issue and lesson from this project here, so the redesign has the full list.

Lessons from explaining the 5 items that wait on the user (T239, T244, T245, T246, T251):
- **No single place lists what waits on the user.** `/sdd-unblock` reads only BLOCKED.md, but tasks tagged at creation (`[decision]` T244/T251, `[manual]` T239/T245/T246) are held by `plan` and never reach BLOCKED.md. I first told the user to answer T244/T251 "in BLOCKED.md with /sdd-unblock", which was wrong. Redesign: the run report and `/sdd-status` should list every held tagged task as "waiting on you", with its question, and `/sdd-unblock` should cover them too.
- **Manual tasks can be invalidated by pending spec changes.** T246 (walk through quickstart V1–V14) includes V13 and parts of V14, which test the per-speaker checks the admin-attests decision removes. Nothing marks T246 as "do after the amendment". Redesign: when a decision or amendment is pending, flag `[manual]` validation tasks that touch the affected FRs/SCs, or order them after it.
- **Numbers in reports must be computed, not recalled.** I said "50 drafted reductions"; the file has 51 items (52 matches of "unreviewed" included the header comment; `content import` created 51) (the bookkeeper had also flagged a 50-vs-40 count note on T238). Reports and summaries should count from the artifacts.
- **`[decision]` tasks need the options and their trade-offs in the task line.** T244's line had the question ("improve or accept?") but not the facts the user needs (the other model reaches 0.94 but false-flags 25% of native words). Converge/sdd-tag should put, or link, the evidence next to the question.
- **Shell gotcha (zsh):** `$B:c…` in `git show $B:content/...` is read as a zsh modifier. Use `"${B}:path"`. Worth a line in the agents' single-command rules.

### 17. T244 and T251 answered (2026-10-16)
- User: T244 (b), accept 0.84 → ticked, deviation recorded in research.md R2. T251 (b), drop the flag → `[decision]` removed and the task reworded to remove `--dev-fleurs` and its quickstart line. T203 set to `wontfix`. App commit `chore(sdd): unblock T244, T251; drop T203`. The next `/sdd-run` has one runnable task in Phase 15: T251.
- **Plugin bug found and fixed (uncommitted):** `plan` held a task only while its BLOCKED.md entry was `open`. A `resolved` or `wontfix` entry whose task was still unticked went back to the agents: T203 (superseded, "resolved") would have been re-run, tuning voice matching that is being dropped. Fix: `wontfix` entries now hold the task (`bin/sdd-tasks.py`, test `test_wontfix_stays_held`; 12 tests pass). `/sdd-unblock` already says to use `wontfix` for dropped tasks; the operator had used `resolved` for T203 by hand.
- Redesign lesson: the BLOCKED.md status and the task checkbox are two sources of truth. A superseded or dropped task needs one clear end state that the planner, `/sdd-status` and the report all read the same way (backlog item 17).

### 18. Preparing T239 (reductions review) for the user (2026-10-16)
- The 52 drafted reductions are not in the database yet (only the 27 starter items are), so Admin → Content shows none of them until `svflyt content import` runs.
- **App bug found while preparing the steps:** approving an item in Admin → Content sets `review_status` but not `user_edited`, and `content import` (without `--overwrite`) resets every item that isn't `user_edited` to the YAML values. So the next import silently turns approved reductions back to `unreviewed`. Not caught by the T235 verifier. Proposed to the user as a task: approval counts as an edit, or is written back to the seed file.
- Redesign lesson: `[manual]` tasks get no "how to" when they are created. The user had to ask how to do T239, and answering meant reading the code. Converge/sdd-tag (or the run report) should write the steps for each `[manual]` task: where to do it, the commands, and what "done" looks like (backlog item 18).

### 19. App decisions register (2026-10-16)
- User: "plugin decisions here, app decisions there". The app's compromises were scattered across research.md, BLOCKED.md and this log, and two (the import progress-bar task, the Content approval bug) existed only here. Added `specs/001-fluency-trainer-mvp/decisions.md` in the app (D1–D7, K1), commit `9dd2703`, and pointed the app's own memory at it.
- Redesign lesson: `/sdd-unblock` (and the operator) should write each answered decision into a register in the app's feature folder, with the target, the result, the status and how to revisit it, so the decisions stay with the app (backlog item 19).

### 20. Recording sets added (2026-10-16)
- User: the admin should help the app learn with recordings as well as uploads, and more than 30 phrases is fine. Added T260–T265 to the app (recording sets in Admin → Record, an accent-pair set of about 160 prompts drafted from the NST lexicon, a length-pair set, eval integration, `[manual]` T264 for natives to record, `[eval]` T265 to measure). App decision D8 in `decisions.md`; spec change for US10/FR-004a goes in the pending amendment.
- **Plugin gap:** `plan` has no dependencies on held tasks. T265 (measure) needs T264's recordings (`[manual]`, held), but `plan` listed T265 as runnable; an agent would have spent a turn finding no data. Worked around with a BLOCKED.md entry, as for T259. The Convergence skip rule covers only tasks that name a *failed* task. Redesign: parse "after Tnnn" / "per Tnnn" references into dependencies, and hold a task whose dependency is open and held (backlog item 20).

### 21. Run #12 (09:10–): T251, T260–T263
- Started with `--phases 15` (no converge: the spec hasn't changed). Preflight checks passed; 5 tasks planned.
- **Operator slip:** started the watcher as a plain background job, so its lines reached the operator only when it exited at the end of the run. The user asked why there was no monitor. Fixed by streaming the watcher's output through a live monitor. Lesson for the redesign: the plugin has no live event stream of its own; watching depends on the operator's scripts (`watch_wf.py`). A run should write its own event feed (task started, verdict, commit, refusal, blocked, stopped) that a monitor, a phone notification (item 10) or the dashboard (item 11) can follow (backlog item 21). User, later: the plugin should have its own monitoring for the redesign, not now. Sketch in the backlog: event feed, watchdog, consumers.
- **T260 rejected once (09:24):** the backend had `POST /api/admin/recording-sets/{id}/review`, but no page called it, though the task said "approves the set in Admin → Content". Attempt 2 added a Recording sets section to Admin → Content. Cause: the operator's task wording put a UI requirement into a backend task, while the Record page was a separate `[ui]` task (T261). Lesson for task writing (operator and converge): keep each user-visible requirement in the task that owns its page, or name the page file in the backend task's files (backlog item 22).
- **T262 (09:36–37):** routine refusals of `find … -exec ls` (twice), then `Write /tmp/claude-502/t262/analyze.py` refused: agents may write only inside the project. A data task (search the NST lexicon for accent homographs) needs a throw-away script, and there is no agent scratch folder (`.sdd/` is guarded against agents). Risk: helper scripts land in the project and in the task's commit. Redesign: give each agent a scratch dir inside the project that is git-ignored and allowed by the guard, e.g. `.sdd/scratch/<task>/`, and say so in the agent prompts (backlog item 23).
- **Run #12 result (09:10–10:04):** 5 done, 0 blocked: T251 `c70cd6b`, T260 `fc3d526` (1 rejection), T261 `68ff275`, T262 `8248183`, T263 `afb0025`; report `b22e6f2`. Refusals: `find -exec`, `python -c`, a Write to /tmp; all worked around.
- **Phone notification failed:** `/sdd-run` called PushNotification at the end: "Mobile push not sent (Remote Control inactive)", `disabledReason: no_transport`. So the existing push reaches the phone only while Remote Control is connected. Input for backlog item 10.
- **After the run (user decisions, app side, recorded in the app's decisions.md D9–D11, commit `6401526`):** accuracy targets don't hold up development; the admin improves accuracy in the product (in-app tuning, T266–T267); content curation moves to Admin → Content (T268). Eight tasks closed as `wontfix` "moved to the admin".
- **Plugin lesson:** Spec Kit tasks have no state for "moved out of development into product use". `wontfix` worked (with the planner fix from section 17), but it reads as "dropped". The redesign could add a `moved` / `product` state that the report and `/sdd-status` show apart (backlog item 24).

### 22. Late note for run #11: T247's unclaimed file (written 2026-10-16)
- This note was meant for section 15 but was never written: the operator's command hung on a stray `cat` reading stdin (that is also where the empty `2026-10-15-language-app-notes.md` came from). Found on 2026-10-16 when the user asked about leftover shells.
- **Bug (plugin):** T247's `svflyt content coverage` command (`backend/src/svenska_flyt/cli.py`) was not in the implementer's `files_changed`, so the bookkeeper committed T247 without it and reported "unclaimed changes". The run went on to T248 with cli.py dirty. The operator committed it as a follow-up (`f1ec319`, `fix(T247)`). Redesign: when the bookkeeper finds unclaimed changes right after a task, attribute them to that task if the verifier's diff covered them, or ask the verifier, instead of leaving them in the tree (backlog item 25).
- **Operator lesson:** background jobs must be checked and stopped when done. The user saw 1 monitor and 3 shells still listed while I reported "nothing running" (I had checked only for run processes). Stopped: the hung T247 command and the finished run monitor; kept the 2 app servers.
- **User request for the redesign (2026-10-16, note only):** pause from the app after the current task finishes, and resume one, several or all tasks, to manage credit use and lose less work at limits; plus, if feasible, a "will this task fit in the remaining allowance" indicator. Filed with usage-limit awareness as backlog item 12a, tied to the dashboard (11) and the event feed (21).
- **Amended by the user (same day, note only):** prefer an automatic pause mid-run. A separate credit watcher signals the run just before the allowance runs out; the run checkpoints every agent's progress, stops, and resumes from the checkpoint after the reset, losing no work. This replaces the estimate and the choice of what to resume; manual pause-now and resume stay in the dashboard. Backlog item 12a updated.
- **Plugin/Spec Kit gap (2026-10-16):** after `/speckit-converge`, the original tasks in phases 1–14 stay unticked (53 in the language app) although Phase 15 carries their remaining work ("per T129 (partial)"). `plan` without `--phases` would list them as runnable again (it reported 55 runnable earlier). Redesign: after converge, mark the originals as superseded by their Convergence task, or have `plan` skip tasks that a later task names as `per Tnnn`, and show the true open count (backlog item 26).
- **Priority change (2026-10-16):** the user wants the credit watcher (backlog 12a) built first, as soon as they say "continue", before more app work: "I need the app's tasks to be run unattended with little to no losses when the credit limits are reached." The rest of the redesign still waits for the app to be finished.

### 23. Credit watcher: findings before the design (2026-10-16)
- **Headless runs already carry the signal.** Every `claude -p --output-format stream-json` run logged `rate_limit_event` lines: `rate_limit_info.status` = `allowed` | `allowed_warning` | `rejected`, `rateLimitType` (`five_hour` / `seven_day`), `utilization` (only once warning: 0.90, 0.91, … 0.99 in 1 % steps), `resetsAt` (epoch seconds), and `unifiedWindows.{five_hour,seven_day}.{utilization,resetsAt}`. They keep arriving during the workflow (agents' calls), not only from the main loop. Run #11: 11 warnings from 0.90 to `rejected`. Runs #5, #7, #8, #10, #11 all ended on `rejected`.
- **Interactive sessions wait at the limit by themselves** ("Usage limit reached · continuing automatically at 1:50pm", seen in the app's own interactive session). Docs: a workflow pauses instead of failing its agents only in an interactive claude.ai session (v2.1.271+; installed 2.1.301), at most twice per run, reset within 24 h; never under `claude -p`, the Agent SDK or background sessions.
- **Docs (code.claude.com, via a research agent):** no pre-limit hook; `StopFailure` hook has `error: rate_limit` but only after the fact; `SIGINT` ends the turn cleanly, `SIGTERM` leaves it unfinished; `claude --resume` can replay a workflow's saved results (`resumeFromRunId`) in the same session; a step still running when stopped starts over; a failed step re-runs everything after it. No documented `-p` example of the headless resume path.
- **The plugin's gap:** at the limit `agent()` returns `null`, and `sdd-run.js` turns that into `outcome: failed` (implementer) or "the verifier did not finish" feedback, so a usage limit looks like a task failure. Workflow scripts can't read files, so a pause must come from outside the process.

### 24. Credit watcher: built, committed, end-to-end test on a toy project (2026-10-16)
- Built directly (user's choice), both routes: the supervisor, plus the interactive route documented. Committed `c517683` (with all earlier run fixes) and `a10a465` (logs) at the user's request.
- Toy project `~/private/sdd-toy` (2 tasks, trusted by the user). Test 1: `sdd-supervise.py --pause-after 1 --no-sleep`. The pause worked: run.json kept `workflow_dir` and `phases`, the checkpoint held T001's implementer result with `verify:T001` cut off, and the tree kept `calc.py` and its test.
- **Bug found by the test:** after a pause, the supervisor waited for the window with the highest usage, the seven-day one (normal usage, reset on Sunday), and gave up ("beyond --max-wait-hours"). Fix: it waits only for the window that hit the limit or crossed the threshold, and a test pause restarts at once. Two new unit tests (10 supervisor tests).
- **Test 2 (resume), passed:** the supervisor started `/sdd-run` again. Preflight: "Resuming: T001's implementation is done and resumes at verification. Keeping the tree as is." The workflow got the checkpoint as `resume`; its first agent was `verify:T001` (approved), then `commit:T001` (`3056ac3`), then T002 normally (`e8e3a7f`), report `035380b`, `run.json` removed, supervisor exit 0. 14:19–14:21. Not tested end to end: a kill in the middle of an implementer (the "continue from the tree" note) and a real usage-limit pause; both are covered by unit tests only.
- **Operator slip and plugin fix (14:24):** I started the supervisor as a Monitor command; a monitor is killed after at most 30 minutes, which would have killed the supervisor mid-run, and because the run has its own process group (for clean stops), the run would have kept going unsupervised. Stopped it during the preflight (nothing written). Fix: the supervisor now stops its run when it gets SIGTERM, SIGHUP or SIGINT (test added, 11 supervisor tests). Operator rule: start the supervisor with `run_in_background` and its output in a file, and follow that file with Monitor.
- **Run #13 (supervised, 14:25–):** T266 committed `185f7d9` (first try); T267 started 14:37. User asked for the credit level mid-run: the stream's `unifiedWindows` carries utilization even below the warning level (five_hour 0.42, seven_day 0.51 at 14:40). **Gap:** the supervisor reports usage only from 90 % and `status.json` has no usage, so the operator had to read the raw stream. To do after this run: write both windows into `status.json` on every event and print every 5 % step; `/sdd-status` shows them.
- **Run #13 result (14:25–14:59, supervised, 1 cycle):** T266 `185f7d9`, T267 `32e5855`, T268 `b4d1599` (rejected once: the API contract still described only the old refusal reason), report `aedcf19`; 0 blocked; no pause needed (five-hour ~42 % mid-run). Refusals: one compound `cd && uv run python -c`, worked around. Supervisor exit 0.
- **Plugin gap:** `/sdd-run` converges only when the newest Convergence phase has no open tasks, but held tasks (`[dep]` T240/T243, `[manual]` T246) stay open forever, so converge never runs again and the committed spec changes (D12–D14) never become tasks. Redesign: count only runnable tasks for that rule, or converge whenever spec.md changed after the last converge (backlog item 27).
