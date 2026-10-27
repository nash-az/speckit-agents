#!/usr/bin/env bash
# Build a toy Spec Kit project fixture for the SC-001 end-to-end check.
# Usage: tests/e2e/make_toy.sh <dir>
# Creates a toy project with two [P] tasks, one [manual] task and one dependent task,
# plus the quickstart.md §1 task-state fixture in <dir>/state/.
set -euo pipefail

DIR="${1:?usage: make_toy.sh <dir>}"
rm -rf "$DIR"
mkdir -p "$DIR/src" "$DIR/tests" "$DIR/.specify/templates"

cat > "$DIR/tasks.md" <<'EOF'
# Tasks: Toy project

## Phase 1: Setup

- [ ] T001 [P] Add a greet function in `src/greet.py`
- [ ] T002 [P] Add a subtract function in `src/sub.py`

## Phase 2: Foundational

- [ ] T003 [manual] Record a demo video of the CLI
- [ ] T004 Depends on T001 in `src/cli.py`
EOF

cat > "$DIR/src/greet.py" <<'EOF'
def greet(name):
    return f"Hello, {name}!"
EOF

cat > "$DIR/src/sub.py" <<'EOF'
def subtract(a, b):
    return a - b
EOF

cat > "$DIR/tests/test_greet.py" <<'EOF'
from src.greet import greet


def test_greet():
    assert greet("world") == "Hello, world!"
EOF

cat > "$DIR/tests/test_sub.py" <<'EOF'
from src.sub import subtract


def test_subtract():
    assert subtract(5, 3) == 2
EOF

cat > "$DIR/pyproject.toml" <<'EOF'
[project]
name = "toy"
version = "0.1.0"

[tool.pytest.ini_options]
testpaths = ["tests"]
EOF

# Task-state fixture (quickstart.md §1), kept in its own folder so the run's tasks.md is unchanged:
# a Convergence phase supersedes every open task before it, which would hold T001–T004.
# check_toy.sh runs `sdd-tasks.py plan` on it and asserts each id's state.
STATE="$DIR/state"
mkdir -p "$STATE/.sdd"

cat > "$STATE/tasks.md" <<'EOF'
# Tasks: Toy project (task-state fixture)

## Phase 8: Older work

- [ ] T020 [manual] Record the old demo by hand

## Phase 9: Convergence

- [ ] T010 [manual] Record a demo of the new CLI
- [ ] T011 Publish the demo after T010 in `docs/demo.md`
- [ ] T012 [dep] [dep:ok] Add the approved `requests` dependency in `pyproject.toml`
- [ ] T013 Pick the output format in `src/fmt.py`
- [ ] T014 Wire the admin export in `src/export.py`
- [ ] T090 Finish the demo work per T020 in `docs/old-demo.md`
EOF

cat > "$STATE/BLOCKED.md" <<'EOF'
# Blocked tasks

## T013
- status: resolved
- tag: decision
- reason: the output format is not in the spec
- question: Option A (JSON) or option B (CSV)?
- answer: use option B

## T014
- status: moved
- tag: decision
- reason: the export belongs to the admin product
- question: Does the export stay in this project?
- answer: no, it moved to the admin product
EOF

cat > "$STATE/.sdd/config.toml" <<'EOF'
[allow]
dependencies = false
downloads = false
EOF

echo "$DIR"
