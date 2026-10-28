---
description: "Task list for the plugin redesign"
---

# Tasks: Plugin redesign

**Input**: `specs/003-plugin-redesign/` — spec.md, plan.md, data-model.md, research.md, contracts/, quickstart.md

**Tests**: required. The constitution requires `python3 -m unittest discover -s tests` and
`claude plugin test .` to pass before every commit, and spec SC-003 asks for e2e coverage. Each
task adds or updates the tests for what it changes, in the same task.

**Read first, every task**: the `data-model.md` section the task names holds the exact design
(function names, signatures, strings, and in places the code). Implement exactly that; do not
redesign. "Regression test only" tasks must not change product code unless the test fails — if it
fails, stop and report `blocked` with the failing output rather than rewriting the feature.

**Test commands** (run all three before finishing any task):
`python3 -m unittest discover -s tests` · `node --test tests/workflow.test.mjs` · `claude plugin test .`

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the user story from spec.md (US1–US7)

---

## Phase 1: Setup

- [ ] T001 Confirm the baseline: run the three test commands above on the unchanged tree and record the results (pass/fail counts) in `specs/003-plugin-redesign/research.md` under a new heading `## Baseline test run`. Do not fix failures here; list them.
- [ ] T002 [P] Check whether anything uses `package.json` / `package-lock.json` (both added in commit `a8de64b`; `package.json` is `{}`): search the repo for `require(`, `import ` of non-relative modules in `workflows/`, `hooks/`, `tests/*.mjs`, `tests/*.ts`. If nothing needs an npm package, delete both files; otherwise leave them and write which file needs them into `research.md` under `## package.json`.

---

## Phase 2: Foundational (`bin/sdd-tasks.py` and the workflow cleanup — everything else reads these)

All tasks in this phase edit `bin/sdd-tasks.py` and `tests/test_sdd_tasks.py`, so they run one after another.

- [x] T003 Add `parse_blocked_entries(path)` to `bin/sdd-tasks.py` exactly as in data-model.md §3.1 (last entry per id wins; fields status/tag/reason/question/answer; status lowercased, default `"open"`; `answer` also takes the following lines that are not `## …` and not `- <word>:`). Re-implement `parse_blocked(path)` as `{id: e["status"] for id, e in parse_blocked_entries(path).items()}`. Tests in `tests/test_sdd_tasks.py`: two entries for one id (last wins), multi-line answer, missing file → `{}`, `parse` command output unchanged for an existing fixture.
- [x] T004 In `bin/sdd-tasks.py`, replace `TERMINAL_STATUSES` with `HOLDING_STATUSES = {"open", "wontfix", "moved"}` and update `hold_reason()`'s status mapping to exactly: `open → ("blocked", "open entry in BLOCKED.md")`, `wontfix → ("wontfix", "dropped in BLOCKED.md (wontfix)")`, `moved → ("moved", "moved to product/admin use")`; `resolved` no longer holds (data-model.md §3.2). Update the comment above the constant and `hold_reason()`'s docstring. Replace the header text written by `block()` with the exact text in §3.2. Tests: resolved → runnable; moved → held with tag `moved`; open → held with tag `blocked`; a new BLOCKED.md gets the new header.
- [x] T005 In `bin/sdd-tasks.py` `hold_reason()`, change the allow check to `if allow_key and (allow.get(allow_key) or f"{tag}:ok" in task["tags"]):` and set `KNOWN_TAGS = set(HOLD_TAGS) | {"eval", "ui", "dep:ok", "download:ok"}` (data-model.md §3.3). Add the two tags to `templates/agent-tags.md` with the sentence given in §3.3. Tests: `[dep] [dep:ok]` with `dependencies = false` → runnable; `[dep]` alone → held; `[DEP:OK]` is parsed as `dep:ok`.
- [x] T006 Rewrite superseded handling in `bin/sdd-tasks.py` `direct_holds()` and `propagate_holds()` exactly as data-model.md §3.4: (1) `superseded-by Tnnn`/`replaced-by Tnnn` on task X holds X with `("superseded", "<ids>")`, regardless of the successor's done state; (2) every open task in a non-Convergence phase before the newest phase whose title matches `^convergence` (case-insensitive) is held `("superseded", "Phase <id>")`; (3) in `propagate_holds()` use `if dep in holds and holds[dep][0] != "superseded":`; compute tag/BLOCKED holds first and let rules 1 and 2 overwrite them (`superseded` wins). Tests: each rule, the "per T020" dependent stays runnable, a `[manual]` T020 that is superseded is held as `superseded` (not `manual`) and its "per T020" successor stays runnable, a phase *after* the Convergence phase is untouched, an older Convergence phase's open tasks are untouched.
- [x] T007 In `bin/sdd-tasks.py` `plan()`, take the entries dict from T003 (convert to statuses where `hold_reason()` needs them) and, for each runnable task whose entry is `resolved` with a non-empty answer, set `task["answer"]` and `task["question"]` (data-model.md §3.5). Update `main()`'s `plan` branch to pass `parse_blocked_entries(args.blocked)`. Tests: a resolved task appears in `phases[].tasks` with `answer` and `question`.
- [x] T008 Add `SNAPSHOT_FIELDS`, `snapshot(parsed)` and `changed(parsed, old)` to `bin/sdd-tasks.py` exactly as data-model.md §3.6, plus two CLI subcommands: `snapshot <tasks.md>` (prints the snapshot) and `changed <tasks.md> --run-json <path>` (reads `task_snapshot` from that JSON file; prints `{"changed": [...]}`, empty list when the key is absent). Update the module docstring's command list. Tests: edited description → listed; only the checkbox ticked → not listed; task moved to another line → not listed.
- [x] T009 Add `config_errors(config)` to `bin/sdd-tasks.py` with exactly the rules and message strings in data-model.md §3.8, add `"errors": config_errors(config)` to the `config` command's output, and remove `"isolation"` from `DEFAULT_CONFIG`. Tests: one test per rule (unknown role, empty model, profile without model, non-string env, secret-like env name, `run.isolation = "worktree"`), and a Bedrock ARN model value produces no error.
- [x] T010 Regression test only (FR-009, research.md "Dependency of a held task"): in `tests/test_sdd_tasks.py` add a test where T2 says "after T1" and T1 is `[manual]` → `plan` holds T2 with tag `held`; and T3 "after T2" → held (fixpoint). Do not change `propagate_holds()` unless the test fails.
- [x] T011 Regression test only (FR-010, research.md "Converge blocked by held tasks"): in `tests/test_sdd_tasks.py` add a test where the newest `## Phase 9: Convergence` phase has only `[manual]` and `wontfix` tasks → `plan --phases 9` reports `runnable: 0` and lists them under `held`. This is the input `skills/sdd-run/SKILL.md` step 2.1 uses to decide to converge again; also confirm by reading step 2.1 that it treats held-only phases as "converge again", and quote the sentence in the test's docstring.
- [x] T012 Remove worktree isolation from `workflows/sdd-run.js` exactly as data-model.md §5.1 (delete `ISOLATION`, `execFileSync`, `git()`, `worktreeFor()`, `createWorktree()`, `mergeWorktree()`, the worktree block before `parallel(...)`, the `wt` lines, the merge loop, `isolation` in `group.finished` and in the args comment). Delete the test `isolation worktree mode runs without crashing…` in `tests/workflow.test.mjs`. Remove `"isolation": …` from the args JSON in `skills/sdd-run/SKILL.md` step 6.4, and rewrite README.md's bullet "Parallel tasks share one working tree" to drop "Isolation in separate worktrees is not built yet" in favor of "Worktree isolation is planned on the Workflow's native per-agent isolation."

- [x] T043 Add `valid_workflow_dir(path)`, `journal_events(workflow_dirs)` and `run_events(project, run_id)` to `bin/sdd-tasks.py` exactly as data-model.md §3.9 (reuse `LABEL_RE`; the event table; `ts` from the first / last line of `agent-<agentId>.jsonl`, reading only the last 64 KB for the last line), plus the CLI subcommand `events <project dir> <run id>`. Tests in `tests/test_sdd_tasks.py` with a fixture transcript directory `tests/fixtures/wf_test/` (a `journal.jsonl` and two short `agent-*.jsonl` files written for the test, modelled on research.md D7's example line): one impl → reject → impl#2 → approve → commit sequence gives `task.started`, `task.implemented`, `task.verified` (reject), `task.started` (attempt 2), `task.implemented`, `task.verified`, `task.approved`, `group.finished` in that order with `ts` set; a `blocked` impl result gives `task.blocked`; a `failed` line gives `task.failed`; an unparseable line is skipped; a directory not named `wf_…` or without `journal.jsonl` lands in `missing`; no `workflows.json` gives empty lists.
- [x] T044 Remove the event feed from `workflows/sdd-run.js` and `skills/sdd-run/SKILL.md` exactly as data-model.md §5.5 (no `import()`, `Date.now()` or `event()` left; step 6.4 drops `eventsDir` and writes `.sdd/runs/<run id>/workflows.json` alongside `workflow_dir`; step 7's report line uses `sdd-tasks.py events`). In `tests/workflow.test.mjs` delete the `event feed writes …` test and add the source check from §5.5.

**Checkpoint**: `bin/sdd-tasks.py` carries the new task-state model and reads run events from the journal; the workflow has no worktree code, no `import()` and no event feed, and launches in the Workflow tool; all tests pass.

---

## Phase 3: User Story 1 — A trustworthy dashboard (P1) 🎯 MVP

**Goal**: rendered documents, one status vocabulary with icons and rollup, a "waiting on you" view, the active feature, localhost-only.

**Independent test**: quickstart.md §3 bullets 1–3, 5 and 6 on the toy project.

All tasks edit `bin/sdd-dashboard.py` (and `tests/test_sdd_dashboard.py`), so they run in order.

- [x] T013 [US1] In `bin/sdd-dashboard.py`, load `bin/sdd-tasks.py` with the importlib snippet in data-model.md §4.1, delete the dashboard's own `parse_tasks(text)` and `parse_blocked(text)`, and make `/api/project/tasks` return phases from `sdd_tasks.parse_tasks()` with each task extended to the exact shape in §2 (`status`, `status_tag`, `status_reason`, `question`, `answer`; mapping rules in §2) and each phase given `status` = the §1 rollup (priority list `blocked, retrying, running, held, paused, queued, moved, wontfix, superseded, done`) over its tasks. `/api/project/blocked` returns `{"text", "entries": [{"id", **entry}]}` from `parse_blocked_entries`. Tests in `tests/test_sdd_dashboard.py` for each status mapping and for the rollup.
- [x] T014 [US1] Add `active_feature(project)` to `bin/sdd-dashboard.py` with the exact lookup order in data-model.md §4.2, delete `find_file()`, make every per-feature endpoint read from the active feature and return `409 {"error": "choose a feature", "features": [...]}` when it is `None`, and extend `POST /api/project/select` with an optional `"feature"` (memory only). Tests: config `run.feature` wins, then `.specify/feature.json`, then the single folder, then 409 with two folders.
- [x] T015 [US1] Add `GET /api/doc?name=<name>` to `bin/sdd-dashboard.py` per data-model.md §4.3 and contracts/dashboard-api.md: the fixed name set (`spec, plan, research, data-model, quickstart, tasks, blocked, decisions, constitution, learnings, config`), `200 {"missing": true}` for a missing file, 404 for any other name; keep `/api/project/spec|plan|decisions|config` as aliases; validate run ids in `/api/runs/<id>/report` with `^[0-9]{8}-[0-9]{4}[a-z0-9-]*$` (else 404). Tests: each name, an unknown name, `name=../../etc/passwd` → 404, a bad run id → 404.
- [x] T016 [US1] Apply data-model.md §4.7 and §4.8 in `bin/sdd-dashboard.py`: reject requests whose Host (without port) is not `127.0.0.1`, `localhost` or `[::1]` with status 421 at the top of `do_GET` and `do_POST`; make `/api/projects` return only the started project and delete the directory scan in `discover_projects()`; add the security-boundary comment at the `ThreadingHTTPServer(('127.0.0.1', …))` call; delete the unused `import subprocess`. Tests: Host `evil.example` → 421; `/api/projects` has one item; plus the source-scan test from §4.8 (fails if the file contains `write_text`, a write-mode `open(`, `.unlink(`, `subprocess`, `os.remove`, `shutil.` or `git `).
- [x] T017 [US1] In `build_html()` of `bin/sdd-dashboard.py`, add the status system from data-model.md §1: one CSS variable per status key with the exact hex colors, a JS `GLYPHS` object (ten inline SVGs: circle-outline, spinner, rotate-arrow, pause-bars, check, clock, alert-triangle, slash-circle, arrow-up-right, arrow-right; 24×24 viewBox, `stroke="currentColor"`, `fill="none"`, `stroke-width="2"`), a JS `STATUS` object mapping each key to its glyph and label template, and one function `statusBadge(key, vars)` returning `<span class="status status-{key}" title="{label}">{svg}<span class="status-label">{label}</span></span>`. The `spinner` glyph rotates with a CSS animation. No icon font, no CDN for icons.
- [x] T018 [US1] In `build_html()` of `bin/sdd-dashboard.py`, rebuild the Tasks view and the Blocked view on the new API: every task card shows `statusBadge(task.status or transient or 'queued', …)` (transient comes later in T026 — for now use `task.status`, `'done'` if ticked, else `'queued'`); each phase heading shows its rollup badge; replace the current "Held / Waiting" logic (which only looks at four tags) with a "Waiting on you" section listing every task with status `held` or `blocked` and its `question`/`status_reason` (FR-005); the Blocked view shows entry badges per §1 "BLOCKED.md entry badges" (`resolved` → `queued` labelled "Answered — runs next"). Overview counters use the same statuses.
- [x] T019 [US1] In `build_html()` of `bin/sdd-dashboard.py`, render documents per data-model.md §4.3: load the pinned `marked@12.0.2`, `@highlightjs/cdn-assets@11.9.0` (script + `styles/github-dark.min.css`) and `dompurify@3.1.6` URLs exactly as written there; render with `DOMPurify.sanitize(marked.parse(text))`, then highlight every `pre code`; build a table of contents when there are 4+ `h2`/`h3`; if a library failed to load, show the old `<pre>` view plus a one-line notice. Add nav entries for every `/api/doc` name (Spec, Plan, Research, Data model, Quickstart, Decisions, Constitution, Learnings, Config, and "Raw document" toggles for tasks and BLOCKED); a `{"missing": true}` response shows "Not written yet". Run reports (Runs view) render the same way.
- [x] T020 [US1] In `build_html()` of `bin/sdd-dashboard.py`, when any per-feature request returns 409, show a feature picker listing `features`; choosing one posts `{"feature": name}` to `/api/project/select` and reloads the views.

**Checkpoint**: quickstart.md §3 bullets 1–3, 5, 6 pass.

---

## Phase 4: User Story 2 — Event feed that agrees, clean tree between runs (P1)

**Goal**: real event timeline, run state with a stale banner, live agent output, runtime files ignored.

**Independent test**: quickstart.md §2 and §3 bullets 4 and 7.

- [x] T021 [US2] Fix `list_runs()` and `get_events()` in `bin/sdd-dashboard.py` per data-model.md §4.4: runs are directories under `.sdd/runs/` containing `workflows.json`, `run_id = entry.name`, sorted by name descending; events come from `run_events()` in `sdd-tasks.py` (T043) with the run-id check from T015, and a run with only `missing` directories shows the "transcript is gone" text. Replace the fixture in `tests/test_sdd_dashboard.py` (currently `.sdd/runs/run-001.jsonl`) with `.sdd/runs/20261025-1200/workflows.json` pointing at a temporary `wf_…` directory holding a small `journal.jsonl`, and add tests that a flat `.sdd/runs/x.jsonl` is ignored and that a gone transcript shows the text.
- [x] T022 [US2] Add `GET /api/run/current` to `bin/sdd-dashboard.py` with exactly the five-rule state logic in data-model.md §4.5 (`none`, `paused` by report, `paused` by supervisor status not `"stopped"`, `running` when `run.json`'s `workflow_dir` is valid and its journal or an agent file changed in the last 30 minutes, else `stale`), and show the red stale banner text from §4.5 in the page. Tests for each state (set file mtimes in the fixture).
- [x] T023 [P] [US2] Document the same stale-run rules (data-model.md §4.5) in `skills/sdd-status/SKILL.md`, so `/sdd-status` reports `stale` the same way as the dashboard.
- [x] T024 [US2] Add `GET /api/runs/<id>/tasks/<task>/log` to `bin/sdd-dashboard.py` per data-model.md §4.6: the task's newest event in the last workflow directory (`run_events()`, T043) gives `agent_id`, and the file is `<workflow_dir>/agent-<agent_id>.jsonl` (the mapping is confirmed in research.md D7). Always status 200; `{"available": false, "reason"}` when not found. Tests with a fixture transcript directory.
- [x] T025 [P] [US2] In `skills/sdd-init/SKILL.md`, replace "Add `.sdd/run.json` to `.gitignore`" with adding all four lines from data-model.md §7 (`.sdd/run.json`, `.sdd/scratch/`, `.sdd/runs/*/`, `.sdd/plugin-feedback.jsonl`) and asking the user to commit `.gitignore`; keep the sentence that the config and run reports stay tracked.
- [x] T026 [US2] In `build_html()` of `bin/sdd-dashboard.py`, derive each task's transient state from the selected run's events with the exact event → state table in data-model.md §4.6 (newest event per task id; `retrying` n/max from reject count and `max_retries` + 1), show it via `statusBadge` when the task has no parked status, recompute each phase heading's rollup badge in the browser over the merged parked + transient states using the same priority list as data-model.md §1 (the backend's phase `status` only knows parked/done states), and add a "Live output" panel on a running task that polls `/api/runs/<id>/tasks/<task>/log` every 3 s and shows "Not available yet" when `available` is false.
- [x] T027 [US2] In `skills/sdd-run/SKILL.md`, add a preflight check before the clean-tree check (step 1, item 3): if `.gitignore` lacks any of the four lines from data-model.md §7, stop with "Run `/sdd-init` to update `.gitignore`, commit it, then start `/sdd-run` again." Delete step 6.2 (the `.sdd/run.json` gitignore step) and renumber.

**Checkpoint**: quickstart.md §2 and §3 bullets 4 and 7 pass.

---

## Phase 5: User Story 3 — One model for "needs a person" (P1)

**Goal**: resolved re-runs with the answer, moved/superseded behave, resume refuses changed tasks.

**Independent test**: quickstart.md §1 and §6.

- [x] T028 [US3] In `workflows/sdd-run.js` `taskBrief(task)`, when `task.answer` is set append the two lines from data-model.md §5.3 (`Earlier question: …` / `Answer from the person (follow it): …`). Test in `tests/workflow.test.mjs`: a planned task with `answer` puts both lines in the implementer prompt captured by the stub.
- [x] T029 [P] [US3] Update `skills/sdd-unblock/SKILL.md` to the new model (data-model.md §3.2): `resolved` = answered, the next `/sdd-run` runs the task again with the answer (replace the line saying resolved/wontfix "hold the task forever"); `wontfix` drops it; new `moved` for work that left the project; tasks the user did by hand are ticked. Keep the decisions-register step unchanged.
- [x] T030 [US3] In `skills/sdd-run/SKILL.md`: step 6.3 adds `"task_snapshot": <output of python3 <helper> snapshot <tasks>>` to `.sdd/run.json`; step 1's resume branch first runs `python3 <helper> changed <tasks> --run-json .sdd/run.json` and, if `changed` is non-empty, stops with the message in data-model.md §11 (list the task ids). Also in step 1: run `python3 <helper> config` and stop, printing each entry, when `errors` is non-empty.
- [x] T031 [US3] Extend the toy e2e (`tests/e2e/make_toy.sh`, `tests/e2e/check_toy.sh`) with the quickstart.md §1 fixture (T010–T014, the Convergence phase with T020/T090) and assert each id's state from `python3 bin/sdd-tasks.py plan … --config …`; the existing toy assertions must still pass.

**Checkpoint**: quickstart.md §1 and §6 pass.

---

## Phase 6: User Story 4 — Architect strong, developer cheap or local (P2)

**Goal**: run profiles in the supervisor, role models in the workflow, models in the report.

**Independent test**: quickstart.md §4.

- [x] T032 [US4] In `workflows/sdd-run.js` add `AGENT_MODELS` and `modelOpt(role)` exactly as data-model.md §5.4 and spread it into the `agent()` calls of `implement()` (`evaluator` for eval tasks, else `implementer`), `verify()`, `bookkeep()` and `repair()` (`implementer`). Tests in `tests/workflow.test.mjs`: with `agentModels: {implementer: 'haiku'}` the stub sees `model: 'haiku'` on impl calls only; with `'inherit'` or no entry, no `model` key.
- [x] T033 [US4] In `skills/sdd-run/SKILL.md` step 6.4 add `"agentModels": <config.agents or {}>` to the args JSON; accept `--profile NAME` in `argument-hint` and arguments — in step 1, refuse to start with `unknown profile NAME` when `[profiles.NAME]` is not in the config; otherwise only record it (an interactive session cannot switch model or provider); step 7's report gains the profile name (or "none") and the configured model per role (or "run's model") (data-model.md §11).
- [x] T034 [US4] In `bin/sdd-supervise.py` implement `--profile NAME` per data-model.md §9: load the project config through `bin/sdd-tasks.py` (importlib, as in §4.1), exit 2 printing every `config_errors()` entry or `unknown profile NAME`; `command()` adds `["--model", profile["model"]]`; the `Popen` env is `dict(os.environ, **profile.get("env", {}), CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS="0")`; the prompt passes `--profile NAME` on to `/sdd-run`. Tests in `tests/test_supervise.py` with the fake `claude` in `tests/fake/`: the command line contains `--model`, the env has the profile's variable, an unknown profile exits 2.
- [x] T035 [P] [US4] In `templates/sdd-config.toml` add commented-out `[agents]` and `[profiles.<name>]` examples with one-line explanations (contracts/config-schema.md "Template"), including the sentence that a gateway to a non-Claude or local model is best-effort and unsupported by Anthropic, and that secrets never go in this file.

**Checkpoint**: quickstart.md §4 passes.

---

## Phase 7: User Story 5 — Scratch space and per-task approvals work (P2)

**Goal**: agents can write scratch files; `[dep:ok]` tasks run and are told they may add the dependency.

**Independent test**: quickstart.md §2 (scratch) and §1 (T012 runnable), plus the prompt check below.

- [x] T036 [P] [US5] In `hooks/rules.ts` add a pure `posixNormalize(path)` (split on `/`, drop `''` and `.`, pop on `..`, join with `/`; no Node imports) and, in `protectedPath()`, normalize the relative path and `return undefined` when it starts with `.sdd/scratch/` (data-model.md §6). Tests in `tests/guard.test.ts`: subagent Edit of `.sdd/scratch/r1/T1/x.py` allowed with `.sdd/**` protected; `.sdd/scratch/../config.toml` refused; `.sdd/config.toml` refused for a subagent; shell `echo x > .sdd/scratch/r1/T1/out.txt` allowed.
- [x] T037 [US5] In `workflows/sdd-run.js` remove the `Allowed this run:` line from `context`, add `allowLine(task)` exactly as data-model.md §5.2, put it right after `context` in `implement()`'s prompt, and change `repair()` to `repair(outcomes, failing, tasks)` with one `id: allowLine` line per approved task (update its call site to pass `phaseInfo.tasks`). Tests in `tests/workflow.test.mjs`: with `allow.dependencies = false`, a task tagged `dep:ok` gets "new dependencies yes", another gets "no"; `taskAllow` overrides the tag.

**Checkpoint**: quickstart.md §2 scratch bullet passes; T012 of the §1 fixture runs.

---

## Phase 8: User Story 6 — The plugin keeps learning (P2)

**Goal**: project lessons in `.sdd/learnings.md`, plugin feedback local and harvested into `logs/`.

**Independent test**: quickstart.md §5.

- [x] T038 [US6] In `skills/sdd-run/SKILL.md` step 7 (both the interrupted branch and the normal branch), add the three end-of-run steps from data-model.md §8 (events read with `sdd-tasks.py events`, T043): project lessons appended to `.sdd/learnings.md` under `## <run id> (<YYYY-MM-DD>)` (no section if none, no duplicate bullets); plugin feedback as JSON lines with exactly the fields `id, date, run_id, project, text, evidence` appended to `.sdd/plugin-feedback.jsonl`; commit `.sdd/learnings.md` with the report (`git add -- .sdd/runs/<id>.md .sdd/learnings.md`); the report states how many lessons and feedback entries were written.
- [x] T039 [P] [US6] Create `bin/sdd-harvest.py` (stdlib only, executable, module docstring with usage) per data-model.md §8: arguments `<project dir> [--log <path>]`; reads `<project>/.sdd/plugin-feedback.jsonl` skipping bad lines; keeps harvested ids in `logs/.harvested.json` (`{"ids": [...]}`) relative to the plugin repo (the directory above `bin/`); appends new entries as `- <date> <run_id>: <text> (evidence: <evidence>)` under `## Harvested <today>` in the log file (default `logs/<YYYY-MM-DD>-<project dir name>-feedback.md`); prints `{"harvested": n, "skipped": m, "log": "<path>"}`. Tests in a new `tests/test_sdd_harvest.py` using a temporary plugin dir: first run harvests, second run harvests 0, a bad line is skipped.

**Checkpoint**: quickstart.md §5 passes.

---

## Phase 9: Polish & cross-cutting

- [x] T040 [P] In `bin/sdd-supervise.py` make the hard stop per window (FR-028, data-model.md §10): `--hard-stop` default `None`, `args.hard_stops = {name: min(1.0, args.hard_stop if args.hard_stop is not None else round(t + 0.01, 2)) for name, t in args.threshold.items()}`, help text as in §10. Tests in `tests/test_supervise.py`: defaults give five_hour 0.96 and seven_day 1.0; `--hard-stop 0.97` gives 0.97 for both.
- [x] T041 Update `README.md`: run profiles and role models (with the local-model caveat), what `resolved`/`wontfix`/`moved` mean, the four `.gitignore` lines, learnings and `bin/sdd-harvest.py`, the dashboard's documents and localhost-only rule. Keep the existing structure and style; remove any statement the tasks above made false.
- [ ] T042 Run the full validation in `specs/003-plugin-redesign/quickstart.md` (§1–§8) and record each section's result (pass/fail and one line of evidence) at the end of `quickstart.md` under `## Validation run`. Any failure is reported, not patched in this task.

---

## Phase 10: User Story 7 — Stop and resume a run from the dashboard (P2)

**Goal**: Stop (after the current task, or now) and Resume buttons for a supervised run, without the
dashboard starting or killing anything (FR-029, FR-030).

**Independent test**: quickstart.md §8. Runs before T042, which validates §1–§8.

- [x] T045 [US7] In `bin/sdd-supervise.py` implement the supervisor side of data-model.md §12.1–§12.2: `write_status()` adds `pid`; delete a leftover `control.json` at startup; add `read_control(logs)` (returns the dict and deletes the file, or `None`); in `Cycle.run()` handle `stop` (`now` → `stop_reason = "operator"` and `stop(proc)`; `after-task` → `self.operator = True`, reuse the existing safe-point path, write `state="stopping"`) and make the safe-point block set `stop_reason = "operator"` when `self.operator`; in `main()` hold after an `"operator"` outcome (`state="held"`), poll for `resume` for at most `--max-wait-hours`, then run the next cycle; and replace the usage-window `sleep_until` in `main()` by the polling loop of §12.2 (`hold` sets `keep_paused`, `release` clears it, at the reset a kept pause goes to `held` instead of the next cycle). Tests in `tests/test_supervise.py` with the fake `claude` in `tests/fake/`: hold during the wait ends `held` at the reset and starts no cycle; release before the reset lets the cycle start; `stop`/`resume` during the wait are discarded; after-task stop waits for the running agent and ends `held`; `now` stops at once; resume starts a second cycle; a `stop` while held is discarded; a stale `control.json` at startup is deleted; hold timeout ends `stopped` and returns 3.
- [x] T046 [US7] In `bin/sdd-dashboard.py` implement data-model.md §4.9 and §12.3: the Origin and Content-Type checks on every `POST` (add `--allow-port`), `OPTIONS` preflight for the two control routes, `supervisor_alive()`, the `controls` table in `GET /api/run/current` (plus `by` and `stopping`), `POST /api/run/stop`, `/api/run/resume`, `/api/run/hold` and `/api/run/release`, and `write_control()` (atomic, refuses when a request is already pending). Replace the source-scan test of §4.8 with the amended one and add the project-unchanged test. Tests for every row of the `controls` table, `202`/`409`/`400`/`403`/`415`, and that a `409` writes nothing. Do after T022 (same file and route).
- [x] T047 [US7] In `build_html()` of `bin/sdd-dashboard.py`, add the run banner of data-model.md §12.4 (Stop after current task, Stop now with a confirm dialog, Resume, Keep paused and Let it resume in the `waiting` state, disabled buttons showing `controls.reason`, 3 s polling, `409` text shown). If the UI split has already moved the page into static files by then, edit that file instead; the behavior is the same. Tests: the existing page tests assert the banner's strings and routes are present. Do after T026 and T046.
- [x] T048 [P] [US7] Add the stop/resume paragraph to `README.md` (supervised runs only, what the three states mean, how to start a supervised run, that the dashboard writes only the control file), and add a "Stop and resume" note to `skills/sdd-status/SKILL.md` where it describes the supervisor's `state`: `stopping` and `held` with the `reason`. Do after T046.

**Checkpoint**: quickstart.md §8 passes.

---

## Dependencies & Execution Order

- **Phase 1** → **Phase 2** (T003–T012, T043–T044, sequential: same files) → all story phases.
- **US1 (Phase 3)**: T013 → T014 → T015 → T016 → T017 → T018 → T019 → T020 (one file).
- **US2 (Phase 4)**: needs US1's T015 (run-id check) and T017 (`statusBadge`). T021 → T022 → T024 → T026 in order (dashboard file); T023, T025 parallel to those; T027 after T025.
- **US3 (Phase 5)**: needs Phase 2. T028 edits `workflows/sdd-run.js` — do it before T032 and T037, which edit the same file. T030 edits `skills/sdd-run/SKILL.md` — after T027, before T033 and T038 (same file).
- **US4 (Phase 6)**: T032 after T028; T033 after T030; T034 after T009; T035 any time after Phase 2.
- **US5 (Phase 7)**: T036 any time after Phase 1; T037 after T032.
- **US6 (Phase 8)**: T038 after T033; T039 any time after Phase 1.
- **US7 (Phase 10)**: T045 after T040 (same file `bin/sdd-supervise.py`; T034 too); T046 after T022; T047 after T026 and T046; T048 after T046. Needs the supervisor, so it cannot be validated on an interactive run.
- **Polish**: T040 any time after Phase 1 but not at the same time as T034 (same file); T041 after all story phases; T042 last (after Phase 10).

Files touched by more than one task (never run those tasks in parallel): `bin/sdd-tasks.py`,
`bin/sdd-dashboard.py`, `workflows/sdd-run.js`, `skills/sdd-run/SKILL.md`, `bin/sdd-supervise.py`,
`tests/test_sdd_tasks.py`, `tests/test_sdd_dashboard.py`, `tests/workflow.test.mjs`, `tests/test_supervise.py`.

## Parallel examples

```text
After Phase 2:  T036 (hooks/rules.ts)  ∥  T039 (bin/sdd-harvest.py)  ∥  T013… (bin/sdd-dashboard.py)  ∥  T029 (sdd-unblock skill)
During US2:     T023 (sdd-status skill)  ∥  T025 (sdd-init skill)  ∥  T021 (dashboard)
```

## Implementation strategy

1. Phases 1–2: the task-state engine and the workflow cleanup. Everything else depends on them.
2. MVP = US1 + US2: a dashboard you can trust and runs that leave the tree clean. Validate with
   quickstart §2–§3, then use it to watch the remaining work.
3. US3 → US4 → US5 → US6 → US7, each validated by its quickstart section.
4. Polish, then the full quickstart run (T042).

The language app resumes on the redesigned plugin after T042 passes. Its first `/sdd-run` will
stop until `/sdd-init` adds the four `.gitignore` lines (T027) — run `/sdd-init` there first and
commit its `.gitignore`.
