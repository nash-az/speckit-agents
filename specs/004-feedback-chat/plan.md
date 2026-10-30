# Implementation Plan: Feedback and chat

**Branch**: `main` (no feature branch; this project works on `main`, runs use `auto/` branches) | **Date**: 2026-10-30 | **Spec**: [spec.md](./spec.md)

**Input**: `specs/004-feedback-chat/spec.md`

**Who implements this**: a cheaper model than the one that wrote this plan. `data-model.md` gives the exact
shapes, state moves and function names; `contracts/` gives the routes and the config. Where a decision is
listed in `research.md`, a task must not reopen it.

## Summary

A person writes a note (or a chat message). A helper starts a read-only `claude -p` session that returns a
structured result: questions, a proposal, nothing to do, or out of scope. The dashboard shows the result as
a card. Pressing Apply runs a deterministic helper that edits `spec.md` and `tasks.md`, records drops in
`BLOCKED.md`, may create or remove a feature folder, appends a line to `feedback-log.md`, and makes one
commit. No model touches a project file.

Approach: two new stdlib helpers in the 003 pattern, and a new dashboard view.
- `bin/sdd-feedback.py` — notes (create, answer, reply, retry, discard), the queue and `max_parallel`,
  starting and reaping drafting sessions, validating results. Spawns the session; the dashboard source does not.
- `bin/sdd-apply.py` — apply a proposal: parse, check, edit, commit. No model call, no network.
- Dashboard: `#feedback` view, note box, note cards, question cards, proposal card, a Reply button, a
  per-task "Give feedback" link, and the chat panel.

## Technical Context

**Language/Version**: Python 3.11+ stdlib (`bin/*.py`); plain JavaScript in the dashboard page script.
**Primary Dependencies**: none new. The `claude` CLI (2.1+) for drafting sessions: `--tools`,
`--permission-mode dontAsk`, `--json-schema`, `--output-format stream-json`, all present in 2.1.314.
**Storage**: notes in the supervisor's state directory (`<state>/feedback/<id>.json`, `<id>.log`), outside the
project; one line per applied note in `specs/<feature>/feedback-log.md` (tracked).
**Testing**: `python3 -m unittest discover -s tests` (helpers on temp git repos, routes through the handler,
the page's script as in 003), a fake `claude` in `tests/fake/` for sessions, the Playwright harness
(`tests/ui/harness.py`) for the view, `node --test`, `claude plugin test .`.
**Target Platform**: macOS/Linux developer machine, single user, localhost.
**Project Type**: Claude Code plugin with a local web dashboard (as 003).
**Performance Goals**: submitting a note answers in under 1 s (the session starts in the background); the
view refreshes on the existing 3 s poll. A drafting session has a 10 minute limit (`[feedback]`).
**Constraints**: backend stdlib only; the dashboard source has no process spawning and no project writes
(`ReadOnlyTest` stays green); nothing in the project changes before Apply; Apply and edits refuse while
`.sdd/run.json` exists; the core (parsing, planning, status, the dashboard's other views) works with no
`claude` binary and no feedback code path loaded (Principle VII).
**Scale/Scope**: one project, tens of notes; at most `max_parallel` (2) sessions at once.

## Constitution Check

| Principle | Check | Status |
|---|---|---|
| I. Spec Kit is the source of truth | Edits go to Spec Kit's own `spec.md` and `tasks.md` in their formats; new specs come from `.specify/templates/spec-template.md`; no Spec Kit command is changed. `feedback-log.md` is an extra file in the feature folder, like `decisions.md`. | PASS |
| II. Safe without a person watching | Not unattended: Apply is a person's click. It refuses during a run, so no spec or constitution edit happens in a run. The session cannot write at all (read tools only) and the guard is a second layer. The constitution is not a target (FR-017). | PASS |
| III. Never guess for a person | The session asks (FR-008) instead of guessing, at most 2 rounds, then states assumptions on the card for the person to accept or edit. Nothing is applied unreviewed. | PASS |
| IV. Verified, not reported | Apply checks every `old` text against the file and re-parses `tasks.md` after editing; a mismatch refuses the whole apply. | PASS |
| V. Deterministic where possible | The model drafts (judgement); parsing, validation, id assignment, edits and the commit are plain code. | PASS |
| VI. One task, one commit | One note applies as one commit holding only its files; it does not touch task commits. | PASS |
| VII. AI-free at the core | The chat and the feedback view are an optional client. With no `claude` binary the feedback routes answer `503 {error}` and every other view works; tested. Nothing in `sdd-tasks.py`, the supervisor or the existing routes imports the feedback helpers except lazily. | PASS |

Post-design re-check: PASS (no change from the gates above). The one point to watch is the dashboard
importing `sdd-feedback.py`: it is imported lazily inside the feedback routes, so a failure to load it cannot
break the dashboard.

## Project Structure

### Documentation (this feature)

```text
specs/004-feedback-chat/
├── plan.md                  # This file
├── research.md              # Decisions D1-D14
├── data-model.md            # Note, result, proposal, state moves, helper functions, apply order
├── quickstart.md            # Validation scenarios
├── contracts/
│   ├── feedback-api.md      # Routes
│   └── config-schema.md     # [feedback] section
└── tasks.md                 # Next: /speckit-tasks
```

### Source Code (repository root)

```text
bin/
├── sdd-feedback.py          # NEW: notes, queue, sessions, result validation
├── sdd-apply.py             # NEW: apply a proposal (edits + one commit)
├── sdd-dashboard.py         # EDIT: routes, #feedback view, chat panel, task "Give feedback"
└── sdd-tasks.py             # EDIT: config_errors checks [feedback]
tests/
├── fake/claude              # EDIT: a drafting scenario (structured result in the final event)
├── test_sdd_feedback.py     # NEW
├── test_sdd_apply.py        # NEW
├── test_sdd_dashboard.py    # EDIT: routes, page script
└── ui/                      # NEW checks: submit, questions, proposal, apply
```

**Structure Decision**: same single-folder layout as 003. Two helpers because drafting (needs a process,
time, a model) and applying (pure, deterministic, tested to death) have nothing in common and Principle V
wants the second to stay model-free.

## Phases

1. **Foundations** (no UI): the note store and state machine; the result schema and validator; the
   `claude` command builder and its test (SC-006); the fake `claude`.
2. **Drafting**: the queue, `max_parallel`, the worker, time limit, interrupted-on-start, log capture.
3. **Apply**: parse and edit `tasks.md`/`spec.md`/`BLOCKED.md`, new task ids, new and removed specs, the log
   line, the commit and rollback.
4. **Routes**: the contract in `contracts/feedback-api.md` with the FR-030 checks.
5. **View**: `#feedback` with the note box, note list, question card, proposal card, drafting log.
6. **Chat and context**: Reply, per-task "Give feedback", the chat panel (story 4); then story 5 (new and
   removed specs) last.

Each phase ends with its tests passing; phases 1-4 are usable from the shell before any UI exists.

## Open points (to settle by experiment in the first tasks, not by guessing)

- A real drafting session must be run once to confirm that `--tools Read,Grep,Glob --permission-mode dontAsk
  --json-schema` returns the structured result in the final `result` event of stream-json and that no write is
  possible (research D1). The fake `claude` copies the shape assumed here; if the real shape differs, only
  `extract_result()` changes.
- How much of `spec.md` plus `tasks.md` plus the code fits a session: tasks.md of 003 is large. The prompt
  passes paths and the person's note, not file contents; the session reads what it needs (research D6).

## Complexity Tracking

None. No Constitution Check violation.
