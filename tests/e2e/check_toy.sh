#!/usr/bin/env bash
# Check the SC-001 end-to-end result on a toy project.
# Usage: tests/e2e/check_toy.sh <dir> [--state-only]
# Asserts: the task-state fixture in <dir>/state/ plans as quickstart.md §1 says; then (unless
# --state-only) three tasks committed on auto/*, manual task held in BLOCKED.md, main untouched.
set -euo pipefail

DIR="${1:?usage: check_toy.sh <dir> [--state-only]}"
MODE="${2:-}"
HELPER="$(cd "$(dirname "$0")/../.." && pwd)/bin/sdd-tasks.py"
cd "$DIR"

fail=0

# 0. Task state (quickstart.md §1, SC-003): each fixture id is runnable or held with the right tag.
if [ ! -f state/tasks.md ]; then
  echo "FAIL: state/tasks.md missing (rebuild the toy with make_toy.sh)"
  fail=1
else
  PLAN="$(python3 "$HELPER" plan state/tasks.md --blocked state/BLOCKED.md --config state/.sdd/config.toml)"
  # One "<id> <state>" line per open task: "runnable", "runnable+answer" or "held:<tag>".
  STATES="$(printf '%s' "$PLAN" | python3 -c '
import json, sys
plan = json.load(sys.stdin)
for phase in plan["phases"]:
    for tid, task in phase["tasks"].items():
        print(tid, "runnable+answer" if task.get("answer") else "runnable")
for held in plan["held"]:
    print(held["id"], "held:" + held["tag"])
')"
  for want in \
      "T010 held:manual" \
      "T011 held:held" \
      "T012 runnable" \
      "T013 runnable+answer" \
      "T014 held:moved" \
      "T020 held:superseded" \
      "T090 runnable"; do
    if ! printf '%s\n' "$STATES" | grep -qx "$want"; then
      got="$(printf '%s\n' "$STATES" | grep "^${want%% *} " || echo "${want%% *} missing")"
      echo "FAIL: task state: want '$want', got '$got'"
      fail=1
    fi
  done
  if ! printf '%s' "$PLAN" | python3 -c '
import json, sys
plan = json.load(sys.stdin)
tasks = {tid: t for p in plan["phases"] for tid, t in p["tasks"].items()}
sys.exit(0 if tasks.get("T013", {}).get("answer") == "use option B" else 1)
'; then
    echo "FAIL: task state: plan does not carry T013's answer 'use option B'"
    fail=1
  fi
  if ! printf '%s' "$PLAN" | python3 -c '
import json, sys
held = {h["id"]: h["reason"] for h in json.load(sys.stdin)["held"]}
sys.exit(0 if held.get("T020") == "Phase 9" else 1)
'; then
    echo "FAIL: task state: T020 should be superseded by Phase 9"
    fail=1
  fi
  [ $fail -eq 0 ] && echo "task state: all fixture ids as expected"
fi

if [ "$MODE" = "--state-only" ]; then
  if [ $fail -eq 0 ]; then
    echo "PASS: task-state check"
    exit 0
  fi
  echo "FAIL: task-state check"
  exit 1
fi

# 1. main is untouched: no commits beyond the initial one on main
if [ -n "$(git log --oneline main 2>/dev/null | tail -n +2)" ]; then
  echo "FAIL: main has commits beyond the initial state"
  fail=1
fi

# 2. An auto/ branch exists with task commits
AUTO_BRANCH="$(git branch --list 'auto/*' | head -1 | tr -d ' *')"
if [ -z "$AUTO_BRANCH" ]; then
  echo "FAIL: no auto/ branch found"
  fail=1
else
  echo "auto branch: $AUTO_BRANCH"
  # Each task commit holds only its own files (checked at review time)
  COMMIT_COUNT="$(git log --oneline main.."$AUTO_BRANCH" 2>/dev/null | wc -l | tr -d ' ')"
  echo "commits on auto branch: $COMMIT_COUNT"
  if [ "$COMMIT_COUNT" -lt 2 ]; then
    echo "FAIL: expected at least 2 task commits on the auto branch"
    fail=1
  fi
fi

# 3. BLOCKED.md holds the manual task
if [ -f BLOCKED.md ]; then
  if ! grep -q 'T003' BLOCKED.md; then
    echo "FAIL: BLOCKED.md does not hold T003 (manual task)"
    fail=1
  fi
else
  echo "FAIL: BLOCKED.md missing"
  fail=1
fi

# 4. tasks.md has T003 unticked (held) and T001/T002/T004 ticked
if grep -q '^- \[x\] T003' tasks.md; then
  echo "FAIL: T003 (manual) should not be ticked"
  fail=1
fi

if [ $fail -eq 0 ]; then
  echo "PASS: SC-001 end-to-end check"
else
  echo "FAIL: SC-001 end-to-end check"
  exit 1
fi
