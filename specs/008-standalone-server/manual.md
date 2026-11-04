# Manual steps: Standalone server (008)

Step-by-step instructions for the `[manual]` tasks in `tasks.md`. The dashboard shows each section on its task's card
under "Waiting on you". A section with a `Record:` line saves what you write in the card's note box under that heading,
when you press **I did it**, in the same commit as the tick.

## T001 Prove that a headless run can name its session (research R2)

What it proves: inside `claude -p`, Bash calls see `CLAUDE_PID` and `CLAUDECODE`, and `CLAUDE_PID` is the `claude -p`
process. The server needs that to tell whether a run's session is still alive. Takes about a minute.

1. Open your own terminal app (Terminal or iTerm), **not** Claude Code. A run's sandbox would block it.
2. Paste this whole block and press Enter. It makes a throwaway git repo with a one-step skill and runs it headless:

   ```sh
   repo=$(mktemp -d "${TMPDIR:-/tmp}/r2-proof.XXXXXX") && cd "$repo" && git init -q && mkdir -p .claude/skills/env
   printf '%s\n' '---' 'name: env' 'description: Print the environment of Bash calls.' '---' \
     '1. Run `env` as one Bash call and print its output verbatim.' \
     '2. If CLAUDE_PID is set, run `ps -o command= -p <that pid>` and print its output as a line PS=<output>.' \
     'Do nothing else.' > .claude/skills/env/SKILL.md
   out=$(env -u CLAUDECODE -u CLAUDE_PID -u CLAUDE_CODE_SESSION_ID \
     claude -p "/env" --permission-mode dontAsk --allowedTools "Skill,Bash(env),Bash(ps:*)")
   printf '%s\n' "$out" | grep -E '^(CLAUDECODE|CLAUDE_PID|CLAUDE_CODE_SESSION_ID|PS)=' || echo "none set"
   ```

3. Look at what it printed:
   - `CLAUDECODE=1` and `CLAUDE_PID=<number>` → both are set.
   - `PS=` followed by a command containing `claude -p` → `CLAUDE_PID` is the `claude -p` process.
   - `none set` → neither is set. That matters too. Record it.
4. Copy the printed lines into this card's note box.
5. Press **I did it** (not while a run is going). It saves the lines under `## R2 evidence (headless)` in
   `research.md` and ticks the task, in one commit.

Record: research.md ## R2 evidence (headless)

## T002 Prove that the plugin can be updated and installed without a terminal (research R11)

What it proves: `claude plugin update` and `claude plugin install` work with no terminal attached (`< /dev/null`), so
the server's Install and Update buttons can call them. This **changes your Claude Code setup**: it reinstalls the
speckit-agents plugin at user scope. That is harmless, and open sessions keep the old copy until restarted.

1. Open your own terminal app, **not** Claude Code.
2. Paste this block and press Enter:

   ```sh
   claude plugin update speckit-agents@speckit-agents < /dev/null; echo "update exit code: $?"
   claude plugin install speckit-agents@speckit-agents --scope user --json < /dev/null; echo "install exit code: $?"
   ```

3. Look at what it printed. Each command prints some output, then an `exit code:` line. `0` means it worked. Anything
   else, or a prompt that waited for you to type, means it failed or needs a terminal.
4. Copy everything the block printed into this card's note box. Add one line at the end: "they need a terminal" or
   "they do not need a terminal".
5. Press **I did it**. It saves your note under `## R11 evidence` in `research.md` and ticks the task.

Record: research.md ## R11 evidence

## T047 Look at the new UI yourself

Comes after T046 (the new UI is built). It covers what tests cannot judge.

1. In a terminal, start the server: `sdd-server` (or the command the README gives). Open the URL it prints.
2. Open this repo, then one other project (for example `~/private/sdd-toy`), with **Load project**.
3. Visit every view in the nav for each project: Roadmap, Overview, Tasks, Jobs, Documents, Decisions, Status.
4. For each view, note anything that looks wrong: layout, colors, spacing, wording, something hard to find. Take a
   screenshot of each problem.
5. In this card's note box, write one line per finding ("Roadmap: the bar overlaps the title on a phone") and paste
   the screenshots into the box (up to 5; the new UI accepts pictures, FR-032). If you found nothing, write
   "nothing to change".
6. Press **I did it**. Your note and pictures are saved under `## UI review` in `research.md`. Claude turns each
   finding into a task at the end of `tasks.md`.

Record: research.md ## UI review

## T048 Reinstall the plugin and check the engine copy

Comes after T010 (the plugin refreshes its engine copy at session start).

1. Make sure the work is committed (ask Claude, or check that the dashboard shows no run going).
2. In your own terminal: `claude plugin marketplace update speckit-agents`, then
   `claude plugin update speckit-agents@speckit-agents`.
3. Start a new interactive `claude` session in any folder, then quit it.
4. Run `ls -l ~/.local/share/speckit-agents/engine/current/VERSION.json; cat ~/.local/share/speckit-agents/engine/current/VERSION.json`.
5. Run `claude -p "hi"` once, then run the command from step 4 again.
6. Paste everything steps 4 and 5 printed into this card's note box and press **I did it**. It saves the output under
   `## R3 evidence (hook)` in `research.md`, and Claude reads the result from it.

Record: research.md ## R3 evidence (hook)

## T050 Point specs 004 and 006 at the new server

Runs cannot edit `spec.md` files, so this one is yours. Or ask Claude to do it in a normal session.

1. Open `specs/004-feedback-chat/spec.md` and `specs/006-spec-cycle/spec.md`.
2. At the top of each, under the title, add this line:
   `> Built on 008's server and UI (specs/008-standalone-server/); routes are under /api/p/<id>/…; views are React components in ui/.`
3. Commit both files (`git commit -m "docs: point 004 and 006 at 008"`), or ask Claude to.
4. Press **I did it** on this card.

## T052 Hand-over with a real session (quickstart §5)

Comes after T051.

1. Follow quickstart.md §5 step by step, in a toy project, with a real `claude` session.
2. For each of its three steps, note whether you saw the expected result.
3. Write that in this card's note box, one line per step, and press **I did it**. It saves the note under
   `## Validation` in `research.md`.

Record: research.md ## Validation

## T053 Install the plugin from the server (quickstart §9 item 3)

Comes after T051. It uses a test `HOME`, so your real setup does not change.

1. Follow quickstart.md §9 item 3.
2. Check that `claude plugin list --json` (with the same test `HOME`) shows the plugin, and that Install and Update are
   greyed out with a reason while a toy run is going.
3. Write what you saw in this card's note box and press **I did it**. It saves the note under `## Validation` in
   `research.md`.

Record: research.md ## Validation

## T054 Decide that the new UI replaces the old one

Comes after T047 and T060.

1. Check that the tests pass (ask Claude, or look at the last run report in the Jobs view).
2. Check that T047 found nothing blocking.
3. If both hold, press **I did it**. If not, write in the note what is still missing and press **Skip**, or leave it open.
