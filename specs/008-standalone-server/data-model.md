# Data model: Standalone server (008)

Exact shapes and rules for the implementer. "Engine" means `bin/` (research R1). All new engine code is Python
standard library only, like the rest of `bin/`. All writes of JSON state files are atomic: write `<name>.tmp`, then
`os.replace()` onto the name. Times are epoch seconds (int) unless stated otherwise.

## 1. Contract version (FR-018, FR-019)

- `bin/sdd-tasks.py` gains `CONTRACT = "1.0"` and
  `contract_compare(theirs: str | None) -> "same" | "minor" | "major"`:
  - `None` or a malformed value counts as `"0.0"`;
  - a different first number → `major`;
  - the same first number but a different second number → `minor`;
  - otherwise `same`.
- Written as `"contract": CONTRACT` into:
  - `.sdd/run.json`, by the `/sdd-run` skill at step 6;
  - `status.json`, in every `write_status()` call;
  - `control.json`, in every request;
  - `attach.json` (§7);
  - the engine copy's `VERSION.json` (§2);
  - the top level of every JSON object an engine command prints (`parse`, `plan`, `state`, `events`, `session`,
    `controls`);
  - the server's `GET /api/server` response, and the response header `X-SDD-Contract` on every API response.
- Readers that find another version never refuse to read. They report it as in §11.

## 2. Engine copy (FR-016, FR-017; research R3)

- Root: `$XDG_DATA_HOME/speckit-agents/engine/`, or `~/.local/share/speckit-agents/engine/` when that variable is
  unset.
- Layout:
  - `<plugin version>/bin/…`: a copy of the plugin's `bin/` without `sdd-dashboard.py` and without `__pycache__`;
  - `<plugin version>/VERSION.json`:
    `{"plugin_version": "0.8.0", "contract": "1.0", "copied_at": 1793620000, "source": "<plugin root>"}`;
  - `current`: a symlink to `<plugin version>`.
- New `bin/sdd-engine.py`:
  - `install --from <plugin root>`:
    1. Read `version` from `<plugin root>/.claude-plugin/plugin.json`. If it is missing, use `"0.0.0+<first 12
       characters of sha256 over the sorted bin/ file contents>"`.
    2. If `current/VERSION.json` already names that version and `source`, print
       `{"contract", "installed": false, "path"}` and exit 0.
    3. Otherwise copy `bin/` into `<version>.tmp-<pid>/bin`, write `VERSION.json`, then
       `os.replace(<version>.tmp-<pid>, <version>)`. If the target exists, remove the target first, but only when
       the target's `VERSION.json` `source` differs.
    4. Create the symlink `current.tmp-<pid>` → `<version>`, then `os.replace` it onto `current`.
    5. Keep the 5 newest version folders by `copied_at` and delete older ones. Never delete the one `current`
       points to.
    6. Print `{"contract", "installed": true, "path", "plugin_version"}`.
  - `path`: prints `{"contract", "path": <resolved current>, "plugin_version"}`, or exits 2 with
    `{"error": "no engine copy"}`.
  - The lock for `install` is `fcntl.flock` on `<root>/.lock`, so two sessions starting at once do not both copy.
- `hooks/register.ts` registers `on('session.start', async ($, e, next) => { ... ; return next(e) })`, which:
  - runs `$.process.run(['python3', `${$.plugin.root}/bin/sdd-engine.py`, 'install', '--from', $.plugin.root])`;
  - ignores the result (a failure never blocks the session);
  - is registered once, with no matcher, before the existing `tool.call` registrations. A throw there is caught and
    still calls `next(e)`.
- `/sdd-init` and `/sdd-run` call the same `install` command as their first step, as a fallback.
- The server finds the engine by reading `<root>/current/VERSION.json` and adding `<root>/current/bin` to
  `sys.path` (importlib, as `bin/` scripts already import each other). It never imports from the repo's `bin/`. A
  test asserts that `server/` contains no path to `bin/` other than through the engine root.

## 3. `.sdd/run.json` additions (FR-008)

```json
{ "run_id": "…", "branch": "…", "phases": […], "workflow_dir": "…",
  "contract": "1.0",
  "started_by": "session" | "supervisor",
  "session": {"pid": 26834, "started": 1793616483} }
```

- `started_by` is `"supervisor"` when the environment variable `SDD_SUPERVISED=1` is set, and `"session"`
  otherwise. The supervisor sets that variable on the `claude -p` it starts. `job` (005) stays as is.
- `session` is written by the new command `python3 <helper> session --pid $PPID --run-json .sdd/run.json`, run by
  the `/sdd-run` skill right after it writes `run.json` at step 6. The command:
  1. reads `ps -o lstart= -p <pid>`;
  2. parses it with `time.strptime(s.strip(), "%a %b %d %H:%M:%S %Y")` (the C locale, which the command forces with
     `LC_ALL=C` in the `ps` environment);
  3. merges `session` into `run.json` atomically;
  4. prints `{"contract", "session"}`.
- If `ps` fails, it writes nothing and prints `{"contract", "session": null, "error"}`; the skill goes on.
- `session_alive(run) -> True | False | None`, in `bin/sdd-tasks.py`:
  - `None` when there is no `session`;
  - `False` when `os.kill(pid, 0)` raises `ProcessLookupError`, or the `lstart` of that pid now differs from
    `started` by more than 1 s;
  - `True` otherwise. `PermissionError` counts as alive.

## 4. Run state, one rule for everyone (FR-006, FR-007)

- `run_state(project: Path, now=None) -> dict` moves from `current_run()` in `bin/sdd-dashboard.py` into
  `bin/sdd-tasks.py`, with the rules as committed in `c11294d` plus `by`:

  ```json
  {"contract": "1.0", "state": "none|running|paused|stale", "run_id": "…|null", "workflow_dir": "…|null",
   "by": "session|supervisor|job|null", "session_alive": true|false|null}
  ```

- `by` is decided in this order:
  1. a live supervisor's `status.json` exists → `"job"` if it names a Job, otherwise `"supervisor"`;
  2. otherwise `run.json` `started_by` (`"session"` when the field is missing);
  3. `null` when the state is `none`.
- `python3 bin/sdd-tasks.py state --project <dir>` prints it. `/sdd-status` step 4 calls it instead of restating
  the rules in prose. The old dashboard imports it, so the dashboard keeps its behaviour until it is removed.

## 5. Controls and `/sdd-stop` (FR-007, FR-014)

- New `bin/sdd-control.py` takes over these from the dashboard and the supervisor, unchanged in behaviour (003
  data-model §12.1 to §12.3):
  - `write_control(state_dir, command, mode)`;
  - `read_control(state_dir)`;
  - `controls(project) -> dict`, the table in 003 §12.3.
- `controls(project)` gains these rows (the reason texts are exact):

  | situation | stop | resume | hold | release | adopt | reason |
  |---|---|---|---|---|---|---|
  | `by == "session"`, session alive, no attachment | false | false | false | false | true | "running in a Claude session" |
  | `by == "session"`, attachment `watching` | false | false | false | false | false | "watching the session's run; takes over if it is cut off" |
  | `by == "session"`, state `stale`, `session_alive` is None | false | false | false | false | true (`confirm_closed` required) | "the session that started this run is unknown; confirm it is closed to take over" |
  | no run, no supervisor | false | false | false | false | false | null |

  The reason `"no supervisor is running; start one with python3 bin/sdd-supervise.py"` is deleted (FR-007).
- `python3 bin/sdd-control.py stop --project <dir> [--now]` makes the same request as `POST …/run/stop`:
  - on success it prints `{"contract", "ok": true, "id"}` and exits 0;
  - on a refusal it prints `{"contract", "error"}` with the same text as the route's 409, and exits 1.
- The new skill `skills/sdd-stop/SKILL.md`:
  1. Call `python3 <helper> state`.
  2. If `by` is `supervisor` or `job`: call the `sdd-control.py stop` command above and report its output.
  3. If `by` is `session` and `session.pid` equals this session's `$PPID`:
     - with `--now`: stop the workflow task named in `run.json` `workflow_dir` with the session's task-stop tool,
       keep `run.json`, and say how to resume (`/sdd-run`);
     - without `--now`: say exactly "Stopping after the current task is not available for runs started in a
       session yet. Use /sdd-stop --now, or let it finish."
  4. If `by` is `session` and the pid differs: say "This run belongs to another Claude session (pid N, started
     HH:MM). Stop it there." and do nothing.
  5. If the state is `none`: "No run is going."

## 6. Server state (FR-002, FR-003, FR-005, FR-009b)

- Folder: `$XDG_STATE_HOME/speckit-agents/server/`, or `~/.local/state/speckit-agents/server/` when that variable
  is unset.
- `server.json`: `{"pid", "started", "port", "contract", "engine_version"}`.
  - Written under `flock` on `server.lock` at start. Removed on a clean exit.
  - At start, if the file names a live pid whose start time matches `started`: print
    `speckit-agents server already running at http://127.0.0.1:<port>` and exit 0.
- `projects.json`: `{"contract": "1.0", "projects": [{"id", "path", "name", "added"}]}`.
  - `path` is `Path.resolve()`.
  - `id` is the first 12 hex characters of the sha256 of `path`.
  - `name` is the folder's base name; when two projects share a base name, the parent's base name is added:
    `"app (private)"`.
  - Adding a path already listed returns the existing entry.
- A project is **missing** when its path no longer exists, or has no `.sdd/config.toml`. It stays listed until it
  is removed.
- Rediscovery at start, and every 0.5 s in the watcher (research R6), per project:
  - `run_state()` (§4);
  - `controls()` (§5);
  - the open Job (005 `sdd-job.py`);
  - `attach.json` (§7).
  Nothing is held only in memory that a restart would lose: every request the server makes goes to a file an
  engine process reads.

## 7. Attachments and hand-over (FR-010; research R5)

- `attach.json` in `state_dir(project)`:
  `{"contract", "run_id", "session": {pid, started} | null, "state", "since", "pid"}`.
  - `pid` is the watcher's own pid.
  - `state` is one of `watching | taken_over | closed | confirm_needed`.
- `POST …/run/adopt {confirm_closed?: bool}`:
  - It is refused (409) when `controls().adopt` is false, or when an `attach.json` with a live watcher pid exists.
  - Otherwise it starts `bin/sdd-supervise.py --project <dir> --adopt <run_id>` detached, through
    `sdd-launch.py adopt`, the same way `launch()` starts supervisors.
  - With `confirm_closed: true` the launcher passes `--confirmed-closed`.
- Supervisor `--adopt RUN_ID` loop, every `--poll` seconds (default 15):
  1. `run.json` is missing, or names another `run_id`: write `state: closed`, then
     `write_status(state="done", reason="run closed by its session")`, delete `attach.json`, and exit 0.
  2. `session_alive(run)` is False, or `--confirmed-closed` was given: write `state: taken_over`, then enter the
     normal cycle loop with `--run-args` taken from `run.json` (`phases`, and `job` when present). The cycle's
     `/sdd-run --yes` resumes from the journal (003 FR-034 acceptance 4).
  3. `session_alive(run)` is None and `--confirmed-closed` was not given: write `state: confirm_needed` and keep
     polling.
  4. Otherwise: `state: watching`.
- The watch ends after `--max-wait-hours` (default 24) with `state: closed`, `reason: "watched too long"`.
- A take-over waits for a slot (§8) before its first cycle, shown as "Waiting for a free place".
- At most one attachment per project. `controls()` reports `adopt: false` while one is live.

## 8. Slots across projects (FR-009a; research R7)

- Folder: `$XDG_STATE_HOME/speckit-agents/slots/`. Settings in `…/server/settings.json`: `{"max_parallel": 2}`.
  - The minimum is 1. A missing file or a bad value means 2.
  - The UI edits this file through `POST /api/settings`.
- New `bin/sdd-slots.py`:
  - `take(project, run_id) -> bool`, under `flock` on `slots/.lock`:
    1. delete slot files whose `pid` is dead;
    2. count the live slot files, plus the in-session runs of listed projects whose `session_alive` is True (read
       `projects.json`, then each project's `run.json`);
    3. if the count is below `max_parallel`, write `slots/<project id>.json` `{"pid", "run_id", "since"}` and
       return True;
    4. otherwise return False.
  - `free(project)` deletes that file if its `pid` is this process.
  - `holders() -> list` returns the live holders for refusal texts.
- The supervisor takes a slot before every cycle (a plain run, a Job run or a take-over) and frees it after:
  - When the take fails and the cycle was started by the person (the first cycle of `sdd-launch.py launch` or
    `launch_job`), exit 4 with `status.json` `{"state": "stopped", "reason": "cap reached: <holders>"}`.
    `POST /run/start` and `POST /jobs/start` check `take` themselves first and answer 409 with the same text, so
    this exit is only a race guard.
  - When the cycle starts by itself (a Job's next run, a take-over), write `status.json` `{"state": "queued",
    "since"}` and retry every `--poll` seconds. Queued supervisors get a free slot in order of `since`: the lowest
    `since` among live queued `status.json` files gets it first, which `take` checks.

## 9. UI (FR-020 to FR-023; research R4)

- Source: `ui/`, with `package.json`, `package-lock.json`, `tsconfig.json`, `vite.config.ts` and `src/`.
  - React 19, TypeScript, Vite. No other runtime dependency without a `[dep]` task.
  - `npm ci && npm run build` writes `server/static/`. The server serves `server/static/index.html` for every
    non-`/api/` path (client-side routing) and the files under `server/static/assets/` with long cache headers.
- The committed `server/static/` must equal a fresh build:
  - `tests/test_ui_build.py` runs the build into a temp folder and compares file hashes;
  - it is skipped with a one-line reason when `node` is not on `PATH`, unless `SDD_UI_BUILD=1`.
- Routes in the page: `/` (project list), `/p/<project id>/<view>`. The views are `overview | tasks | runs | jobs
  | waiting | decisions | docs`.
- Live updates:
  - one `EventSource('/api/stream')`. Each event is `{"project": id, "topic": "tasks|run|runs|jobs|blocked|docs|server", "at"}`;
  - on an event the page refetches only that topic's API for that project and redraws it in place (React state),
    keeping scroll position and open panels;
  - on a disconnected stream it retries every 2 s and shows "Reconnecting" in the header.
- Phone width: every view at 375 px wide has no horizontal scroll. A browser test checks
  `document.documentElement.scrollWidth <= innerWidth` for each view.

## 10. Folder browser (FR-003a)

- `GET /api/fs/list?path=<abs>` → `{"path", "parent", "entries": [{"name", "path", "sdd": bool, "listed": bool}]}`.
  - Folders only, sorted by name. Hidden folders (a leading `.`) are left out unless `&hidden=1`.
  - `sdd` means the folder has `.sdd/config.toml`. `listed` means it is in `projects.json`.
  - At most 500 entries; beyond that, `"truncated": true`.
- `path` defaults to `Path.home()`. A `path` whose `resolve()` is not inside `Path.home().resolve()` → 403
  `{"error": "outside your home folder"}`. Symlinks are resolved before that check.
- It is a GET, but it passes the same origin and Host checks as the state-changing routes (003 FR-030, data-model
  §4.7, §4.9), so another website cannot read folder names.
- `POST /api/projects/add {"path"}`:
  - checks the folder is inside home and is a git repository; otherwise 422 `{"error": "not a git repository"}`;
  - with `.sdd/config.toml` it adds the project and answers 200 with the entry;
  - without it, it answers 200 `{"needs_setup": true, "path", "plan": <sdd-setup.py detect output>}` and adds
    nothing. The UI then shows the setup prompt (§12).

## 11. Version warnings (FR-019)

- `GET /api/server` returns `{"contract", "engine": {"plugin_version", "contract", "path"} | null,
  "installed_plugin_version": str | null, "warnings": [...]}`.
  - `installed_plugin_version` comes from `claude plugin list --json`, read once at start and every 10 minutes; null
    when `claude` is not on `PATH`.
- Warnings are computed per pair:
  - server ↔ engine copy;
  - engine copy ↔ the installed plugin (an older copy: "the engine copy is older than the installed plugin; start
    a Claude session to refresh it");
  - server ↔ each project's `run.json`, `status.json`, `attach.json` and Job file.
- Each warning is `{"what", "ours", "theirs", "level": "minor|major", "project"?}`.
- On any `major` warning that involves a project (or the engine copy, which involves all projects), every write
  route for the affected projects answers 409 `{"error": "version mismatch: <what> <ours> vs <theirs>"}`. The UI
  disables the matching buttons and shows that text. Reads keep working.
- No engine copy → `engine: null`, a major warning "no engine copy: start a Claude session with the plugin
  installed", and every route except `GET /api/server` and `GET /api/projects` answers 503 with that text.

## 12. Project setup (FR-025; research R12)

New `bin/sdd-setup.py`. Each command takes `--project <dir>`, prints JSON with `contract`, and writes nothing
unless it is `apply`.

- `detect` → `{"speckit": bool, "specify_cli": bool, "candidates": [{"command": [argv], "source", "why"}],
  "caches": [{"tool", "path"}], "files": [{"path", "action": "create|merge|append|unchanged", "diff"}]}`.
  Detection rules, in this order, without duplicates:

  | Found | Candidates |
  |---|---|
  | `pyproject.toml` with `[tool.pytest…]`, or a `tests/` folder with `test_*.py` | `uv run pytest -q` if `uv.lock` exists, else `python3 -m pytest -q`; `python3 -m unittest discover -s tests` if no pytest config |
  | `pyproject.toml` with `[tool.mypy]` / `[tool.ruff]` | `uv run mypy .` / `uv run ruff check .` (or `python3 -m …` without `uv.lock`) |
  | `package.json` scripts `test`, `lint`, `typecheck` | `npm test`, `npm run lint`, `npm run typecheck`; `pnpm` when `pnpm-lock.yaml` exists |
  | `Makefile` / `justfile` targets `test`, `lint`, `check` | `make <target>` / `just <target>` |

  Caches: `uv cache dir`, `npm config get cache`, `pnpm store path`, each run only when the tool is on `PATH`;
  the home folder is written as `~`.

- `check --command <json argv>` runs one command from the project root (no shell) with a 10-minute timeout, and
  prints `{"exit", "seconds", "output"}`. `output` is the last 200 lines.
- `apply --checks <json list of argv> [--commit]` does what `/sdd-init` steps 2 to 5 write:
  - `.sdd/config.toml` from `templates/sdd-config.toml`, with `[checks] commands`;
  - the tasks-template override with the `## Agent tags` section;
  - `.claude/settings.json` merged by union, never removing anything, plus `Bash(<command> *)` allow rules and
    the caches under `sandbox.filesystem.allowWrite`;
  - the five `.gitignore` lines.

  With `--commit`, it commits exactly those paths with `chore: set up speckit-agents`. That is refused on a
  protected branch (`main`/`master` are allowed here, because this is the person's own setup commit, made with their
  confirmation, and not a run's).

  It prints `{"written": [paths], "commit": sha | null}`. If `.sdd/config.toml` already exists, it refuses.
- Server routes:
  - `POST /api/setup/detect {"path"}`;
  - `POST /api/setup/check {"path", "command"}`;
  - `POST /api/setup/apply {"path", "checks", "commit"}`, which then adds the project.

  All are under the request checks. A path outside home → 403.
- The `/sdd-init` skill keeps step 2's search for check commands, which uses judgement, and then calls `detect`,
  `check` and `apply`, so the files are always written by the same code.
- Without Spec Kit (`speckit: false`), the prompt offers `specify init --here --ai claude` (run as an argument list)
  when `specify_cli` is true, and otherwise shows the install link. Setup goes on only once `.specify/` exists.

## 13. Plugin install and update (FR-026, FR-027; research R11)

- Server settings (`settings.json`, §8) gain `plugin_source`. The default is the server clone's own root folder,
  which is the marketplace.
- `GET /api/plugin` → `{"installed": bool, "version", "installPath", "contract", "needed": "ok|install|update|server_older", "reason"}`.
  - The data comes from `claude plugin list --json`, cached for 60 s.
  - `needed` is decided by comparing the plugin's contract (read from `<installPath>/bin/sdd-tasks.py` `CONTRACT`
    through the engine copy, or `VERSION.json`) with the server's: an older major means `update`, a newer major
    means `server_older`.
- `POST /api/plugin/install` and `POST /api/plugin/update` (body `{}`):
  - refused (409) while any listed project has a live run, attachment or queued supervisor;
  - otherwise they run the commands in research R11 as argument lists, each with a 5-minute timeout, and answer
    `{"ok", "output", "version"}` or `409 {"error", "output"}`;
  - after success they run `sdd-engine.py install --from <installPath>`;
  - when the output asks for `--accept-command`, they answer `409 {"confirm_command", "sha256"}`, and a repeat
    with `{"accept_command": "<sha256>"}` passes it.
- The UI shows a plugin badge in the header: Installed 0.8.0 / Install / Update / Update the server. After an
  install or update it says "Open Claude sessions keep the old version until they are restarted."
