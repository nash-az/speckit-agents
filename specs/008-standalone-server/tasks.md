# Tasks: Standalone server

**Input**: `specs/008-standalone-server/` (spec.md, plan.md, research.md, data-model.md, contracts/, quickstart.md)
**Checks**: `python3 -m unittest discover -s tests`, `node --test tests/workflow.test.mjs`, `claude plugin test .`.

## Format: `[ID] [P?] [Story] Description`

- Every task is one line. A cited section ("§N") means `specs/008-standalone-server/data-model.md` §N, including
  its rules and its **Tests** list: implementing a task means implementing that section's rules and those tests.
  "003 §N" and "005 §N" mean those features' data-model sections.
- Agent tags (`[manual] [decision] [dep] [download] [eval] [ui]`) follow the project's tasks template.
- Tests always run with temporary `HOME`, `XDG_STATE_HOME` and `XDG_DATA_HOME`, and never use a hard-coded `/tmp`.
- The old dashboard (`bin/sdd-dashboard.py`, `tests/ui/`) keeps passing every test until T055.
- A `[ui]` task lists `server/static/` among its files and rebuilds it with `cd ui && npm run build`. Implementers
  never commit; the bookkeeper does.

---

## Phase 1: Setup and proofs

- [x] T001 [P] [manual] From a terminal, not a run (a nested `claude -p` cannot start its own sandbox inside a run), prove research R2 for headless runs: in a temp git repo add `.claude/skills/env/SKILL.md` whose only step runs `env` as a Bash call, run `claude -p "/env" --permission-mode dontAsk --allowedTools "Skill,Bash(env)"`, and record under `## R2 evidence (headless)` in `specs/008-standalone-server/research.md` whether `CLAUDE_PID` and `CLAUDECODE` are set and whether `CLAUDE_PID` is the `claude -p` process (`ps -o command -p <pid>`).
- [x] T002 [P] [manual] Prove research R11 on your own machine (it changes your Claude Code setup): in a terminal run `claude plugin update speckit-agents@speckit-agents < /dev/null` and `claude plugin install speckit-agents@speckit-agents --scope user --json < /dev/null`, and record both outputs and exit codes under `## R11 evidence` in `specs/008-standalone-server/research.md`, with one line saying whether they need a terminal.
- [x] T003 [P] Bump `.claude-plugin/plugin.json` `version` to `0.8.0` (research R3) and add `tests/test_plugin_manifest.py` asserting the file parses, `version` matches `^\d+\.\d+\.\d+$` and `name` is `speckit-agents`.

---

## Phase 2: Foundational engine (blocks every story)

- [x] T004 Implement §1's `CONTRACT` and `contract_compare` in `bin/sdd-tasks.py`, with §1's tests in `tests/test_sdd_tasks.py`.
- [x] T005 After T004, move `state_dir`, `get_status` and `supervisor_alive` into `bin/sdd-tasks.py` and add `session_alive` and the `session` command per §3 (`bin/sdd-supervise.py` and `bin/sdd-dashboard.py` import the moved functions), with §3's tests in `tests/test_run_state.py`.
- [x] T006 After T005, implement §4 in `bin/sdd-tasks.py`: `current_state()` and the `state` command, then make `current_run()` in `bin/sdd-dashboard.py` return `current_state()` without the new keys, with §4's tests in `tests/test_run_state.py`; every test in `tests/test_sdd_dashboard.py` keeps passing.
- [x] T007 After T006, create `bin/sdd-control.py` per §5 (moving `write_control` and `run_controls` from `bin/sdd-dashboard.py`, whose `run_state` stays and calls the new module, and `read_control`, `send_control` from `bin/sdd-supervise.py`, adding the `flock`, the new rows and the CLI), update the old callers to import it, delete the old reason text and the dashboard tests that asserted it, with §5's tests in `tests/test_sdd_control.py`.
- [x] T008 After T007, add `"contract"` per §1 to `write_status()` and `write_control()`, to `sdd-job.py` `save()`, and to the top-level JSON printed by every command of `bin/sdd-tasks.py`, `sdd-decide.py`, `sdd-manual.py`, `sdd-unblock.py`, `sdd-edit.py`, `sdd-job.py`, `sdd-launch.py`, `sdd-harvest.py`; in `bin/sdd-supervise.py` make `write_status()` atomic (tmp + `os.replace`), add `engine_version` per §6, and set `SDD_SUPERVISED=1` on the `claude -p` environment; tests in `tests/test_contract_field.py` (each command's output has `contract == "1.0"`) and `tests/test_supervise.py` (the fake `claude` records `SDD_SUPERVISED=1`).
- [ ] T009 [P] After T004, create `bin/sdd-engine.py` (`install`, `path`) per §2, with §2's tests in `tests/test_sdd_engine.py`.
- [ ] T010 After T009, register `session.start` in `hooks/register.ts` per §2, keep `claude plugin test .` passing, add a test there if the testing kit can fire `session.start`, and otherwise record one line saying so in research.md R3.
- [ ] T011 After T005, after T009, update `skills/sdd-run/SKILL.md` (new step 0 runs `python3 <plugin>/bin/sdd-engine.py install --from <plugin>` and ignores failures; step 6 runs `python3 <helper> session --run-json .sdd/run.json` as its own Bash call right after writing `run.json`, and records `workflow_task` with `workflow_dir`, per §3), `skills/sdd-init/SKILL.md` (the same step 0) and `skills/sdd-status/SKILL.md` (step 4 calls `python3 <helper> state --project .`, reports `state` and `origin`, and adds a line when `run.json`'s `contract` is not the same), with assertions for these exact commands in `tests/test_skill_text.py`.

---

## Phase 3: User Story 1 — One dashboard for all my projects (P1)

**Goal**: a standalone server with a project list and per-project API. **Independent test**: quickstart.md §2.

- [ ] T012 After T008, create `bin/sdd-views.py` per §14 by moving the listed functions out of `bin/sdd-dashboard.py` unchanged (the dashboard imports them), and extend the read-only guard test to `bin/sdd-views.py` with the named exceptions; every test in `tests/test_sdd_dashboard.py` and `tests/ui/` passes unchanged.
- [ ] T013 After T012, create `server/sdd-server.py` and `server/app.py` per §6 (arguments, the Claude-session refusal and the cleaned child environment, single instance, start without an engine, engine reload, `ThreadingHTTPServer` on 127.0.0.1) and §10's request checks, with §6's core tests in `tests/test_server_core.py` plus a test that fails when any file under `server/` names the repository's `bin/` or loads modules other than through `--engine`.
- [ ] T014 After T013, create `server/projects.py` with `GET /api/projects`, `POST /api/projects/add`, `POST /api/projects/<id>/remove` and `POST /api/p/<id>/feature` per §6 and §10, with §6's project tests (5 projects, symlink, missing, restart) in `tests/test_server_projects.py`.
- [ ] T015 [P] After T014, add `GET /api/fs/list` to `server/projects.py` per §10, with §10's tests in `tests/test_server_projects.py`.
- [ ] T016 After T014, mount every GET route of the old dashboard (the list at the top of `bin/sdd-dashboard.py`, except `/`, `/api/projects` and `/api/project/select`) under `/api/p/<id>/…` in `server/app.py`, answered by the `bin/sdd-views.py` functions with that project's path and its `feature` (unknown id → 404, missing project → 410), with `tests/test_server_routes.py` comparing each route's JSON with the old dashboard's for the same toy project.
- [ ] T017 After T016, mount the old dashboard's write routes under `/api/p/<id>/…` in `server/app.py` (run stop, resume, hold, release and start; decisions answer and apply; manual done and skip; blocked done, answer and drop; doc save and commit; jobs start, continue, merge and drop), calling the same engine helpers, with tests in `tests/test_server_routes.py` comparing the resulting files with the old dashboard's (with `sdd-launch` mocked as in `tests/test_sdd_dashboard.py`).
- [ ] T018 After T016, create `server/watch.py` and `GET /api/stream` per research R6 and contracts/server-api.md (one thread, 0.5 s, topics `tasks | run | runs | jobs | blocked | docs | server`, `: ping` every 15 s, dropped clients ignored), with tests in `tests/test_server_stream.py`: a `tasks.md` touch gives a `tasks` event within 2 s, a journal touch gives `run`, and a disconnect leaves no traceback.

---

## Phase 4: User Story 3 — The engine comes with the plugin; mismatches are flagged (P1)

**Goal**: versions are visible and a major mismatch locks writes. **Independent test**: quickstart.md §1, §4.

- [ ] T020 After T017, create `server/versions.py` and `GET /api/server` per §11 and §1 (the server's own `CONTRACT`, live files only, the write lock, 503 with no engine), with §11's tests in `tests/test_server_versions.py`.

---

## Phase 5: User Story 2 — Every run looks the same however it was started (P1)

**Goal**: the server reports the origin, the session and the attachment. **Independent test**: quickstart.md §3.

- [ ] T021 After T007, after T016, extend `GET /api/p/<id>/run/current` with `origin`, `session_alive`, `attach` and the `sdd-control.py` `controls`, with tests in `tests/test_server_routes.py` for every row of §5's new table and a check that `python3 bin/sdd-tasks.py state` gives the same `state` and `origin`.

---

## Phase 6: User Story 4 — Hand-over after a cut-off (P2)

**Goal**: attach to an in-session run and take it over only when its session has ended. **Independent test**: quickstart.md §5.

- [ ] T022 [P] After T005, create `bin/sdd-slots.py` per §8 (settings, `count`, `can_take`, `take`, `enqueue`, `free`, `holders`), with §8's slot tests in `tests/test_sdd_slots.py`.
- [ ] T023 After T022, after T008, make `bin/sdd-supervise.py` take and free a slot around every cycle per §8 (person start: exit 4 on a race; starts by itself: `enqueue`, `queued`, retry), with §8's supervisor tests in `tests/test_supervise.py`.
- [ ] T024 After T023, add `--adopt RUN_ID` and `--confirmed-closed` to `bin/sdd-supervise.py` per §7, with §7's tests in `tests/test_adopt.py`.
- [ ] T025 After T024, add `adopt()` and the `adopt` command to `bin/sdd-launch.py` per §7, and a `can_take()` refusal to `launch()`, `launch_job()` and `continue_job()` (not to `adopt()`) per §8, plus `adopt()`'s three `confirm_closed` checks per §7, with tests in `tests/test_sdd_launch.py`.
- [ ] T026 After T025, after T017, add `POST /api/p/<id>/run/adopt`, `GET /api/slots`, `GET /api/settings` and `POST /api/settings` (`max_parallel` only; `plugin_source` and `claude_command` are rejected with 400) to `server/app.py` per contracts/server-api.md, with tests in `tests/test_server_routes.py` (adopt → 202 and an `attach.json`, adopt twice → 409, confirm_closed required → 409 without it, confirm_closed on a live running session → 409, settings round trip, `max_parallel: 0` → 400).

---

- [ ] T019 After T021, after T026, after T018, add the restart test of §6 (a fake supervised run, Job and attachment survive a server restart and show with their controls within 10 s) to `tests/test_server_core.py`.

---

## Phase 7: User Story 5 — Stop a run from a command (P2)

**Goal**: `/sdd-stop` equals the buttons. **Independent test**: quickstart.md §6.

- [ ] T027 After T007, after T011, create `skills/sdd-stop/SKILL.md` per §5 (the five cases, the exact texts, each command as its own Bash call), add `sdd-stop` to the skills line in `README.md`'s file tree, and assert the file, its `name: sdd-stop` and both commands in `tests/test_skill_text.py`.

---

## Phase 8: User Story 7 — Set up projects and keep the plugin installed (P2)

**Goal**: opening an unset-up repo offers setup; the plugin can be installed and updated. **Independent test**: quickstart.md §9.

- [ ] T028 [P] After T004, create `bin/sdd-setup.py` with the `detect` command per §12 (`PATH` lookups and file reads only), with §12's detect tests in `tests/test_sdd_setup.py`.
- [ ] T029 After T028, add `check` and `apply` (`--commit`, `--dry-run`, `--refresh`) to `bin/sdd-setup.py` per §12, reading templates from the engine folder, with §12's check and apply tests in `tests/test_sdd_setup.py`.
- [ ] T030 After T029, rewrite `skills/sdd-init/SKILL.md` so every file it writes goes through `python3 <plugin>/bin/sdd-setup.py apply --checks … [--refresh]` per §12 (steps 1, 2 and the sandbox re-check stay; step 2 no longer writes `config.toml` itself), with assertions in `tests/test_skill_text.py`.
- [ ] T031 After T029, after T014, create `server/setup.py` with `POST /api/setup/detect`, `/api/setup/check`, `/api/setup/apply` and `/api/setup/specify` per §12 (`apply` adds the project; outside home → 403), with tests in `tests/test_server_setup.py`.
- [ ] T032 After T020, create `server/plugin.py` with `GET /api/plugin`, `POST /api/plugin/install` and `POST /api/plugin/update` per §13, and the fake `tests/fake/claude-plugin`, with §13's tests in `tests/test_server_plugin.py`.

---

## Phase 9: User Story 6 — A nicer, faster UI (P2)

**Goal**: a React + TypeScript UI with parity and live updates. **Independent test**: quickstart.md §7.

- [ ] T033 [dep] Create `ui/` per §9 (`package.json` with exact versions of the listed runtime and build dependencies, `package-lock.json` from `npm install` through Artifactory `npm-virtual` with the credentials of `.sdd/learnings.md` never written into the repo, strict `tsconfig.json`, `vite.config.ts`, `src/main.tsx` rendering a placeholder, `README.md` with `npm ci` and `npm run build`), build into `server/static/`, and add `ui/node_modules/` to `.gitignore`.
- [ ] T034 After T033, after T013, serve `server/static/` from `server/app.py` per §9 (index for non-API paths, cache headers, content types, no traversal), with tests in `tests/test_server_core.py` and the opt-in `tests/test_ui_build.py` per §9.
- [ ] T035 [ui] After T034, add `ui/src/api.ts`, `stream.ts`, `router.tsx`, `App.tsx` (header with project switcher, plugin badge, version warnings, Reconnecting; view navigation; a feature picker always shown in the header that calls `POST /api/p/<id>/feature`, as the old dashboard's header picker) and `theme.css` per §9, and rebuild `server/static/`.
- [ ] T036 [ui] After T035, add the project list view and `components/FolderBrowser.tsx` and `components/SetupPrompt.tsx` (§10, §12: dry-run diffs, candidates with checkbox and edit box, Run checks with pass/fail and output, "keep anyway", the unsandboxed warning text, "Commit the setup" ticked, Set up, and `specify init` when Spec Kit is missing), and rebuild `server/static/`.
- [ ] T037 [ui] After T035, add `views/Overview.tsx` (the old Overview's facts, and a "choose a feature" prompt pointing at the header picker when none is active) and `components/RunBanner.tsx` covering every `controls` row of 003 §12.3 and §5 (including Supervise this run, the confirm-closed variant, Watching, Taken over, Waiting for a free place, a run's engine version when it differs), and rebuild `server/static/`.
- [ ] T038 [ui] [P] After T035, add `views/Tasks.tsx` (phases with roll-up, `components/StatusBadge.tsx` for the ten 003 status keys, finished phases folded last, the task log panel, updated in place on `tasks` and `run` events), and rebuild `server/static/`.
- [ ] T039 [ui] [P] After T035, add `views/Runs.tsx` (run list, Start run panel with the cap refusal text, run detail with tasks newest first, events, report, commits), and rebuild `server/static/`.
- [ ] T040 [ui] [P] After T035, add `views/Jobs.tsx` (job cards, Start job, Continue, Merge, Drop, the hold/release checkbox, 005 §9 texts), and rebuild `server/static/`.
- [ ] T041 [ui] [P] After T035, add `views/Waiting.tsx` and `views/Decisions.tsx` (manual-task buttons with the steps box and `Record:` line from the feature's `manual.md`, doc links in task text, BLOCKED.md entry buttons, decisions with options and Save, as 003 FR-033, FR-036, FR-037), and rebuild `server/static/`.
- [ ] T042 [ui] [P] After T035, add `views/Docs.tsx` (the document tabs, rendering with `marked`, `dompurify` and `highlight.js`, working links, the spec and constitution editor with Save and Commit as 003 FR-035), and rebuild `server/static/`.
- [ ] T043 [ui] After T036, add `views/Settings.tsx` (`max_parallel`) and `components/PluginDialog.tsx` (Install and Update with a confirm, the accept-command confirmation with command and hash, the restart note, disabled while a run is going, per §13), and rebuild `server/static/`.
- [ ] T044 After T034, after T036, create `tests/server_ui/harness.py` and `tests/server_ui/test_smoke.py` per §9 (page loads with no console errors and lists the toy project).
- [ ] T045 After T044, after T036, after T037, after T038, after T039, after T040, after T041, after T042, after T043, port every test in `tests/ui/` to `tests/server_ui/` keeping each assertion and changing only selectors and paths (a failure is fixed in the UI, never in the assertion), and list the ported tests under `## UI parity` in `specs/008-standalone-server/research.md`.
- [ ] T046 After T045, add `tests/server_ui/test_live.py` and `tests/server_ui/test_phone.py` per §9 (an event on the page within 2 s with no reload and the scroll kept; at 375×800 no view scrolls sideways).
- [ ] T061 After T008, add `--image` to `bin/sdd-manual.py` `done` per §16 (types by first bytes, at most 5 and 10 MB each, `evidence/<task id>-<n>.<ext>`, links in the saved note, one commit, nothing left on a refusal), with §16's engine tests in `tests/test_sdd_manual.py`.
- [ ] T062 After T061, after T017, accept `images` in `POST /api/p/<id>/manual/done` in `server/app.py` per §16 and contracts/server-api.md (base64 to temp files, `--image`, temp files removed, 60 MB body limit for this route), with §16's route tests in `tests/test_server_routes.py`.
- [ ] T063 [ui] After T062, after T041, add `components/NoteBox.tsx` per §16 (paste or drop pictures, thumbnails with remove, the refusal text under the box, I did it sends them, Skip does not) and use it in the manual-task card of `views/Waiting.tsx` and the Roadmap's Waiting on you list, and rebuild `server/static/`.
- [ ] T064 After T063, after T044, add `tests/server_ui/test_note_pictures.py` per §16 (paste shows a thumbnail, remove clears it, I did it with one picture ticks the task and links `evidence/<task id>-1.png`).
- [ ] T047 [manual] After T046, after T064, look at the new UI in your own browser (`sdd-server`, each view on this repo and one other project) and note what tests cannot judge (layout, colors, spacing, wording); add findings as tasks at the end of this file, or write "nothing to change" here.

---

## Phase 11: User Story 8 — The whole project at a glance (P2)

**Goal**: the roadmap is a project's first view. **Independent test**: US8's independent test in spec.md.

- [ ] T056 After T012, add `roadmap`, `waiting_all` and `runs_all` to `bin/sdd-views.py` per §15 (stages from the table, progress, overall, file order then unlisted folders, warnings for an unreadable file), with §15's view tests in `tests/test_roadmap.py`.
- [ ] T057 After T056, add `roadmap-save` and `activate` to `bin/sdd-edit.py` per §15 (validation, the exact refusal texts, the plain commit), with §15's write tests in `tests/test_roadmap.py`.
- [ ] T058 After T057, after T017, add `GET /api/p/<id>/roadmap`, `/waiting-all`, `/runs-all`, `POST /api/p/<id>/roadmap` and `POST /api/p/<id>/feature/activate` to `server/app.py` per §15 and contracts/server-api.md, with tests in `tests/test_server_routes.py`.
- [ ] T059 [ui] After T058, after T035, add `views/Roadmap.tsx` as the project's first view (`/p/<id>` opens it): rows in order with stage, done/total and what needs you, the overall bar, one Waiting on you list naming each item's feature with its buttons, the run history across features, Start run on a row (confirm, then `feature/activate`, then the Start run panel), and reorder (up/down) and state editing with Save; rebuild `server/static/`.
- [ ] T060 After T059, after T044, add `tests/server_ui/test_roadmap.py` covering US8 acceptance 1 to 6.

---

## Phase 10: Polish

- [ ] T048 [manual] After T010, reinstall the plugin (commit, then `claude plugin marketplace update speckit-agents` and `claude plugin update speckit-agents@speckit-agents`), start an interactive session in any folder, check `~/.local/share/speckit-agents/engine/current/VERSION.json` names 0.8.0, then run one `claude -p "hi"` and record in research.md R3 whether the hook fired there (expected: skipped only when `SDD_SUPERVISED=1`).
- [ ] T049 [P] After T032, add a "Server" section to `README.md` (install by git clone and `python3 server/sdd-server.py --install-command`, start with `sdd-server` from a terminal, Load project, setup, the plugin badge, versions, the cap, runs surviving a restart).
- [ ] T050 [P] [manual] Add a note at the top of `specs/004-feedback-chat/spec.md` and `specs/006-spec-cycle/spec.md` (specs are protected during runs): "Built on 008's server and UI (`specs/008-standalone-server/`); routes are under `/api/p/<id>/…`; views are React components in `ui/`."
- [ ] T051 After T046, run quickstart.md §1 to §4, §6, §7, §8 and §9 items 1, 2 and 4 with the fake `claude` (the named tests and CLI steps on temp repos) and record each section's result under `## Validation` in `specs/008-standalone-server/research.md`.
- [ ] T052 [manual] After T051, run quickstart.md §5 (hand-over) for real and record the result under `## Validation` in research.md.
- [ ] T053 [manual] After T051, run quickstart.md §9 item 3 (Install plugin from the server, with a test `HOME`) and record the result under `## Validation` in research.md.
- [ ] T054 [manual] After T047, after T060, decide that the new UI replaces the old one: tick this when the ported tests pass and T047 found nothing blocking.
- [ ] T055 After T054, delete `bin/sdd-dashboard.py` and `tests/ui/`, keep the `bin/sdd-views.py` guard test without importing the dashboard, replace the dashboard launch lines in `CLAUDE.md` and `README.md` with `sdd-server`, and remove `sdd-dashboard.py` from `bin/sdd-engine.py`'s exclusions and its test.

---

## Dependencies and order

Phase-level:
- Phase 2 needs Phase 1's T003 only.
- Phases 3 to 8 need Phase 2.
- Phase 4 needs Phase 3 (T017).
- T019 (the restart test) sits in Phase 6, because it needs the attachment (T026) and the new run state (T021).
- Phase 5 needs Phase 3 (T016).
- Phase 6 needs Phases 2 and 3 (T017).
- Phase 7 needs Phase 2.
- Phase 8 needs Phases 3 (T014) and 4 (T020).
- Phase 9 needs Phase 3 (T013), and its UI tasks need T033, which waits for your approval of the dependencies.
- Phase 11 needs Phase 3 (T012, T017) and Phase 9 (T035, T044).
- Phase 10 needs Phases 8, 9 and 11.

Task-level: every edge is written as "After Tnnn" in the task itself. Note that "T045 after T036 to T043" is spelled
out one by one so the parser sees each.

## MVP and strategy

MVP = Phases 1 to 5: the engine, the server with the project list, the per-project API, the stream, versions, and
the correct run state. The old dashboard keeps working throughout. Then hand-over (Phase 6) and `/sdd-stop`
(Phase 7), then setup and the plugin (Phase 8), then the UI (Phase 9). The old page is removed last (T055).

## Parallel examples

- Phase 1: T001, T002 and T003 together.
- After T004: T009, T022 and T028 together (different new files).
- After T035: T038, T039, T040, T041 and T042 together (different view files).
