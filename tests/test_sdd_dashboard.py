#!/usr/bin/env python3
"""Tests for bin/sdd-dashboard.py (D301).

The unittest classes cover task status mapping and phase rollup (data-model.md §1, §2) and the
tasks/blocked endpoints; `unittest discover` runs them. The `main()` script below
starts the dashboard server on a test port, creates sample .sdd/runs/ data
and status.json, then asserts the API endpoints return correct data.
"""

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from unittest import mock
import urllib.error
from pathlib import Path

PORT = 19765
PROJECT = Path("/tmp/speckit-dashboard-test")

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("sdd_dashboard", HERE.parent / "bin" / "sdd-dashboard.py")
dash = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dash)

STATUS_TASKS = """# Tasks

## Phase 1: Setup

- [x] T001 Done already in `a.py`
- [ ] T002 [manual] Record audio
- [ ] T003 [decision] Pick a database
- [ ] T004 [dep] Add a package
- [ ] T005 [download] Fetch the model
- [ ] T006 Needs work after T002
- [ ] T007 Ran and blocked in `b.py`
- [ ] T008 Dropped in `c.py`
- [ ] T009 Moved elsewhere in `d.py`
- [ ] T010 Answered and runnable in `e.py`
- [ ] T011 Old approach, superseded-by T012
- [ ] T012 New approach in `f.py`
- [ ] T013 [dep] [dep:ok] Approved package
"""

STATUS_BLOCKED = """# Blocked tasks

## T007
- status: open
- tag: failed
- reason: checks failed twice
- question: Which API should it call?
- answer:

## T008
- status: wontfix
- reason: not needed

## T009
- status: moved
- reason: product work

## T010
- status: open
- question: Old question?

## T010
- status: resolved
- question: Which port?
- answer: Use 8080.
  Not 80.
"""


def write_project(root, tasks, blocked=None, config=None):
    (root / ".sdd").mkdir(parents=True, exist_ok=True)
    (root / "tasks.md").write_text(tasks, encoding="utf-8")
    if blocked is not None:
        (root / "BLOCKED.md").write_text(blocked, encoding="utf-8")
    if config is not None:
        (root / ".sdd" / "config.toml").write_text(config, encoding="utf-8")


class TaskStatusTest(unittest.TestCase):
    """data-model.md §2: each sdd-tasks.py hold maps to one status."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_project(self.root, STATUS_TASKS, STATUS_BLOCKED)
        self.result = dash.project_tasks(self.root / "tasks.md", self.root / "BLOCKED.md",
                                         self.root / ".sdd" / "config.toml")
        self.tasks = {t["id"]: t for p in self.result["phases"] for t in p["tasks"]}

    def tearDown(self):
        self.tmp.cleanup()

    def assertStatus(self, task_id, status, tag, question=None, answer=None):
        task = self.tasks[task_id]
        self.assertEqual((task["status"], task["status_tag"]), (status, tag), task_id)
        self.assertEqual(task["question"], question, task_id)
        self.assertEqual(task["answer"], answer, task_id)

    def test_shape(self):
        task = self.tasks["T002"]
        for key in ("id", "text", "tags", "parallel", "story", "files", "deps", "superseded_by",
                    "done", "line", "status", "status_tag", "status_reason", "question", "answer"):
            self.assertIn(key, task)
        self.assertEqual(self.result["text"], STATUS_TASKS)
        phase = self.result["phases"][0]
        self.assertEqual((phase["id"], phase["title"]), ("1", "Setup"))
        self.assertIn("status", phase)

    def test_done(self):
        self.assertStatus("T001", "done", None)
        self.assertIsNone(self.tasks["T001"]["status_reason"])

    def test_person_tags_are_held(self):
        for task_id, tag in (("T002", "manual"), ("T003", "decision"), ("T004", "dep"), ("T005", "download")):
            self.assertStatus(task_id, "held", tag)
            self.assertEqual(self.tasks[task_id]["status_reason"], dash.sdd_tasks.HOLD_TAGS[tag])

    def test_propagated_hold_is_held_by_dependency(self):
        self.assertStatus("T006", "held", "dependency")
        self.assertIn("T002", self.tasks["T006"]["status_reason"])

    def test_open_entry_is_blocked_with_question(self):
        self.assertStatus("T007", "blocked", "open", question="Which API should it call?")
        self.assertEqual(self.tasks["T007"]["status_reason"], "open entry in BLOCKED.md")

    def test_wontfix_and_moved(self):
        self.assertStatus("T008", "wontfix", "wontfix")
        self.assertStatus("T009", "moved", "moved")

    def test_resolved_entry_is_runnable_with_answer(self):
        self.assertStatus("T010", None, None, answer="Use 8080.\n  Not 80.")
        self.assertIsNone(self.tasks["T010"]["status_reason"])

    def test_explicit_superseded_names_successor(self):
        self.assertStatus("T011", "superseded", "T012")
        self.assertStatus("T012", None, None)

    def test_dep_ok_tag_is_runnable(self):
        self.assertStatus("T013", None, None)

    def test_allowed_dependencies_are_runnable(self):
        write_project(self.root, STATUS_TASKS, STATUS_BLOCKED, "[allow]\ndependencies = true\n")
        result = dash.project_tasks(self.root / "tasks.md", self.root / "BLOCKED.md",
                                    self.root / ".sdd" / "config.toml")
        tasks = {t["id"]: t for p in result["phases"] for t in p["tasks"]}
        self.assertIsNone(tasks["T004"]["status"])
        self.assertEqual(tasks["T005"]["status"], "held")

    def test_convergence_supersedes_older_phases(self):
        write_project(self.root, "# Tasks\n\n## Phase 1: Old\n\n- [ ] T020 [manual] Old work\n"
                                 "- [x] T021 Finished\n\n## Phase 2: Convergence\n\n- [ ] T090 Redo per T020\n")
        result = dash.project_tasks(self.root / "tasks.md", None, None)
        tasks = {t["id"]: t for p in result["phases"] for t in p["tasks"]}
        self.assertEqual((tasks["T020"]["status"], tasks["T020"]["status_tag"]), ("superseded", "Phase 2"))
        self.assertEqual(tasks["T021"]["status"], "done")
        self.assertIsNone(tasks["T090"]["status"])
        # superseded plus done reads done: dropped statuses rank after done (T062 answer (b))
        self.assertEqual([p["status"] for p in result["phases"]], ["done", "queued"])

    def test_missing_tasks_file(self):
        self.assertEqual(dash.project_tasks(self.root / "nope.md", None, None), {"text": "", "phases": []})
        self.assertEqual(dash.project_tasks(None, None, None), {"text": "", "phases": []})

    def test_phase_rollup_over_tasks(self):
        self.assertEqual(self.result["phases"][0]["status"], "blocked")


class RollupTest(unittest.TestCase):
    """data-model.md §1: the first key of the priority list that any task has."""

    ORDER = ["blocked", "retrying", "running", "held", "paused", "queued",
             "done", "moved", "wontfix", "superseded"]

    def test_order_is_exact(self):
        self.assertEqual(list(dash.ROLLUP_ORDER), self.ORDER)

    def test_each_key_beats_every_later_key(self):
        for i, key in enumerate(self.ORDER):
            for later in self.ORDER[i + 1:]:
                self.assertEqual(dash.rollup([later, key, later]), key, (key, later))

    def test_single_status(self):
        for key in self.ORDER:
            self.assertEqual(dash.rollup([key]), key)

    def test_open_runnable_task_counts_as_queued(self):
        self.assertEqual(dash.rollup(["done", None]), "queued")
        self.assertEqual(dash.rollup([None, "held"]), "held")

    def test_no_tasks_has_no_status(self):
        self.assertIsNone(dash.rollup([]))

    def test_all_done(self):
        self.assertEqual(dash.rollup(["done", "done"]), "done")

    def test_done_plus_dropped_reads_done(self):
        """T062 answer (b): dropped tasks do not keep a finished phase from reading done."""
        for dropped in ("wontfix", "moved", "superseded"):
            self.assertEqual(dash.rollup(["done", dropped, "done"]), "done", dropped)
        self.assertEqual(dash.rollup(["wontfix", "superseded"]), "wontfix")


class BlockedEndpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_project(self.root, STATUS_TASKS, STATUS_BLOCKED)

    def tearDown(self):
        self.tmp.cleanup()

    def test_entries_from_parse_blocked_entries(self):
        result = dash.project_blocked(self.root / "BLOCKED.md")
        self.assertEqual(result["text"], STATUS_BLOCKED)
        by_id = {e["id"]: e for e in result["entries"]}
        self.assertEqual([e["id"] for e in result["entries"]], ["T007", "T008", "T009", "T010"])
        self.assertEqual(by_id["T007"], {"id": "T007", "status": "open", "tag": "failed",
                                         "reason": "checks failed twice",
                                         "question": "Which API should it call?", "answer": ""})
        self.assertEqual(by_id["T010"]["status"], "resolved")  # the last entry per id wins
        self.assertEqual(by_id["T010"]["question"], "Which port?")

    def test_missing_file(self):
        self.assertEqual(dash.project_blocked(self.root / "missing.md"), {"text": "", "entries": []})
        self.assertEqual(dash.project_blocked(None), {"text": "", "entries": []})

    def test_parsers_removed_from_dashboard(self):
        self.assertFalse(hasattr(dash, "parse_tasks"))
        self.assertFalse(hasattr(dash, "parse_blocked"))


class HandlerTest(unittest.TestCase):
    """The two endpoints through the request handler, without a socket (the sandbox may forbid
    binding a port)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_project(self.root / "specs" / "001-only", STATUS_TASKS, STATUS_BLOCKED)
        (self.root / ".sdd").mkdir()
        (self.root / ".sdd" / "config.toml").write_text("[run]\nmax_retries = 2\n", encoding="utf-8")
        self.saved =(dash.DashboardHandler.project, dash.DashboardHandler.feature)
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.root, None

    def tearDown(self):
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.saved
        self.tmp.cleanup()

    def get(self, path):
        return request(self, "GET", path)

    def test_tasks_endpoint(self):
        data = self.get("/api/project/tasks")
        tasks = {t["id"]: t for p in data["phases"] for t in p["tasks"]}
        self.assertEqual(tasks["T002"]["status"], "held")
        self.assertEqual(tasks["T007"]["question"], "Which API should it call?")
        self.assertEqual(data["phases"][0]["status"], "blocked")

    def test_blocked_endpoint(self):
        data = self.get("/api/project/blocked")
        self.assertEqual({e["id"] for e in data["entries"]}, {"T007", "T008", "T009", "T010"})


RUN_JOURNAL = [
    {"type": "launched"},
    {"type": "started", "key": "v2:01", "agentId": "a01", "label": "impl:T1", "phase": "Build"},
    {"type": "result", "key": "v2:01", "agentId": "a01",
     "result": {"status": "done", "summary": "done", "files_changed": ["a.py"]}},
    {"type": "started", "key": "v2:02", "agentId": "a02", "label": "verify:T1", "phase": "Build"},
    {"type": "result", "key": "v2:02", "agentId": "a02",
     "result": {"verdict": "approve", "problems": [], "checks_run": []}},
    {"type": "started", "key": "v2:03", "agentId": "a03", "label": "impl:T2", "phase": "Build"},
    {"type": "result", "key": "v2:03", "agentId": "a03",
     "result": {"status": "blocked", "summary": "", "files_changed": [], "tag": "manual",
                "reason": "needs a person"}},
]


def write_run(project, run_id, workflow_dirs):
    """`.sdd/runs/<run_id>/workflows.json` listing `workflow_dirs` (data-model.md §3.9)."""
    run_dir = project / ".sdd" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "workflows.json").write_text(
        json.dumps({"workflow_dirs": [str(d) for d in workflow_dirs]}), encoding="utf-8")


def write_workflow_dir(path, journal=RUN_JOURNAL):
    path.mkdir(parents=True, exist_ok=True)
    (path / "journal.jsonl").write_text("\n".join(json.dumps(e) for e in journal) + "\n",
                                        encoding="utf-8")
    return path


class RunsTest(unittest.TestCase):
    """`/api/runs` and `/api/runs/<id>/events` from run directories (data-model.md §4.4)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "project"
        (self.root / ".sdd" / "runs").mkdir(parents=True)
        self.wf = write_workflow_dir(Path(self.tmp.name).resolve() / "transcripts" / "wf_test")
        write_run(self.root, "20261025-1200", [self.wf])
        self.saved = (dash.DashboardHandler.project, dash.DashboardHandler.feature)
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.root, None

    def tearDown(self):
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.saved
        self.tmp.cleanup()

    def test_run_directory_listed_with_counts(self):
        runs = request(self, "GET", "/api/runs")
        self.assertEqual([r["id"] for r in runs], ["20261025-1200"])
        run = runs[0]
        self.assertEqual((run["approved"], run["blocked"], run["events_count"]), (1, 1, 6))
        self.assertEqual(set(run), {"id", "events_count", "approved", "blocked", "interrupted",
                                    "finished", "stopped", "tasks", "feature", "job"})
        self.assertIsNone(run["job"])
        self.assertFalse(run["finished"])
        (self.root / ".sdd" / "runs" / "20261025-1200.md").write_text("# Report\n", encoding="utf-8")
        self.assertTrue(request(self, "GET", "/api/runs")[0]["finished"])

    def test_run_tasks_outcomes_and_commits(self):
        ev = lambda kind, i, **f: dict({"ts": f.pop("ts", "2026-10-25T10:00:00Z"), "kind": kind, "id": i}, **f)
        events = [ev("task.started", "T1", attempt=1, ts="2026-10-25T10:00:00Z"), ev("task.approved", "T1"),
                  {"ts": "2026-10-25T10:05:00Z", "kind": "group.finished", "ids": ["T1"], "checks_passed": True, "commits": ["abc1234"]},
                  ev("task.started", "T2", attempt=1), ev("task.verified", "T2", verdict="reject"),
                  ev("task.started", "T3", attempt=1), ev("task.blocked", "T3"),
                  ev("task.started", "T4", attempt=2)]
        got = {t["id"]: (t["outcome"], t["commits"], t["attempts"]) for t in dash.run_tasks(events, finished=True)}
        self.assertEqual(got, {"T1": ("committed", ["abc1234"], 1), "T2": ("rejected", [], 1),
                               "T3": ("blocked", [], 1), "T4": ("interrupted", [], 2)})
        self.assertEqual(dash.run_tasks(events, finished=False)[3]["outcome"], "running")
        self.assertEqual([t["id"] for t in dash.run_tasks(events, True)], ["T1", "T2", "T3", "T4"])

    def test_run_feature_from_listing_or_spec_dates(self):
        for name, day in (("001-a", "2026-10-01"), ("002-b", "2026-10-20"), ("003-c", "2026-10-30")):
            (self.root / "specs" / name).mkdir(parents=True)
            (self.root / "specs" / name / "spec.md").write_text(f"# S\n\n**Created**: {day}\n", encoding="utf-8")
        self.assertEqual(dash.run_feature(self.root, "20261025-1200", {"workflow_dirs": []}), "002-b")
        self.assertEqual(dash.run_feature(self.root, "20261030-0900", {}), "003-c")
        self.assertEqual(dash.run_feature(self.root, "20261025-1200", {"feature": "001-a"}), "001-a")
        self.assertIsNone(dash.run_feature(self.root, "20260101-0000", {}))
        index = request(self, "GET", "/api/runs/tasks")
        self.assertEqual(index, {})  # the active feature is 003-c, the run belongs to 002-b
        dash.DashboardHandler.feature = "002-b"
        index = request(self, "GET", "/api/runs/tasks")
        self.assertTrue(index)
        self.assertTrue(all(v[0]["run"] == "20261025-1200" for v in index.values()))

    def test_sorted_by_name_descending_not_mtime(self):
        write_run(self.root, "20261026-0900", [self.wf])
        write_run(self.root, "20261024-0800", [self.wf])  # written last: newest mtime
        runs = request(self, "GET", "/api/runs")
        self.assertEqual([r["id"] for r in runs], ["20261026-0900", "20261025-1200", "20261024-0800"])

    def test_flat_jsonl_and_dirs_without_workflows_json_ignored(self):
        runs_dir = self.root / ".sdd" / "runs"
        (runs_dir / "x.jsonl").write_text('{"kind": "task.approved", "id": "T9"}\n', encoding="utf-8")
        (runs_dir / "20261027-0000").mkdir()  # no workflows.json
        (runs_dir / "20261027-0000.md").write_text("# Report\n", encoding="utf-8")
        self.assertEqual([r["id"] for r in request(self, "GET", "/api/runs")], ["20261025-1200"])

    def test_events_from_journal(self):
        data = request(self, "GET", "/api/runs/20261025-1200/events")
        self.assertEqual(data["missing"], [])
        self.assertNotIn("notice", data)
        self.assertEqual(data["workflow_dirs"], [str(self.wf)])
        kinds = [e["kind"] for e in data["events"]]
        self.assertEqual(kinds, ["task.started", "task.implemented", "task.verified",
                                 "task.approved", "task.started", "task.blocked"])

    def test_bad_run_id_is_404(self):
        for bad in ("run-001", "..", "..%2F..%2Fetc", "20261025-1200X"):
            request(self, "GET", f"/api/runs/{bad}/events", status=404)

    def test_gone_transcript_shows_text(self):
        write_run(self.root, "20261020-0800", [Path(self.tmp.name).resolve() / "gone" / "wf_gone"])
        data = request(self, "GET", "/api/runs/20261020-0800/events")
        self.assertEqual(data["events"], [])
        self.assertEqual(len(data["missing"]), 1)
        self.assertEqual(data["notice"],
                         "This run's transcript is gone (Claude Code keeps transcripts for "
                         "`cleanupPeriodDays`, default 30); its report is `.sdd/runs/20261020-0800.md`.")
        html = dash.build_html()
        self.assertIn("data.notice", html)

    def test_no_workflows_json_gives_empty_events(self):
        data = request(self, "GET", "/api/runs/20261027-0000/events")
        self.assertEqual((data["workflow_dirs"], data["events"]), ([], []))
        self.assertNotIn("notice", data)


class CurrentRunTest(unittest.TestCase):
    """`/api/run/current` (data-model.md §4.5, FR-017): each of the five rules, in order."""

    RUN_ID = "20261025-1200"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.root = base / "project"
        (self.root / ".sdd" / "runs").mkdir(parents=True)
        self.wf = write_workflow_dir(base / "transcripts" / "wf_current")
        (self.wf / "agent-a01.jsonl").write_text("{}\n", encoding="utf-8")
        self.set_age(self.wf / "journal.jsonl", 0)
        self.set_age(self.wf / "agent-a01.jsonl", 0)
        self.saved = (dash.DashboardHandler.project, dash.DashboardHandler.feature)
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.root, None
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(base / "state")})
        self.env.start()
        self.status_file = dash.logs_dir(self.root) / "status.json"

    def tearDown(self):
        self.env.stop()
        (dash.DashboardHandler.project, dash.DashboardHandler.feature) = self.saved
        self.tmp.cleanup()

    @staticmethod
    def set_age(path, minutes):
        t = time.time() - minutes * 60
        os.utime(path, (t, t))

    def write_run_json(self, workflow_dir=None):
        data = {"run_id": self.RUN_ID, "workflow_dir": str(workflow_dir or self.wf)}
        (self.root / ".sdd" / "run.json").write_text(json.dumps(data), encoding="utf-8")

    def write_status(self, state):
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        self.status_file.write_text(json.dumps({"state": state}), encoding="utf-8")

    def current(self):
        """The response without `controls`, which is all-false here: status.json has no live pid
        (the controls table itself is RunControlTest's)."""
        data = request(self, "GET", "/api/run/current")
        self.assertEqual(data.pop("controls"), NO_CONTROLS)
        return data

    def test_none_without_run_json(self):
        self.assertEqual(self.current(), {"state": "none", "run_id": None, "workflow_dir": None})

    def test_paused_when_report_exists(self):
        self.write_run_json()
        self.set_age(self.wf / "journal.jsonl", 120)
        self.set_age(self.wf / "agent-a01.jsonl", 120)
        (self.root / ".sdd" / "runs" / (self.RUN_ID + ".md")).write_text("# Report\n", encoding="utf-8")
        self.assertEqual(self.current(), {"state": "paused", "run_id": self.RUN_ID,
                                          "workflow_dir": str(self.wf)})

    def test_report_beats_recent_activity(self):
        self.write_run_json()
        (self.root / ".sdd" / "runs" / (self.RUN_ID + ".md")).write_text("# Report\n", encoding="utf-8")
        self.assertEqual(self.current()["state"], "paused")

    def test_paused_when_supervisor_not_stopped(self):
        self.write_run_json()
        self.set_age(self.wf / "journal.jsonl", 120)
        self.set_age(self.wf / "agent-a01.jsonl", 120)
        for state in ("waiting", "running", "held"):
            self.write_status(state)
            self.assertEqual(self.current()["state"], "paused", state)

    def test_live_supervisor_running_is_running(self):
        self.write_run_json()
        self.set_age(self.wf / "journal.jsonl", 120)
        self.set_age(self.wf / "agent-a01.jsonl", 120)
        for state, want in (("running", "running"), ("stopping", "running"), ("held", "paused"), ("waiting", "paused")):
            self.status_file.write_text(json.dumps({"state": state, "pid": os.getpid()}), encoding="utf-8")
            data = request(self, "GET", "/api/run/current")
            self.assertEqual(data["state"], want, state)

    def test_stopped_supervisor_does_not_pause(self):
        self.write_run_json()
        self.write_status("stopped")
        self.assertEqual(self.current()["state"], "running")
        self.set_age(self.wf / "journal.jsonl", 120)
        self.set_age(self.wf / "agent-a01.jsonl", 120)
        self.assertEqual(self.current()["state"], "stale")

    def test_running_when_journal_recent(self):
        self.write_run_json()
        self.set_age(self.wf / "journal.jsonl", 29)
        self.set_age(self.wf / "agent-a01.jsonl", 120)
        self.assertEqual(self.current(), {"state": "running", "run_id": self.RUN_ID,
                                          "workflow_dir": str(self.wf)})

    def test_running_when_agent_file_recent(self):
        self.write_run_json()
        self.set_age(self.wf / "journal.jsonl", 120)
        self.set_age(self.wf / "agent-a01.jsonl", 5)
        self.assertEqual(self.current()["state"], "running")

    def test_stale_when_nothing_changed_for_30_minutes(self):
        self.write_run_json()
        self.set_age(self.wf / "journal.jsonl", 31)
        self.set_age(self.wf / "agent-a01.jsonl", 31)
        (self.wf / "notes.txt").write_text("not an agent file\n", encoding="utf-8")  # recent, ignored
        self.assertEqual(self.current(), {"state": "stale", "run_id": self.RUN_ID,
                                          "workflow_dir": str(self.wf)})

    def test_stale_when_workflow_dir_invalid(self):
        bad = Path(self.tmp.name).resolve() / "transcripts" / "not-a-wf"
        bad.mkdir(parents=True)
        (bad / "journal.jsonl").write_text("{}\n", encoding="utf-8")  # recent, but the name is invalid
        self.write_run_json(bad)
        self.assertEqual(self.current()["state"], "stale")
        self.write_run_json(Path(self.tmp.name).resolve() / "gone" / "wf_gone")
        self.assertEqual(self.current()["state"], "stale")

    def test_stale_when_run_json_unreadable(self):
        (self.root / ".sdd" / "run.json").write_text("not json", encoding="utf-8")
        self.assertEqual(self.current(), {"state": "stale", "run_id": None, "workflow_dir": None})

    def test_banner_text_in_page(self):
        html = dash.build_html()
        self.assertIn("/api/run/current", html)
        self.assertIn('id="stale-banner"', html)
        self.assertIn("run.state === 'stale'", html)
        self.assertIn("' left <code>.sdd/run.json</code> behind and is not running. ' +\n"
                      "        'Start <code>/sdd-run</code> to resume or clean it up.'", html)
        self.assertIn(".stale-banner { background: rgba(239,68,68,0.15); border: 1px solid var(--danger); "
                      "color: var(--danger);", html)

    def test_run_banner_in_page(self):
        """data-model.md §12.4 (FR-029, FR-030): the run banner's strings, routes and polling."""
        html = dash.build_html()
        self.assertIn('id="run-banner"', html)
        for text in ("Stop after current task", "Stop now", "Stopping after ", "Stopped by you", "Resume",
                     "Waiting for credits until ", "Keep paused", "Will stay paused after the reset at ",
                     "Let it resume"):
            self.assertIn(text, html)
        for command in ("'stop', 'after-task'", "'stop', 'now'", "'resume'", "'hold'", "'release'"):
            self.assertIn(command, html)
        self.assertIn("fetch('/api/run/' + command, {", html)
        self.assertIn("method: 'POST',", html)
        self.assertIn("headers: { 'Content-Type': 'application/json' },", html)
        self.assertIn("window.confirm(question)", html)
        self.assertIn("res.status === 202", html)
        self.assertIn("(data && data.error)", html)
        self.assertIn(' disabled title="', html)
        self.assertIn("c.reason", html)
        self.assertIn("setInterval(refreshAll, 3000)", html)
        # Every route the banner calls exists on the server.
        for command in ("stop", "resume", "hold", "release"):
            self.assertIn("/api/run/" + command, dash.CONTROL_ROUTES)


class TaskLogTest(unittest.TestCase):
    """`/api/runs/<id>/tasks/<task>/log` (data-model.md §4.6, FR-021) over a fixture transcript
    directory: the task's newest event in the last workflow directory names `agent-<id>.jsonl`."""

    RUN_ID = "20261025-1200"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.root = base / "project"
        (self.root / ".sdd" / "runs").mkdir(parents=True)
        # An older workflow directory where T1 ran as agent a99, then the current one (a01, a02).
        old_journal = [{"type": "started", "key": "v2:99", "agentId": "a99", "label": "impl:T1"}]
        self.old = write_workflow_dir(base / "transcripts" / "wf_old", old_journal)
        (self.old / "agent-a99.jsonl").write_text('{"old": true}\n', encoding="utf-8")
        self.wf = write_workflow_dir(base / "transcripts" / "wf_log")
        (self.wf / "agent-a01.jsonl").write_text('{"n": "impl"}\n', encoding="utf-8")
        (self.wf / "agent-a02.jsonl").write_text(
            "".join(json.dumps({"n": i}) + "\n" for i in range(250)), encoding="utf-8")
        write_run(self.root, self.RUN_ID, [self.old, self.wf])
        self.write_run_json(self.RUN_ID, self.wf)
        self.saved = (dash.DashboardHandler.project, dash.DashboardHandler.feature)
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.root, None

    def tearDown(self):
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.saved
        self.tmp.cleanup()

    def write_run_json(self, run_id, workflow_dir):
        data = {"run_id": run_id, "workflow_dir": str(workflow_dir)}
        (self.root / ".sdd" / "run.json").write_text(json.dumps(data), encoding="utf-8")

    def log(self, run_id=RUN_ID, task="T1"):
        return request(self, "GET", f"/api/runs/{run_id}/tasks/{task}/log")

    def test_newest_event_names_the_transcript(self):
        data = self.log()  # T1's newest event is verify:T1 (agent a02), the last 200 of 250 lines
        self.assertEqual(set(data), {"available", "label", "lines"})
        self.assertTrue(data["available"])
        self.assertEqual(data["label"], "verify:T1")
        self.assertEqual(len(data["lines"]), 200)
        self.assertEqual(data["lines"][0], json.dumps({"n": 50}))
        self.assertEqual(data["lines"][-1], json.dumps({"n": 249}))

    def test_task_with_only_an_implementer(self):
        (self.wf / "agent-a03.jsonl").write_text('{"n": "t2"}\n', encoding="utf-8")
        data = self.log(task="T2")
        self.assertEqual((data["available"], data["label"], data["lines"]),
                         (True, "impl:T2", ['{"n": "t2"}']))

    def test_long_lines_truncated(self):
        (self.wf / "agent-a02.jsonl").write_text("x" * 5000 + "\n", encoding="utf-8")
        self.assertEqual(self.log()["lines"], ["x" * 2000])

    def test_transcript_records_are_readable(self):
        records = [
            {"type": "api-request-blob", "message": {"role": "user", "content": [{"type": "text", "text": "noise"}]}},
            {"type": "attachment", "attachment": {"type": "sandbox_instructions"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": ""}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Running the tests."}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q", "description": "Run pytest"}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "content": "72 passed", "is_error": False}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "content": [{"type": "text", "text": "boom"}], "is_error": True}]}},
        ]
        (self.wf / "agent-a02.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records) + "not json\n", encoding="utf-8")
        self.assertEqual(self.log()["lines"], [
            "Running the tests.", "> Bash: Run pytest", "< 72 passed", "< error: boom", "not json"])

    def test_older_workflow_directory_is_ignored(self):
        write_run(self.root, self.RUN_ID, [self.wf, self.old])  # the last directory is now wf_old
        self.write_run_json(self.RUN_ID, self.wf)
        data = self.log(task="T2")  # T2 ran only in wf_log
        self.assertFalse(data["available"])

    def test_run_not_active(self):
        self.assertEqual(self.log(run_id="20261026-0900"),
                         {"available": False, "reason": "run is not active"})
        (self.root / ".sdd" / "run.json").unlink()
        self.assertEqual(self.log(), {"available": False, "reason": "run is not active"})

    def test_not_found_cases_are_200(self):
        missing = [
            self.log(task="T9"),  # no event for the task
            self.log(task="T3"),  # well-formed id, no event
        ]
        (self.wf / "agent-a02.jsonl").unlink()
        missing.append(self.log())  # transcript file gone
        self.write_run_json(self.RUN_ID, Path(self.tmp.name).resolve() / "gone" / "wf_gone")
        missing.append(self.log())  # workflow directory gone
        for data in missing:
            self.assertFalse(data["available"])
            self.assertIsInstance(data["reason"], str)
            self.assertEqual(set(data), {"available", "reason"})

    def test_ids_validated(self):
        for run_id, task in (("run-001", "T1"), ("..", "T1"), (self.RUN_ID, "X1"),
                             (self.RUN_ID, "T1x2"), (self.RUN_ID, "..")):
            data = self.log(run_id=run_id, task=task)
            self.assertFalse(data["available"], (run_id, task))


OWN_ORIGIN = "http://127.0.0.1:8765"  # the dashboard's own origin at DashboardHandler.port (8765)


def raw_request(method, path, body=None, host="127.0.0.1:8765", origin=OWN_ORIGIN,
                content_type="application/json", raw=None):
    """Run one request through DashboardHandler without a socket; returns (status, headers, body).
    `host=None`, `origin=None` and `content_type=None` leave that header out. Origin and
    Content-Type are sent only for POST (Origin also for OPTIONS). `raw` replaces the JSON body."""
    handler = dash.DashboardHandler.__new__(dash.DashboardHandler)
    handler.path, handler.command = path, method
    handler.request_version, handler.requestline = "HTTP/1.1", f"{method} {path} HTTP/1.1"
    handler.wfile = io.BytesIO()
    if raw is None:
        raw = json.dumps(body).encode() if body is not None else b""
    handler.rfile = io.BytesIO(raw)
    handler.headers = {"Content-Length": str(len(raw))}
    if host is not None:
        handler.headers["Host"] = host
    if method in ("POST", "OPTIONS") and origin is not None:
        handler.headers["Origin"] = origin
    if method == "POST" and content_type is not None:
        handler.headers["Content-Type"] = content_type
    {"GET": handler.do_GET, "POST": handler.do_POST, "OPTIONS": handler.do_OPTIONS}[method]()
    head, _, out = handler.wfile.getvalue().partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
    return int(lines[0].split()[1]), headers, out


def request(test, method, path, body=None, status=200, host="127.0.0.1:8765", **kw):
    """raw_request() that asserts the status; returns the parsed body (JSON when it parses, else text)."""
    code, _, out = raw_request(method, path, body, host=host, **kw)
    test.assertEqual(code, status, out)
    try:
        return json.loads(out)
    except ValueError:
        return out.decode()


class ActiveFeatureTest(unittest.TestCase):
    """data-model.md §4.2 (FR-027): config `run.feature`, then `.specify/feature.json`, then the
    only `specs/` folder, else 409 and the person chooses."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".sdd").mkdir()
        (self.root / ".specify").mkdir()
        for name in ("001-a", "002-b", "003-c"):
            (self.root / "specs" / name).mkdir(parents=True)
            (self.root / "specs" / name / "spec.md").write_text(f"# Spec {name}\n", encoding="utf-8")
        self.saved = (dash.DashboardHandler.project, dash.DashboardHandler.feature)
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.root, None

    def tearDown(self):
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.saved
        self.tmp.cleanup()

    def config(self, feature):
        (self.root / ".sdd" / "config.toml").write_text(f'[run]\nfeature = "{feature}"\n', encoding="utf-8")

    def feature_json(self, directory):
        (self.root / ".specify" / "feature.json").write_text(
            json.dumps({"feature_directory": directory}), encoding="utf-8")

    def test_config_wins(self):
        self.config("specs/001-a")
        self.feature_json("specs/002-b")
        self.assertEqual(dash.active_feature(self.root), self.root / "specs" / "001-a")
        self.assertEqual(request(self, "GET", "/api/project/spec"), "# Spec 001-a\n")

    def test_feature_json_when_config_empty(self):
        self.config("")
        self.feature_json("specs/002-b")
        self.assertEqual(dash.active_feature(self.root), self.root / "specs" / "002-b")
        self.assertEqual(request(self, "GET", "/api/project/spec"), "# Spec 002-b\n")

    def test_single_folder(self):
        for name in ("001-a", "003-c"):
            shutil.rmtree(self.root / "specs" / name)
        self.assertEqual(dash.active_feature(self.root), self.root / "specs" / "002-b")
        self.assertEqual(request(self, "GET", "/api/project/spec"), "# Spec 002-b\n")

    def test_409_with_several_folders(self):
        self.config("")
        self.assertIsNone(dash.active_feature(self.root))
        expected = {"error": "choose a feature", "features": ["001-a", "002-b", "003-c"]}
        for path in ("/api/project/spec", "/api/project/plan", "/api/project/tasks",
                     "/api/project/blocked", "/api/project/decisions"):
            self.assertEqual(request(self, "GET", path, status=409), expected, path)

    def test_config_is_not_per_feature(self):
        self.config("")
        self.assertIn("[run]", request(self, "GET", "/api/project/config"))

    def test_select_feature_in_memory(self):
        files = sorted(str(p) for p in self.root.rglob("*"))
        result = request(self, "POST", "/api/project/select", {"feature": "003-c"})
        self.assertEqual(result, {"ok": True, "path": str(self.root), "feature": "003-c"})
        self.assertEqual(request(self, "GET", "/api/project/spec"), "# Spec 003-c\n")
        self.assertEqual(sorted(str(p) for p in self.root.rglob("*")), files)  # nothing written

    def test_select_unknown_feature(self):
        result = request(self, "POST", "/api/project/select", {"feature": "../../etc"}, status=400)
        self.assertEqual(result["error"], "unknown feature")
        self.assertIsNone(dash.DashboardHandler.feature)

    def test_find_file_removed(self):
        self.assertFalse(hasattr(dash, "find_file"))


class DocEndpointTest(unittest.TestCase):
    """data-model.md §4.3 (FR-001): `/api/doc?name=` serves a fixed name set, nothing else."""

    FEATURE_FILES = {"spec": "spec.md", "plan": "plan.md", "research": "research.md",
                     "data-model": "data-model.md", "quickstart": "quickstart.md", "tasks": "tasks.md",
                     "blocked": "BLOCKED.md", "decisions": "decisions.md"}
    PROJECT_FILES = {"constitution": ".specify/memory/constitution.md",
                     "learnings": ".sdd/learnings.md", "config": ".sdd/config.toml"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.feature = self.root / "specs" / "001-only"
        self.feature.mkdir(parents=True)
        (self.root / ".specify" / "memory").mkdir(parents=True)
        (self.root / ".sdd" / "runs").mkdir(parents=True)
        self.saved = (dash.DashboardHandler.project, dash.DashboardHandler.feature)
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.root, None

    def tearDown(self):
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.saved
        self.tmp.cleanup()

    def write_all(self):
        for name, rel in self.FEATURE_FILES.items():
            (self.feature / rel).write_text(f"# {name}\n", encoding="utf-8")
        for name, rel in self.PROJECT_FILES.items():
            (self.root / rel).write_text(f"# {name}\n", encoding="utf-8")

    def test_name_set_is_exact(self):
        self.assertEqual(set(dash.DOC_NAMES), set(self.FEATURE_FILES) | set(self.PROJECT_FILES))

    def test_each_name(self):
        self.write_all()
        for name in list(self.FEATURE_FILES) + list(self.PROJECT_FILES):
            self.assertEqual(request(self, "GET", f"/api/doc?name={name}"), f"# {name}\n", name)

    def test_content_types(self):
        self.write_all()
        self.assertEqual(content_type(self, "/api/doc?name=config"), "text/plain")
        self.assertEqual(content_type(self, "/api/doc?name=spec"), "text/markdown")

    def test_each_name_missing(self):
        for name in list(self.FEATURE_FILES) + list(self.PROJECT_FILES):
            self.assertEqual(request(self, "GET", f"/api/doc?name={name}"), {"missing": True}, name)

    def test_unknown_names_are_404(self):
        for query in ("name=readme", "name=../../etc/passwd", "name=..%2F..%2Fetc%2Fpasswd",
                      "name=spec.md", "name=", "", "name=spec&name=plan"):
            request(self, "GET", f"/api/doc?{query}", status=404)

    def test_feature_doc_needs_a_feature(self):
        (self.root / "specs" / "002-other").mkdir()
        (self.root / ".sdd" / "config.toml").write_text("# cfg\n", encoding="utf-8")
        result = request(self, "GET", "/api/doc?name=research", status=409)
        self.assertEqual(result, {"error": "choose a feature", "features": ["001-only", "002-other"]})
        self.assertEqual(request(self, "GET", "/api/doc?name=config"), "# cfg\n")  # project-wide

    def test_aliases(self):
        self.write_all()
        for alias in ("spec", "plan", "decisions", "config"):
            self.assertEqual(request(self, "GET", f"/api/project/{alias}"), f"# {alias}\n", alias)
        (self.feature / "plan.md").unlink()
        self.assertEqual(request(self, "GET", "/api/project/plan"), {"missing": True})

    def test_report_run_id_validated(self):
        (self.root / ".sdd" / "runs" / "20261027-1431.md").write_text("# Report\n", encoding="utf-8")
        (self.root / ".sdd" / "secret.md").write_text("# Secret\n", encoding="utf-8")
        self.assertEqual(request(self, "GET", "/api/runs/20261027-1431/report"), "# Report\n")
        for bad in ("run-001", "..%2Fsecret", "..", "2026102-1431", "20261027-1431X", "20261027-1431.md"):
            request(self, "GET", f"/api/runs/{bad}/report", status=404)
        request(self, "GET", "/api/runs/20261027-0000/report", status=404)  # valid id, no report


def content_type(test, path):
    handler = dash.DashboardHandler.__new__(dash.DashboardHandler)
    handler.path, handler.command = path, "GET"
    handler.request_version, handler.requestline = "HTTP/1.1", f"GET {path} HTTP/1.1"
    handler.wfile, handler.rfile, handler.headers = io.BytesIO(), io.BytesIO(), {"Host": "localhost:8765"}
    handler.do_GET()
    head = handler.wfile.getvalue().partition(b"\r\n\r\n")[0].decode()
    return re.search(r"Content-Type: ([^\r\n]+)", head).group(1)


class HostAndDiscoveryTest(unittest.TestCase):
    """data-model.md §4.7 (FR-015, FR-022): localhost Host names only; one project."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "started"
        (self.root / ".sdd").mkdir(parents=True)
        self.saved = (dash.DashboardHandler.project, dash.DashboardHandler.started_project,
                      dash.DashboardHandler.feature)
        dash.DashboardHandler.project, dash.DashboardHandler.started_project = self.root, self.root
        dash.DashboardHandler.feature = None

    def tearDown(self):
        (dash.DashboardHandler.project, dash.DashboardHandler.started_project,
         dash.DashboardHandler.feature) = self.saved
        self.tmp.cleanup()

    def test_foreign_host_is_421(self):
        for host in ("evil.example", "evil.example:8765", "127.0.0.1.evil.example", None):
            request(self, "GET", "/api/projects", host=host, status=421)
            request(self, "POST", "/api/project/select", {"feature": "x"}, host=host, status=421)

    def test_localhost_names_are_allowed(self):
        for host in ("127.0.0.1", "127.0.0.1:8765", "localhost", "LOCALHOST:1", "[::1]", "[::1]:8765"):
            request(self, "GET", "/api/projects", host=host)

    def test_projects_has_only_the_started_project(self):
        other = Path(self.tmp.name) / "other"
        (other / ".specify").mkdir(parents=True)
        self.assertEqual(request(self, "GET", "/api/projects"),
                         [{"path": str(self.root), "name": "started"}])
        dash.DashboardHandler.project = other  # selecting another project does not add it
        self.assertEqual(len(request(self, "GET", "/api/projects")), 1)


class DocEditRouteTest(unittest.TestCase):
    """`/api/doc/state`, `/api/doc/save`, `/api/doc/commit` and `/api/features` (FR-035) through the handler."""

    def setUp(self):
        import subprocess
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        write_project(self.root / "specs" / "001-only", STATUS_TASKS, STATUS_BLOCKED)
        (self.root / "specs" / "001-only" / "spec.md").write_text("# Spec\n", encoding="utf-8")
        for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "T"),
                     ("add", "."), ("commit", "-q", "-m", "init")):
            subprocess.run(["git", *args], cwd=self.root, capture_output=True, check=True)
        self.saved = (dash.DashboardHandler.project, dash.DashboardHandler.feature)
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.root, None

    def tearDown(self):
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.saved
        self.tmp.cleanup()

    def test_features_says_which_feature_runs_use(self):
        # The person looks at another feature than the one runs use: both are named, nothing is written.
        (self.root / "specs" / "002-other").mkdir()
        (self.root / ".specify").mkdir(exist_ok=True)
        (self.root / ".specify" / "feature.json").write_text('{"feature_directory": "specs/001-only"}')
        dash.DashboardHandler.feature = "002-other"
        self.assertEqual(request(self, "GET", "/api/features"),
                         {"features": ["001-only", "002-other"], "active": "002-other", "runs": "001-only"})

    def test_features_and_state(self):
        self.assertEqual(request(self, "GET", "/api/features"),
                         {"features": ["001-only"], "active": "001-only", "runs": "001-only"})
        st = request(self, "GET", "/api/doc/state?name=spec")
        self.assertEqual((st["path"], st["dirty"], st["locked"]), ("specs/001-only/spec.md", False, None))
        request(self, "GET", "/api/doc/state?name=plan", status=404)

    def test_save_then_commit(self):
        base = request(self, "GET", "/api/doc/state?name=spec")["hash"]
        saved = request(self, "POST", "/api/doc/save", {"name": "spec", "text": "# Spec\n\nMore.\n", "base": base})
        self.assertTrue(saved["ok"] and saved["dirty"])
        self.assertIn("More.", request(self, "GET", "/api/doc?name=spec"))
        done = request(self, "POST", "/api/doc/commit", {"name": "spec", "message": "docs(spec): more"})
        self.assertTrue(done["ok"])
        self.assertFalse(done["dirty"])

    def test_blocked_entry_routes(self):
        request(self, "POST", "/api/blocked/done", {"text": "x"}, status=400)
        got = request(self, "POST", "/api/blocked/answer", {"task": "T007"}, status=409)
        self.assertIn("write the answer first", got["error"])
        done = request(self, "POST", "/api/blocked/answer", {"task": "T007", "text": "the v2 API"})
        self.assertTrue(done["ok"] and done["commit"])
        blocked = (self.root / "specs" / "001-only" / "BLOCKED.md").read_text()
        self.assertIn("the v2 API", blocked)
        got = request(self, "POST", "/api/blocked/done", {"task": "T007"}, status=409)  # no longer open
        self.assertIn("no open entry", got["error"])

    def test_manual_task_routes(self):
        request(self, "POST", "/api/manual/done", {"note": "x"}, status=400)
        got = request(self, "POST", "/api/manual/done", {"task": "T003"}, status=409)  # a decision, not manual
        self.assertIn("not an open manual task", got["error"])
        got = request(self, "POST", "/api/manual/skip", {"task": "T002"}, status=409)
        self.assertIn("say why", got["error"])
        done = request(self, "POST", "/api/manual/done", {"task": "T002", "note": "checked"})
        self.assertTrue(done["ok"] and done["commit"])
        self.assertIn("- [x] T002", (self.root / "specs" / "001-only" / "tasks.md").read_text())

    def test_refusals_carry_the_reason(self):
        got = request(self, "POST", "/api/doc/save", {"name": "spec", "text": "# X\n", "base": "stale"}, status=409)
        self.assertIn("changed since you opened it", got["error"])
        request(self, "POST", "/api/doc/save", {"name": "plan", "text": "# X\n", "base": "x"}, status=400)
        request(self, "POST", "/api/doc/commit", {"name": "spec"}, status=400)
        got = request(self, "POST", "/api/doc/commit", {"name": "spec", "message": "docs: x"}, status=409)
        self.assertIn("no uncommitted change", got["error"])


class NavigationTest(unittest.TestCase):
    """The section lives in the URL hash, and there is no project picker: the dashboard serves the
    project it was started in. Behaviour is checked in a browser (tests/ui)."""

    def setUp(self):
        self.html = dash.build_html()

    def test_section_is_kept_in_the_url(self):
        self.assertIn("function sectionFromHash()", self.html)
        self.assertIn("history[sectionShown ? 'pushState' : 'replaceState']", self.html)
        self.assertIn("showSection(sectionFromHash());", self.html)
        self.assertIn("addEventListener('popstate'", self.html)

    def test_overview_is_a_summary_not_a_file_list(self):
        self.assertNotIn("Project Files", self.html)
        self.assertNotIn("data-file=", self.html)
        for part in ("ov-progress", "ov-needs", "ov-next", "ov-phases", "ov-runs"):
            self.assertIn('id="%s"' % part, self.html)

    def test_no_project_picker(self):
        for gone in ("Load Project", "btn-load-project", "project-select", "selectProjectPath"):
            self.assertNotIn(gone, self.html)
        self.assertIn('id="project-name"', self.html)


class StatusSystemTest(unittest.TestCase):
    """data-model.md §1 (FR-003, SC-002): CSS colors, GLYPHS, STATUS and statusBadge() in build_html()."""

    COLORS = {"queued": "#8b93a1", "running": "#3b82f6", "retrying": "#eab308", "paused": "#64748b",
              "done": "#22c55e", "held": "#f59e0b", "blocked": "#ef4444", "wontfix": "#6b7280",
              "moved": "#a78bfa", "superseded": "#94a3b8"}
    GLYPH_OF = {"queued": "circle-outline", "running": "spinner", "retrying": "rotate-arrow",
                "paused": "pause-bars", "done": "check", "held": "clock", "blocked": "alert-triangle",
                "wontfix": "slash-circle", "moved": "arrow-up-right", "superseded": "arrow-right"}

    @classmethod
    def setUpClass(cls):
        cls.html = dash.build_html()
        start = cls.html.index("function escapeHtml(")
        cls.js = cls.html[start:cls.html.index("function loadProject(")]

    def test_css_variable_per_key(self):
        self.assertEqual(set(self.COLORS), set(dash.ROLLUP_ORDER))
        for key, color in self.COLORS.items():
            self.assertIn("--st-%s: %s;" % (key, color), self.html)
            self.assertIn(".status-%s { color: var(--st-%s); }" % (key, key), self.html)

    def test_no_glyph_and_color_shared(self):
        pairs = {(self.GLYPH_OF[k], self.COLORS[k]) for k in self.COLORS}
        self.assertEqual(len(pairs), len(self.COLORS))

    def test_spinner_animates(self):
        self.assertRegex(self.html, r"svg\.glyph-spinner \{ animation: [a-z-]+ 1s linear infinite; \}")

    def test_no_icon_font_or_cdn(self):
        for needle in ("font-awesome", "fontawesome", "material-icons", "Material+Icons", "lucide", "feather"):
            self.assertNotIn(needle.lower(), self.html.lower())

    def run_js(self, expr):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        script = self.js + "\nprocess.stdout.write(JSON.stringify(" + expr + "));"
        out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_glyphs_and_status_objects(self):
        got = self.run_js("{glyphs: GLYPHS, status: STATUS}")
        self.assertEqual(set(got["glyphs"]), set(self.GLYPH_OF.values()))
        for svg in got["glyphs"].values():
            for attr in ('viewBox="0 0 24 24"', 'stroke="currentColor"', 'fill="none"', 'stroke-width="2"'):
                self.assertIn(attr, svg)
        self.assertEqual({k: v["glyph"] for k, v in got["status"].items()}, self.GLYPH_OF)

    def test_status_badge_markup(self):
        got = self.run_js("[statusBadge('held', {tag: 'manual'}), statusBadge('running', {step: 'Verifying'}),"
                          " statusBadge('retrying', {n: 1, max: 2}), statusBadge('superseded', {by: 'T020'}),"
                          " statusBadge('wontfix'), statusBadge('queued', {label: 'Answered — runs next'}),"
                          " statusBadge('held'), GLYPHS['clock']]")
        held, running, retry, sup, wontfix, answered, bare, clock = got
        self.assertEqual(held, '<span class="status status-held" title="Held: manual">' + clock +
                         '<span class="status-label">Held: manual</span></span>')
        self.assertIn('title="Running: Verifying"', running)
        self.assertIn('class="glyph-spinner"', running)
        self.assertIn('>Retry 1/2<', retry)
        self.assertIn('>Superseded by T020<', sup)
        self.assertIn('>Won&#39;t fix<', wontfix)
        self.assertIn('>Answered — runs next<', answered)
        self.assertIn('>Held<', bare)


class TasksViewTest(unittest.TestCase):
    """T018 (FR-004, FR-005): task cards, phase rollup badges, "Waiting on you", BLOCKED.md entry badges."""

    setUpClass = classmethod(StatusSystemTest.setUpClass.__func__)
    run_js = StatusSystemTest.run_js

    def test_task_status_key(self):
        got = self.run_js("[taskStatusKey({status: 'held'}), taskStatusKey({status: null, done: true}),"
                          " taskStatusKey({status: null, done: false})]")
        self.assertEqual(got, ["held", "done", "queued"])

    def test_task_card_badges(self):
        got = self.run_js("[taskCardHtml({id: 'T1', text: 'a', tags: ['manual'], status: 'held', status_tag: 'manual'}),"
                          " taskCardHtml({id: 'T2', text: 'b', tags: [], status: 'superseded', status_tag: 'T9'}),"
                          " taskCardHtml({id: 'T3', text: 'c', tags: [], status: null, done: true}),"
                          " taskCardHtml({id: 'T4', text: 'd', tags: [], status: null, done: false}),"
                          " statusBadge('done'), statusBadge('queued')]")
        held, sup, done, queued, done_badge, queued_badge = got
        self.assertIn('title="Held: manual"', held)
        self.assertIn('>Superseded by T9<', sup)
        self.assertIn(done_badge, done)
        self.assertIn("task-done", done)
        self.assertIn(queued_badge, queued)

    def test_phase_heading_rollup(self):
        got = self.run_js("[phaseHeadingHtml({title: 'Setup', status: 'blocked'}), phaseHeadingHtml({title: 'Empty', status: null}),"
                          " statusBadge('blocked')]")
        self.assertIn(got[2], got[0])
        self.assertNotIn('class="status ', got[1])

    def test_waiting_on_you(self):
        tasks = ("[{id: 'T1', text: 'a', tags: ['manual'], status: 'held', status_tag: 'manual',"
                 " status_reason: 'needs a person'},"
                 " {id: 'T2', text: 'b', tags: [], status: 'blocked', status_tag: 'open',"
                 " status_reason: 'stuck', question: 'Which port?'},"
                 " {id: 'T3', text: 'c', tags: [], status: 'wontfix'},"
                 " {id: 'T4', text: 'd', tags: [], status: null, done: false},"
                 " {id: 'T5', text: 'e', tags: [], status: 'done', done: true},"
                 " {id: 'T6', text: 'f', tags: [], status: 'held', status_tag: 'dependency',"
                 " status_reason: 'depends on T1, which is held (manual)'}]")
        got = self.run_js("(function(ts) { return {waiting: ts.filter(isWaiting).map(function(t) { return t.id; }),"
                          " held: waitingItemHtml(ts[0], 'Phase 1'), blocked: waitingItemHtml(ts[1], 'Phase 1'),"
                          " counts: countTasks([{tasks: ts}])}; })(" + tasks + ")")
        self.assertEqual(got["waiting"], ["T1", "T2"])
        self.assertIn("needs a person", got["held"])
        self.assertIn("Which port?", got["blocked"])
        self.assertIn('title="Blocked"', got["blocked"])
        self.assertEqual(got["counts"], {"total": 6, "done": 1, "waiting": 2, "blocked": 1})

    def test_entry_badges(self):
        got = self.run_js("[entryBadge({status: 'open'}), entryBadge({status: 'resolved'}), entryBadge({status: 'wontfix'}),"
                          " entryBadge({status: 'moved'}), statusBadge('blocked'), statusBadge('wontfix'), statusBadge('moved'),"
                          " entryItemHtml({id: 'T7', status: 'resolved', reason: 'r', question: 'q?', answer: 'yes', tag: 'decision'})]")
        open_, resolved, wontfix, moved, b_blocked, b_wontfix, b_moved, item = got
        self.assertEqual(open_, b_blocked)
        self.assertIn('class="status status-queued" title="Answered — runs next"', resolved)
        self.assertEqual(wontfix, b_wontfix)
        self.assertEqual(moved, b_moved)
        self.assertIn("Answered — runs next", item)
        self.assertIn(">yes<", item)

    def test_old_held_logic_gone(self):
        self.assertNotIn("Held / Waiting", self.html)
        self.assertNotIn("loadBlockedData", self.html)
        self.assertIn("Waiting on you", self.html)


class TransientStateTest(unittest.TestCase):
    """T026 (FR-021, data-model.md §4.6, §1): transient states from the run's events, merged phase
    rollups in the browser, and the "Live output" panel on a running task."""

    setUpClass = classmethod(StatusSystemTest.setUpClass.__func__)
    run_js = StatusSystemTest.run_js

    EVENTS = ("[{kind: 'task.started', id: 'T1'},"
              " {kind: 'task.started', id: 'T2'}, {kind: 'task.implemented', id: 'T2'},"
              " {kind: 'task.started', id: 'T3'}, {kind: 'task.implemented', id: 'T3'},"
              " {kind: 'task.verified', id: 'T3', verdict: 'reject'},"
              " {kind: 'task.started', id: 'T3'}, {kind: 'task.implemented', id: 'T3'},"
              " {kind: 'task.verified', id: 'T3', verdict: 'reject'},"
              " {kind: 'task.started', id: 'T4'}, {kind: 'task.implemented', id: 'T4'},"
              " {kind: 'task.verified', id: 'T4', verdict: 'approve'}, {kind: 'task.approved', id: 'T4'},"
              " {kind: 'group.finished', ids: ['T4']},"
              " {kind: 'task.started', id: 'T5'}, {kind: 'task.blocked', id: 'T5'},"
              " {kind: 'task.started', id: 'T6'}, {kind: 'task.failed', id: 'T6'}]")

    def test_rollup_order_matches_python(self):
        self.assertEqual(self.run_js("ROLLUP_ORDER"), list(dash.ROLLUP_ORDER))

    def test_event_to_state_table(self):
        got = self.run_js("transientStates(" + self.EVENTS + ", 2, 'running')")
        self.assertEqual(got, {
            "T1": {"key": "running", "vars": {"step": "Implementing"}},
            "T2": {"key": "running", "vars": {"step": "Verifying"}},
            "T3": {"key": "retrying", "vars": {"n": 3, "max": 3}},
            "T4": {"key": "running", "vars": {"step": "Committing"}},
            "T5": {"key": "blocked", "vars": {"label": "Blocked (not recorded yet)"}},
        })

    def test_retry_uses_max_retries(self):
        got = self.run_js("transientStates([{kind: 'task.verified', id: 'T1', verdict: 'reject'}], 4, null)")
        self.assertEqual(got, {"T1": {"key": "retrying", "vars": {"n": 2, "max": 5}}})

    def test_paused_run_shows_paused(self):
        got = self.run_js("transientStates(" + self.EVENTS + ", 2, 'paused')")
        self.assertEqual(set(got), {"T1", "T2", "T3", "T4", "T5"})
        self.assertEqual({k: v["key"] for k, v in got.items()},
                         {"T1": "paused", "T2": "paused", "T3": "paused", "T4": "paused", "T5": "blocked"})

    def test_badges_and_merge(self):
        got = self.run_js(
            "(function() { var states = transientStates(" + self.EVENTS + ", 2, 'running');"
            " var phases = mergeTransient([{title: 'A', status: 'done', tasks: ["
            "  {id: 'T1', status: null, done: false}, {id: 'T9', status: 'done', done: true}]},"
            " {title: 'B', status: 'held', tasks: [{id: 'T3', status: null, done: false},"
            "  {id: 'T2', status: 'held', status_tag: 'manual', done: false}]},"
            " {title: 'C', status: 'queued', tasks: [{id: 'T5', status: null, done: false}]},"
            " {title: 'D', status: null, tasks: []}], states);"
            " return {status: phases.map(function(p) { return p.status; }),"
            " t1: taskBadge(phases[0].tasks[0]), t3: taskBadge(phases[1].tasks[0]),"
            " t2: taskBadge(phases[1].tasks[1]), t5: taskBadge(phases[2].tasks[0]),"
            " t9: phases[0].tasks[0 + 1].transient}; })()")
        self.assertEqual(got["status"], ["running", "retrying", "blocked", None])
        self.assertIn('title="Running: Implementing"', got["t1"])
        self.assertIn('>Retry 3/3<', got["t3"])
        self.assertIn('title="Held: manual"', got["t2"])  # a parked status wins over the event
        self.assertIn('title="Blocked (not recorded yet)"', got["t5"])  # task.blocked before BLOCKED.md has it
        self.assertIsNone(got["t9"])

    def test_live_output_panel(self):
        got = self.run_js(
            "(function() { var t = {id: 'T1', text: 'a', tags: [], status: null, done: false,"
            " transient: {key: 'running', vars: {step: 'Verifying'}}};"
            " var q = {id: 'T2', text: 'b', tags: [], status: null, done: false};"
            " var before = taskCardHtml(t); liveLogs.T1 = {available: true, lines: ['<one>', 'two']};"
            " return [before, taskCardHtml(t), taskCardHtml(q),"
            " liveOutputBodyHtml({available: false, reason: 'run is not active'})]; })()")
        before, after, queued, unavailable = got
        self.assertIn("Live output", before)
        self.assertIn('data-live-task="T1"', before)
        self.assertIn("Not available yet", before)
        self.assertIn("&lt;one&gt;\ntwo", after)
        self.assertNotIn("Live output", queued)
        self.assertIn("Not available: run is not active", unavailable)

    def test_a_clicked_card_opens_its_log_under_itself(self):
        self.assertIn("data-task-card", self.html)
        self.assertIn("function showsLog(t)", self.html)
        self.assertIn("openLogs[tid]", self.html)

    def test_a_clicked_run_opens_its_events_and_report_under_itself(self):
        self.assertIn("var runDetailEl", self.html)
        self.assertIn("block.appendChild(runDetailEl)", self.html)
        self.assertNotIn('<div class="card-title">Event Feed</div>', self.html)  # no separate feed at the bottom
        self.assertNotIn("View report", self.html)  # no icon buttons: the click opens both
        self.assertIn("function loadReport()", self.html)

    def test_event_text_is_a_sentence(self):
        for text in ("function eventText(ev)", "'implementation started'", "'verifier rejected'",
                     "'checks passed, committed'", "escapeHtml(eventText(ev))"):
            self.assertIn(text, self.html)
        self.assertNotIn("payloadShort", self.html)  # no raw JSON in the feed rows

    def test_task_card_names_the_run_that_did_it(self):
        got = self.run_js("(function() { taskRuns = {T1: [{run: '20261029-1003', outcome: 'committed', commits: ['168b8ce']},"
                          " {run: '20261028-1005', outcome: 'rejected', commits: []}]}; return [taskRunChipHtml('T1'), taskRunChipHtml('T2')]; })()")
        self.assertIn('data-run="20261029-1003"', got[0])
        self.assertIn("168b8ce", got[0])
        self.assertIn("+1 more", got[0])
        self.assertEqual(got[1], "")

    def overview_js(self, expr):
        """Run expr with the Overview helpers, which sit after the slice in self.js."""
        start = self.html.index("    function shortText(")
        short = self.html[start:self.html.index("}", self.html.index("return", start)) + 1]
        a = self.html.index("    var NEEDS_GROUPS")
        b = self.html.index("    // The first open tasks nothing is holding")
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        script = self.js + "\n" + short + "\n" + self.html[a:b] + "\nprocess.stdout.write(JSON.stringify(" + expr + "));"
        out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_overview_needs_groups_and_running_now(self):
        got = self.overview_js("""(function() { var ph = [{title: 'P', tasks: [
            {id: 'T1', text: 'blocked one', status: 'blocked', question: 'Which API?'},
            {id: 'T2', text: 'record audio', status: 'held', status_tag: 'manual'},
            {id: 'T3', text: 'add a package', status: 'held', status_tag: 'dep'},
            {id: 'T4', text: 'after T2', status: 'held', status_tag: 'dependency'},
            {id: 'T5', text: 'pick db', status: 'held', status_tag: 'decision'},
            {id: 'T6', text: 'working', status: null, transient: {key: 'running', vars: {step: 'Verifying'}}},
            {id: 'T7', text: 'again', status: null, transient: {key: 'retrying', vars: {n: 2, max: 3}}}]}];
          var g = needsGroups(ph, [{id: 'T5', question: 'Which database?'}]);
          var rows = runningRows(ph);
          return [Object.keys(g).map(function(k) { return k + ':' + g[k].map(function(r) { return r.id; }).join(','); }),
                  overviewNeedsHtml(ph, [{id: 'T5', question: 'Which database?'}]),
                  rows.map(function(r) { return r.t.id; }),
                  overviewRunningHtml(rows, {T6: new Date(Date.now() - 125000).toISOString()}, {T6: '< 297 passed'}),
                  sinceText(new Date(Date.now() - 3 * 3600000 - 60000).toISOString())]; })()""")
        groups, needs, running, html, since = got
        self.assertEqual(groups, ["blocked:T1", "decision:T5", "manual:T2", "other:T3"])  # T4 waits on T2, not on the person
        self.assertEqual(needs["count"], 4)
        self.assertLess(needs["html"].index("Blocked by a run"), needs["html"].index("Decisions"))
        self.assertEqual(running, ["T6", "T7"])
        self.assertIn('data-goto-task="T6"', html)
        self.assertIn("2 min", html)
        self.assertIn("&lt; 297 passed", html)
        self.assertEqual(since, "3 h 1 min")

    def test_polls_log_route(self):
        self.assertIn("'/tasks/' + encodeURIComponent(id) + '/log'", self.html)
        self.assertIn("setInterval(refreshAll, 3000)", self.html)
        refresh = self.html[self.html.index("function refreshAll("):]
        self.assertIn("if (selectedSection === 'tasks') loadTasks();", refresh)
        tasks = self.html[self.html.index("function renderTasks("):self.html.index("function pollLiveOutputs(")]
        self.assertIn("pollLiveOutputs(runId, running)", tasks)

    def test_max_retries_in_tasks_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root, STATUS_TASKS, STATUS_BLOCKED, "[run]\nmax_retries = 4\n")
            got = dash.project_tasks(root / "tasks.md", root / "BLOCKED.md", root / ".sdd" / "config.toml")
            self.assertEqual(got["max_retries"], 4)
            got = dash.project_tasks(root / "tasks.md", None, None)
            self.assertEqual(got["max_retries"], 2)


class DocumentsViewTest(unittest.TestCase):
    """T019 (FR-001, data-model.md §4.3): pinned renderer URLs, sanitized markdown, TOC, offline fallback."""

    setUpClass = classmethod(StatusSystemTest.setUpClass.__func__)
    run_js = StatusSystemTest.run_js

    URLS = ('<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>',
            '<script src="https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/highlight.min.js"></script>',
            '<script src="https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js"></script>',
            '<link href="https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/styles/github-dark.min.css" rel="stylesheet">')

    def test_pinned_urls(self):
        for tag in self.URLS:
            self.assertIn(tag, self.html)
        self.assertNotIn("npm/highlight.js", self.html)

    def test_no_other_external_urls(self):
        """T065 (research.md `## First browser run`): the four pinned files are the page's only network
        loads; the web fonts link made every offline browser test fail, so the CSS uses its system fallbacks."""
        loads = re.findall(r'<(?:script|link)\b[^>]*\b(?:src|href)="(https?://[^"]+)"', self.html)
        pinned = [re.search(r'(?:src|href)="([^"]+)"', tag).group(1) for tag in self.URLS]
        self.assertEqual(sorted(loads), sorted(pinned))
        self.assertNotIn("fonts.googleapis.com", self.html)

    def test_nav_entry_per_doc_name(self):
        got = self.run_js("DOC_TITLES")
        self.assertEqual(set(got), set(dash.DOC_NAMES))
        for name in dash.DOC_NAMES:
            if name in ("tasks", "blocked"):
                self.assertIn('data-raw="%s"' % name, self.html)
            elif name in ("spec", "plan", "research", "data-model", "quickstart", "constitution", "learnings"):
                self.assertNotIn('class="nav-item" data-section="%s"' % name, self.html)  # a tab under Docs
            else:
                self.assertIn('class="nav-item" data-section="%s"' % name, self.html)
        self.assertIn('class="nav-item" data-section="docs"', self.html)
        self.assertIn("var DOC_GROUP = ['spec', 'plan', 'research', 'data-model', 'quickstart', 'constitution', 'learnings'];", self.html)
        self.assertIn("Raw document", self.html)
        self.assertIn("'Not written yet'", self.html)

    def test_toc_needs_four_headings(self):
        got = self.run_js("[tocFor([{level: 'h2', text: 'A'}, {level: 'h2', text: 'B'}, {level: 'h3', text: 'C'}], 'doc'),"
                          " tocFor([{level: 'h2', text: 'One thing'}, {level: 'h3', text: 'One thing'},"
                          " {level: 'h2', text: '<b>x</b>'}, {level: 'h3', text: '§4.3 Docs'}], 'doc-content')]")
        none, toc = got
        self.assertIsNone(none)
        self.assertEqual(toc["ids"], ["doc-content-one-thing", "doc-content-one-thing-2",
                                      "doc-content-b-x-b", "doc-content-4-3-docs"])
        self.assertIn('<li class="toc-h3"><a href="#doc-content-one-thing-2">One thing</a></li>', toc["html"])
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", toc["html"])

    def test_fallback_without_libraries(self):
        got = self.run_js("(function() { var el = {innerHTML: ''}; renderDoc(el, '# T <script>', 'markdown');"
                          " return [docLibs(), el.innerHTML, fallbackHtml('x', DOC_NOTICE)]; })()")
        libs, html, plain = got
        self.assertIsNone(libs)
        self.assertIn('<div class="doc-notice">', html)
        self.assertIn('<pre class="doc-plain"># T &lt;script&gt;</pre>', html)
        self.assertEqual(html.count("<div"), 1)  # one notice line, then the old <pre> view

    def test_markdown_is_sanitized_then_highlighted(self):
        got = self.run_js(
            "(function() { var calls = [], codes = [{c: 1}, {c: 2}];"
            " var libs = {marked: {parse: function(t) { calls.push('parse'); return '<p>' + t + '</p>'; }},"
            " DOMPurify: {sanitize: function(h) { calls.push('sanitize'); return 'CLEAN' + h; }},"
            " hljs: {highlightElement: function(c) { calls.push('hl' + c.c); }}};"
            " var el = {id: 'doc-content', innerHTML: '', querySelectorAll: function(s) { return s === 'pre code' ? codes : []; }};"
            " renderDoc(el, 'hi', 'markdown', libs); var md = el.innerHTML;"
            " renderDoc(el, 'a = <1>', 'text', libs);"
            " return {calls: calls, md: md, text: el.innerHTML}; })()")
        self.assertEqual(got["calls"], ["parse", "sanitize", "hl1", "hl2", "hl1", "hl2"])
        self.assertEqual(got["md"], "CLEAN<p>hi</p>")
        self.assertEqual(got["text"], '<pre><code class="language-ini">a = &lt;1&gt;</code></pre>')

    def test_doc_helpers(self):
        got = self.run_js("[docUrl('data-model'), docKind('config'), docKind('spec')]")
        self.assertEqual(got, ["/api/doc?name=data-model", "text", "markdown"])

    def test_reports_render_as_documents(self):
        self.assertIn("fetchDoc('/api/runs/' + encodeURIComponent(runId) + '/report')", self.html)
        self.assertIn("renderDoc(rd('report-view'), d.text, 'markdown')", self.html)
        self.assertNotIn("function loadFile(", self.html)


class FeaturePickerTest(unittest.TestCase):
    """T020 (FR-027, data-model.md §4.2): a 409 from a per-feature request shows a picker of `features`;
    choosing one posts `{"feature": name}` to /api/project/select and reloads the views."""

    setUpClass = classmethod(StatusSystemTest.setUpClass.__func__)
    run_js = StatusSystemTest.run_js

    def test_picker_features(self):
        got = self.run_js("[pickerFeatures(409, {error: 'choose a feature', features: ['001-a', '002-b']}),"
                          " pickerFeatures(404, {features: ['001-a']}), pickerFeatures(409, {error: 'x'}),"
                          " pickerFeatures(409, null)]")
        self.assertEqual(got, [["001-a", "002-b"], None, None, None])

    def test_picker_markup(self):
        got = self.run_js("[featurePickerHtml(['001-a', '<b>\"x']), featurePickerHtml([])]")
        buttons, empty = got
        self.assertEqual(buttons.count("<button"), 2)
        self.assertIn('data-feature="001-a">001-a</button>', buttons)
        self.assertIn('data-feature="&lt;b&gt;&quot;x">&lt;b&gt;&quot;x</button>', buttons)
        self.assertNotIn("<button", empty)

    def test_markup_and_wiring(self):
        self.assertIn('id="feature-picker"', self.html)
        self.assertIn('id="feature-picker-list"', self.html)
        self.assertIn("fetchJSON('/api/project/select', 'POST', { feature: name })", self.html)
        chooser = self.html[self.html.index("function chooseFeature("):self.html.index("function reloadViews(")]
        self.assertIn("reloadViews();", chooser)
        doc = self.html[self.html.index("function showDoc("):self.html.index("function docUrl(")]
        self.assertIn("showFeaturePicker(features)", doc)

    def test_fetch_json_409_shows_picker(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        fetch_json = self.html[self.html.index("function fetchJSON("):self.html.index("function escapeHtml(")]
        script = (
            "var shown = [];\n"
            "function showFeaturePicker(f) { shown.push(f); }\n"
            "function fakeRes(status, body) { return {status: status, ok: status < 300,"
            " json: function() { return Promise.resolve(body); }}; }\n"
            "var answers = {'/a': fakeRes(409, {error: 'choose a feature', features: ['001-a', '002-b']}),"
            " '/b': fakeRes(200, {phases: []}), '/c': fakeRes(404, {error: 'nope'})};\n"
            "function fetch(url) { return Promise.resolve(answers[url]); }\n"
            + self.html[self.html.index("function pickerFeatures("):self.html.index("function featurePickerHtml(")]
            + fetch_json +
            "\nPromise.all([fetchJSON('/a'), fetchJSON('/b'), fetchJSON('/c')]).then(function(r) {"
            " process.stdout.write(JSON.stringify({results: r, shown: shown})); });")
        out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
        got = json.loads(out.stdout)
        self.assertEqual(got["results"], [None, {"phases": []}, None])
        self.assertEqual(got["shown"], [["001-a", "002-b"]])


NO_CONTROLS = {"stop": False, "resume": False, "hold": False, "release": False,
               "reason": dash.NO_SUPERVISOR}


class RunControlTest(unittest.TestCase):
    """data-model.md §4.9 and §12.3 (FR-029, FR-030): the `controls` table of `/api/run/current`,
    the four control routes and `write_control()`."""

    RESETS_AT = 1793000000  # any fixed epoch; the reason shows it as local HH:MM

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.root = base / "project"
        (self.root / ".sdd").mkdir(parents=True)
        self.saved = (dash.DashboardHandler.project, dash.DashboardHandler.feature,
                      dash.DashboardHandler.port, dash.DashboardHandler.allow_ports,
                      dash.DashboardHandler.started_project, dash.DashboardHandler.html)
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.root, None
        dash.DashboardHandler.port, dash.DashboardHandler.allow_ports = 8765, ()
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(base / "state")})
        self.env.start()
        self.logs = dash.logs_dir(self.root)
        self.hhmm = time.strftime("%H:%M", time.localtime(self.RESETS_AT))

    def tearDown(self):
        self.env.stop()
        (dash.DashboardHandler.project, dash.DashboardHandler.feature,
         dash.DashboardHandler.port, dash.DashboardHandler.allow_ports,
         dash.DashboardHandler.started_project, dash.DashboardHandler.html) = self.saved
        self.tmp.cleanup()

    def write_status(self, state, pid=None, **fields):
        """status.json as the supervisor writes it; `pid` defaults to this (live) process."""
        data = {"state": state, "pid": os.getpid() if pid is None else pid, **fields}
        (self.logs / "status.json").write_text(json.dumps(data), encoding="utf-8")

    def current(self):
        return request(self, "GET", "/api/run/current")

    def control_file(self):
        return self.logs / "control.json"

    def assert_nothing_written(self):
        self.assertEqual(sorted(p.name for p in self.logs.iterdir()), ["status.json"])

    # -- the controls table, one test per row ---------------------------------------------------

    def test_running(self):
        self.write_status("running")
        self.assertEqual(self.current()["controls"],
                         {"stop": True, "resume": False, "hold": False, "release": False, "reason": None})

    def test_stopping(self):
        self.write_status("stopping", mode="after-task")
        data = self.current()
        self.assertEqual(data["controls"], {"stop": True, "resume": False, "hold": False, "release": False,
                                            "reason": "stopping after the current task"})
        self.assertIs(data["stopping"], True)
        self.assertNotIn("by", data)

    def test_held(self):
        self.write_status("held", reason="stopped by operator")
        data = self.current()
        self.assertEqual(data["controls"],
                         {"stop": False, "resume": True, "hold": False, "release": False, "reason": None})
        self.assertEqual(data["by"], "operator")
        self.assertNotIn("stopping", data)

    def test_waiting_not_kept_paused(self):
        self.write_status("waiting", window="five_hour", resets_at=self.RESETS_AT, keep_paused=False)
        self.assertEqual(self.current()["controls"], {
            "stop": False, "resume": False, "hold": True, "release": False,
            "reason": f"waiting for the usage window to reset at {self.hhmm}"})

    def test_waiting_kept_paused(self):
        self.write_status("waiting", window="five_hour", resets_at=self.RESETS_AT, keep_paused=True)
        self.assertEqual(self.current()["controls"], {
            "stop": False, "resume": False, "hold": False, "release": True,
            "reason": f"will stay paused after the reset at {self.hhmm}"})

    def test_no_status_json(self):
        data = self.current()
        self.assertEqual(data["controls"], NO_CONTROLS)
        self.assertNotIn("by", data)
        self.assertNotIn("stopping", data)

    def test_supervisor_not_alive(self):
        for pid in ("123", 0, -5, True, 1.5):  # not a usable pid
            self.write_status("running", pid=pid)
            self.assertEqual(self.current()["controls"], NO_CONTROLS, pid)
        (self.logs / "status.json").write_text(json.dumps({"state": "running"}), encoding="utf-8")
        self.assertEqual(self.current()["controls"], NO_CONTROLS)
        self.write_status("running", pid=424242)
        with mock.patch.object(dash.os, "kill", side_effect=ProcessLookupError) as kill:
            self.assertEqual(self.current()["controls"], NO_CONTROLS)
        kill.assert_called_with(424242, 0)

    def test_permission_error_counts_as_alive(self):
        self.write_status("running", pid=1)
        with mock.patch.object(dash.os, "kill", side_effect=PermissionError) as kill:
            self.assertTrue(self.current()["controls"]["stop"])
        kill.assert_called_with(1, 0)  # signal 0 only

    # -- the control routes ---------------------------------------------------------------------

    def assert_written(self, data, command, mode=None):
        control = json.loads(self.control_file().read_text(encoding="utf-8"))
        self.assertEqual(data, {"ok": True, "id": control["id"]})
        self.assertRegex(control["id"], r"^[0-9a-f]{32}$")
        expected = {"id": control["id"], "command": command, "requested_at": control["requested_at"]}
        if mode:
            expected["mode"] = mode
        self.assertEqual(control, expected)
        self.assertLessEqual(abs(control["requested_at"] - time.time()), 5)
        self.assertFalse((self.logs / "control.json.tmp").exists())

    def test_stop_after_task_is_202(self):
        self.write_status("running")
        self.assert_written(request(self, "POST", "/api/run/stop", {}, status=202), "stop", "after-task")

    def test_stop_without_body_defaults_to_after_task(self):
        self.write_status("running")
        self.assert_written(request(self, "POST", "/api/run/stop", status=202), "stop", "after-task")

    def test_stop_now_is_202(self):
        self.write_status("running")
        self.assert_written(request(self, "POST", "/api/run/stop", {"mode": "now"}, status=202), "stop", "now")

    def test_stopping_allows_only_now(self):
        self.write_status("stopping", mode="after-task")
        data = request(self, "POST", "/api/run/stop", {"mode": "after-task"}, status=409)
        self.assertEqual(data, {"error": "stopping after the current task"})
        self.assert_nothing_written()
        self.assert_written(request(self, "POST", "/api/run/stop", {"mode": "now"}, status=202), "stop", "now")

    def test_resume_hold_release_are_202_in_their_state(self):
        for command, state, fields in (("resume", "held", {}),
                                       ("hold", "waiting", {"keep_paused": False, "resets_at": self.RESETS_AT}),
                                       ("release", "waiting", {"keep_paused": True, "resets_at": self.RESETS_AT})):
            self.write_status(state, **fields)
            self.assert_written(request(self, "POST", f"/api/run/{command}", {}, status=202), command)
            self.control_file().unlink()  # the supervisor consumes it

    def test_409_when_not_available_writes_nothing(self):
        cases = (("running", {}, ("resume", "hold", "release"), None),
                 ("held", {}, ("stop", "hold", "release"), "not available now"),
                 ("waiting", {"keep_paused": False, "resets_at": self.RESETS_AT}, ("stop", "resume", "release"),
                  f"waiting for the usage window to reset at {self.hhmm}"),
                 ("waiting", {"keep_paused": True, "resets_at": self.RESETS_AT}, ("stop", "resume", "hold"),
                  f"will stay paused after the reset at {self.hhmm}"),
                 ("done", {}, ("stop", "resume", "hold", "release"), "not available now"))
        for state, fields, commands, reason in cases:
            self.write_status(state, **fields)
            for command in commands:
                data = request(self, "POST", f"/api/run/{command}", {}, status=409)
                self.assertEqual(data, {"error": reason or "not available now"}, (state, command))
                self.assert_nothing_written()

    def test_409_without_supervisor_writes_nothing(self):
        for command in ("stop", "resume", "hold", "release"):
            data = request(self, "POST", f"/api/run/{command}", {}, status=409)
            self.assertEqual(data, {"error": dash.NO_SUPERVISOR})
        self.assertEqual(list(self.logs.iterdir()), [])

    def test_409_when_a_request_is_pending(self):
        self.write_status("running")
        first = request(self, "POST", "/api/run/stop", {}, status=202)
        before = self.control_file().read_bytes()
        data = request(self, "POST", "/api/run/stop", {"mode": "now"}, status=409)
        self.assertEqual(data, {"error": "a request is already pending"})
        self.assertEqual(self.control_file().read_bytes(), before)  # never overwritten
        self.assertEqual(json.loads(before)["id"], first["id"])

    def test_unknown_mode_is_400(self):
        self.write_status("running")
        for mode in ("later", "", None, 1):
            request(self, "POST", "/api/run/stop", {"mode": mode}, status=400)
        self.assert_nothing_written()

    def test_bad_body_is_400(self):
        self.write_status("running")
        request(self, "POST", "/api/run/stop", raw=b"{not json", status=400)
        request(self, "POST", "/api/run/stop", ["now"], status=400)
        self.assert_nothing_written()

    def test_write_control_refuses_when_pending(self):
        first = dash.write_control(self.logs, "hold")
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertIs(dash.write_control(self.logs, "release"), False)
        self.assertEqual(json.loads(self.control_file().read_text())["command"], "hold")

    # -- §4.9: Origin and Content-Type on every POST, preflight on the control routes ----------

    def test_missing_origin_is_403(self):
        self.write_status("running")
        for path, body in (("/api/run/stop", {}), ("/api/project/select", {"feature": "x"})):
            data = request(self, "POST", path, body, origin=None, status=403)
            self.assertEqual(data, {"error": "origin not allowed"})
        self.assert_nothing_written()

    def test_foreign_origin_is_403(self):
        self.write_status("running")
        for origin in ("http://evil.example", "http://127.0.0.1:9999", "https://127.0.0.1:8765",
                       "http://127.0.0.1:8765.evil.example", "null", "http://127.0.0.1"):
            for path in ("/api/run/stop", "/api/run/resume", "/api/run/hold", "/api/run/release",
                         "/api/project/select"):
                request(self, "POST", path, {}, origin=origin, status=403)
        self.assert_nothing_written()

    def test_wrong_content_type_is_415(self):
        self.write_status("running")
        for content_type in (None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
            for path in ("/api/run/stop", "/api/project/select"):
                request(self, "POST", path, {}, content_type=content_type, status=415)
        self.assert_nothing_written()

    def test_allowed_origins(self):
        dash.DashboardHandler.allow_ports = (9000,)
        self.write_status("running")
        origins = ("http://127.0.0.1:8765", "http://localhost:8765", "http://[::1]:8765",
                   "http://127.0.0.1:9000", "http://localhost:9000", "http://[::1]:9000")
        for origin in origins:
            code, headers, _ = raw_request("POST", "/api/run/stop", {}, origin=origin,
                                           content_type="application/json; charset=utf-8")
            self.assertEqual(code, 202, origin)
            self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)
            self.control_file().unlink()
        request(self, "POST", "/api/project/select", {}, origin="http://localhost:9000", status=400)  # past the checks

    def test_host_check_comes_first(self):
        request(self, "POST", "/api/run/stop", {}, host="evil.example", origin=None, status=421)

    def test_preflight(self):
        dash.DashboardHandler.allow_ports = (9000,)
        for path in ("/api/run/stop", "/api/run/resume", "/api/run/hold", "/api/run/release"):
            code, headers, _ = raw_request("OPTIONS", path, origin="http://localhost:9000")
            self.assertEqual(code, 204, path)
            self.assertEqual(headers["Access-Control-Allow-Origin"], "http://localhost:9000")
            self.assertEqual(headers["Access-Control-Allow-Methods"], "POST")
            self.assertEqual(headers["Access-Control-Allow-Headers"], "Content-Type")
            for origin in ("http://evil.example", None):
                code, headers, _ = raw_request("OPTIONS", path, origin=origin)
                self.assertEqual(code, 403, (path, origin))
                self.assertNotIn("Access-Control-Allow-Origin", headers)
        code, headers, _ = raw_request("OPTIONS", "/api/project/select", origin="http://localhost:9000")
        self.assertEqual(code, 404)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_other_routes_send_no_cors_headers(self):
        self.write_status("running")
        for path in ("/api/run/current", "/api/status", "/api/projects"):
            _, headers, _ = raw_request("GET", path)
            self.assertFalse(any(h.startswith("Access-Control-") for h in headers), path)
        _, headers, _ = raw_request("POST", "/api/project/select", {})
        self.assertFalse(any(h.startswith("Access-Control-") for h in headers))

    def test_allow_port_option(self):
        with mock.patch.object(dash.http.server, "ThreadingHTTPServer") as server, \
                mock.patch("builtins.print"):
            server.return_value.serve_forever.side_effect = KeyboardInterrupt
            dash.main(["--project", str(self.root), "--port", "8800", "--allow-port", "9000",
                       "--allow-port", "9001"])
        self.assertEqual(dash.DashboardHandler.port, 8800)
        self.assertEqual(dash.DashboardHandler.allow_ports, (9000, 9001))
        server.assert_called_once_with(("127.0.0.1", 8800), dash.DashboardHandler)

    def test_port_zero_reports_the_chosen_port(self):
        """`--port 0`: the first output line and the allowed Origins use the OS-chosen port (UI tests)."""
        with mock.patch.object(dash.http.server, "ThreadingHTTPServer") as server, \
                mock.patch("builtins.print") as out:
            server.return_value.server_address = ("127.0.0.1", 54321)
            server.return_value.serve_forever.side_effect = KeyboardInterrupt
            dash.main(["--project", str(self.root), "--port", "0"])
        self.assertEqual(dash.DashboardHandler.port, 54321)
        self.assertIn("http://127.0.0.1:54321 ", out.call_args_list[0].args[0])


class DecisionsTest(unittest.TestCase):
    """FR-033: open [decision] tasks, and answers saved outside the project for /sdd-unblock."""

    TASKS = ("## Phase 1: A\n\n"
             "- [ ] T001 [decision] Pick a port. Options: (a) 80; (b) 8080\n"
             "- [ ] T002 [manual] Record audio\n"
             "- [x] T003 [decision] Already settled\n"
             "- [ ] T004 Plain task\n")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.root = base / "project"
        (self.root / ".sdd").mkdir(parents=True)
        (self.root / "specs" / "001-a").mkdir(parents=True)
        (self.root / "specs" / "001-a" / "tasks.md").write_text(self.TASKS, encoding="utf-8")
        self.saved = (dash.DashboardHandler.project, dash.DashboardHandler.feature,
                      dash.DashboardHandler.port, dash.DashboardHandler.allow_ports)
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.root, None
        dash.DashboardHandler.port, dash.DashboardHandler.allow_ports = 8765, ()
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(base / "state")})
        self.env.start()
        self.answers = dash.answers_dir(self.root)

    def tearDown(self):
        self.env.stop()
        (dash.DashboardHandler.project, dash.DashboardHandler.feature,
         dash.DashboardHandler.port, dash.DashboardHandler.allow_ports) = self.saved
        self.tmp.cleanup()

    def project_files(self):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file())

    def test_lists_only_open_decision_tasks(self):
        data = request(self, "GET", "/api/decisions")
        self.assertEqual([d["id"] for d in data["open"]], ["T001"])
        self.assertIn("(b) 8080", data["open"][0]["text"])
        self.assertIsNone(data["open"][0]["pending"])
        self.assertFalse(data["recorded"])

    def test_answer_is_saved_outside_the_project_and_listed_as_pending(self):
        before = self.project_files()
        request(self, "POST", "/api/decisions/answer", {"task": "T001", "answer": " (b) 8080, it is free "},
                status=202)
        saved = json.loads((self.answers / "T001.json").read_text())
        self.assertEqual((saved["task"], saved["answer"]), ("T001", "(b) 8080, it is free"))
        self.assertEqual(self.project_files(), before)  # FR-020: nothing in the project changed
        data = request(self, "GET", "/api/decisions")
        self.assertEqual(data["open"][0]["pending"]["answer"], "(b) 8080, it is free")

    def test_answer_can_be_changed_before_it_is_applied(self):
        for answer in ("(a)", "(b)"):
            request(self, "POST", "/api/decisions/answer", {"task": "T001", "answer": answer}, status=202)
        self.assertEqual(json.loads((self.answers / "T001.json").read_text())["answer"], "(b)")
        self.assertEqual([p.name for p in self.answers.iterdir()], ["T001.json"])

    def test_only_open_decisions_can_be_answered(self):
        for task in ("T002", "T003", "T004", "T999"):
            request(self, "POST", "/api/decisions/answer", {"task": task, "answer": "x"}, status=404)
        self.assertFalse(self.answers.exists())

    def test_bad_input_is_400(self):
        bodies = ({"task": "../T001", "answer": "x"}, {"task": "T001"}, {"task": "T001", "answer": "  "},
                  {"task": "T001", "answer": "x" * (dash.ANSWER_MAX_CHARS + 1)}, {"task": 1, "answer": "x"})
        for body in bodies:
            request(self, "POST", "/api/decisions/answer", body, status=400)
        request(self, "POST", "/api/decisions/answer", raw=b"{nope", status=400)
        self.assertFalse(self.answers.exists())

    def test_origin_and_content_type_are_checked(self):
        body = {"task": "T001", "answer": "x"}
        request(self, "POST", "/api/decisions/answer", body, origin="http://evil.example", status=403)
        request(self, "POST", "/api/decisions/answer", body, origin=None, status=403)
        request(self, "POST", "/api/decisions/answer", body, content_type="text/plain", status=415)
        self.assertFalse(self.answers.exists())

    def init_git(self):
        import subprocess
        for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "T"),
                     ("add", "."), ("commit", "-q", "-m", "init")):
            subprocess.run(["git", *args], cwd=self.root, capture_output=True, check=True)

    def test_saving_applies_and_commits_when_the_tree_is_clean(self):
        self.init_git()
        data = request(self, "POST", "/api/decisions/answer", {"task": "T001", "answer": "(b)"}, status=200)
        self.assertTrue(data["applied"])
        self.assertRegex(data["commit"], r"^[0-9a-f]{7,}$")
        self.assertIn("- [x] T001", (self.root / "specs" / "001-a" / "tasks.md").read_text())
        self.assertIn("T001", (self.root / "specs" / "001-a" / "decisions.md").read_text())
        self.assertFalse((self.answers / "T001.json").exists())
        self.assertEqual(request(self, "GET", "/api/decisions")["open"], [])

    def test_a_refused_apply_keeps_the_answer_and_apply_can_be_retried(self):
        self.init_git()
        (self.root / "stray.txt").write_text("mine")
        data = request(self, "POST", "/api/decisions/answer", {"task": "T001", "answer": "(b)"}, status=202)
        self.assertFalse(data["applied"])
        self.assertIn("other changes", data["reason"])
        self.assertTrue((self.answers / "T001.json").exists())
        self.assertIn("- [ ] T001", (self.root / "specs" / "001-a" / "tasks.md").read_text())
        (self.root / "stray.txt").unlink()
        data = request(self, "POST", "/api/decisions/apply", {"task": "T001"}, status=200)
        self.assertTrue(data["applied"])
        self.assertFalse((self.answers / "T001.json").exists())

    def test_apply_needs_a_saved_answer_and_the_origin_checks(self):
        request(self, "POST", "/api/decisions/apply", {"task": "T001"}, status=404)
        request(self, "POST", "/api/decisions/apply", {"task": "x"}, status=400)
        request(self, "POST", "/api/decisions/apply", {"task": "T001"}, origin="http://evil.example", status=403)

    def test_the_page_has_a_decisions_view_with_a_save_button(self):
        html = dash.build_html()
        for needle in ('id="section-decisions"', "function loadDecisions", "/api/decisions/answer",
                       "decision-submit", "Show decisions.md", "function decisionTextHtml",
                       "decision-choices", "/api/decisions/apply", "Apply now",
                       "inlineCode(t.text)"):
            self.assertIn(needle, html)


class StartRunTest(unittest.TestCase):
    """FR-034: `GET /api/run/plan` and `POST /api/run/start`, with the launcher stubbed."""

    TASKS = "## Phase 1: A\n\n- [ ] T001 Do it\n- [ ] T002 [manual] You\n"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.root = base / "project"
        (self.root / ".sdd").mkdir(parents=True)
        (self.root / "specs" / "001-a").mkdir(parents=True)
        (self.root / "specs" / "001-a" / "tasks.md").write_text(self.TASKS, encoding="utf-8")
        self.saved = (dash.DashboardHandler.project, dash.DashboardHandler.feature,
                      dash.DashboardHandler.port, dash.DashboardHandler.allow_ports)
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.root, None
        dash.DashboardHandler.port, dash.DashboardHandler.allow_ports = 8765, ()
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(base / "state")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        (dash.DashboardHandler.project, dash.DashboardHandler.feature,
         dash.DashboardHandler.port, dash.DashboardHandler.allow_ports) = self.saved
        self.tmp.cleanup()

    def init_git(self):
        import subprocess
        for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "T"),
                     ("add", "."), ("commit", "-q", "-m", "init")):
            subprocess.run(["git", *args], cwd=self.root, capture_output=True, check=True)

    def test_plan_reports_runnable_phases_and_why_it_cannot_start(self):
        data = request(self, "GET", "/api/run/plan")
        self.assertFalse(data["can_start"])  # not a git repository yet
        self.assertIn("git repository", data["reason"])
        self.init_git()
        data = request(self, "GET", "/api/run/plan")
        self.assertTrue(data["can_start"])
        self.assertEqual(data["phases"], [{"id": "1", "title": "A", "runnable": 1}])
        self.assertEqual((data["runnable"], data["held"]), (1, 1))

    def test_start_calls_the_launcher_and_answers_202(self):
        self.init_git()
        with mock.patch.object(dash.sdd_launch, "launch", return_value={"pid": 99, "run_args": "--no-converge",
                                                                      "resumes": None}) as launch:
            data = request(self, "POST", "/api/run/start", {"phases": ["1"], "no_converge": True, "max_tasks": 2},
                           status=202)
        self.assertEqual((data["ok"], data["pid"]), (True, 99))
        args = launch.call_args.args
        self.assertEqual(args[0], self.root)
        self.assertEqual(args[2:], (["1"], True, 2))

    def test_a_refusal_is_409_with_the_reason(self):
        self.init_git()
        (self.root / "stray.txt").write_text("mine")
        data = request(self, "POST", "/api/run/start", {}, status=409)
        self.assertIn("working tree has changes", data["error"])

    def test_bad_bodies_are_400_and_nothing_starts(self):
        with mock.patch.object(dash.sdd_launch, "launch") as launch:
            for body in ({"phases": "1"}, {"phases": [{}]}, {"no_converge": "yes"}, {"max_tasks": "3"},
                         {"max_tasks": True}, ["x"]):
                request(self, "POST", "/api/run/start", body, status=400)
            request(self, "POST", "/api/run/start", raw=b"{nope", status=400)
        launch.assert_not_called()

    def test_origin_and_content_type_are_checked(self):
        with mock.patch.object(dash.sdd_launch, "launch") as launch:
            request(self, "POST", "/api/run/start", {}, origin="http://evil.example", status=403)
            request(self, "POST", "/api/run/start", {}, origin=None, status=403)
            request(self, "POST", "/api/run/start", {}, content_type="text/plain", status=415)
        launch.assert_not_called()

    def test_the_runs_view_has_a_start_panel_with_a_confirmation(self):
        html = dash.build_html()
        for needle in ('id="start-run-card"', "function loadStartRun", "function startRun",
                       "/api/run/start", "/api/run/plan", "window.confirm"):
            self.assertIn(needle, html)


class JobsRouteTest(unittest.TestCase):
    """005 contracts/jobs-api.md: the job routes through the handler, on a temporary git repository, with the
    supervisor spawn stubbed."""

    TASKS = "## Phase 1: A\n\n- [ ] T001 Do it\n- [ ] T002 [manual] You\n"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.root = base / "project"
        (self.root / ".sdd").mkdir(parents=True)
        (self.root / "specs" / "001-a").mkdir(parents=True)
        (self.root / "specs" / "001-a" / "tasks.md").write_text(self.TASKS, encoding="utf-8")
        (self.root / ".gitignore").write_text(".sdd/\n", encoding="utf-8")
        self.saved = (dash.DashboardHandler.project, dash.DashboardHandler.feature,
                      dash.DashboardHandler.port, dash.DashboardHandler.allow_ports)
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.root, None
        dash.DashboardHandler.port, dash.DashboardHandler.allow_ports = 8765, ()
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(base / "state")})
        self.env.start()
        self.spawn = mock.patch.object(dash.sdd_launch, "_spawn", return_value=mock.Mock(pid=4242))
        self.spawned = self.spawn.start()

    def tearDown(self):
        self.spawn.stop()
        self.env.stop()
        (dash.DashboardHandler.project, dash.DashboardHandler.feature,
         dash.DashboardHandler.port, dash.DashboardHandler.allow_ports) = self.saved
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, check=True).stdout.strip()

    def init_git(self):
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "init")

    def record(self, job_id, state="working", **fields):
        record = {"id": job_id, "title": f"Job {job_id}", "feature": "001-a", "scope": {"phases": ["1"]},
                  "scope_tasks": ["T001", "T002"], "blocked_at_start": 0,
                  "limits": {"max_tasks": None, "until": None, "max_runs": 20}, "branch": f"auto/{job_id}",
                  "base": {"branch": "main", "commit": "abc1234"}, "state": state, "reason": None, "runs": [],
                  "no_progress_runs": 0, "created": 1, "ended": None, "merge_commit": None, **fields}
        dash.sdd_job.save(self.root, record)
        return record

    def test_list_open_first_then_newest_with_shown_and_progress(self):
        self.assertEqual(request(self, "GET", "/api/jobs"), {"jobs": []})
        self.record("20261030-0900", "merged")
        self.record("20261031-0900", "dropped")
        self.record("20261029-0900", "waiting")
        jobs = request(self, "GET", "/api/jobs")["jobs"]
        self.assertEqual([j["id"] for j in jobs], ["20261029-0900", "20261031-0900", "20261030-0900"])
        self.assertEqual(jobs[0]["shown"], {"state": "waiting"})
        self.assertEqual(jobs[0]["progress"], {"done": 0, "total": 2, "waiting_on_you": 1, "left": 1})
        self.assertEqual(jobs[0]["title"], "Job 20261029-0900")

    def test_one_job_with_its_runs_and_runs_name_their_job(self):
        self.record("20261031-1012", "waiting", runs=["20261031-1012"])
        wf = write_workflow_dir(Path(self.tmp.name).resolve() / "transcripts" / "wf_job")
        for run_id, job in (("20261031-1012", None), ("20261031-1530", "20261031-1012"), ("20261030-0800", None)):
            write_run(self.root, run_id, [wf])
            if job:
                listing = self.root / ".sdd" / "runs" / run_id / "workflows.json"
                listing.write_text(json.dumps({"workflow_dirs": [str(wf)], "feature": "001-a", "job": job}),
                                   encoding="utf-8")
        runs = {r["id"]: r["job"] for r in request(self, "GET", "/api/runs")}
        self.assertEqual(runs, {"20261031-1530": "20261031-1012", "20261031-1012": None, "20261030-0800": None})
        job = request(self, "GET", "/api/jobs/20261031-1012")
        self.assertEqual(job["id"], "20261031-1012")
        self.assertEqual(job["shown"], {"state": "waiting"})
        self.assertEqual([r["id"] for r in job["runs"]], ["20261031-1530", "20261031-1012"])
        self.assertIn("tasks", job["runs"][0])

    def test_bad_or_unknown_id_is_404(self):
        self.record("20261031-1012", "done")
        for bad in ("..", "run-001", "20261031-1012X", "..%2F..%2Fetc", "20261031-0000"):
            request(self, "GET", f"/api/jobs/{bad}", status=404)
        with mock.patch.object(dash.sdd_launch, "continue_job") as cont, \
                mock.patch.object(dash.sdd_job, "merge") as merge:
            for bad in ("..", "20261031-0000"):
                request(self, "POST", f"/api/jobs/{bad}/continue", {}, status=404)
                request(self, "POST", f"/api/jobs/{bad}/merge", {}, status=404)
                request(self, "POST", f"/api/jobs/{bad}/drop", {}, status=404)
        cont.assert_not_called()
        merge.assert_not_called()

    def test_plan_says_whether_a_job_can_start(self):
        data = request(self, "GET", "/api/jobs/plan")
        self.assertEqual((data["can_start"], data["reason"]), (False, "the project is not a git repository"))
        self.init_git()
        data = request(self, "GET", "/api/jobs/plan")
        self.assertEqual(set(data), {"phases", "runnable", "held", "can_start", "reason"})
        self.assertEqual((data["can_start"], data["reason"]), (True, None))
        self.assertEqual(data["phases"], [{"id": "1", "title": "A", "runnable": 1}])
        self.assertEqual((data["runnable"], data["held"]), (1, 1))
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")  # the dry run changed nothing
        self.assertEqual(dash.sdd_job.list_jobs(self.root), [])
        self.record("20261031-1012", "waiting")
        data = request(self, "GET", "/api/jobs/plan")
        self.assertEqual((data["can_start"], data["reason"]), (False, "another job is open: Job 20261031-1012"))

    def test_start_creates_the_job_and_a_second_start_is_refused(self):
        self.init_git()
        data = request(self, "POST", "/api/jobs/start", {"phases": ["1"], "max_tasks": 3, "title": "First"},
                       status=202)
        self.assertTrue(data["ok"])
        record = dash.sdd_job.load(self.root, data["job"])
        self.assertEqual((record["title"], record["scope"], record["limits"]),
                         ("First", {"phases": ["1"]}, {"max_tasks": 3, "until": None, "max_runs": 20}))
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), f"auto/{data['job']}")
        command = self.spawned.call_args.args[0]
        self.assertEqual(command[-2:], ["--job", data["job"]])
        refused = request(self, "POST", "/api/jobs/start", {}, status=409)
        self.assertEqual(refused, {"error": "another job is open: First"})

    def test_start_refusal_and_bad_bodies(self):
        self.init_git()
        (self.root / "stray.txt").write_text("mine")
        refused = request(self, "POST", "/api/jobs/start", {"tasks": ["T001"]}, status=409)
        self.assertEqual(refused["error"], "the working tree has changes; commit or stash them first: stray.txt")
        with mock.patch.object(dash.sdd_launch, "launch_job") as launch:
            for body in ({"phases": "1"}, {"tasks": [{}]}, {"title": 5}, {"max_tasks": "3"}, {"max_runs": True},
                         {"until": "soon"}, ["x"]):
                request(self, "POST", "/api/jobs/start", body, status=400)
            request(self, "POST", "/api/jobs/start", raw=b"{nope", status=400)
            request(self, "POST", "/api/jobs/start", {}, origin="http://evil.example", status=403)
            request(self, "POST", "/api/jobs/start", {}, content_type="text/plain", status=415)
        launch.assert_not_called()
        self.assertEqual(self.spawned.call_count, 0)

    def test_continue_passes_only_the_given_limits(self):
        self.record("20261031-1012", "waiting")
        with mock.patch.object(dash.sdd_launch, "continue_job", return_value={"job": "x", "pid": 1}) as cont:
            data = request(self, "POST", "/api/jobs/20261031-1012/continue", {"max_tasks": None, "until": 1.5e9},
                           status=202)
            self.assertEqual(data, {"ok": True})
            self.assertEqual(cont.call_args.args, (self.root, "20261031-1012", {"max_tasks": None, "until": 1.5e9}))
            for body in ({"max_tasks": "2"}, {"max_runs": 1.5}, {"until": False}, ["x"]):
                request(self, "POST", "/api/jobs/20261031-1012/continue", body, status=400)
            self.assertEqual(cont.call_count, 1)

    def test_continue_refusal_is_409(self):
        self.init_git()
        self.record("20261031-1012", "done")
        data = request(self, "POST", "/api/jobs/20261031-1012/continue", {}, status=409)
        self.assertEqual(data, {"error": "this job cannot be continued in state done"})
        self.assertEqual(self.spawned.call_count, 0)

    def test_merge_and_drop(self):
        self.init_git()
        self.git("switch", "-q", "-c", "auto/20261031-1012")
        (self.root / "work.txt").write_text("done\n")
        self.git("add", "work.txt")
        self.git("commit", "-q", "-m", "work")
        self.record("20261031-1012", "working")
        refused = request(self, "POST", "/api/jobs/20261031-1012/merge", {}, status=409)
        self.assertEqual(refused, {"error": "this job cannot be merged in state working"})
        refused = request(self, "POST", "/api/jobs/20261031-1012/drop", {}, status=409)
        self.assertEqual(refused, {"error": "this job cannot be dropped in state working"})
        self.record("20261031-1012", "done")
        data = request(self, "POST", "/api/jobs/20261031-1012/merge", {}, status=200)
        self.assertEqual(data, {"ok": True, "commit": self.git("rev-parse", "HEAD")})
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual(dash.sdd_job.load(self.root, "20261031-1012")["state"], "merged")

        self.git("switch", "-q", "-c", "auto/20261031-1100")
        self.record("20261031-1100", "stopped")
        data = request(self, "POST", "/api/jobs/20261031-1100/drop", {}, status=200)
        self.assertEqual(data, {"ok": True})
        self.assertEqual(dash.sdd_job.load(self.root, "20261031-1100")["state"], "dropped")
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertTrue(self.git("branch", "--list", "auto/20261031-1100"))  # the branch is kept


class ReadOnlyTest(unittest.TestCase):
    """data-model.md §4.8 (FR-020): the dashboard's source has no way to write or run anything,
    except `write_control()` and `write_answer()` (FR-033), which write `control.json` and one answer file
    in the supervisor's state directory, outside the project."""

    SOURCE = HERE.parent / "bin" / "sdd-dashboard.py"
    FORBIDDEN = (r"\.unlink\(", r"subprocess", r"os\.remove", r"shutil\.", r"git ",
                 r"""open\([^)]*,\s*(mode\s*=\s*)?['"][rbt]*[wax+]""",  # a write-mode open(
                 r"""\.open\(\s*(mode\s*=\s*)?['"][rbt]*[wax+]""")  # a write-mode Path.open(

    def function_lines(self, source, name):
        """The line numbers (1-based) of the body of the top-level function `name`."""
        import ast
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return range(node.body[0].lineno, node.end_lineno + 1)
        self.fail(f"no function {name}")

    def test_source_has_no_writes(self):
        source = self.SOURCE.read_text(encoding="utf-8")
        for pattern in self.FORBIDDEN:
            self.assertIsNone(re.search(pattern, source), pattern)
        lines = source.splitlines()
        writer = [*self.function_lines(source, "write_control"), *self.function_lines(source, "write_answer")]
        for pattern in (r"write_text", r"os\.replace"):
            hits = [n for n, line in enumerate(lines, 1) if re.search(pattern, line)]
            self.assertTrue(hits, pattern)
            self.assertEqual([n for n in hits if n not in writer], [], pattern)
        # The only signal is the liveness probe `os.kill(pid, 0)` in supervisor_alive() (§12.3).
        alive = self.function_lines(source, "supervisor_alive")
        kills = [(n, line.strip()) for n, line in enumerate(lines, 1) if re.search(r"os\.kill", line)]
        self.assertTrue(kills)
        for n, line in kills:
            self.assertIn(n, alive, line)
            self.assertEqual(line, "os.kill(pid, 0)")

    def test_write_control_leaves_the_project_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            project = base / "project"
            (project / ".sdd" / "runs").mkdir(parents=True)
            (project / "specs" / "001").mkdir(parents=True)
            (project / ".sdd" / "config.toml").write_text("[run]\n", encoding="utf-8")
            (project / ".sdd" / "run.json").write_text('{"run_id": "20261028-1000"}', encoding="utf-8")
            (project / "specs" / "001" / "tasks.md").write_text("# Tasks\n", encoding="utf-8")

            def snapshot():
                return {str(p.relative_to(project)): (p.read_bytes() if p.is_file() else None)
                        for p in sorted(project.rglob("*"))}

            before = snapshot()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(base / "state")}):
                logs = dash.logs_dir(project)
                self.assertTrue(dash.write_control(logs, "stop", "now"))
            self.assertEqual(snapshot(), before)
            target = logs / "control.json"
            self.assertTrue(target.is_file())
            slug = re.sub(r"[^A-Za-z0-9]+", "-", str(project)).strip("-")
            self.assertEqual(target.parent, base / "state" / "speckit-agents" / slug)
            self.assertNotIn(project, target.parents)
            self.assertEqual(json.loads(target.read_text())["command"], "stop")


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

    # Sample feature files, in the folder `run.feature` names
    feature = PROJECT / "specs" / "001"
    feature.mkdir(parents=True)
    (feature / "spec.md").write_text("# Feature Spec\n\n## User Story 1\n\nDo something.\n")
    (feature / "plan.md").write_text("# Plan\n\n- [ ] T101 Design\n- [ ] T102 Implement\n")
    (feature / "tasks.md").write_text("# Tasks\n\n## Phase 1: Setup\n\n- [x] T001 [P] First task in `a.py`\n- [ ] T002 [manual] Needs a person in `b.py`\n")
    (feature / "BLOCKED.md").write_text("## T002\n- status: open\n- reason: needs recording\n- question: Who will record?\n")
    (feature / "decisions.md").write_text("# Decisions\n\n- T011: Use Playwright (2026-10-24)\n")
    (PROJECT / ".sdd" / "config.toml").write_text("[run]\nfeature = \"specs/001\"\n")

    # Sample run 20261025-1200: workflows.json pointing at a wf_… directory with a journal
    wf = write_workflow_dir(PROJECT.resolve() / "transcripts" / "wf_smoke")
    write_run(PROJECT, "20261025-1200", [wf])
    (PROJECT / ".sdd" / "runs" / "20261025-1200.md").write_text("# Run Report\n\n- approved: T1\n- blocked: T2\n")
    (PROJECT / ".sdd" / "runs" / "20261027-1431.md").write_text("# Run Report\n\n- approved: T1\n- blocked: T2\n")

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
        assert r["id"] == "20261025-1200", f"expected 20261025-1200, got {r['id']}"
        assert r["approved"] == 1, f"expected 1 approved, got {r['approved']}"
        assert r["blocked"] == 1, f"expected 1 blocked, got {r['blocked']}"
        assert r["finished"] is True
        assert r["stopped"] is None
        print("PASS: /api/runs")

        # /api/runs/<id>/events
        evs = api("/api/runs/20261025-1200/events")["events"]
        assert len(evs) == 6, f"expected 6 events, got {len(evs)}"
        assert evs[0]["kind"] == "task.started"
        assert evs[-1]["kind"] == "task.blocked"
        print("PASS: /api/runs/20261025-1200/events")

        # /api/runs/<id>/report
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/runs/20261027-1431/report")
        with urllib.request.urlopen(req, timeout=5) as resp:
            report = resp.read().decode()
        assert "# Run Report" in report
        assert "approved: T1" in report
        print("PASS: /api/runs/20261027-1431/report")

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
        assert tasks["phases"][0]["title"] == "Setup"
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
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/runs/20261027-0000/report", timeout=5)
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
