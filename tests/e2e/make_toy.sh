#!/usr/bin/env bash
# Build a toy Spec Kit project fixture for the SC-001 end-to-end check.
# Usage: tests/e2e/make_toy.sh <dir>
# Creates a toy project with two [P] tasks, one [manual] task and one dependent task.
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

echo "$DIR"
