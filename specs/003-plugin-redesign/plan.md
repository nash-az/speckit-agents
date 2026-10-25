# Implementation Plan: Plugin redesign

**Branch**: `003-plugin-redesign` | **Date**: 2026-10-25 (revised after review pass) | **Spec**: [spec.md](./spec.md)

**Input**: `specs/003-plugin-redesign/spec.md`

**Who implements this**: a cheaper, less capable model than the one that wrote this plan. Every
task must be executable without choosing an approach: `data-model.md` gives the exact functions,
shapes, strings and in several places the code. Where it says "already works — regression test
only", the task must not re-implement anything.

## Summary

Make the plugin trustworthy before building the app further with it. The dashboard gets real
document rendering, one status vocabulary with icons, a correct event feed, the active feature, live
agent output and a localhost-only guard. Task state gets one model: `resolved` re-runs with the
answer, `moved` and `superseded` exist and behave predictably, per-task dependency approval works.
Runs leave the tree clean (runtime files ignored), record project lessons and plugin feedback, and
can run on a cheaper or local model (run profiles) with per-role models inside. Broken worktree
isolation is removed. The supervisor's hard stop becomes per window.

Approach: extend the existing files; the dashboard calls `bin/sdd-tasks.py` instead of parsing tasks
itself, so the CLI and the dashboard cannot disagree.

## Technical Context

**Language/Version**: Python 3.11+ stdlib (`bin/*.py`); plain JavaScript (`workflows/sdd-run.js`,
dashboard page script); TypeScript guard hooks (`hooks/*.ts`).
**Primary Dependencies**: none new on the backend. Browser-side, pinned from jsDelivr: `marked`,
`@highlightjs/cdn-assets`, `dompurify`. Check whether `package.json`/`package-lock.json` (added in
`a8de64b`, `package.json` is `{}`) are used by anything; delete them if not.
**Storage**: files only (`.sdd/`, `specs/<feature>/`, `logs/` in this repo).
**Testing**: `python3 -m unittest discover -s tests`, `node --test tests/workflow.test.mjs`,
`claude plugin test .`, `tests/e2e/` scripts.
**Target Platform**: macOS/Linux developer machine, single user.
**Project Type**: Claude Code plugin with a local web dashboard.
**Performance Goals**: none beyond feeling instant locally.
**Constraints**: backend stdlib only; dashboard localhost-only, no auth, never writes; no AI model
needed for engine, dashboard, CLI or status (Principle VII); no secrets in config.
**Scale/Scope**: one project and one run per dashboard instance.

## Constitution Check

| Principle | Check | Status |
|---|---|---|
| I. Spec Kit is the source of truth | No Spec Kit command or format is changed; superseded handling is computed by this plugin (FR-008), converge is untouched | PASS |
| II. Safe without a person watching | Scratch carve-out is a narrow path exception with traversal handled; resume refuses on changed tasks; config errors stop the run; broken worktree code (silent failures) is removed | PASS |
| III. Never guess for a person | `resolved` re-runs only after a person answered, with the answer in the prompt; changed tasks on resume stop for a person | PASS |
| IV. Verified, not reported | No change to verification; dashboard is observation only | PASS |
| V. Deterministic where possible | Status computed by the same code as the run; superseded rule deterministic. Learnings are model-written by design (judgement) | PASS |
| VI. One task, one commit | Unchanged; runtime files are ignored so they never leak into task commits | PASS |
| VII. AI-free at the core | Dashboard, CLI, parsing, status and harvest need no model. Learnings and the future chat are optional model work on top | PASS |

No violations.

## Project Structure

### Documentation

```text
specs/003-plugin-redesign/
├── spec.md, plan.md, research.md, data-model.md, quickstart.md
├── contracts/dashboard-api.md, contracts/config-schema.md, contracts/action-channel.md
├── checklists/requirements.md
└── tasks.md            # /speckit-tasks
```

### Source code (existing layout)

```text
bin/sdd-tasks.py        # data-model §3: BLOCKED entries, HOLDING_STATUSES, dep:ok, superseded,
                        #   answers in plan, snapshot/changed, config_errors
bin/sdd-dashboard.py    # §4: use sdd-tasks, active feature, /api/doc, events dirs, run state,
                        #   live log, Host check, badges/rollup, marked+hljs+DOMPurify
bin/sdd-supervise.py    # §9 profiles, §10 per-window hard stop
bin/sdd-harvest.py      # NEW, §8 plugin feedback → logs/
workflows/sdd-run.js    # §5: remove worktree, allowLine, answer in brief, role models
hooks/rules.ts          # §6: scratch carve-out with path normalization
skills/sdd-run/SKILL.md     # §7 .gitignore check, §11 args/run.json/report/resume/learnings
skills/sdd-init/SKILL.md    # §7 .gitignore lines
skills/sdd-status/SKILL.md  # §4.5 stale-run rules
skills/sdd-unblock/SKILL.md # resolved now re-runs; moved status
templates/sdd-config.toml, templates/agent-tags.md
README.md               # profiles, local-model caveat, resolved semantics, harvest
tests/                  # unit tests per section; e2e per quickstart
```

**Structure Decision**: same single-repository layout; one new file (`bin/sdd-harvest.py`).

## Order of work (for /speckit-tasks)

1. `bin/sdd-tasks.py` (everything else reads it).
2. Guard (`hooks/rules.ts`) and `.gitignore` handling (skills) — independent of 1.
3. `workflows/sdd-run.js` and `skills/sdd-run/SKILL.md` (needs 1).
4. `bin/sdd-dashboard.py` backend, then page (needs 1).
5. `bin/sdd-supervise.py`, `bin/sdd-harvest.py` (needs 1's `config_errors`).
6. Docs, templates, e2e, quickstart validation.

## Complexity Tracking

Empty.
