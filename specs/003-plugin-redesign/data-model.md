# Data Model: Plugin redesign

Exact enough to implement from. Every change names the file and function. Line numbers refer to the
code as of commit `a8de64b` and may drift — find the function by name, not by line.

**Reviewed 2026-10-25 (whole-project review pass).** This version replaces the first draft. Errors
fixed in the first draft: the superseded-annotation direction was backwards (§3.4), the scratch fix
ignored git (§7), the model-id check rejected Bedrock/Vertex ids (§3.8), and the worktree permission
change targeted a feature that does not work (§5.1, now removed).

---

## 1. Status taxonomy (shared by backend and frontend)

The **key** strings are compared byte for byte between Python and JavaScript. Use exactly these.

| Key | Kind | Color | Glyph | Label |
|---|---|---|---|---|
| `queued` | transient | `#8b93a1` | circle-outline | "Queued" |
| `running` | transient | `#3b82f6` | spinner (animated) | "Running: {step}" (`Implementing` / `Verifying` / `Committing`) |
| `retrying` | transient | `#eab308` | rotate-arrow | "Retry {n}/{max}" |
| `paused` | transient | `#64748b` | pause-bars | "Paused" |
| `done` | transient | `#22c55e` | check | "Done" |
| `held` | parked | `#f59e0b` | clock | "Held: {tag}" |
| `blocked` | parked | `#ef4444` | alert-triangle | "Blocked" |
| `wontfix` | parked | `#6b7280` | slash-circle | "Won't fix" |
| `moved` | parked | `#a78bfa` | arrow-up-right | "Moved" |
| `superseded` | parked | `#94a3b8` | arrow-right | "Superseded by {by}" |

No two keys share both glyph and color (SC-002). Colors are new CSS variables in `build_html()`'s
`:root` block: `--st-queued`, `--st-running`, … one per key, with the values above.

Rendered markup, always all three parts (FR-003):
`<span class="status status-{key}" title="{label}">{glyph svg}<span class="status-label">{label}</span></span>`
where the glyph is an inline SVG (24×24 viewBox, `stroke="currentColor"`, `fill="none"`,
`stroke-width="2"`, rendered at 14×14) and the span's `color` is the key's variable. Glyphs live in
one JS object `GLYPHS` keyed by glyph name; statuses map to glyphs in one JS object `STATUS`
(`{queued: {glyph: 'circle-outline', label: 'Queued'}, …}`). No icon font, no CDN.

**Rollup (FR-004)** — priority list, most urgent first:
`blocked, retrying, running, held, paused, queued, moved, wontfix, superseded, done`.
A phase's status is the first key in this list that any of its tasks has. A phase with no tasks
shows nothing.

**BLOCKED.md entry badges** (the Blocked/"waiting on you" view): `open` → `blocked`;
`resolved` → `queued` with label "Answered — runs next"; `wontfix` → `wontfix`; `moved` → `moved`.

---

## 2. Task as returned by the dashboard (`GET /api/project/tasks`)

```jsonc
{
  "id": "T057", "text": "...", "tags": ["manual"], "parallel": false, "story": "US1",
  "files": ["path/a.py"], "deps": ["T050"], "superseded_by": [], "done": false, "line": 42,
  "status": "held",          // parked key from §1, or "done" if ticked, or null if open and runnable
  "status_tag": "manual",    // what drives the label: the hold tag, the BLOCKED status, or the superseding id
  "status_reason": "needs a person to do it (recording, hardware, an account)",
  "question": null,          // BLOCKED.md question when status is "blocked", else null
  "answer": null             // BLOCKED.md answer when the entry is resolved, else null
}
```
Mapping from `bin/sdd-tasks.py` hold tags to `status`: `manual|decision|dep|download → held`
(`status_tag` = that tag); `blocked → blocked`; `wontfix → wontfix`; `moved → moved`;
`superseded → superseded` (`status_tag` = superseding id or `Phase N`); `held` (propagated from a
dependency) `→ held` (`status_tag` = `"dependency"`). A ticked task has `status: "done"` and no hold.
The frontend replaces `null` with the transient state from the event feed (§4.6), or `queued`.

---

## 3. `bin/sdd-tasks.py`

### 3.1 BLOCKED.md entries
New function, the only BLOCKED.md parser:
```python
def parse_blocked_entries(path: Path | None) -> dict[str, dict]:
    """{task id: {"status", "tag", "reason", "question", "answer"}} — the LAST entry per id wins."""
```
Rules: an entry starts at a line matching `BLOCKED_HEAD_RE` (`## T…`). Inside an entry, a line
`- <key>: <value>` with key in {status, tag, reason, question, answer} sets that field (status
lowercased; default status `"open"`, other fields default `""`). The `answer` field also takes every
following line that is not `## …` and not `- <word>:` — joined with `\n` and stripped — because
`/sdd-unblock` writes answers "under the `- answer:` line". An entry ends at the next `## ` line.
Re-implement `parse_blocked(path)` as `{id: e["status"] for id, e in parse_blocked_entries(path).items()}`
so the `parse` command's output is unchanged.

### 3.2 Which BLOCKED statuses hold a task (FR-023)
Replace `TERMINAL_STATUSES = {"open", "resolved", "wontfix"}` with
`HOLDING_STATUSES = {"open", "wontfix", "moved"}` and update every reference. In `hold_reason()`,
the status → (tag, reason) mapping becomes:
`open → ("blocked", "open entry in BLOCKED.md")`,
`wontfix → ("wontfix", "dropped in BLOCKED.md (wontfix)")`,
`moved → ("moved", "moved to product/admin use")`.
`resolved` no longer holds: the task is runnable. Update the comment above the constant and the
docstring of `hold_reason()` to say so.

`block()`'s header text (written into every new BLOCKED.md) becomes exactly:
```
# Blocked tasks

Tasks an unattended run could not finish. Answer the question under `- answer:` and set
`status: resolved`: the next `/sdd-run` runs the task again with your answer.
Set `status: wontfix` to drop the task, or `status: moved` if it became work outside this project.
```
`block()`'s duplicate check stays as is (`existing.get(task_id) == "open"` → don't append); a
resolved task that fails again gets a new open entry appended, which wins (§3.1).

### 3.3 Per-task dependency/download approval (FR-014)
In `hold_reason()`'s tag loop, the allow check becomes:
```python
allow_key = ALLOW_KEYS.get(tag)
if allow_key and (allow.get(allow_key) or f"{tag}:ok" in task["tags"]):
    continue
```
`KNOWN_TAGS = set(HOLD_TAGS) | {"eval", "ui", "dep:ok", "download:ok"}` (so `[DEP:OK]` is lowercased).
Add both tags to `templates/agent-tags.md`: "`[dep:ok]` / `[download:ok]` — added next to `[dep]` /
`[download]` by a person to approve that one task even when the run does not allow dependencies /
downloads."

### 3.4 Superseded tasks (FR-008)
Current code (`direct_holds()`) treats `superseded-by Tnnn` on task X as superseding **Tnnn**, and
only while X is open. Nothing in real projects uses it (checked: no occurrence in the language app).
Replace with:

1. **Explicit**: `superseded-by Tnnn` / `replaced-by Tnnn` on task X means **X** is superseded by
   Tnnn. In `direct_holds()`: for each open task with a non-empty `superseded_by` list,
   `holds[task["id"]] = ("superseded", ", ".join(task["superseded_by"]))`. Do not skip based on the
   successor's `done` state.
2. **Computed after converge**: find the newest phase whose title matches `^convergence` (case
   insensitive). Every open task in a phase that comes *before* it in the file and whose title does
   **not** match `^convergence` gets `holds[id] = ("superseded", f"Phase {newest_id}")`, unless it
   already has a hold.
3. In `propagate_holds()`, a dependency held as `superseded` does **not** hold its dependents:
   change the condition to `if dep in holds and holds[dep][0] != "superseded":`.

Order: compute the tag/BLOCKED holds first, then apply rules 1 and 2, which **overwrite** them —
`superseded` wins. A superseded task's work has moved to its successor, so its old question no
longer matters; and if a tag hold won instead, rule 3 would not apply and the successor (e.g. a
Convergence task that says "per T020") would be held by the very task it replaces. Test this case:
T020 tagged `[manual]` in an older phase, T090 "per T020" in the newest Convergence phase → T020
`superseded`, T090 runnable.

### 3.5 Answers reach the implementer (FR-023)
`plan()` takes the entries dict from §3.1 instead of the status dict (pass
`{id: e["status"]}` where `hold_reason` needs a status). For each runnable task whose entry has
status `resolved` and a non-empty answer, set `task["answer"] = entry["answer"]` and
`task["question"] = entry["question"]` on the task dict that goes into `phases[].tasks`.

### 3.6 Resume safety (FR-018)
```python
SNAPSHOT_FIELDS = ("text", "tags", "files", "deps", "story")

def snapshot(parsed: dict) -> dict[str, dict]:
    return {t["id"]: {f: t[f] for f in SNAPSHOT_FIELDS} for p in parsed["phases"] for t in p["tasks"]}

def changed(parsed: dict, old: dict[str, dict]) -> list[str]:
    """Ids present in both whose snapshot fields differ. Checkbox (`done`) and `line` are ignored."""
    new = snapshot(parsed)
    return sorted(i for i in old if i in new and old[i] != new[i])
```
CLI: `sdd-tasks.py snapshot <tasks.md>` prints the snapshot;
`sdd-tasks.py changed <tasks.md> --run-json .sdd/run.json` reads `task_snapshot` from the run.json
and prints `{"changed": [...]}` (empty list if run.json has no `task_snapshot`).

### 3.7 Run profiles and role models — config shape (FR-011, FR-025)
```toml
[agents]                 # optional; a missing role uses the run's model
implementer = "haiku"
verifier = "opus"

[profiles.cheap]         # optional; used by the supervisor's --profile
model = "haiku"
[profiles.local]
model = "claude-sonnet-5-5"            # the name the gateway expects
env = { ANTHROPIC_BASE_URL = "http://localhost:4000" }
```

### 3.8 Config validation (FR-012, FR-016, FR-025)
New function `config_errors(config: dict) -> list[str]`, and the `config` command's JSON output
gains `"errors": [...]`. Errors (one string each, naming the entry):
- `[agents]` key not in `{"implementer", "verifier", "evaluator", "bookkeeper"}` → `"agents.<key>: unknown role"`.
- `[agents]` value not a non-empty string → `"agents.<key>: model must be a non-empty string"`.
- `[profiles.<name>]` without a non-empty string `model` → `"profiles.<name>.model: missing"`.
- `[profiles.<name>].env` not a table of string → string → `"profiles.<name>.env: must map names to strings"`.
- an env name containing `KEY`, `TOKEN`, `SECRET` or `PASSWORD` (case-insensitive) →
  `"profiles.<name>.env.<VAR>: secrets must come from your environment, not the config"`.
- `run.isolation == "worktree"` → `"run.isolation: worktree isolation was removed; delete this line"`.
Model strings are not checked further: aliases, Claude ids, Bedrock ARNs and Vertex names all pass.
Remove `"isolation"` from `DEFAULT_CONFIG`.

---

### 3.9 Run events from the workflow journal (FR-006, FR-021, FR-024)
Workflow scripts have no file system and no clock (research.md D6), so no event file is written.
`bin/sdd-tasks.py` derives the events from the engine's transcript directories instead.

**Where the directories are.** `.sdd/runs/<run id>/workflows.json` is `{"workflow_dirs": ["<abs>", …]}`,
oldest first. The main loop writes it (`skills/sdd-run/SKILL.md` step 6.4, at the moment it adds
`workflow_dir` to `.sdd/run.json`); a resumed run appends its new directory. Git-ignored by
`.sdd/runs/*/` (§7).

**`valid_workflow_dir(path) -> bool`**: true only when the path is absolute, its name matches
`^wf_[A-Za-z0-9_-]+$`, it is a directory, and it contains `journal.jsonl`. Every reader skips
directories that fail this (a project file must not make the dashboard read arbitrary paths).

**`journal_events(workflow_dirs: list[Path]) -> list[dict]`**. For each valid directory in order,
read `journal.jsonl` line by line, skipping lines that don't parse (same as `checkpoint()`).
Remember `agentId → label` from `started` lines. Match labels with the existing `LABEL_RE`
(`kind` impl / eval / verify / commit / repair, `ids`, attempt `n`); ignore other labels.
- `ts`: for a `started` line, the `timestamp` of the first line of `<dir>/agent-<agentId>.jsonl`; for
  `result` / `failed`, the `timestamp` of its last line (read only the last 64 KB). Missing → `null`.
- Every event: `{"ts", "kind", "id", "label", "agent_id", "workflow": <index into workflow_dirs>}`
  plus the fields below. `id` is the task id; commit and repair events use `"ids": [...]` instead.

| journal line | event(s) |
|---|---|
| `started` impl / eval | `task.started` with `attempt` (n, default 1) |
| `result` impl / eval, `status: done` | `task.implemented` with `attempt` |
| `result` impl / eval, `status: blocked` | `task.blocked` with `reason`, `tag` |
| `result` verify | `task.verified` with `verdict`, `problems`; then `task.approved` if `verdict == "approve"` |
| `started` repair | `group.repair` with `ids` |
| `result` commit | `group.finished` with `ids`, `checks_passed`, `commits`; then `task.blocked` for each id in `blocked_recorded` |
| `failed` (any task label) | `task.failed` with `error` (the line's `error` field, or `""`) |

`verify:` labels carry no attempt suffix; journal order keeps the attempts apart.
Kinds no longer produced: `task.resumed`, `task.interrupted`, `phase.started`, `phase.skipped`,
`run.finished`. Consumers use the run state (§4.5) and the report instead.

**`run_events(project: Path, run_id: str) -> dict`**: reads `workflows.json` and returns
`{"run_id", "workflow_dirs", "missing": [dirs that failed valid_workflow_dir], "events": journal_events(valid)}`.
No `workflows.json` → `workflow_dirs: []`, `events: []`.

**CLI:** `sdd-tasks.py events <project dir> <run id>` prints `run_events()` as JSON.

## 4. `bin/sdd-dashboard.py`

### 4.1 Use `sdd-tasks.py` for all task state (FR-002, FR-007)
Load it once at module level (the hyphen prevents a plain import; do not rename the file):
```python
import importlib.util
_spec = importlib.util.spec_from_file_location("sdd_tasks", Path(__file__).with_name("sdd-tasks.py"))
sdd_tasks = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sdd_tasks)
```
Delete the dashboard's own `parse_tasks(text)` and `parse_blocked(text)`. `/api/project/tasks`
returns `{"text": <raw>, "phases": [...]}` where phases come from `sdd_tasks.parse_tasks()` and each
task is extended per §2, using `sdd_tasks.parse_blocked_entries`, `load_config`, `direct_holds`,
`propagate_holds`. `/api/project/blocked` returns `{"text": <raw>, "entries": [{"id", **entry}]}`
from `parse_blocked_entries`.

### 4.2 Active feature (FR-027)
```python
def active_feature(project: Path) -> Path | None:
```
Order: `run.feature` from `.sdd/config.toml` (via `sdd_tasks.load_config`) if non-empty → `feature_directory`
from `.specify/feature.json` → the only subdirectory of `specs/` if there is exactly one → `None`.
All per-feature files are read from this directory. Delete `find_file()`. When it returns `None`,
every per-feature endpoint returns `{"error": "choose a feature", "features": [<specs/* names>]}`
with status 409, and the UI shows a picker (an in-memory choice via the existing
`POST /api/project/select` route extended with an optional `"feature"` field — memory only, never
written to disk).

### 4.3 Documents (FR-001)
`GET /api/doc?name=<name>` with `name` one of a fixed set — anything else → 404:
`spec, plan, research, data-model, quickstart, tasks, blocked, decisions` (files in the active
feature: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, `tasks.md`,
`BLOCKED.md`, `decisions.md`), `constitution` (`.specify/memory/constitution.md`), `learnings`
(`.sdd/learnings.md`), `config` (`.sdd/config.toml`, served as text, not markdown). Run reports keep
`GET /api/runs/<id>/report`; `<id>` must match `^[0-9]{8}-[0-9]{4}[a-z0-9-]*$` or → 404 (no path
traversal). A missing file → `{"missing": true}` with status 200; the UI shows "Not written yet".
Keep the old `/api/project/spec|plan|decisions|config` routes as aliases.

Client side: load these pinned browser builds from jsDelivr:
`https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js`,
`https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/highlight.min.js` (its default build
includes python, javascript, typescript, bash, json and ini, which covers TOML) and
`https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/styles/github-dark.min.css`.
Do not use the plain `highlight.js` npm package paths — they are Node builds, not browser builds. Render with
`el.innerHTML = DOMPurify.sanitize(marked.parse(text))` — also load
`https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js` (documents are local, but agents
write them; never inject unsanitized HTML). Then `el.querySelectorAll('pre code').forEach(hljs.highlightElement)`.
Table of contents: when the document has 4 or more `h2`/`h3` headings, give each an `id` (slug of its
text) and render a link list above the document. If a CDN script fails to load (offline), fall back
to the current `<pre>` view and show a one-line notice — the dashboard must never be blank.
tasks.md and BLOCKED.md are NOT rendered through marked in the Tasks/Blocked views; those views stay
structured (cards) and use §1 badges. The `tasks`/`blocked` documents are still viewable rendered
under a "Raw document" toggle.

### 4.4 Events (FR-006)
`list_runs()`: iterate `.sdd/runs/` **directories** (`entry.is_dir()`); a run is a directory
containing `workflows.json`; `run_id = entry.name`. `get_events()` returns `run_events(project, run_id)`
from `sdd-tasks.py` (§3.9, loaded as in §4.1; validate `run_id` as in §4.3). Sort runs by the directory
name descending (run ids are timestamps), not by mtime. A run whose `missing` is non-empty and whose
`events` is empty shows "This run's transcript is gone (Claude Code keeps transcripts for
`cleanupPeriodDays`, default 30); its report is `.sdd/runs/<id>.md`." The UI already polls every 3 s
(`setInterval(refreshAll, 3000)`); keep polling — no server-sent events.

### 4.5 Stale run (FR-017)
`GET /api/run/current` returns `{"state": "none" | "running" | "paused" | "stale", "run_id", "workflow_dir"}`:
- no `.sdd/run.json` → `none`;
- `.sdd/runs/<id>.md` exists for its `run_id` → `paused` (an interrupted run waiting to resume — its
  report is written and committed, the skill keeps `run.json`);
- supervisor `status.json` exists and its `state` is not `"stopped"` → `paused`;
- `run.json`'s `workflow_dir` passes `valid_workflow_dir()` (§3.9) and its `journal.jsonl` or any
  `agent-*.jsonl` in it was modified less than 30 minutes ago → `running`;
- otherwise → `stale`.
The UI shows `stale` as a red banner: "Run <id> left `.sdd/run.json` behind and is not running.
Start `/sdd-run` to resume or clean it up." `/sdd-status` applies the same rules (document them in
`skills/sdd-status/SKILL.md`).

### 4.6 Live output (FR-021)
`GET /api/runs/<id>/tasks/<task_id>/log` (validate both ids; task id `^T\d+[a-z]?$`):
1. `workflow_dir` from `.sdd/run.json` when its `run_id` equals `<id>`; otherwise →
   `{"available": false, "reason": "run is not active"}`.
2. The task's agent: the newest event for the task in `run_events()` (§3.9) whose `workflow` is the
   last directory. Its `agent_id` names the transcript (the journal maps labels to agent ids,
   research.md D7). No such event → not available.
3. The file is `<workflow_dir>/agent-<agent_id>.jsonl`.
4. Return `{"available": true, "label": ..., "lines": [last 200 lines, each truncated to 2000 chars]}`;
   anything not found → `{"available": false, "reason": "..."}` with status 200, never 404/500.
Transient status for the Tasks view comes from the same event feed: newest event per task id —
`task.started → running(Implementing)`, `task.implemented → running(Verifying)`,
`task.verified` with `verdict: reject` → `retrying` (n = number of rejects so far + 1,
max = `max_retries` + 1), `task.approved → running(Committing)`. When the run state (§4.5) is
`paused`, a task whose newest event is one of these shows `paused` instead.
`task.blocked|task.failed` → no transient state (the parked state from §2 applies after the
bookkeeper records it).

### 4.7 Host header and discovery (FR-015, FR-022)
At the top of `do_GET` and `do_POST`: if the `Host` header, without its port, is not one of
`127.0.0.1`, `localhost`, `[::1]` → respond 421 and return. Keep binding to `127.0.0.1`; add a
comment at the bind call: "Security boundary: localhost only, no auth (FR-022). Do not add a --host
option." `/api/projects` returns only `[{"path": <started project>, "name": <dir name>}]`; delete
`discover_projects()`'s directory scan.

### 4.8 Read-only (FR-020)
No route writes to disk. Add a unit test that reads `bin/sdd-dashboard.py`'s source and fails if it
contains `write_text`, `open(` with a write mode, `.unlink(`, `subprocess`, `os.remove`,
`shutil.` or `git ` — the dashboard needs none of them. (The `subprocess` import exists today and is
unused; delete it.)

---

## 5. `workflows/sdd-run.js`

### 5.1 Remove worktree isolation (FR-016)
Delete `ISOLATION`, `execFileSync`, `git()`, `worktreeFor()`, `createWorktree()`, `mergeWorktree()`,
the `worktrees` block before `parallel(...)`, the `wt` lines inside it, the merge loop after it, and
`isolation` from `group.finished`'s payload and from the args comment. The group runs exactly as
`parallel(group.map(id => () => runTask(phaseInfo.tasks[id])))`.

### 5.2 Per-task allow line (FR-014)
Remove the `Allowed this run:` line from `context`. Add:
```javascript
function allowLine(task) {
  const o = TASK_ALLOW[task.id] ?? {}
  const deps = o.dependencies ?? (task.tags.includes('dep:ok') || !!A.allow?.dependencies)
  const downloads = o.downloads ?? (task.tags.includes('download:ok') || !!A.allow?.downloads)
  return `Allowed for this task: new dependencies ${deps ? 'yes' : 'no'}, large downloads ${downloads ? 'yes' : 'no'}.`
}
```
and put `allowLine(task)` on the line right after `context` in `implement()`'s prompt.

`repair()` fixes several tasks at once, so it gets one allow line per approved task. Change its
signature to `repair(outcomes, failing, tasks)`, where `tasks` is the phase's task map
(`phaseInfo.tasks`), update its one call site to pass `phaseInfo.tasks`, and after `context` add:
`ids.map(id => `${id}: ${allowLine(tasks[id])}`).join('\n')`.

### 5.3 Answer in the brief (FR-023)
In `taskBrief(task)`, when `task.answer` is set append:
`Earlier question: ${task.question}\nAnswer from the person (follow it): ${task.answer}`.

### 5.4 Role models (FR-011)
```javascript
const AGENT_MODELS = A.agentModels ?? {}
const modelOpt = role => (AGENT_MODELS[role] && AGENT_MODELS[role] !== 'inherit') ? { model: AGENT_MODELS[role] } : {}
```
Spread into each `agent()` call: `implement()` → `modelOpt(evalTask ? 'evaluator' : 'implementer')`,
`verify()` → `modelOpt('verifier')`, `bookkeep()` → `modelOpt('bookkeeper')`,
`repair()` → `modelOpt('implementer')`.

---

### 5.5 No event feed in the workflow (FR-006)
Workflow scripts can't `import()`, have no file system, and `Date.now()` / `Math.random()` /
argless `new Date()` throw (research.md D6). In `workflows/sdd-run.js` delete the `fs` and `path`
imports, `EVENTS_DIR`, `event()`, `closeEvents()`, every `event(…)` and `closeEvents()` call, and
`eventsDir` from the args comment. In `skills/sdd-run/SKILL.md` step 6.4 remove `"eventsDir"` from the
args JSON, and where the main loop adds `workflow_dir` to `.sdd/run.json`, also create or extend
`.sdd/runs/<run id>/workflows.json` (§3.9). In step 7 replace the report line about
`.sdd/runs/<run id>/events.jsonl` with "the run's events: `python3 <helper> events . <run id>`".
Tests: delete `event feed writes …` in `tests/workflow.test.mjs` and add a test that the workflow
source contains none of `import(`, `require(`, `Date.now(`, `Math.random(`, `new Date()`.

## 6. Guard (`hooks/rules.ts`) — scratch carve-out (FR-013)

In `protectedPath()`, before matching globs:
```typescript
const rel = posixNormalize(relativeTo(root, path))
if (rel.startsWith('.sdd/scratch/')) return undefined
```
with `posixNormalize` resolving `.` and `..` segments (a path that normalizes outside `.sdd/scratch/`
— e.g. `.sdd/scratch/../config.toml` — is matched normally). Write it as a small pure function in
`rules.ts` (no Node `path` import; the hook runtime may not provide it): split on `/`, drop `''` and
`.`, pop on `..`, join. This one change covers both the Edit/Write hook and shell write targets,
for the main loop and subagents. `forLoop()` in `register.ts` is unchanged.

---

## 7. Git-ignored runtime files (FR-026)

Required `.gitignore` lines in a project:
```
.sdd/run.json
.sdd/scratch/
.sdd/runs/*/
.sdd/plugin-feedback.jsonl
```
(`.sdd/runs/*/` ignores the per-run directories with `workflows.json`; `.sdd/runs/<id>.md` reports stay tracked.)
- `skills/sdd-init/SKILL.md`: add all four lines (today it adds only `.sdd/run.json`) and tell the
  person to commit `.gitignore`.
- `skills/sdd-run/SKILL.md` preflight, new first check before the clean-tree check: if `.gitignore`
  lacks any of the four lines, stop and say "Run `/sdd-init` to update `.gitignore`, commit it, then
  start `/sdd-run` again." Remove the now-redundant step 6.2 (`.gitignore` lists `.sdd/run.json`).
- Remove the claim "It is git-ignored" from the agent prompts only if this check is not in place —
  with it, the claim is true; keep it.

---

## 8. Learnings and plugin feedback (FR-024)

**At the end of every run** (`skills/sdd-run/SKILL.md` step 7, both the interrupted and the normal
branch), the main loop, after writing the run report:
1. Reads the run's events (`python3 <helper> events . <run id>`, §3.9) and the run outcome, and writes **project lessons** — facts
   true of this codebase that would change how the next run goes (a check command that needs a
   flag, a path the sandbox refuses, a test that is slow or flaky, a recurring verifier complaint) —
   appended to `.sdd/learnings.md` under `## <run id> (<YYYY-MM-DD>)` as bullets. No lessons → no
   section. Never duplicate a bullet already in the file.
2. Writes **plugin feedback** — problems with the plugin itself (a command the plugin's own prompt
   told an agent to run that the guard or permissions refused, an agent schema error, a crash, a
   prompt that contradicts the guard) — one JSON line each, appended to `.sdd/plugin-feedback.jsonl`:
   `{"id": "<run id>-<n>", "date": "YYYY-MM-DD", "run_id": "...", "project": "<dir name>", "text": "...", "evidence": "<event kinds/ids or a short quote>"}`.
3. Commits `.sdd/learnings.md` together with the report (`git add -- .sdd/runs/<id>.md .sdd/learnings.md`).
   `plugin-feedback.jsonl` is git-ignored (§7) and never committed in the project.

**Harvest** — new `bin/sdd-harvest.py` (stdlib only), run from the plugin repo:
`python3 bin/sdd-harvest.py <project dir> [--log logs/<file>.md]`
- Reads `<project>/.sdd/plugin-feedback.jsonl` (skips unparseable lines).
- Keeps already-harvested ids in `logs/.harvested.json` (`{"ids": [...]}`); skips those.
- Appends each new entry to the log file (default `logs/<YYYY-MM-DD>-<project dir name>-feedback.md`)
  as `- <date> <run_id>: <text> (evidence: <evidence>)`, under a `## Harvested <today>` heading.
- Prints `{"harvested": n, "skipped": m, "log": "<path>"}`.

---

## 9. Run profiles in the supervisor (FR-025)

`bin/sdd-supervise.py`:
- New option `--profile NAME`. Load the project's config with `sdd-tasks.py`'s `load_config` (same
  importlib pattern as §4.1) and run `config_errors()`; on any error, print them and exit 2 before
  starting anything. Unknown profile → exit 2 with `unknown profile NAME`.
- `command()` adds `["--model", profile["model"]]` to the `claude` arguments.
- The env for `Popen` becomes `dict(os.environ, **profile.get("env", {}), CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS="0")`.
- The prompt passes the profile name on: `/sdd-run --yes --profile NAME …` (the skill only records it
  in the report; it does not change models itself).
Interactive `/sdd-run` (without the supervisor) cannot switch provider; `--profile` is accepted there
only to record it. Document in README: a profile pointing at a gateway for a non-Claude/local model
is best-effort and unsupported by Anthropic; such models often lack the tool calling the agents need.

## 10. Supervisor hard stop per window (FR-028)

`--hard-stop` default becomes `None`. Compute:
`args.hard_stops = {name: min(1.0, args.hard_stop if args.hard_stop is not None else round(t + 0.01, 2)) for name, t in args.threshold.items()}`.
Help text: "stop at once at this utilization (default: each window's threshold + 0.01)".

## 11. Run report additions (FR-012, FR-024)
`skills/sdd-run/SKILL.md` step 7's report gains: the profile name (or "none"), the configured model
per role (or "run's model"), the learnings added, and the number of plugin feedback entries written.
The Workflow args JSON (step 6.4) gains `"agentModels": <config.agents or {}>` and loses `"isolation"`.
Step 6.3's `run.json` gains `"task_snapshot": <sdd-tasks.py snapshot output>`. Step 1's resume branch
runs `sdd-tasks.py changed <tasks> --run-json .sdd/run.json` first; if `changed` is not empty, stop
and list those tasks: "these tasks changed since run <id> started; their interrupted work is in the
tree — review it, then stash it (`git stash push -u -m …`) and start again." (FR-018: refuse, don't
guess.) Step 1 also stops when `sdd-tasks.py config` reports any `errors`.
