#!/usr/bin/env python3
"""Tests for bin/sdd-dashboard.py (D301).

Starts the dashboard server on a test port, creates sample .sdd/runs/ data
and status.json, then asserts the API endpoints return correct data.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

PORT = 19765
PROJECT = Path("/tmp/speckit-dashboard-test")


def wait_for(url, timeout=10):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(0.2)
    raise last


def api(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5) as r:
        return json.loads(r.read())


def apiText(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5) as r:
        return r.read().decode()


def main():
    if PROJECT.exists():
        shutil.rmtree(PROJECT)
    (PROJECT / ".sdd" / "runs").mkdir(parents=True)

    # Sample project files
    (PROJECT / "spec.md").write_text("# Feature Spec\n\n## User Story 1\n\nDo something.\n")
    (PROJECT / "plan.md").write_text("# Plan\n\n- [ ] T101 Design\n- [ ] T102 Implement\n")
    (PROJECT / "tasks.md").write_text("# Tasks\n\n## Phase 1\n\n- [x] T001 [P] First task in `a.py`\n- [ ] T002 [manual] Needs a person in `b.py`\n")
    (PROJECT / "BLOCKED.md").write_text("## T002\n- status: open\n- reason: needs recording\n- question: Who will record?\n")
    (PROJECT / "decisions.md").write_text("# Decisions\n\n- T011: Use Playwright (2026-10-24)\n")
    (PROJECT / ".sdd" / "config.toml").write_text("[run]\nfeature = \"specs/001\"\n")

    # Sample events.jsonl for run-001
    events = [
        {"ts": 1000, "kind": "phase.started", "id": "1", "title": "Phase 1", "taskCount": 2},
        {"ts": 1001, "kind": "task.started", "id": "T1", "tags": ["P"], "files": ["a.py"], "deps": []},
        {"ts": 1002, "kind": "task.implemented", "id": "T1", "attempt": 0, "summary": "done", "files": ["a.py"]},
        {"ts": 1003, "kind": "task.verified", "id": "T1", "verdict": "approve", "problems": []},
        {"ts": 1004, "kind": "task.approved", "id": "T1", "files": ["a.py"]},
        {"ts": 1005, "kind": "task.started", "id": "T2", "tags": ["manual"], "files": ["b.py"], "deps": []},
        {"ts": 1006, "kind": "task.blocked", "id": "T2", "tag": "manual", "reason": "needs a person"},
        {"ts": 1007, "kind": "run.finished", "run_id": "run-001", "stopped": None, "approved": 1, "blocked": 1, "skipped": 0},
    ]
    (PROJECT / ".sdd" / "runs" / "run-001.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    (PROJECT / ".sdd" / "runs" / "run-001.md").write_text("# Run Report\n\n- approved: T1\n- blocked: T2\n")

    # Sample status.json (same slug computation as the server)
    resolved = str(PROJECT.resolve())
    slug = re.sub(r"[^A-Za-z0-9]+", "-", resolved).strip("-")
    state_dir = Path(os.path.expanduser("~/.local/state/speckit-agents")) / slug
    state_dir.mkdir(parents=True, exist_ok=True)
    status = {
        "state": "running",
        "cycle": 1,
        "usage": {
            "five_hour": {"utilization": 0.87, "resets_at": int(time.time()) + 3600},
            "seven_day": {"utilization": 0.45, "resets_at": int(time.time()) + 86400},
        },
    }
    (state_dir / "status.json").write_text(json.dumps(status, indent=2))

    proc = subprocess.Popen([sys.executable, "bin/sdd-dashboard.py", "--project", str(PROJECT), "--port", str(PORT)])
    try:
        wait_for(f"http://127.0.0.1:{PORT}/")

        # /api/runs
        runs = api("/api/runs")
        assert len(runs) == 1, f"expected 1 run, got {len(runs)}"
        r = runs[0]
        assert r["id"] == "run-001", f"expected run-001, got {r['id']}"
        assert r["approved"] == 1, f"expected 1 approved, got {r['approved']}"
        assert r["blocked"] == 1, f"expected 1 blocked, got {r['blocked']}"
        assert r["finished"] is True
        assert r["stopped"] is None
        print("PASS: /api/runs")

        # /api/runs/<id>/events
        evs = api("/api/runs/run-001/events")
        assert len(evs) == 8, f"expected 8 events, got {len(evs)}"
        assert evs[0]["kind"] == "phase.started"
        assert evs[-1]["kind"] == "run.finished"
        print("PASS: /api/runs/run-001/events")

        # /api/runs/<id>/report
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/runs/run-001/report")
        with urllib.request.urlopen(req, timeout=5) as resp:
            report = resp.read().decode()
        assert "# Run Report" in report
        assert "approved: T1" in report
        print("PASS: /api/runs/run-001/report")

        # /api/status
        st = api("/api/status")
        assert st is not None
        assert st["state"] == "running"
        assert st["cycle"] == 1
        assert st["usage"]["five_hour"]["utilization"] == 0.87
        print("PASS: /api/status")

        # /api/project/spec
        spec = apiText("/api/project/spec")
        assert "# Feature Spec" in spec
        print("PASS: /api/project/spec")

        # /api/project/plan
        plan = apiText("/api/project/plan")
        assert "# Plan" in plan
        print("PASS: /api/project/plan")

        # /api/project/tasks
        tasks = api("/api/project/tasks")
        assert tasks is not None
        assert "phases" in tasks
        assert len(tasks["phases"]) == 1
        assert tasks["phases"][0]["title"] == "Phase 1"
        assert len(tasks["phases"][0]["tasks"]) == 2
        assert tasks["phases"][0]["tasks"][0]["id"] == "T001"
        assert tasks["phases"][0]["tasks"][0]["done"] is True
        assert tasks["phases"][0]["tasks"][1]["id"] == "T002"
        assert tasks["phases"][0]["tasks"][1]["done"] is False
        assert "manual" in tasks["phases"][0]["tasks"][1]["tags"]
        print("PASS: /api/project/tasks")

        # /api/project/blocked
        blocked = api("/api/project/blocked")
        assert len(blocked["entries"]) == 1
        assert blocked["entries"][0]["id"] == "T002"
        assert blocked["entries"][0]["status"] == "open"
        print("PASS: /api/project/blocked")

        # /api/project/decisions
        decisions = apiText("/api/project/decisions")
        assert "# Decisions" in decisions
        print("PASS: /api/project/decisions")

        # /api/project/config
        config = apiText("/api/project/config")
        assert "[run]" in config
        print("PASS: /api/project/config")

        # /api/runs/<id>/report not found
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/runs/nonexistent/report", timeout=5)
            assert False, "expected 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
        print("PASS: 404 for missing report")

        print("\nAll dashboard tests passed.")
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        shutil.rmtree(PROJECT, ignore_errors=True)
        shutil.rmtree(state_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
