# Implementation Plan: Standalone server

**Branch**: `main` (spec on `main`; the run gets its own `auto/` branch) | **Date**: 2026-11-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/008-standalone-server/spec.md` (clarified 2026-11-02).

## Summary

A new standalone server (`server/`, Python standard library) with a rebuilt TypeScript UI (`ui/`, React + Vite,
built into `server/static/`). It opens any project set up for the plugin and shows and controls every run, however
the run was started. The plugin stays the engine: `bin/` keeps every file format and every process that acts on
them (supervisor, launcher), and gains a contract version, one run-state rule, a controls helper, a session
record, slots for the cross-project cap, an `--adopt` mode for hand-over, and a setup command (`sdd-setup.py`)
shared by `/sdd-init` and the server. The engine copy (a fixed, versioned folder) is refreshed by the plugin at
session start and by the server after it installs or updates the plugin. The server uses only that copy, and flags
version mismatches. The server is installed on its own (a git clone, R10) and can install and update the plugin
(R11) and set up new projects (R12).

## Technical Context

**Language/Version**: Python 3 (standard library only) for the engine and server; TypeScript 5+ (tsc 7.0.2 seen)
with React 19 and Vite 8 for the UI; TypeScript for the plugin hook (`hooks/register.ts`).
**Primary Dependencies**: none at runtime for Python. UI build: `react`, `react-dom`, `vite`,
`@vitejs/plugin-react`, `typescript`, installed through Artifactory `npm-virtual` (research R4), pinned by
`package-lock.json`.
**Storage**: files only. Project files (`.sdd/…`, `tasks.md`); `state_dir(project)` (existing);
`~/.local/state/speckit-agents/{server,slots}/`; `~/.local/share/speckit-agents/engine/`.
**Testing**: `python3 -m unittest discover -s tests`; browser tests (Python Playwright, `channel="chrome"`) in
`tests/server_ui/` under `SDD_UI_TESTS=1`; `node --test tests/workflow.test.mjs`; `claude plugin test .`; UI unit
tests with `npm test` in `ui/` (Vitest, only if added by a `[dep]` task; otherwise none).
**Target Platform**: macOS and Linux, one person's machine, localhost only.
**Project Type**: a Claude Code plugin (engine and skills) plus a local web application (server and UI).
**Performance Goals**: an event reaches the page within 2 s (SC-006); rediscovery after a restart within 10 s
(SC-007a); 5+ projects open (SC-002).
**Constraints**: no AI on any server path (Principle VII); the server never runs inside a Claude session
(FR-001, FR-011); writes stay atomic; nothing the server holds only in memory may be lost by a restart (FR-009b).
**Scale/Scope**: about 10 projects, 2 parallel runs, 50 API routes, 8 views.

## Constitution Check

| Principle | Check | Result |
|---|---|---|
| I. Spec Kit stays the source of truth | No Spec Kit file format changes; the plugin parts used are a skill (`/sdd-stop`) and a hook event (`session.start`) | Pass |
| II. Safe without a person watching | Runs keep the guard, sandbox and `auto/` branch; the server starts runs outside any sandboxed session so their own sandbox works (FR-011); a major version mismatch disables writes; the folder browser is bound to home and the request checks | Pass |
| III. Never guess on a person's behalf | Take-over needs a positive end signal or the person's confirmation (FR-010); new npm dependencies are `[dep]` tasks | Pass |
| IV. Verified, not reported | Every task has tests; UI parity is shown by ported browser tests before removal (FR-021) | Pass |
| V. Deterministic where possible | State, controls, slots, adoption and versions are plain code; `bin/sdd-tasks.py` stays where V names it | Pass |
| VI. One task, one commit | Tasks are split per file group (tasks.md) | Pass |
| VII. AI-free at the core | The server and engine run with no model; Claude is only what a run starts | Pass |

Post-design re-check: still all pass. One thing to watch: the committed UI build (`server/static/`) is generated
output in git. It is accepted because using the server must not need Node (FR-020), and a test keeps it equal to a
fresh build (data-model §9).

## Project Structure

### Documentation (this feature)

```text
specs/008-standalone-server/
├── spec.md
├── plan.md            # this file
├── research.md        # R1–R12
├── data-model.md      # §1–§14
├── quickstart.md      # §1–§9
├── contracts/
│   ├── server-api.md
│   └── engine-cli.md
├── checklists/requirements.md
└── tasks.md           # /speckit-tasks
```

### Source Code (repository root)

```text
bin/                       # the engine (copied by sdd-engine.py install)
├── sdd-tasks.py           # + CONTRACT, contract_compare, state_dir/get_status/supervisor_alive (moved), current_state, session_alive, `state`, `session`
├── sdd-views.py           # new: read models moved from the dashboard (data-model §14)
├── sdd-control.py         # new: write_control, read_control, controls, CLI (moved from the dashboard/supervisor)
├── sdd-engine.py          # new: install, path
├── sdd-slots.py           # new: take, free, holders
├── sdd-setup.py           # new: detect, check, apply (shared with /sdd-init)
├── sdd-supervise.py       # + --adopt, slots, SDD_SUPERVISED, contract in status.json
├── sdd-launch.py          # + adopt, cap check
├── sdd-dashboard.py       # old page; imports sdd-views, sdd-control, current_state; removed at parity (R9)
└── …                      # sdd-decide, sdd-manual, sdd-unblock, sdd-edit, sdd-job, sdd-harvest: + contract field
hooks/register.ts          # + session.start → sdd-engine.py install
skills/sdd-stop/SKILL.md   # new
skills/sdd-run/SKILL.md    # + install fallback, session record, contract in run.json
skills/sdd-status/SKILL.md # step 4 calls `sdd-tasks.py state`
skills/sdd-init/SKILL.md   # + install fallback; every file write goes through sdd-setup.py apply
server/
├── sdd-server.py          # entry point: single instance, args, serve
├── app.py                 # routes (server-wide + /api/p/<id>/… delegating to engine functions)
├── projects.py            # projects.json, folder browser
├── setup.py               # setup prompt routes (sdd-setup.py through the engine copy)
├── plugin.py              # claude plugin list/install/update
├── watch.py               # 0.5 s file watcher → SSE topics
├── versions.py            # CONTRACT, warnings, write lock
└── static/                # built UI (committed)
ui/                        # React + TypeScript source (package.json, src/…)
tests/
├── test_sdd_engine.py, test_sdd_control.py, test_sdd_slots.py, test_adopt.py, test_run_state.py
├── test_server_*.py       # server unit and route tests (port 0, temp XDG dirs)
├── test_ui_build.py
└── server_ui/             # browser tests ported from tests/ui
```

**Structure Decision**: one repo with two install paths (research R1, R10). The plugin (agents, skills, hooks,
workflow, `bin/`) is installed by Claude Code, or by the server's Install plugin (R11). The server and UI are run
from a git clone of the repo, installed and updated on their own (R10), and they reach the engine only through the
engine copy.

## Complexity Tracking

| Addition | Why it is needed | Simpler alternative rejected because |
|---|---|---|
| Engine copy with versioned folders and a symlink | The server needs a stable path and a way to see versions (FR-016) | Reading the plugin cache path directly: it changes with every update and needs the `claude` CLI |
| Slot files with `flock` | The cap must hold while the server is down (FR-009a, FR-009b) | A broker inside the server: lost on restart |
| A committed UI build | Using the UI must not need Node (FR-020) | Building at install: needs Node and Artifactory on every machine |
| Old and new UI side by side until parity | Removing the old page first would leave no working UI | A big-bang switch: no way to prove parity first |
