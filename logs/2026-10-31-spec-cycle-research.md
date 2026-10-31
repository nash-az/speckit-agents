# 2026-10-31: running Spec Kit's specify / clarify / plan / tasks / analyze from the dashboard

Research for the user's ask: "a way to trigger speckit specify/clarify and plan from the dashboard, preferably
in a recommended speckit flow and cycle". Nothing is built; this is the input for a spec.

## The problem today

The dashboard and runs only see the open tasks of one feature's `tasks.md`. Everything before that (writing the
spec, answering clarify questions, the plan, the tasks) happens in a chat session, so it is invisible and needs
the person at a terminal. 005 Jobs automates the back half (tasks → runs → merge); this is the front half.

## Spec Kit's recommended cycle

- Spec Kit's own bundled workflow `speckit` ("Full SDD Cycle", `.specify/workflows/speckit/workflow.yml`):
  `specify` → gate `review-spec` → `plan` → gate `review-plan` → `tasks` → `implement`.
- The commands' documented order adds the optional steps: `constitution` (once per project) → `specify` →
  `clarify` → `plan` → `checklist` (optional, "unit tests for requirements") → `tasks` → `analyze` (read-only
  cross-check, run before implementing) → `implement`. Community write-ups agree on this order; they differ
  only on whether clarify and analyze are required.
- For this plugin, `implement` is replaced by a Job (005): the agents run the tasks.

## What was checked

1. **Spec Kit's workflow engine** (`specify workflow run|resume|status`, v1.0.9.dev0). Each `command` step runs
   one `claude -p "/speckit-<cmd> …" --output-format json` (`integrations/base.py`, extra flags via
   `SPECKIT_INTEGRATION_CLAUDE_EXTRA_ARGS`). A `gate` step pauses when stdin is not a TTY; it is answered with
   `specify workflow resume <run_id> --input <verdict_input>=approve`. State is in
   `.specify/workflows/runs/<run_id>/{state.json,inputs.json,log.jsonl}`; `--json` gives one JSON object.
   **Gap:** a gate only chooses between fixed options between steps. Nothing can answer a question *inside* a
   step, so `/speckit-clarify` (which asks one question at a time and waits) cannot be answered through the
   engine without rewriting the command. `verdict_input` also still has open bugs (spec-kit PR #3912).
2. **Headless multi-turn Claude Code** (2.1.314): `claude -p … --output-format json` returns `session_id`;
   `claude -p "<answer>" --resume <session_id>` continues the same conversation. `--json-schema`,
   `--disallowedTools`, `--append-system-prompt` exist.
3. **Proof (throwaway clone of this repo, 005 spec):**
   - Turn 1: `claude -p "/speckit-clarify" --output-format json --permission-mode acceptEdits
     --disallowedTools AskUserQuestion` ended its turn at exactly one question, in Spec Kit's format
     (`**Question:**`, why it matters, `**Recommended:** Option A`, an options table, "reply with the letter")
     and changed no file. Cost $0.46.
   - Turn 2: `claude -p "recommended" --resume <id> …` wrote the answer into `spec.md` (a new
     `### Session 2026-10-31` entry plus the FRs it affects; 22+/9- lines) and asked question 2. Cost $0.72.
   - So the real, unchanged `/speckit-clarify` runs from a dashboard: one question per card, one `--resume`
     per answer. The same works for any command that stops to ask (specify's `[NEEDS CLARIFICATION]`, plan).
   - The clone and its edits were deleted; the answer was the test's, not the user's.
4. **Existing tools:** no Spec Kit add-on runs the slash commands from a browser. spec-kit-axi opens a browser
   review page for one feature; speckit-web views and runs `specify init`; SpecKit Tracker (VS Code) and
   spectatui (terminal) show status. None handle clarify's questions.
5. **This repo:** 004 already plans the same pieces for feedback: a helper-started session
   (`bin/sdd-feedback.py`, FR-004), questions shown as decision cards (FR-008), a deterministic helper that
   writes and commits (FR-007, FR-014), new specs through a proposal (US5, FR-012). Constitution I (Spec Kit
   stays the source of truth), III (never guess for a person) and VII (AI-free core) all apply.

## Options

| | How | For | Against |
|---|---|---|---|
| A. Spec Kit's workflow engine | Ship a custom workflow (`specify workflow add`), dashboard buttons call `run`/`resume`, status read from `.specify/workflows/runs/` | Spec Kit's own machinery and state files; gates built in | Clarify can't be answered inside a step; engine API still moving (verdict bugs); one blocking process per cycle |
| B. 004-style proposals | A read-only session returns structured JSON; a helper writes the files | Strongest safety (session can't write) | Re-implements what `/speckit-specify` and `/speckit-plan` write: a whole plan as JSON edits; drifts from Spec Kit (Principle I) |
| **C. Step sessions (recommended)** | A helper runs the real `/speckit-<cmd>` in `claude -p`, one `--resume` per answer; writes confined to the feature folder; the person approves the diff, the helper commits | Spec Kit commands unchanged; clarify works (proven); same helper/state/card patterns as 004 and 005; AI-free status | We own the small cycle state machine instead of Spec Kit's engine |

C keeps the door open to A: the cycle's state machine is small and deterministic, and if Spec Kit's engine later
supports answering inside a step, the helper can hand the gates to it.

## Recommended design (option C)

**One object per feature: its cycle.** Stages in Spec Kit's order, each with a state
(`not started` / `working` / `needs you` / `review` / `approved` / `out of date`):

| Stage | Command | You do | Gate |
|---|---|---|---|
| Specify | `/speckit-specify <your description>` | type what you want; answer up to 3 `[NEEDS CLARIFICATION]` | review spec |
| Clarify | `/speckit-clarify` | answer up to 5 questions, one card each (recommended option preselected) | — (part of spec review) |
| Plan | `/speckit-plan` | answer anything it asks | review plan (plan, research, data model, contracts, quickstart) |
| Checklist (optional) | `/speckit-checklist <domain>` | pick the domain | — |
| Tasks | `/speckit-tasks` (tags from our template), then `sdd-tasks.py plan` as a deterministic check | — | review tasks |
| Analyze | `/speckit-analyze` (read-only) | fix (re-runs the stage with the findings as a note) or accept each CRITICAL/HIGH finding | none left open |
| Build | Start job (005) | settle what is parked | merge |

**The loop.** In every stage: **Approve** (helper commits only that stage's files, one commit per stage, e.g.
`spec(006): plan`), **Request changes** (a note; the helper resumes the same session with it), or **Discard**
(the helper restores the files). If an earlier artifact changes after a later one was approved (spec edited after
the plan), the later stages show **out of date**, found by plain code from the commit each stage was approved
at, with Re-run or Keep. This is the cycle: change upstream, refresh downstream.

**Mechanics.**
- `bin/sdd-spec.py` (helper, like `sdd-launch.py`): starts/resumes the step session detached, keeps the
  session id, each turn's reply, the stage states and the cost in the state directory, and parses a
  `**Question:**` reply into a card (options table → buttons, free-text box; falls back to showing the reply
  as Markdown). The dashboard server only calls it (003 FR-020/FR-033 pattern).
- Session flags: `--output-format json --resume <id> --disallowedTools AskUserQuestion`, the project's own
  settings and sandbox, a model and time limit from a `[spec]` section in `.sdd/config.toml` (Sonnet is
  likely enough for clarify; about $0.5 a question on Opus in the probe).
- Safety: a guard mode for step sessions (a marker like `.sdd/spec-step.json`) that allows writes only to
  `specs/<feature>/**` and `.specify/feature.json`, and refuses code edits, commits and pushes; the helper
  commits after Approve. Steps are refused while a run uses the same checkout (rule of 2026-10-30), or later
  run in a worktree under `/tmp`.
- Principle VII: stage states come from files and the state directory by plain code; the dashboard and CLI
  work with no session at all; the sessions are an optional client.
- Dashboard: a **Features** list (every `specs/*` folder with its stage), **New feature** (a description box
  → Specify), and per feature a stage stepper with the current card ("Needs you" also lists open questions).

## Relation to 004 and 005, and order

- 005 Jobs is the back half (Build). This is the front half; together they are the full cycle.
- 004's US5 (new specs through a proposal) is better done as the Specify stage; 004 keeps feedback on approved
  specs and tasks and shares the helper, the question cards and the config section.
- Suggested order: clarify and plan 005 (in chat, as now) → build 005 → specify this as **006** (could be the
  first feature written through its own Specify stage once the minimum exists) → 004 on top.

## Open questions for the user

1. One commit per stage on the current branch (like the FR-035 editor), or a `spec/<feature>` branch merged
   once the tasks are approved?
2. Should Clarify run by itself after Specify, or only when you press it?
3. Should Analyze findings block Start job, or only warn?
4. Model for step sessions: the run's model (Opus) or a cheaper default?

Sources: [Spec Kit workflows reference](https://github.github.com/spec-kit/reference/workflows.html),
[spec-kit discussion #3717](https://github.com/github/spec-kit/discussions/3717),
[spec-kit PR #3912](https://github.com/github/spec-kit/pull/3912),
[Claude Code headless](https://docs.claude.com/en/docs/claude-code/headless),
[Plannotator: GitHub Spec Kit](https://docs.plannotator.ai/frameworks/github-spec-kit.md),
[spec-kit-axi](https://github.com/d0whc3r/spec-kit-axi/wiki/Getting-Started),
[speckit-web](https://github.com/wmc-cache/speckit-web),
[spectatui](https://github.com/tinesoft/spectatui).
