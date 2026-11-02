# Research: Standalone server (008)

All findings dated 2026-11-02, checked on this machine (macOS, Claude Code with the plugin installed at user scope)
unless marked as coming from the docs.

## R1. Where the code splits: the plugin owns formats, the server owns the HTTP side and the UI

- **Decision**: The **engine** is every script in `bin/` except `bin/sdd-dashboard.py`. That includes
  `sdd-supervise.py` and `sdd-launch.py`, plus new modules this feature adds there. The **server** is new code in
  `server/` (HTTP, push stream, project list, folder browser, version checks) and `ui/` (TypeScript source, built
  into `server/static/`). The server imports only the engine copy (R3), never `bin/` in the repo.
- **Rationale**: The supervisor and the launcher are deterministic, standard-library Python that reads and writes the
  formats the plugin defines (`status.json`, `control.json`, `.sdd/run.json`, Jobs). Keeping them in the engine
  means one version of every format. It also lets `/sdd-stop` and `/sdd-status` work with no server running
  (FR-014, FR-015). The server still owns orchestration in the person's sense: it decides when to start, stop,
  resume and attach, and it starts the engine's processes. The engine does not move, so the constitution's
  Principle V reference to `bin/sdd-tasks.py` stays true.
- **Alternatives**: (a) moving the supervisor and launcher into `server/`: then `/sdd-stop` without a server would
  need a second writer of `control.json`, which is the drift FR-017 forbids; (b) a separate repo for the server:
  contract changes would then span two repos and two commits, so one repo with two install paths was kept.

## R2. The session behind `/sdd-run` can be named, and the name survives a reused pid

- **Evidence**: In a Bash tool call of an interactive session, `$PPID` is the `claude` process itself:
  `ps -o pid,ppid,lstart,command -p $PPID` printed `26834 26583 Mon 2 Nov 11:48:03 2026 claude`. Its parent was the
  login shell.
- **Decision**: `/sdd-run` records `session: {pid, started}` in `.sdd/run.json`. `pid` is `$PPID` of its Bash call.
  `started` is that process's start time from `ps -o lstart= -p <pid>`, parsed to epoch seconds. A session is alive
  when the pid exists **and** its start time still matches, so a reused pid is not mistaken for the session.
- **Open, proven by a task**: that `$PPID` is also the `claude` process under `claude -p` (expected, untested), and
  that `--permission-mode dontAsk` lets the skill run `python3 <helper> session --pid $PPID` (a variable in a single
  command; earlier refusals were for pipes, `;` and loops). If either fails, the fallback is US4 acceptance 4.

## R3. The engine copy is refreshed by the plugin at session start

- **Docs (via the claude-code-guide agent)**: The plugin's hooks module (a "mod") can register `session.start`. It
  fires once per loaded mod before the first prompt (https://code.claude.com/docs/en/plugins/mods/reference.md).
  `$.plugin.root` is the plugin's install folder. There is no `$.plugin.version`, so the version is read from
  `.claude-plugin/plugin.json`. No install or update lifecycle hook exists. It is not documented whether
  `session.start` fires in `claude -p` (to be tested). Under managed settings, hooks in an installed mod may not run.
- **Evidence**: `claude plugin list --json` lists `speckit-agents@speckit-agents` with `version: "0.1.0"` and
  `installPath: ~/.claude/plugins/cache/speckit-agents/speckit-agents/0.1.0`. The version has never been bumped.
- **Decision**:
  - `session.start` in `hooks/register.ts` runs `python3 <plugin root>/bin/sdd-engine.py install`. That copies
    `bin/` into `~/.local/share/speckit-agents/engine/<plugin version>/` and points the `current` symlink at it in
    one atomic step (data-model §2), but only when the version differs from the one installed.
  - The same command runs at the start of `/sdd-init` and `/sdd-run`, as a fallback for setups where the hook does
    not fire.
  - The server never copies the engine itself. It reports a missing copy (US3 acceptance 4), and it flags a stale
    one by comparing the copy's version with `claude plugin list --json` when that command is on `PATH`.
- **Decision (versions)**: `plugin.json` `version` becomes meaningful and is bumped by every release that changes
  the engine. This feature starts it at `0.8.0`. The **contract version** is separate (`CONTRACT = "1.0"`, data-model
  §1): it changes only when an exchanged format changes, a major bump for an incompatible change.

## R4. The UI toolchain installs through Artifactory

- **Evidence**: With a throwaway `.npmrc` pointing at `https://artifactory.workday.com/artifactory/api/npm/npm-virtual/`
  (credentials from the env file, never printed, the file deleted after), `npm view` resolved `react` 19.3.1 and
  `svelte` 5.57.3. `npm install vite typescript svelte @sveltejs/vite-plugin-svelte` added 41 packages in 25 s:
  `vite/8.3.5`, `tsc` 7.0.2. Node is v24.13.0 and npm 11.6.2 (through fnm).
- **Decision**: React 19 + TypeScript + Vite. Svelte would make a smaller bundle, but React has far more examples a
  cheaper implementer model has seen, which matters for the architect/developer split. `ui/package-lock.json` pins
  exact versions, and `npm ci` is the only install command the tasks use. A dependency is never added by a run (it is
  `[dep]`, so a person approves it).
- **Decision (built files)**: The built UI (`server/static/`) is committed, so using the server needs no Node
  (FR-020). A test rebuilds it and fails when the committed build does not match the source (data-model §9).

## R5. Runs already survive the process that started them

- **Evidence**: `bin/sdd-launch.py` starts the supervisor with `start_new_session=True` and output to `launch.log`,
  and the supervisor starts `claude -p` the same way. Both keep their state in `state_dir(project)` files
  (`status.json`, `control.json`).
- **Decision**: FR-009b needs no new process model. On start, the server rebuilds its view of every listed project
  from those files (data-model §6). Attachments are watched by a detached `sdd-supervise.py --adopt` process, not
  inside the server, so they survive a server restart the same way.

## R6. Live updates without polling every 3 seconds

- **Decision**: The server keeps one watcher thread. Every 0.5 s it reads each open project's watched files
  (`tasks.md`, `BLOCKED.md`, `.sdd/run.json`, the run's journal and agent files, the state dir) by size and modify
  time. On a change it pushes an event on `GET /api/stream` (server-sent events). The UI then fetches only what
  changed. Polling files is fine at this size, and it keeps the server standard-library only (no `watchdog`
  package). A change reaches the page within about 1 s, inside SC-006's 2 s.
- **Alternatives**: WebSocket (needs a library or a hand-written framing layer); OS file events (`kqueue`, `inotify`:
  not portable in the standard library).

## R7. The cap across projects is enforced where runs start

- **Decision**: Slots live in `~/.local/state/speckit-agents/slots/`, one file per running supervised cycle. The
  supervisor takes a slot before each cycle and frees it after, under an `fcntl.flock` lock on `slots/.lock`. A dead
  holder's slot counts as free. Live in-session runs in listed projects (R2) are counted too.
- **Why in the supervisor**: Jobs start their next run without the server (005). If the server enforced the cap
  alone, a Job could go over it while the server is down.
- **Alternatives**: a broker inside the server (fails FR-009b); one global lock (that is a cap of 1).

## R8. One server per person

- **Decision**: `~/.local/state/speckit-agents/server/server.json` holds `{pid, port, started, contract}`, written
  under `flock`. A start that finds a live pid with a matching start time prints the address and exits 0 (FR-002).
  The default port is 8765; `--port 0` picks a free one. Binding to a busy port without `--port` tries the next 10
  ports.

## R9. What happens to the old dashboard

- **Decision**: `bin/sdd-dashboard.py` and `tests/ui/` stay until the new UI passes the ported browser tests
  (FR-021). Then they are removed in one task, and CLAUDE.md's launch line changes. Until then both can run side by
  side on different ports. They read the same files, and only one at a time should be used to act.

## R10. The server is installed on its own (user, 2026-11-02)

- **Decision**: The server is installed and updated independently of the plugin (FR-027). Proposed: a git clone of
  this repository. `python3 server/sdd-server.py --install-command` writes `~/.local/bin/sdd-server` (first line
  `# written by sdd-server --install-command`; another file there is never overwritten), and that runs the server
  from the clone. Updating the server is `git pull --ff-only` in the clone, plus a restart. The engine copy holds
  `bin/` only. The person confirmed the git clone (2026-11-02).
- **Rationale**: The person asked for the server to stand on its own, and for the server to install and update
  the plugin (R11), not the other way round.
- **Consequence**: server and engine versions can drift, so the data-model §11 warnings are a normal case. When the
  server is older, the warning says to update the server (FR-027). When the plugin is older, it offers Update plugin.

## R11. The server can install and update the plugin with the `claude` command

- **Evidence**: `claude plugin --help` lists `install|i [options] <plugin>` (with `--scope user|project|local`,
  `--marketplace <source>`, `--json`), `update <plugin>` ("Update a plugin to the latest version (restart required to
  apply)"), `marketplace add <source>` and `marketplace update [name]`. `claude plugin list --json` gives
  `installPath` and `version`.
- **Decision**: Install is `claude plugin marketplace add <source>`, done only when `claude plugin marketplace list`
  lacks it, followed by `claude plugin install speckit-agents@speckit-agents --scope user --json`. Update is
  `claude plugin marketplace update speckit-agents`, followed by `claude plugin update speckit-agents@speckit-agents`.
  After either one: `sdd-engine.py install --from <installPath>`, run with the engine found at `installPath`. These
  are run as argument lists, without a shell. `<source>` is a server setting (`plugin_source`; default: the folder
  of the server's own clone, which is also the marketplace).
- **Limits**: `update` goes only to the latest version; no way to install an older version was found. So when the
  server is the older side, the server is updated, not the plugin. A marketplace that declares a command source asks
  for `--accept-command <sha256>`; the server shows that command and its hash to the person and passes the flag only
  after they confirm.
- **To prove (task)**: that both commands work with no terminal attached, and what they print with `--json`.

## R12. Project setup without a model

- **Evidence**: `skills/sdd-init/SKILL.md` has six steps. Steps 1, 3, 5 and most of 4 are mechanical: Spec Kit is
  present, the tasks template section, the ignore lines, and merging `templates/settings.json`. Two parts use
  judgement or Claude itself:
  - step 2 *finds* the project's check commands by reading its files;
  - step 4 re-runs the checks inside the sandbox, which only a Claude session has.
- **Decision**: a new engine command `bin/sdd-setup.py` does the mechanical steps (data-model §12), and the
  `/sdd-init` skill calls it. Check commands come from fixed detection rules, which the person reviews and edits.
  The server runs each chosen command outside the sandbox and shows the result. The sandbox re-check moves to the
  first run's preflight, which already runs the checks and names the missing `allowWrite` entry. `.claude/settings.json`
  is written by the server after the person approves the diff in the UI; Claude Code's write protection applies to
  Claude sessions, not to the server.
- **Alternatives**: running `claude -p "/sdd-init"` from the server: a model in the setup path for no gain, and the
  write to `.claude/settings.json` would be refused headless anyway.
