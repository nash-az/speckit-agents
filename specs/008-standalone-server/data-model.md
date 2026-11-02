# Data model: Standalone server (008)

This file holds the exact shapes and rules for the implementer. Every task in tasks.md is one line that points
here; the **Tests** list of each section is part of the task that implements that section.

"Engine" means `bin/` plus `templates/` (research R1). All new engine and server code is Python standard library
only. Every JSON state file is written atomically: write `<name>.tmp`, then `os.replace()` onto the name. Times are
epoch seconds (int) unless stated otherwise. "003 §N" means `specs/003-plugin-redesign/data-model.md`, and "005 §N"
means `specs/005-jobs/data-model.md`. Tests use temporary folders, set `HOME`, `XDG_STATE_HOME` and `XDG_DATA_HOME`
to temporary directories, and never touch the real home.

## 1. Contract version (FR-018, FR-019)

- `bin/sdd-tasks.py` gains `CONTRACT = "1.0"` and
  `contract_compare(theirs: str | None) -> "same" | "minor" | "major"`:
  - `None` or a malformed value counts as `"0.0"`;
  - a different first number → `major`;
  - the same first number but a different second number → `minor`;
  - otherwise `same`.
- `server/versions.py` has its own `CONTRACT = "1.0"`. A test in `tests/test_server_versions.py` reads the
  repository's `bin/sdd-tasks.py` and fails when the two differ, so they are changed together. Tests may read the
  repository; server code may not.
- `"contract": CONTRACT` is written into:
  - `.sdd/run.json`, by the `session` command (§3);
  - `status.json`, by every `write_status()` call;
  - `control.json`, by every request;
  - `attach.json` (§7);
  - Job records, by `sdd-job.py` `save()`;
  - `VERSION.json` (§2);
  - the top-level JSON object printed by every engine command.
- The server sends the header `X-SDD-Contract` on every API response.
- Readers never refuse to read another version; they report it as in §11.
- **Tests** (`tests/test_sdd_tasks.py`):
  - `None`, `""` and `"x"` count as `0.0`;
  - `"1.0"` → same; `"1.3"` → minor; `"2.0"` and `"0.9"` → major.

## 2. Engine copy (FR-016, FR-017; research R3)

- **Root**: `$XDG_DATA_HOME/speckit-agents/engine/`, or `~/.local/share/speckit-agents/engine/` when that variable
  is unset.
- **Layout**:
  - `<plugin version>/bin/…` is the plugin's `bin/` without `sdd-dashboard.py` and without `__pycache__`;
  - `<plugin version>/templates/…` is the plugin's `templates/`;
  - `<plugin version>/VERSION.json` is
    `{"plugin_version": "0.8.0", "contract": "1.0", "copied_at": 1793620000, "source": "<plugin root>"}`;
  - `current` is a symlink to `<plugin version>`.
- An **engine folder** is a folder that holds `bin/` and `templates/`. That is either a version folder or the
  repository itself. Everything that takes an engine path takes the engine folder, never `bin/` itself.
- **`bin/sdd-engine.py install --from <plugin root>`**:
  1. If the environment has `SDD_SUPERVISED=1`, print `{"contract", "installed": false, "skipped": "supervised run"}`
     and exit 0. Headless runs are sandboxed and cannot write there.
  2. Read `version` from `<plugin root>/.claude-plugin/plugin.json`. If it is missing, use
     `"0.0.0+<first 12 hex characters of the sha256 over the sorted bin/ file contents>"`.
  3. If `current/VERSION.json` already names that version and that `source`, print
     `{"contract", "installed": false, "path"}` and exit 0.
  4. Otherwise copy `bin/` and `templates/` into `<version>.tmp-<pid>/`, write `VERSION.json` there, and then
     `os.replace(<version>.tmp-<pid>, <version>)`. If `<version>` already exists with a different `source`, delete
     it first.
  5. Create a symlink `current.tmp-<pid>` → `<version>`, then `os.replace` it onto `current`.
  6. Keep the 5 newest version folders by `copied_at`, delete the older ones, and never delete the one `current`
     points to.
  7. Print `{"contract", "installed": true, "path", "plugin_version"}`.
  The whole command runs under `fcntl.flock` on `<root>/.lock`.
- **`bin/sdd-engine.py path`** prints `{"contract", "path": <resolved current>, "plugin_version"}`, or exits 2 with
  `{"contract", "error": "no engine copy"}`.
- **Session-start hook**: `hooks/register.ts` registers `on('session.start', …)` once, with no matcher, before the
  `tool.call` registrations. The hook:
  - runs `$.process.run(['python3', `${$.plugin.root}/bin/sdd-engine.py`, 'install', '--from', $.plugin.root])`;
  - ignores the result;
  - catches every error;
  - always returns `next(e)`.
- **Fallback**: `/sdd-init` and `/sdd-run` run the same `install` command as their first step.
- **How the server loads modules**: with `importlib.util.spec_from_file_location(<name>, <engine>/bin/<file>)`,
  as `bin/` scripts already load each other. Files with dashes cannot be imported through `sys.path`. The server
  never loads from the repository's `bin/` unless `--engine` names the repository; the tests do exactly that, with
  `--engine .`.
- **Tests** (`tests/test_sdd_engine.py`):
  - a first install copies `bin/` (without `sdd-dashboard.py` and `__pycache__`) and `templates/`, and `current`
    points at it;
  - the same version again → `installed: false`;
  - a new version moves `current`;
  - the sixth version prunes the oldest, never `current`;
  - `path` with no copy → exit 2;
  - a missing `version` → `0.0.0+<hash>`;
  - `SDD_SUPERVISED=1` → skipped, nothing written;
  - two threads installing at once end with one valid copy.

## 3. `.sdd/run.json` additions and the session (FR-008; research R2)

```json
{ "run_id": "…", "branch": "…", "phases": […], "workflow_dir": "…",
  "workflow_task": "<the Workflow tool's task id>",
  "contract": "1.0",
  "started_by": "session" | "supervisor",
  "session": {"pid": 26834, "started": 1793616483} }
```

- **`python3 <helper> session --run-json .sdd/run.json`** is a new `bin/sdd-tasks.py` command. It needs no shell
  variable on its command line. It:
  1. takes the session's pid from the environment variable `CLAUDE_PID`, which is set in a session's Bash calls
     (research R2). Without it, it does not guess: it writes `session: null` and goes on (US4 acceptance 4 covers a
     session that cannot be identified);
  2. reads that process's start time from `ps -o lstart= -p <pid>`, with `LC_ALL=C` in the environment, and parses
     it with `time.mktime(time.strptime(s.strip(), "%a %b %d %H:%M:%S %Y"))`;
  3. sets `started_by` to `"supervisor"` when `SDD_SUPERVISED=1` is in its environment, and to `"session"`
     otherwise;
  4. merges `contract`, `started_by` and `session` into the file atomically, keeping every other key;
  5. prints `{"contract", "session", "started_by"}`. If `ps` fails, it still writes `contract` and `started_by`,
     writes `session: null`, and adds `"error"`.
- **The `/sdd-run` skill**:
  - runs this command as its own Bash call right after it writes `run.json` at step 6;
  - adds `"workflow_task": <the task id the Workflow tool returned>` when it adds `workflow_dir`.
- **Shared state helpers.** `state_dir(project)` moves from `bin/sdd-supervise.py` to `bin/sdd-tasks.py`, and so do
  `get_status(project)` and `supervisor_alive(status)` from `bin/sdd-dashboard.py`. Behaviour and signatures stay
  the same. The old places import them from `sdd-tasks.py`; `sdd-tasks.py` imports nothing from the other scripts.
- **`session_alive(run) -> True | False | None`**, in `bin/sdd-tasks.py`:
  - `None` when `session` is missing or null;
  - `False` when `os.kill(pid, 0)` raises `ProcessLookupError`, or the pid's current `lstart` differs from
    `started` by more than 1 s;
  - `True` otherwise. `PermissionError` counts as alive.
- **Tests** (`tests/test_run_state.py`):
  - `session` with `CLAUDE_PID` set to this process's pid writes the right `started`, keeps the other keys, and
    sets `started_by: session`; with `SDD_SUPERVISED=1` it sets `supervisor`; without `CLAUDE_PID` it writes
    `session: null`;
  - `session_alive` with this process's pid and real start time → True; with a wrong start time → False; with a
    dead pid (start `true`, wait for it, use its pid) → False; with no `session` → None;
  - `state_dir`, `get_status` and `supervisor_alive` still work through their old names in the dashboard and the
    supervisor.

## 4. Run state: one rule for everyone (FR-006, FR-007)

- **`current_state(project: Path, now=None) -> dict`**, a new function in `bin/sdd-tasks.py`. It is not called
  `run_state`, because the dashboard already has a `run_state()` that adds `by: "operator"`; that one stays in the
  dashboard, unchanged, and calls `sdd-control.py` `controls()`. `current_state` returns:

  ```json
  {"contract": "1.0", "state": "none|running|paused|stale", "run_id": "…|null", "workflow_dir": "…|null",
   "origin": "session|supervisor|job|null", "session_alive": true|false|null}
  ```

- **`state`** follows the 003 §4.5 rules exactly, as committed in `c11294d`:
  1. no `.sdd/run.json` → `none`;
  2. `.sdd/runs/<run_id>.md` exists → `paused`;
  3. a live supervisor's `status.json` with a state other than `stopped` → `paused`, except `running` or
     `stopping` → `running`; a dead supervisor's file is skipped;
  4. a journal or agent file changed within 30 minutes → `running`;
  5. otherwise `stale`.
- **`origin`**, first match wins:
  1. a live supervisor's `status.json` (state not `stopped` or `done`) → `"job"` when it names a Job, otherwise
     `"supervisor"`. An `--adopt` supervisor does not write `status.json` while it only watches (§7), so a watched
     in-session run keeps `origin: session`;
  2. otherwise `run.json` `started_by`, which defaults to `"session"` when missing;
  3. `null` when `state` is `none`.
- `python3 bin/sdd-tasks.py state --project <dir>` prints it. `/sdd-status` step 4 calls it.
- `current_run()` in `bin/sdd-dashboard.py` returns `current_state()` without `contract`, `origin` and
  `session_alive`, so its routes and tests do not change.
- **Tests** (`tests/test_run_state.py`):
  - each 003 §4.5 case from the current-run tests in `tests/test_sdd_dashboard.py`;
  - `origin` for a live supervisor, a live supervisor with a Job, `started_by: session`, and a missing
    `started_by`;
  - a leftover `status.json` `{"state": "done"}` with a dead pid plus a live journal → `running`, origin
    `session`.

## 5. Controls and `/sdd-stop` (FR-007, FR-014)

- **`bin/sdd-control.py`** (new) takes over the following, unchanged in behaviour (003 §12.1 to §12.3):
  - `write_control` and the controls table, from `bin/sdd-dashboard.py` (`write_control`, `run_controls`);
  - `read_control` and `send_control`, from `bin/sdd-supervise.py`.
  Every write of `control.json` goes through `write_control`, which holds `fcntl.flock` on
  `<state dir>/control.lock` across its check that no request is pending and the replace. Several processes can
  then write without overwriting a pending request.
- **`controls(project) -> dict`** has the old keys plus `adopt` and `confirm_closed`, and these new rows. The reason
  texts are exact.

  | situation (in-session run means `origin == "session"` and `state` not `none`) | stop | resume | hold | release | adopt | confirm_closed | reason |
  |---|---|---|---|---|---|---|---|
  | in-session run, `session_alive` True, no live attachment | false | false | false | false | true | false | "running in a Claude session" |
  | in-session run, `session_alive` False, no live attachment | false | false | false | false | true | false | "the session that started this run has ended; take it over to finish it" |
  | in-session run, `session_alive` None, `state` `stale`, no live attachment | false | false | false | false | true | true | "the session that started this run is unknown; confirm it is closed to take it over" |
  | in-session run, `session_alive` True, `state` `stale`, no live attachment | false | false | false | false | true | true | "the session is still open but its run has done nothing for 30 minutes; confirm the session no longer runs it to take it over" |
  | supervised run (`started_by: supervisor`), no live supervisor, `state` not `none` | false | false | false | false | false | false | "the supervisor stopped; Start run resumes it" |
  | in-session run, live attachment (`attach.json` watcher pid alive) | false | false | false | false | false | false | "watching the session's run; takes over if the session ends" |
  | no run and no supervisor | false | false | false | false | false | false | null |

  The reason `no supervisor is running; start one with python3 bin/sdd-supervise.py` is deleted.
- **The `sdd-control.py` command line**:
  - `controls --project <dir>`;
  - `stop [--now] | resume | hold | release --project <dir>`, which make the same requests as the routes;
  - on success it prints `{"contract", "ok": true, "id"}` and exits 0;
  - on a refusal it prints `{"contract", "error"}` with the route's 409 text and exits 1.
- **`skills/sdd-stop/SKILL.md`** (new):
  1. Call `python3 <helper> state --project .`.
  2. If `origin` is `supervisor` or `job`: call `python3 <plugin>/bin/sdd-control.py stop --project . [--now]` and
     report its output.
  3. If `origin` is `session` and `run.json` `session.pid` equals this session's `CLAUDE_PID`:
     - with `--now`: stop the task `run.json` `workflow_task` with the task-stop tool, keep `run.json`, and say
       "Stopped. Start /sdd-run again to resume.";
     - without `--now`: say exactly "Stopping after the current task is not available for runs started in a session
       yet. Use /sdd-stop --now, or let it finish."
  4. If `origin` is `session` and the pid differs: say "This run belongs to another Claude session (pid N, started
     HH:MM). Stop it there." and do nothing.
  5. If `state` is `none`: say "No run is going."
- **Tests** (`tests/test_sdd_control.py`):
  - every row of 003 §12.3 and every new row;
  - the CLI and `POST /api/run/stop` write the same `control.json` (compare the parsed files, minus `id` and
    `requested_at`);
  - a pending request → exit 1, "a request is already pending";
  - two processes calling `write_control` at once → one succeeds and one is refused.

## 6. Server state and start-up (FR-001, FR-002, FR-003, FR-005, FR-009b, FR-011)

- **Folder**: `$XDG_STATE_HOME/speckit-agents/server/`, or `~/.local/state/speckit-agents/server/` when that
  variable is unset.
- **Refusing a Claude session (FR-001, FR-011)**: `server/sdd-server.py` refuses to start when the environment has
  `CLAUDECODE` set. It prints "Start sdd-server from a terminal, not from a Claude session: runs it starts would
  inherit the session's sandbox." and exits 3. Every process the server starts gets an environment without
  `CLAUDECODE`, `CLAUDE_PID` or any variable starting with `CLAUDE_CODE_`.
- **`server.json`**: `{"pid", "started", "port", "contract", "engine_version"}`.
  - It is written under `flock` on `server.lock` at start and removed on a clean exit.
  - If it names a live pid whose start time matches `started`, the new start prints
    `speckit-agents server already running at http://127.0.0.1:<port>` and exits 0.
- **Arguments**:
  - `--port`: default 8765; `0` means any free port. A busy default tries the next 10 ports.
  - `--engine <engine folder>`: default `<engine root>/current`.
  - `--install-command`: writes the `~/.local/bin/sdd-server` shim of research R10 and exits.
- **Start without an engine**: the server starts even when no engine folder exists. Then `GET /api/server` reports
  `engine: null` (§11), every other route except `GET /api/projects` answers 503, and the server loads the modules
  once the engine appears.
- **Engine reload (spec edge case "the plugin is updated while the server runs")**: every 10 s the server
  re-resolves `current/VERSION.json`. When `plugin_version` changes, it loads the engine modules again from the new
  folder under fresh module names, and new requests and new processes use them. Running supervisors keep the engine
  they were started from: `write_status` adds `"engine_version"`, read from the `VERSION.json` next to its own
  `bin/`, or `"repo"`. The UI shows a run's engine version when it differs from the server's current one.
- **`projects.json`**: `{"contract": "1.0", "projects": [{"id", "path", "name", "added", "feature"}]}`.
  - `path` is `Path.resolve()`.
  - `id` is the first 12 hex characters of the sha256 of `path`.
  - `name` is the folder's base name; when two projects share a base name, the parent's base name is added, as in
    `"app (private)"`.
  - `feature` is the feature the person picked (`POST /api/p/<id>/feature`), or null to follow the project's
    `active_feature()` (003 §4.2).
  - Adding a path that is already listed returns the existing entry.
- **Missing project**: one whose path no longer exists or has no `.sdd/config.toml`. It stays listed until it is
  removed.
- **Rediscovery**: at start, and in the watcher every 0.5 s (research R6), the server reads each project's
  `current_state()` (§4), `controls()` (§5), open Job (005) and `attach.json` (§7). Everything the server starts is
  a detached engine process with its own state files, so a restart loses nothing.
- **Tests** (`tests/test_server_core.py`, `tests/test_server_projects.py`). The checks themselves run inside Claude
  sessions, so every test and the browser harness start the server with `CLAUDECODE`, `CLAUDE_PID` and every
  `CLAUDE_CODE_*` variable removed from its environment; only the refusal test sets `CLAUDECODE` on purpose.
  - `CLAUDECODE=1` → exit 3 with the message;
  - a second start → "already running", exit 0;
  - starting without an engine folder → `/api/server` has `engine: null` and other routes answer 503; creating the
    folder → routes work within 15 s;
  - a new `VERSION.json` → reloaded within 15 s;
  - 5 toy projects are added and listed; adding one again or through a symlink gives one entry; a moved folder shows
    as missing; the list survives a restart;
  - the restart test (FR-009b, SC-007a): a fake supervised run, a fake Job and a fake attachment are live (their
    state files and live dummy processes); kill the server and start it again; within 10 s
    `/api/p/<id>/run/current` and `/api/projects` show each with its controls.

## 7. Attachments and hand-over (FR-010; research R5)

- **What hand-over covers** (decided 2026-11-02): only sessions that have ended. An interactive session that hits a
  usage limit stays open and pauses its workflow itself, so it is not taken over while its pid is alive. If such a
  run goes `stale`, the person can still take it over, with `confirm_closed`.
- **`attach.json`** in `state_dir(project)`:
  `{"contract", "run_id", "session": {pid, started} | null, "state", "since", "pid"}`.
  - `pid` is the watcher's own pid.
  - `state` is one of `watching | taken_over | closed | confirm_needed`.
- **`POST /api/p/<id>/run/adopt {"confirm_closed"?: bool}`**:
  - 409 when `controls().adopt` is false; when `controls().confirm_closed` is true and the body lacks
    `confirm_closed: true`; and when the body has `confirm_closed: true` but `controls().confirm_closed` is false.
    `sdd-launch.py adopt` applies the same three checks, so the CLI cannot bypass them;
  - otherwise it starts `bin/sdd-launch.py adopt <project> --run-id <id> [--confirmed-closed]`. That starts
    `bin/sdd-supervise.py --project <dir> --adopt <run_id> [--confirmed-closed]` detached, the same way
    `launch()` starts supervisors.
- **The `--adopt RUN_ID` loop**, every `--poll` seconds (default 15). Until it takes over it writes only
  `attach.json`, never `status.json`.
  1. `run.json` is missing, or names another `run_id`: write `state: closed`, delete `attach.json`, exit 0.
  2. `session_alive(run)` is False, or `--confirmed-closed` was given: write `state: taken_over`. Then wait for a
     slot (§8, a start by itself), and enter the normal cycle loop with `--run-args` built from `run.json`
     (`--phases` from `phases`, `--job` when `job` is present). From here on it writes `status.json` as every
     supervisor does. The first cycle's `/sdd-run --yes` resumes from the journal (003 FR-034 acceptance 4), and a
     usage limit during it is handled as in any supervised run.
  3. `session_alive(run)` is None and `--confirmed-closed` was not given: write `state: confirm_needed` and keep
     polling.
  4. Otherwise write `state: watching`.
- After `--max-wait-hours` (default 24) it writes `state: closed` with `reason: "watched too long"` and exits 3.
- **Tests** (`tests/test_adopt.py`, with the fake `claude`):
  - the run closed → exit 0, nothing started, `attach.json` gone;
  - the session pid is dead → exactly one resume cycle, with the run's phases;
  - alive for 3 polls → no cycle, then dead → one cycle;
  - no session and no confirmation → `confirm_needed` and no cycle; with `--confirmed-closed` → one cycle;
  - a reused pid with another start time counts as dead;
  - `status.json` is not written while watching;
  - `sdd-launch.py adopt --confirmed-closed` on a live, running (not stale) session → refused, nothing started.

## 8. Slots across projects (FR-009a; research R7)

- **Folders**: `$XDG_STATE_HOME/speckit-agents/slots/`, holding `<project id>.json` files and a `queue/` folder.
- **Settings**: `…/server/settings.json` holds `{"max_parallel": 2, "plugin_source": "<path or URL>",
  "claude_command": "claude"}`.
  - `max_parallel` is at least 1; a missing file or a bad value means 2.
  - `plugin_source` and `claude_command` (§13) can be set only by editing the file, never through the API, because
    they choose what the server runs.
- **`bin/sdd-slots.py`** (new). Every function holds `fcntl.flock` on `slots/.lock`.
  - **`count()`** = the live slot files (a file whose `pid` is dead is deleted) + the in-session runs of listed
    projects. An in-session run is a `run.json` with `started_by == "session"` (or missing) and
    `session_alive` True. A supervised run is counted only by its slot file.
  - **`can_take() -> (bool, holders)`** takes nothing and applies exactly `take()`'s rules with `waiting_since=None`
    (the count and the queue), so a start it allows is not then refused by the queue; `holders` lists the slot
    holders and the waiters. The server routes and `sdd-launch.py` use it to refuse.
  - **`take(project, run_id, waiting_since=None) -> bool`**:
    - if `count() >= max_parallel`, return False;
    - if `slots/queue/` holds a live entry with a lower `since` than `waiting_since` (or any live entry when
      `waiting_since` is None), return False;
    - otherwise write `slots/<project id>.json` `{"pid", "run_id", "since"}`, delete this process's queue entry,
      and return True.
  - **`enqueue(project, since)`** writes `slots/queue/<project id>.json` `{"pid", "since"}`.
  - **`free(project)`** deletes the slot file when its `pid` is this process.
  - **`holders() -> list`** lists `{"project", "run_id", "since", "kind": "supervised|session"}`.
- **Who may start, and what happens at the cap**:
  - Person starts: Start run (`launch`), Start job (`launch_job`) and Continue (`continue_job`). Supervise this run is
    not cap-checked: the watched run already counts, and its take-over queues as a start by itself. The route or
    launcher calls `can_take()` first and answers 409 "cap reached: <project name> (<run
    id>), …". If the supervisor still finds no slot on its first cycle (a race), it writes `status.json`
    `{"state": "stopped", "reason": "cap reached: …"}` and exits 4.
  - Starts by itself: every later cycle (the restart after a usage reset, a Resume, a Job's next run, `after_settle`)
    and a take-over. The supervisor calls `enqueue()`, writes `status.json` `{"state": "queued", "since"}`, and
    retries `take(…, waiting_since=since)` every `--poll` seconds.
  - The supervisor frees its slot when a cycle ends, and takes one again before the next.
- **Tests** (`tests/test_sdd_slots.py`, plus supervisor cases in `tests/test_supervise.py`):
  - two takes succeed and a third fails;
  - a dead holder is reclaimed;
  - a supervised run's own `run.json` (`started_by: supervisor`) is not counted again;
  - an in-session run counts;
  - the queue grants the oldest waiter first;
  - `can_take()` is False while a live waiter is queued, and the refusal names it;
  - a bad settings file → 2;
  - 5 threads taking at once never exceed the cap;
  - with the fake `claude`: cap 1 and a held slot → a person start exits 4; a Job's second run waits as `queued`
    and then runs.

## 9. UI (FR-020 to FR-023; research R4)

- **Source**: `ui/`, with `package.json` (exact versions), `package-lock.json`, `tsconfig.json` (strict),
  `vite.config.ts` and `src/`.
  - Runtime dependencies: `react`, `react-dom`, `marked` 12.0.2, `dompurify` 3.1.6, `highlight.js` 11.9.0.
  - Build dependencies: `vite`, `@vitejs/plugin-react`, `typescript`.
  - Nothing else is added without a `[dep]` task.
- **Build**: `npm ci && npm run build` writes `server/static/` (`build.outDir = '../server/static'`,
  `emptyOutDir: true`, `base: '/'`).
  - A UI task lists `server/static/` among its files, so the bookkeeper commits the rebuilt output with the task.
    Implementers never commit.
- **Serving** (`server/app.py`):
  - `server/static/index.html` for every path not under `/api/`, with `Cache-Control: no-cache`;
  - `/assets/…` with `max-age=31536000, immutable`;
  - correct content types;
  - a path that resolves outside `server/static/` → 404.
- **`tests/test_ui_build.py`** runs only when `SDD_UI_BUILD=1`: it rebuilds into a temporary folder and compares
  file hashes with `server/static/`. It is skipped otherwise, so run checks never call npm.
- **Page routes**:
  - `/` is the project list;
  - `/p/<project id>/<view>`, where the views are `overview | tasks | runs | jobs | waiting | decisions | docs |
    settings`.
- **Module layout** in `ui/src/`:
  - `api.ts`: typed wrappers for contracts/server-api.md;
  - `stream.ts`: `useStream()` over `EventSource('/api/stream')`, retrying every 2 s and exposing
    `reconnecting`;
  - `router.tsx`: the History API, no router library;
  - `App.tsx`: the header and navigation;
  - `views/<View>.tsx`: one file per view;
  - `components/`: shared parts (`StatusBadge`, `RunBanner`, `FolderBrowser`, `SetupPrompt`, `PluginDialog`);
  - `theme.css`: color tokens for dark and light, and a 16 px phone gutter.
- **Live updates**: on a stream event `{"project", "topic", "at"}` the page refetches only that topic's API for
  that project and updates React state in place, keeping scroll position and open panels.
- **Browser tests**: in `tests/server_ui/`, with the harness of tests/ui/harness.py adapted (start
  `server/sdd-server.py --port 0 --engine .` with temporary `HOME` and XDG folders and the `CLAUDE*` variables
  removed (§6); Chrome through Playwright,
  `channel="chrome"`; the same skip rules). Phone width: at 375×800, for every view,
  `document.documentElement.scrollWidth <= innerWidth`. Live: a journal line appears on the page within 2 s, with
  no reload, and the scroll position is kept.

## 10. Folder browser and request checks (FR-003a)

- **Request checks** (`server/app.py`, copied from `bin/sdd-dashboard.py` with one change for GET):
  - every request: an allowed Host, or 421, as in 003 §4.7;
  - POST: an allowed Origin, or 403; `Content-Type: application/json`, or 415, as in 003 §4.9;
  - `GET /api/fs/list` and `GET /api/plugin`: a present Origin must be allowed, and a missing one is fine
    (browsers send none on same-origin GETs); `Sec-Fetch-Site`, when present, must be `same-origin` or `none`;
    otherwise 403.
- **`GET /api/fs/list?path=<abs>&hidden=0|1`** → `{"path", "parent", "entries": [{"name", "path", "sdd": bool,
  "listed": bool}], "truncated": bool}`.
  - It lists folders only, sorted by name. Hidden folders (a leading `.`) are left out unless `hidden=1`.
  - `sdd` means the folder has `.sdd/config.toml`; `listed` means it is in `projects.json`.
  - At most 500 entries.
  - `path` defaults to `Path.home()`. After `resolve()` it must lie inside `Path.home().resolve()`, or 403
    `{"error": "outside your home folder"}`.
- **`POST /api/projects/add {"path"}`**:
  - inside home, or 403;
  - a git repository (`git -C <path> rev-parse --git-dir` exits 0), or 422 `{"error": "not a git repository"}`;
  - with `.sdd/config.toml`: added, 200 with the entry;
  - without it: 200 `{"needs_setup": true, "path", "plan": <sdd-setup.py detect output>}`, and nothing is added.
- **Tests** (`tests/test_server_projects.py`, temporary `HOME`):
  - listing works, with the flags;
  - 600 folders → 500 and `truncated`;
  - `/etc` → 403; a symlink pointing outside home → 403;
  - a foreign Origin → 403; a missing Origin → 200; `Sec-Fetch-Site: cross-site` → 403;
  - add for a non-git folder → 422, and for a git folder with no `.sdd/` → `needs_setup`.

## 11. Version warnings (FR-019)

- **`GET /api/server`** → `{"contract", "engine": {"plugin_version", "contract", "path"} | null,
  "installed_plugin_version": str | null, "warnings": [...]}`.
  - `installed_plugin_version` comes from `<claude_command> plugin list --json`, run as an argument list with a
    10 s timeout and cached for 10 minutes; null when the command is missing.
- **What is compared**: only live files, so leftovers from before 008 never lock anything.
  - server ↔ engine copy;
  - engine copy ↔ installed plugin;
  - and, per project:
    - `run.json` while `current_state()` is not `none`;
    - `status.json` only while its supervisor is alive;
    - `attach.json` only while its watcher is alive;
    - the open Job only.
  A missing `contract` in one of these live files counts as `0.0`.
- **A warning** is `{"what", "ours", "theirs", "level": "minor|major", "project"?}`. The `what` texts are exact:
  - "engine copy";
  - "installed plugin";
  - "this run";
  - "the supervisor";
  - "the attachment";
  - "the open Job".
  An engine copy older than the installed plugin adds the text "start a Claude session to refresh it".
- **Write lock**: a major warning on a project, or on the server ↔ engine copy pair (which affects every project),
  makes every write route of the affected projects answer 409. The engine copy ↔ installed plugin pair only warns:
  it never locks, so a plugin update does not lock anything before a session refreshes the copy. A locked route
  answers `{"error": "version mismatch: <what> <ours> vs <theirs>"}`. The UI
  disables those buttons and shows the text, and reads keep working. The lock lifts by itself when the live file
  ends (the run closes, the supervisor exits).
- **No engine copy**: `engine: null`, plus a major warning "no engine copy: start a Claude session with the plugin
  installed". See §6 for the 503s.
- **Tests** (`tests/test_server_versions.py`):
  - an engine `VERSION.json` at 1.3 → minor, writes work;
  - at 2.0 → major, writes 409, reads 200;
  - no copy → 503;
  - a project with a live run whose `run.json` has no contract → a major warning for that project only;
  - the same project after the run closed → no warning;
  - a leftover dead `status.json` with no contract → no warning;
  - the two `CONTRACT` constants are equal (§1).

## 12. Project setup (FR-025; research R12)

`bin/sdd-setup.py` (new). Every command takes `--project <dir>` and prints JSON with `contract`. Templates are read
from `<engine folder>/templates/`. `[checks] commands` are strings, as in today's `config.toml`.

- **`detect`** runs nothing but `PATH` lookups (`shutil.which`) and file reads. It prints:
  - `{"speckit": bool, "specify_cli": bool, "configured": bool, "candidates": [{"command": str, "source", "why"}],
    "files": [{"path", "action": "create|merge|append|unchanged"}]}`;
  - `configured` is true when `.sdd/config.toml` exists.

  The candidate rules, applied in this order, without duplicates:

  | Found | Candidates |
  |---|---|
  | `pyproject.toml` with `[tool.pytest` or `[tool.pytest.ini_options]`, or `pytest.ini` | `uv run pytest -q` when `uv.lock` exists, otherwise `python3 -m pytest -q` |
  | a `tests/` folder with `test_*.py` and no pytest config | `python3 -m unittest discover -s tests` |
  | `pyproject.toml` with `[tool.mypy]` / `[tool.ruff]` | `uv run mypy .` / `uv run ruff check .` (`python3 -m …` without `uv.lock`) |
  | `package.json` scripts `test`, `lint`, `typecheck` | `npm test`, `npm run lint`, `npm run typecheck` (`pnpm …` when `pnpm-lock.yaml` exists) |
  | a `Makefile` / `justfile` with targets `test`, `lint`, `check` | `make <target>` / `just <target>` |

- **`check --command "<string>"`** runs `shlex.split(command)` from the project root, with no shell and a 10-minute
  timeout. It prints `{"exit", "seconds", "output"}`, where `output` is the last 200 lines. A command that needs
  another folder uses the tool's own flag (`uv run --directory backend …`, `pnpm -C frontend …`), never `cd`.
- **`apply --checks '<json list of strings>' [--commit] [--dry-run] [--refresh]`**:
  - Without `--refresh`, an existing `.sdd/config.toml` → refuse "already set up; use --refresh". With
    `--refresh`, keep the existing `[checks] commands` unless `--checks` is given, and do everything else the same.
  - It writes:
    - `.sdd/config.toml` from `templates/sdd-config.toml`, with `[checks] commands`;
    - the tasks-template override with the `## Agent tags` section, added or replaced (as `/sdd-init` step 3);
    - `.claude/settings.json`, merged by union (never removing anything), plus one allow rule `Bash(<command> *)`
      per check, plus the caches under `sandbox.filesystem.allowWrite`. The caches come from `uv cache dir`,
      `npm config get cache` and `pnpm store path`, each run only when that tool is used by a chosen check, with
      the home folder written as `~`;
    - the five `.gitignore` lines.
  - `--dry-run` writes nothing and prints `{"files": [{"path", "action", "diff"}]}`: the exact unified diff of each
    file. The setup prompt shows this before Set up.
  - `--commit` commits exactly the written paths on the current branch, with the message
    `chore: set up speckit-agents`.
  - It prints `{"written": [paths], "commit": sha | null}`.
- **The `/sdd-init` skill** keeps step 1 (Spec Kit present), step 2's search for the check commands (which uses
  judgement and runs each one), and step 4's sandbox re-check. It writes files only through
  `apply --checks … [--refresh]`, so the files always come from the same code.
- **Server routes** (contracts/server-api.md): `POST /api/setup/detect {path}`, `POST /api/setup/check {path,
  command}`, `POST /api/setup/apply {path, checks, commit, dry_run}` and `POST /api/setup/specify {path}`.
  - The last one runs `specify init --here --ai claude` as an argument list when `specify_cli` is true.
  - `apply` without `dry_run` then adds the project.
  - The setup prompt shows, above Run checks, the exact text: "Checks run on your machine with your rights, outside
    any sandbox. Only run checks for projects you trust."
- **Tests** (`tests/test_sdd_setup.py`, toy repositories):
  - detect for: pytest config with `uv.lock`; plain `tests/test_*.py`; `package.json` scripts with
    `pnpm-lock.yaml`; a Makefile `test` target; nothing; no `.specify/`;
  - detect starts no process (patch `subprocess` to fail);
  - `check false` → exit 1;
  - `apply --dry-run` writes nothing and shows the diffs;
  - `apply --commit` → one commit with exactly those paths;
  - a second `apply` is refused; `--refresh` keeps the checks;
  - existing `.claude/settings.json` entries are kept.

## 13. Plugin install and update (FR-026, FR-027; research R11)

- **`GET /api/plugin`** → `{"installed": bool, "version", "installPath", "contract", "needed":
  "ok|install|update|server_older", "reason"}`.
  - The data comes from `<claude_command> plugin list --json`, cached for 60 s.
  - `contract` is read from `<installPath>/bin/sdd-tasks.py` (the `CONTRACT = "…"` line, by a regular
    expression, never by importing it).
  - `needed`: a contract major lower than the server's → `update`; higher → `server_older`; not installed →
    `install`.
- **`POST /api/plugin/install`** and **`POST /api/plugin/update`**, body `{}` or `{"accept_command": "<sha256>"}`:
  - 409 while any listed project has a live run, a live attachment or a queued supervisor;
  - install runs `marketplace add <plugin_source>` (only when `marketplace list` lacks it), then
    `install speckit-agents@speckit-agents --scope user --json`;
  - update runs `marketplace update speckit-agents`, then `update speckit-agents@speckit-agents`;
  - each command is an argument list with a 5-minute timeout and `stdin` set to `DEVNULL`;
  - when a command's JSON output asks for a command confirmation (`shownCommand.sha256`), the route answers 409
    `{"confirm_command": <the shown command>, "sha256"}` and keeps that hash in memory for 10 minutes. A repeat
    with `accept_command` passes `--accept-command <sha256>` only when it equals the kept hash, and 409 otherwise;
  - on success it runs `<installPath>/bin/sdd-engine.py install --from <installPath>` and answers
    `{"ok": true, "output", "version"}`.
- **UI**: a plugin badge in the header reads Installed 0.8.0 / Install plugin / Update plugin / Update the server.
  After an install or update the page says "Open Claude sessions keep the old version until they are restarted."
- **Tests** (`tests/test_server_plugin.py`, with `claude_command` pointing at the fake `tests/fake/claude-plugin`,
  which prints canned JSON):
  - missing → install; an old contract → update; a newer one → `server_older` with no install route;
  - a live run → 409;
  - the accept-command flow: the right hash passes and a wrong one → 409;
  - the engine copy is refreshed afterwards.

## 14. Read models in the engine (FR-017; research R1)

`bin/sdd-views.py` (new) takes over every function of `bin/sdd-dashboard.py` that builds an API answer from files
and does no HTTP or HTML work. That is:

- `read_text`, `read_json` and `read_lines`;
- `feature_names`, `active_feature`, `run_tasks`, `run_feature`, `runs_by_task` and `list_runs`;
- `job_view`, `list_job_views`, `get_job`, `job_plan` and `open_job_view`;
- `get_events` and `get_report`;
- `answers_dir`, `read_answers`, `open_decisions`, `write_answer` and `try_apply`;
- `task_log`, with its helpers `_unavailable`, `_block_text` and `_readable_line`;
- `task_status`, `rollup`, `project_tasks` and `project_blocked`;
- `_whole_or_none`, `_number_or_none`, `_id_list` and `job_limits_from`.

Each keeps its signature. `run_controls` and `write_control` go to `sdd-control.py` (§5); `get_status`,
`supervisor_alive`, `logs_dir` (as `state_dir`), `_reset_time` and `_recently_modified` go to `sdd-tasks.py` (§3,
§4). `run_state`, `current_run`, `is_speckit_project`, `discover_projects`, `host_without_port`, `build_html` and
`main` stay in the dashboard. No engine module imports the dashboard.

`bin/sdd-views.py` gets the same read-only guard test as `bin/sdd-dashboard.py` (no subprocess, unlink or `git `),
except for `write_answer` and `try_apply`, which call `sdd-decide.py` as today; the exception is named in the test.

## 15. Roadmap (FR-028 to FR-031)

- **The file** `.sdd/roadmap.toml`, tracked by git, read with `tomllib`, and written by `bin/sdd-edit.py` (below) in
  exactly this form:

  ```toml
  # Build order for this project, read by speckit-agents. Spec Kit never reads it.
  [[feature]]
  id = "005-jobs"            # a folder under specs/, or a short name for a feature not written yet
  title = "Jobs"             # optional; shown when the folder does not exist yet
  state = ""                 # "" (derived), "done", "superseded" or "parked"
  note = ""                  # optional, shown on the row
  ```

  Entries appear in build order. Feature folders not in the file follow them, in folder order. An unreadable file
  shows a warning and folder order, and never stops the view.
- **Stage**, derived from the files, first match from the top:

  | condition | stage |
  |---|---|
  | `state` is `superseded` or `parked` | that state |
  | `state` is `done`, or every task is ticked or dropped (`wontfix`/`moved`/`superseded` in BLOCKED.md or the task state) | `done` |
  | no folder | `not written` |
  | a task is ticked, or a run in `.sdd/runs/*/workflows.json` names the feature | `building` |
  | `tasks.md` | `tasks` |
  | `plan.md` | `planned` |
  | `spec.md` with a `## Clarifications` heading | `clarified` |
  | `spec.md` | `spec` |

- **Progress**: ticked tasks / (all tasks minus dropped ones), per feature. Overall: the sum over the features whose
  stage is not `superseded`, `parked` or `not written`.
- **Engine** (`bin/sdd-views.py`): `roadmap(project) -> {"contract", "features": [{"id", "title", "exists", "stage",
  "state", "note", "done", "total", "waiting", "active"}], "overall": {"done", "total"}, "warnings": []}`. Also
  `waiting_all(project)`, which is the "Waiting on you" list of every feature, each item with `feature`, and
  `runs_all(project)`, which is every run of `.sdd/runs/` with its `feature` from `workflows.json`, newest first.
- **Writes** (`bin/sdd-edit.py`, which already saves and commits the spec and the constitution for 003 FR-035):
  - `roadmap-save --entries '<json list>'` validates the entries: `id` matches `^[0-9A-Za-z][0-9A-Za-z._-]*$`,
    `state` is one of the four values, and no id appears twice. It writes the file in the form above and commits it
    with `chore(roadmap): update build order`, under the same refusals as the spec editor (a run is going, other
    changes are staged).
  - `activate --feature <folder>` writes `.specify/feature.json` as `{"feature_directory": "specs/<folder>"}`
    atomically. It refuses with an exact text:
    - while `.sdd/run.json` exists: "a run is going: switch the feature after it ends";
    - when `[run] feature` is set in `.sdd/config.toml`: "the project config pins the feature to <x>";
    - when the folder has no `tasks.md`: "<folder> has no tasks yet".
- **Routes**:
  - `GET /api/p/<id>/roadmap`;
  - `GET /api/p/<id>/waiting-all`;
  - `GET /api/p/<id>/runs-all`;
  - `POST /api/p/<id>/roadmap {entries}`;
  - `POST /api/p/<id>/feature/activate {feature}`.
  The POST routes go through the request checks and the version lock (§10, §11).
- **Initial content**: this repository's `.sdd/roadmap.toml` is written with the specs, in the order the person
  set on 2026-11-02:
  - 001 and 002 superseded (by 003);
  - 003 done ("T001 won't fix");
  - then 005, 008, 006, 004 and 007 ("Parallelism", not written yet).
- **Tests** (`tests/test_roadmap.py`, toy features in every stage):
  - each stage row of the table;
  - order: file entries first, then unlisted folders;
  - an unreadable file → a warning and folder order;
  - the overall figure leaves out superseded, parked and not-written features;
  - `waiting_all` across two features;
  - `roadmap-save` validation and the commit;
  - each of `activate`'s three refusals, and a successful write that leaves `git status` clean (the file is ignored).
