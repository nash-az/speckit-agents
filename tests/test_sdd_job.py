"""bin/sdd-job.py (spec 005, data-model §1 and §7): the Job record, its id and its default title."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_job", HERE.parent / "bin" / "sdd-job.py")
job = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(job)


def record(job_id, state="working", **extra):
    r = {"id": job_id, "title": "Phase 11", "feature": "003-plugin-redesign", "scope": {"phases": ["11"]},
         "scope_tasks": ["T053"], "blocked_at_start": 0,
         "limits": {"max_tasks": None, "until": None, "max_runs": 20},
         "branch": f"auto/{job_id}", "base": {"branch": "main", "commit": "fb10ba3"}, "state": state,
         "reason": None, "runs": [], "no_progress_runs": 0, "created": 1793450000, "ended": None,
         "merge_commit": None}
    r.update(extra)
    return r


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_keeps_unknown_keys(self):
        r = record("20261031-1012", future_field={"a": [1, 2]})
        path = job.save(self.root, r)
        self.assertEqual(path, self.root / ".sdd" / "jobs" / "20261031-1012.json")
        self.assertEqual(job.load(self.root, "20261031-1012"), r)
        loaded = job.load(self.root, "20261031-1012")
        loaded["state"] = "waiting"
        job.save(self.root, loaded)
        again = job.load(self.root, "20261031-1012")
        self.assertEqual(again["state"], "waiting")
        self.assertEqual(again["future_field"], {"a": [1, 2]})

    def test_atomic_write_leaves_no_tmp(self):
        job.save(self.root, record("20261031-1012"))
        job.save(self.root, record("20261031-1012", state="done"))
        names = sorted(p.name for p in (self.root / ".sdd" / "jobs").iterdir())
        self.assertEqual(names, ["20261031-1012.json"])
        self.assertFalse(any(n.endswith(".tmp") for n in names))
        json.loads((self.root / ".sdd" / "jobs" / "20261031-1012.json").read_text())

    def test_save_refuses_a_bad_id(self):
        for bad in (None, "", "../x", "2026-10-31", "20261031-1012/../x"):
            with self.assertRaises(ValueError):
                job.save(self.root, {"id": bad})
        self.assertFalse((self.root / ".sdd" / "jobs").exists())

    def test_load_missing_or_malformed(self):
        self.assertIsNone(job.load(self.root, "20261031-1012"))
        self.assertIsNone(job.load(self.root, "../../etc/passwd"))
        d = self.root / ".sdd" / "jobs"
        d.mkdir(parents=True)
        (d / "20261031-1012.json").write_text("{not json")
        self.assertIsNone(job.load(self.root, "20261031-1012"))

    def test_list_newest_first(self):
        self.assertEqual(job.list_jobs(self.root), [])
        for i in ("20261030-0900", "20261031-1012", "20261031-1012-2", "20261031-1012-10", "20261029-2359"):
            job.save(self.root, record(i))
        (self.root / ".sdd" / "jobs" / "notes.json").write_text("{}")
        (self.root / ".sdd" / "jobs" / "20261031-1012.json.tmp").write_text("{}")
        self.assertEqual([r["id"] for r in job.list_jobs(self.root)],
                         ["20261031-1012-10", "20261031-1012-2", "20261031-1012", "20261030-0900",
                          "20261029-2359"])

    def test_open_job(self):
        self.assertIsNone(job.open_job(self.root))
        job.save(self.root, record("20261029-0900", state="merged"))
        job.save(self.root, record("20261030-0900", state="dropped"))
        self.assertIsNone(job.open_job(self.root))
        for state in ("working", "waiting", "held", "stopped", "failed", "done"):
            job.save(self.root, record("20261031-0900", state=state))
            self.assertEqual(job.open_job(self.root)["id"], "20261031-0900", state)
        job.save(self.root, record("20261031-0900", state="merged"))
        self.assertIsNone(job.open_job(self.root))


class IdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.now = time.mktime((2026, 10, 31, 10, 12, 30, 0, 0, -1))

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_time_form(self):
        self.assertEqual(job.new_id(self.root, self.now), "20261031-1012")
        self.assertRegex(job.new_id(self.root), r"^\d{8}-\d{4}$")

    def test_suffixes_when_taken(self):
        job.save(self.root, record("20261031-1012"))
        self.assertEqual(job.new_id(self.root, self.now), "20261031-1012-2")
        job.save(self.root, record("20261031-1012-2"))
        self.assertEqual(job.new_id(self.root, self.now), "20261031-1012-3")
        job.save(self.root, record("20261031-1012-3"))
        self.assertEqual(job.new_id(self.root, self.now), "20261031-1012-4")


class TitleTest(unittest.TestCase):
    def test_every_form(self):
        self.assertEqual(job.default_title({"phases": ["11"]}), "Phase 11")
        self.assertEqual(job.default_title({"phases": ["3", "4", "5"]}), "Phases 3–5")
        self.assertEqual(job.default_title({"phases": ["5", "3", "4"]}), "Phases 3–5")
        self.assertEqual(job.default_title({"phases": ["3", "5"]}), "Phases 3, 5")
        self.assertEqual(job.default_title({"phases": ["3", "4", "5", "7"]}), "Phases 3–5, 7")
        self.assertEqual(job.default_title({"phases": ["2a", "3"]}), "Phases 2a, 3")
        self.assertEqual(job.default_title({"tasks": ["T061", "T063"]}), "T061, T063")
        self.assertEqual(job.default_title({"tasks": ["T061"]}), "T061")
        self.assertEqual(job.default_title({"all": True}), "All open tasks")
        self.assertEqual(job.default_title({}), "All open tasks")


def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


class DisplayStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "project"
        self.root.mkdir()
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(self.tmp.name) / "state")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write_status(self, **fields):
        (job.sdd_supervise.state_dir(self.root) / "status.json").write_text(json.dumps(fields))

    def test_paused_needs_working_waiting_and_a_live_pid(self):
        self.write_status(state="waiting", pid=os.getpid(), resets_at=1793460000, keep_paused=True)
        self.assertEqual(job.display_state(self.root, record("20261031-1012")),
                         {"state": "paused", "resets_at": 1793460000, "keep_paused": True})
        self.write_status(state="waiting", pid=os.getpid(), resets_at=1793460000)
        self.assertEqual(job.display_state(self.root, record("20261031-1012"))["keep_paused"], False)

    def test_waiting_status_with_a_dead_pid_is_interrupted(self):
        self.write_status(state="waiting", pid=dead_pid(), resets_at=1793460000)
        self.assertEqual(job.display_state(self.root, record("20261031-1012")), {"state": "interrupted"})

    def test_working_without_a_live_supervisor_is_interrupted(self):
        self.assertEqual(job.display_state(self.root, record("20261031-1012")), {"state": "interrupted"})
        self.write_status(state="running", pid=dead_pid())
        self.assertEqual(job.display_state(self.root, record("20261031-1012")), {"state": "interrupted"})
        self.write_status(state="running", pid="12")
        self.assertEqual(job.display_state(self.root, record("20261031-1012")), {"state": "interrupted"})

    def test_working_with_a_live_running_supervisor_stays_working(self):
        self.write_status(state="running", pid=os.getpid())
        self.assertEqual(job.display_state(self.root, record("20261031-1012")), {"state": "working"})

    def test_other_stored_states_are_shown_as_stored(self):
        self.write_status(state="waiting", pid=os.getpid(), resets_at=1793460000)
        for state in ("waiting", "held", "stopped", "failed", "done", "merged", "dropped"):
            self.assertEqual(job.display_state(self.root, record("20261031-1012", state=state)),
                             {"state": state}, state)
        self.write_status(state="waiting", pid=dead_pid())
        self.assertEqual(job.display_state(self.root, record("20261031-1012", state="held")), {"state": "held"})


class ScopeArgsTest(unittest.TestCase):
    def test_each_form(self):
        self.assertEqual(job.scope_args(record("20261031-1012", scope={"phases": ["3", "5"]})), "--phases 3,5")
        self.assertEqual(job.scope_args(record("20261031-1012", scope={"phases": ["11"]})), "--phases 11")
        self.assertEqual(job.scope_args(record("20261031-1012", scope={"tasks": ["T061", "T063"]})),
                         "--tasks T061,T063")
        self.assertEqual(job.scope_args(record("20261031-1012", scope={"all": True})), "")
        self.assertEqual(job.scope_args(record("20261031-1012", scope={})), "")


TASKS = """# Tasks

## Phase 1: Setup

- [x] T001 Done earlier `a.py`
- [ ] T002 Outside the scope `b.py`

## Phase 2: Work

- [x] T003 Ticked `c.py`
- [ ] T004 Runnable `d.py`
- [ ] T005 [manual] Record audio
- [ ] T006 Blocked one `e.py`
- [ ] T007 Behind a held task, after T005 `f.py`
- [ ] T008 Dropped `g.py`
- [ ] T009 Added later `h.py`
"""

BLOCKED = """# Blocked

## T006
- status: open
- tag: failed
- reason: checks failed
- question: what now?

## T008
- status: wontfix
"""


class ProgressAndLimitsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.feature = self.root / "specs" / "003-plugin-redesign"
        self.feature.mkdir(parents=True)
        (self.feature / "tasks.md").write_text(TASKS)
        (self.feature / "BLOCKED.md").write_text(BLOCKED)

    def tearDown(self):
        self.tmp.cleanup()

    def scoped(self, **extra):
        base = {"scope": {"phases": ["2"]},
                "scope_tasks": ["T003", "T004", "T005", "T006", "T007", "T008"]}
        base.update(extra)
        return record("20261031-1012", **base)

    def test_progress_counts_ticked_held_and_blocked(self):
        # done T003; waiting T005 (manual), T006 (open entry); left T004, T007 (dependency), T009 (joined
        # the phase later); T008 (wontfix) is dropped work and not counted.
        self.assertEqual(job.progress(self.root, self.scoped()),
                         {"done": 1, "total": 6, "waiting_on_you": 2, "left": 3})

    def test_progress_task_scope_and_all(self):
        r = self.scoped(scope={"tasks": ["T001", "T004", "T005"]}, scope_tasks=["T004", "T005"])
        self.assertEqual(job.progress(self.root, r), {"done": 0, "total": 2, "waiting_on_you": 1, "left": 1})
        r = self.scoped(scope={"all": True}, scope_tasks=[])
        self.assertEqual(job.progress(self.root, r), {"done": 0, "total": 6, "waiting_on_you": 2, "left": 4})

    def test_progress_follows_ticks(self):
        tasks = self.feature / "tasks.md"
        tasks.write_text(TASKS.replace("- [ ] T004", "- [x] T004"))
        self.assertEqual(job.progress(self.root, self.scoped()),
                         {"done": 2, "total": 6, "waiting_on_you": 2, "left": 2})

    def test_no_limit(self):
        self.assertIsNone(job.limit_reached(self.root, self.scoped(), now=1793450000))

    def test_max_tasks(self):
        r = self.scoped(limits={"max_tasks": 2, "until": None, "max_runs": 20})
        self.assertIsNone(job.limit_reached(self.root, r))
        (self.feature / "tasks.md").write_text(TASKS.replace("- [ ] T004", "- [x] T004"))
        self.assertEqual(job.limit_reached(self.root, r), "max_tasks")
        # Only scope_tasks count: T001 is ticked but not in scope.
        r = self.scoped(scope_tasks=["T001", "T004"], limits={"max_tasks": 2, "until": None, "max_runs": 20})
        self.assertEqual(job.limit_reached(self.root, r), "max_tasks")
        r = self.scoped(scope_tasks=["T002", "T004"], limits={"max_tasks": 2, "until": None, "max_runs": 20})
        self.assertIsNone(job.limit_reached(self.root, r))

    def test_until(self):
        r = self.scoped(limits={"max_tasks": None, "until": 1793450000, "max_runs": 20})
        self.assertIsNone(job.limit_reached(self.root, r, now=1793449999))
        self.assertEqual(job.limit_reached(self.root, r, now=1793450000), "until")

    def test_max_runs(self):
        r = self.scoped(runs=["20261031-1012"], limits={"max_tasks": None, "until": None, "max_runs": 2})
        self.assertIsNone(job.limit_reached(self.root, r))
        r["runs"].append("20261031-1530")
        self.assertEqual(job.limit_reached(self.root, r), "max_runs")

    def test_first_reached_limit_wins(self):
        r = self.scoped(runs=["a", "b"], limits={"max_tasks": 1, "until": 1, "max_runs": 2})
        self.assertEqual(job.limit_reached(self.root, r, now=2), "max_tasks")
        r["limits"]["max_tasks"] = None
        self.assertEqual(job.limit_reached(self.root, r, now=2), "until")


if __name__ == "__main__":
    unittest.main()
