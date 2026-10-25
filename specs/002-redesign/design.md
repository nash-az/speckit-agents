# Speckit-agents Redesign: proper spec kit flow

## Source of truth

This redesign is driven by the notes in `CLAUDE.md` ("Lessons from real runs") and `logs/2026-10-14-language-app.md` (12 real unattended runs on the language app). Every change below maps to a recorded lesson or backlog item.

## Proper spec kit flow

The unattended run sits on top of Spec Kit's canonical flow, never replacing it:

```
/speckit-specify   →   /speckit-plan   →   /speckit-tasks   →   /speckit-analyze
                                                   ↓
                                          /sdd-init  (once per project)
                                                   ↓
                                          /sdd-tag   (tag existing tasks)
                                                   ↓
                                          /sdd-run   (converge → plan → confirm → run → report)
                                                   ↓
                                          /sdd-unblock  (answer BLOCKED.md, tick manual tasks)
                                                   ↓
                                          /sdd-status   (see where things stand)
                                                   ↓
                                          /sdd-run again (resume / next phase)
```

`sdd-run` itself runs `/speckit-converge` first (the code-vs-spec gap check), then tags and plans only the new Convergence phase's runnable tasks. Older open tasks stay unticked; converge has already turned what they still need into new tasks.

## Redesign principles (from the constitution)

1. **Spec Kit stays the source of truth.** The plugin layers on top; it never forks specs/plan/tasks.
2. **Safe without a person watching.** Sandbox, permission rules, run guard, isolated `auto/` branch, fail-closed, work never deleted (stashed).
3. **Never guess on a person's behalf.** Manual/decision/dep/download work is parked with one actionable question.
4. **Verified, not reported.** Done = independent verifier approves + project checks pass.
5. **Deterministic where possible.** Parsing, planning, ticking, recording are plain code.
6. **One task, one commit.** One commit per approved task holding only its files.

## Changes

### 1. Held tasks are "waiting on you" everywhere (backlog items 1, 17)

A task tagged `[manual]`, `[decision]`, `[dep]` (not allowed), or `[download]` (not allowed), or with an open `wontfix`/`resolved` BLOCKED.md entry, is **held**. The run report and `/sdd-status` list every held task as "waiting on you" with its question, not just BLOCKED.md entries. `/sdd-unblock` covers held tagged tasks too (not only BLOCKED.md entries).

Rationale: `[decision]` T244/T251 and `[manual]` T239/T245/T246 were held by `plan` but never reached BLOCKED.md, so the operator wrongly told the user to answer them "in BLOCKED.md".

### 2. Single source of truth for held vs done (backlog items 17, 13)

BLOCKED.md status and the task checkbox must agree:
- `status: open` → task stays held, never re-run.
- `status: resolved` or `wontfix` → task stays held (dropped), never re-enter the plan.
- Only ticking the box (via `/sdd-unblock` tick) marks a task done.

Rationale: T203 (`status: resolved`, superseded) would have been re-run by `plan` because its box was still unticked. The planner now treats any non-open terminal status as holding the task.

### 3. Task dependencies from cross-references (backlog item 20)

Parse `after Tnnn`, `per Tnnn`, `blocked by Tnnn`, `needs Tnnn` (and variants) in task text into dependencies. A task whose dependency is open and held is itself held (not runnable). A Convergence task `per T129 (partial)` supersedes T129's remaining work; `plan` skips superseded originals.

Rationale: T265 (measure) needed T264's recordings (`[manual]`, held) but `plan` listed T265 as runnable; an agent spent a turn finding no data.

### 4. Converge rule counts only runnable tasks (backlog item 27)

After converge, if the newest Convergence phase has open tasks but none runnable (all held/wontfix), converge again so spec changes become tasks. Don't converge again if there are runnable tasks.

Rationale: held tasks `[dep]` T240/T243, `[manual]` T246 stayed open forever, so converge never ran again and committed spec changes never became tasks.

### 5. Mark originals superseded after converge (backlog item 26)

After `/speckit-converge`, original tasks whose remaining work is carried by a Convergence task (`per T129 (partial)`) are marked superseded. `plan` skips superseded tasks and shows the true open count. Without this, phases 1-14's 53 unticked tasks in the language app would be listed as runnable again.

### 6. Event feed for monitoring (backlog items 10, 11, 21, 30)

The run writes `.sdd/runs/<run id>/events.jsonl` with one JSON line per observable step: task started, implementer/evaluator result, verifier verdict, commit, blocked, skipped, held, stopped, refused command. A monitor, phone notification, or dashboard can follow this feed without reading agent transcripts. The supervisor writes both usage windows into `status.json` on every event and prints every 5% step; `/sdd-status` shows them.

### 7. Credit-aware pausing (backlog items 12, 12a)

The supervisor already pauses before the usage limit and resumes after the reset. The run itself should avoid starting a new agent when a usage window is near its threshold (no new agents past 95% of the five-hour window, since the run stops at the first finished agent anyway). The supervisor's hard stop is at least one point above the window's threshold so it waits for a safe point; only the real limit stops mid-agent.

Rationale: run #14's hard stop cut off an implementer mid-flight; a one-long-implementer can use 4% of a five-hour window in 3 minutes, so a 95% threshold often won't reach a safe point.

### 8. Per-task dependency approval (backlog items 13, 29)

A task tagged `[dep]` can be individually approved (e.g. `[dep:ok]` or an `approved_deps` list in config) so the workflow passes "dependencies: yes" to that task's implementer only, without turning `[allow] dependencies` on globally and leaving it on.

Rationale: removing `[dep]` let the plan run T240/T243, but the workflow still told the implementer "new dependencies: no" from `[allow]`, so it blocked itself.

### 9. Agent scratch dir (backlog item 23)

Each agent gets a git-ignored scratch dir `.sdd/scratch/<run id>/<task id>/` for throw-away scripts. The guard allows writes there. Agents must not write to `/tmp` or outside the project.

Rationale: T262's data task needed a throw-away script; `Write /tmp/...` was refused and helper scripts risked landing in the task's commit.

### 10. Verifier runs project checks before approving (backlog item 9)

The verifier runs the project's checks (lint, type check, tests) as part of its approval, not only task-specific tests. T213 was approved then failed mypy at commit.

### 11. Attribute unclaimed changes to the last task (backlog item 25)

When the bookkeeper finds unclaimed changes right after a task's commit, attribute them to that task if the verifier's diff covered them, instead of leaving them dirty for the next task.

Rationale: T247's `cli.py` was missed from its commit and left dirty; T248 started with it uncommitted.

### 12. Browser tool decision for [ui] tasks (T011)

Decision: **Playwright, outside the sandbox.** Chromium cannot start inside the macOS sandbox (language-app T243), and claude-in-chrome needs the person's real Chrome/sign-ins, so neither works unattended inside a run. `[ui]` means "verified by build and tests during the run and listed for a person to look at": the implementer writes Playwright tests, the verifier runs build+test, and a person runs `pnpm test:e2e` outside the sandbox (or the task is held person-like for GPU/browser-bound work).

Rationale: browser-bound `[eval]` work (T209/T210) can't finish inside one usage window on CPU; GPU-bound evals need an unsandboxed lane or a person.

### 13. E2e test automation (T009)

Script `tests/e2e/make_toy.sh` and `tests/e2e/check_toy.sh` for SC-001: toy project, headless `/sdd-run --yes`, assert commits/BLOCKED.md/`main`.

### 14. Worktree isolation for parallel groups (T010)

When `[run] isolation = "worktree"`, run each task of a group in its own worktree; the bookkeeper merges them onto the run branch in task order.

### 15. Manual tasks get "how to" steps (backlog item 18)

`[manual]` tasks get steps written by converge/sdd-tag (or the run report): where to do it, the commands, and what "done" looks like. The user had to ask how to do T239; answering meant reading the code.

### 16. Flag manual tasks invalidated by pending spec changes (backlog item 2)

When a decision or amendment is pending, flag `[manual]` validation tasks that touch the affected FRs/SCs, or order them after it. T246 (walk through quickstart V1–V14) included V13/V14 which test per-speaker checks the admin-attests decision removes — nothing marked T246 as "do after the amendment".

### 17. Decision tasks need options and trade-offs (backlog item 4)

`[decision]` task lines need the options and their trade-offs, not just the question. T244's line had the question ("improve or accept?") but not the facts (the other model reaches 0.94 but false-flags 25% of native words). Converge/sdd-tag should put, or link, the evidence next to the question.

### 18. Numbers in reports computed from artifacts (backlog item 3)

Reports and summaries should count from the artifacts, not from memory. "50 drafted reductions" was wrong — the file had 51 items.

### 19. Keep user-visible requirements in the owning task (backlog item 22)

Keep each user-visible requirement in the task that owns its page, or name the page file in the backend task's files. T260's wording put a UI requirement into a backend task while the Record page was a separate `[ui]` task.

### 20. "moved"/"product" state (backlog item 24)

Spec Kit tasks have no state for "moved out of development into product use". `wontfix` worked but reads as "dropped". The redesign could add a `moved`/`product` state that the report and `/sdd-status` show apart.

### 21. Verifier walks user flow end to end (backlog item 28)

For `[ui]` and admin tasks, have the verifier (or a reviewer at phase end) walk the user flow end to end ("can the admin actually do X from here?"), not only check the task's wording. Each feature passed its verifier because the task text asked only for what was built.

### 22. Shell gotchas (backlog item 5)

Agent rules include shell gotchas (zsh `$B:c…` modifier read as zsh modifier; use `"${B}:path"`).

### What stays the same

- Constitution principles I–VI.
- `bin/sdd-tasks.py` subcommands: `parse`, `plan`, `tick`, `block`, `config`, `checkpoint`.
- `workflows/sdd-run.js` shape: implementer → verifier (retry max N) → bookkeeper (checks → commit per task).
- Agent names: `sdd-implementer`, `sdd-verifier`, `sdd-evaluator`, `sdd-bookkeeper`.
- Skill names: `sdd-init`, `sdd-tag`, `sdd-run`, `sdd-status`, `sdd-unblock`.
- Run marker `.sdd/run.json`, guard active only while it exists, main-loop exemption for `.sdd/`.
- One commit per task, never push, never touch `main`.
