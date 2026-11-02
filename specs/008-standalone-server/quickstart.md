# Quickstart: validate the standalone server (008)

Prerequisites:
- Python 3 and `git`.
- The plugin installed at user scope.
- For §7 only: Node through the Artifactory registry (research R4).
- Toy projects come from the fixture builder in `tests/test_sdd_dashboard.py`. A real `claude` is needed only in §5
  and §8; everything else uses `tests/fake/claude`.

## §1 Engine copy (US3)
1. `python3 bin/sdd-engine.py install --from .` → `installed: true`; `~/.local/share/speckit-agents/engine/current/VERSION.json`
   names the `plugin.json` version.
2. Run it again → `installed: false`.
3. Start an interactive Claude session in any folder; `copied_at` changes only if the plugin version changed.
4. Record whether `session.start` also fired under `claude -p` (research R3).

## §2 Server and projects (US1)
1. Run `sdd-server` (or `python3 server/sdd-server.py`) → it prints the address. Run it a second time → it prints
   "already running at …" and exits 0.
2. Click Load project, browse from home (folders set up for the plugin are marked), and add two toy projects; one
   by browsing, one by pasting its path.
3. Restart the server → both are still listed. Move one toy folder away → it shows as missing, with Remove from list.
4. Try `/etc` in the path box → "outside your home folder".

## §3 One view of every run (US2)
1. Toy project with a leftover `status.json` `{"state": "done", "pid": <dead>}`. Start an in-session run (fake
   `claude`) → the UI says "Running in a Claude session" with Supervise this run, and no Stop or Resume buttons.
2. `python3 bin/sdd-tasks.py state --project <toy>` → `state: running`, `by: session`.

## §4 Versions (US3)
1. Edit the copy's `VERSION.json` to `"contract": "1.9"` → a minor warning; buttons work.
2. `"contract": "2.0"` → a major warning; every write button is disabled with the reason; reads work.
3. Remove the `current` symlink → every view says "no engine copy: start a Claude session …".

## §5 Hand-over (US4), with a real `claude` session
1. In a toy project, start `/sdd-run` in an interactive session. In the UI, click Supervise this run → "Watching".
2. Quit the session while a task runs → within one poll (15 s) the banner shows "Taken over after a cut-off"; the
   run finishes; no task is committed twice (`git log`).
3. Repeat, but let the run finish in the session → the attachment ends "closed" and nothing new starts.

## §6 `/sdd-stop` (US5)
1. Supervised toy run: `/sdd-stop` writes the same `control.json` as Stop after current task (compare the files).
2. Stop the server, then `/sdd-stop --now` → the run stops.
3. In-session run, from another session: `/sdd-stop` names the owning session and does nothing.

## §7 UI (US6)
1. `cd ui && npm ci && npm run build` → `server/static/` matches the committed build (`tests/test_ui_build.py`).
2. `SDD_UI_TESTS=1 python3 -m unittest discover -s tests/server_ui` → all pass, including 375 px width checks and
   "event reaches the page within 2 s".

## §8 Restart and the cap (FR-009a, FR-009b)
1. Start a supervised run in toy A and a Job in toy B; restart the server → both show with their controls within
   10 s and finish.
2. With `max_parallel` 2 and both running, Start run in toy C → refused, naming A and B. Let B's Job reach its next
   run while C would also wait → it shows "Waiting for a free place" and starts when A finishes.

## §9 Setup and the plugin (US7)
1. A toy git repo with Spec Kit and a `tests/test_x.py`, no `.sdd/`: Load project → the setup prompt lists
   `python3 -m unittest discover -s tests`. Run checks → pass. Confirm with "Commit the setup" → one commit
   `chore: set up speckit-agents` holding exactly the setup files; the project opens; `/sdd-run` passes preflight.
2. A repo without Spec Kit → the prompt offers `specify init` (or the install link).
3. With a test `HOME`, so nothing real changes: the plugin is missing → Install plugin → `claude plugin list --json`
   shows it, and the engine copy matches. While a toy run is going, Install and Update are disabled with the reason.
4. Engine copy with `"contract": "2.0"` against a server on `1.0` → "update the server"; no plugin button.
