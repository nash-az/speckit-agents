"""bin/sdd-job.py (spec 005, data-model §1 and §7): the Job record, its id and its default title."""
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
