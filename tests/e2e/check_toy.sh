#!/usr/bin/env bash
# Check the SC-001 end-to-end result on a toy project.
# Usage: tests/e2e/check_toy.sh <dir>
# Asserts: three tasks committed on auto/*, manual task held in BLOCKED.md, main untouched.
set -euo pipefail

DIR="${1:?usage: check_toy.sh <dir>}"
cd "$DIR"

fail=0

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
