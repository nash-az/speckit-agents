"""bin/sdd-job.py (spec 005, data-model §1 and §7): the Job record, its id, its default title, Merge, Drop
and the CLI."""
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


PENDING = {"state": "pending", "commit": None, "files": []}


class RunStateTest(unittest.TestCase):
    """009 plan "Data model": `owns_run_json`, `normalize`, `shown_state`, `is_open`, `open_run`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "project"
        (self.root / ".sdd").mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(self.tmp.name) / "state")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write_status(self, **fields):
        (job.sdd_supervise.state_dir(self.root) / "status.json").write_text(json.dumps(fields))

    def write_run_json(self, **fields):
        (self.root / ".sdd" / "run.json").write_text(json.dumps(fields))

    def own_run_json(self, job_id="20261031-1012"):
        self.write_run_json(run_id="20261031-1013", job=job_id, branch=f"auto/{job_id}")

    # owns_run_json

    def test_owns_run_json_by_job_or_branch(self):
        r = record("20261031-1012")
        self.assertFalse(job.owns_run_json(self.root, r))
        self.write_run_json(job="20261031-1012")
        self.assertTrue(job.owns_run_json(self.root, r))
        self.write_run_json(job="20261101-0900", branch="auto/20261031-1012")
        self.assertTrue(job.owns_run_json(self.root, r))
        self.write_run_json(job="20261101-0900", branch="auto/other")
        self.assertFalse(job.owns_run_json(self.root, r))
        self.write_run_json(job=None, branch=None)
        self.assertFalse(job.owns_run_json(self.root, record("20261031-1012", branch=None)))
        (self.root / ".sdd" / "run.json").write_text("{not json")
        self.assertFalse(job.owns_run_json(self.root, r))
        (self.root / ".sdd" / "run.json").write_text("[]")
        self.assertFalse(job.owns_run_json(self.root, r))

    # normalize: one test per table row, with and without a run.json of its own

    def test_merged_is_ended_landed(self):
        for own in (False, True):
            if own:
                self.own_run_json()
            n = job.normalize(self.root, record("20261031-1012", state="merged", merge_commit="abc123"))
            self.assertEqual((n["state"], n["outcome"]), ("ended", "done"), own)
            self.assertEqual(n["landing"], {"state": "landed", "commit": "abc123", "files": []})
            n = job.normalize(self.root, record("20261031-1012", state="merged", reason="max_tasks reached",
                                                merge_commit="abc123"))
            self.assertEqual(n["outcome"], "limit")

    def test_dropped_is_ended_not_landed(self):
        for own in (False, True):
            if own:
                self.own_run_json()
            n = job.normalize(self.root, record("20261031-1012", state="dropped", reason="stopped by you"))
            self.assertEqual((n["state"], n["outcome"]), ("ended", "dropped"), own)
            self.assertEqual(n["landing"], {"state": "not-landed", "commit": None, "files": []})

    def test_closed_005_states_with_their_run_json_are_suspended(self):
        self.own_run_json()
        for state in ("done", "stopped", "failed", "waiting", "held"):
            n = job.normalize(self.root, record("20261031-1012", state=state, reason="kept paused at the reset"))
            self.assertEqual(n["state"], "suspended", state)
            self.assertEqual(n["reason"], "kept paused at the reset")
            self.assertNotIn("outcome", n)
            self.assertNotIn("landing", n)

    def test_closed_005_states_without_their_run_json_are_ended_pending(self):
        self.write_run_json(job="20261101-0900", branch="auto/20261101-0900")  # another run's
        for state in ("done", "stopped", "failed", "waiting"):
            n = job.normalize(self.root, record("20261031-1012", state=state))
            self.assertEqual((n["state"], n["outcome"], n["landing"]), ("ended", state, PENDING), state)
        n = job.normalize(self.root, record("20261031-1012", state="stopped", reason="until 18:00 reached"))
        self.assertEqual(n["outcome"], "limit")

    def test_held_without_its_run_json_is_ended_stopped(self):
        n = job.normalize(self.root, record("20261031-1012", state="held"))
        self.assertEqual((n["state"], n["outcome"], n["landing"]), ("ended", "stopped", PENDING))

    def test_working_stays_working(self):
        for own in (False, True):
            if own:
                self.own_run_json()
            n = job.normalize(self.root, record("20261031-1012"))
            self.assertEqual(n["state"], "working", own)
            self.assertNotIn("landing", n)

    def test_outcome_from_the_reason(self):
        cases = [("max_runs reached", "limit"), ("only items that need you are left", "waiting"),
                 ("no progress in two runs", "failed"), ("stopped by you", "stopped"),
                 ("kept paused for 12 hours", "stopped"), ("something else", "done"), (None, "done")]
        for reason, outcome in cases:
            n = job.normalize(self.root, record("20261031-1012", state="done", reason=reason))
            self.assertEqual(n["outcome"], outcome, reason)
        n = job.normalize(self.root, record("20261031-1012", state="failed", reason="something else"))
        self.assertEqual(n["outcome"], "failed")

    def test_009_records_kept_and_unknown_states_read_as_working(self):
        landed = {"state": "landed", "commit": "abc", "files": []}
        r = record("20261031-1012", state="ended", outcome="done", landing=landed)
        self.assertEqual(job.normalize(self.root, r), r)
        n = job.normalize(self.root, record("20261031-1012", state="ended", outcome="done", landing="x"))
        self.assertEqual(n["landing"], PENDING)
        r = record("20261031-1012", state="suspended", reason="the run was cut off")
        self.assertEqual(job.normalize(self.root, r), r)
        self.assertEqual(job.normalize(self.root, record("20261031-1012", state="interrupted"))["state"], "working")
        self.assertEqual(job.normalize(self.root, record("20261031-1012", state=None))["state"], "working")

    def test_normalize_returns_a_copy_and_writes_nothing(self):
        r = record("20261031-1012", state="merged", merge_commit="abc")
        job.save(self.root, r)
        before = (self.root / ".sdd" / "jobs" / "20261031-1012.json").read_text()
        n = job.normalize(self.root, r)
        n["base"]["branch"] = "changed"
        self.assertEqual(r["state"], "merged")
        self.assertEqual(r["base"]["branch"], "main")
        self.assertEqual((self.root / ".sdd" / "jobs" / "20261031-1012.json").read_text(), before)

    # shown_state

    def test_shown_paused_with_a_live_supervisor_in_the_usage_wait(self):
        self.write_status(state="waiting", pid=os.getpid(), resets_at=1793460000, keep_paused=True)
        self.assertEqual(job.shown_state(self.root, record("20261031-1012")),
                         {"state": "paused", "outcome": None, "reason": None, "landing": None,
                          "resets_at": 1793460000, "keep_paused": True})
        self.write_status(state="waiting", pid=os.getpid(), resets_at=1793460000)
        self.assertIs(job.shown_state(self.root, record("20261031-1012"))["keep_paused"], False)

    def test_shown_working_with_a_live_running_supervisor(self):
        self.write_status(state="running", pid=os.getpid())
        self.assertEqual(job.shown_state(self.root, record("20261031-1012")),
                         {"state": "working", "outcome": None, "reason": None, "landing": None})

    def test_shown_suspended_when_no_supervisor_and_its_run_json(self):
        self.own_run_json()
        self.write_status(state="waiting", pid=dead_pid(), resets_at=1793460000)
        self.assertEqual(job.shown_state(self.root, record("20261031-1012")),
                         {"state": "suspended", "outcome": None, "reason": "the run was cut off", "landing": None})
        shown = job.shown_state(self.root, record("20261031-1012", reason="kept paused at the reset"))
        self.assertEqual(shown["reason"], "kept paused at the reset")

    def test_shown_ended_when_no_supervisor_and_no_run_json_of_its_own(self):
        expected = {"state": "ended", "outcome": "stopped",
                    "reason": "the supervisor ended without recording the run's end", "landing": PENDING}
        self.assertEqual(job.shown_state(self.root, record("20261031-1012")), expected)
        self.write_run_json(job="20261101-0900", branch="auto/20261101-0900")
        self.write_status(state="running", pid=dead_pid())
        self.assertEqual(job.shown_state(self.root, record("20261031-1012")), expected)

    def test_shown_otherwise_the_normalized_state(self):
        self.write_status(state="waiting", pid=os.getpid(), resets_at=1793460000)
        self.assertEqual(job.shown_state(self.root, record("20261031-1012", state="merged", merge_commit="abc")),
                         {"state": "ended", "outcome": "done", "reason": None,
                          "landing": {"state": "landed", "commit": "abc", "files": []}})
        self.assertEqual(job.shown_state(self.root, record("20261031-1012", state="suspended", reason="r")),
                         {"state": "suspended", "outcome": None, "reason": "r", "landing": None})
        self.assertEqual(job.shown_state(self.root, record("20261031-1012", state="waiting",
                                                           reason="only items that need you are left")),
                         {"state": "ended", "outcome": "waiting", "reason": "only items that need you are left",
                          "landing": PENDING})

    # is_open and open_run

    def test_is_open_for_each_landing_state(self):
        for state, opened in (("landed", False), ("nothing", False), ("not-landed", False),
                              ("needs-merge", True), ("pending", True)):
            r = record("20261031-1012", state="ended", outcome="done",
                       landing={"state": state, "commit": None, "files": []})
            self.assertIs(job.is_open(self.root, r), opened, state)

    def test_is_open_for_working_paused_and_suspended(self):
        self.assertTrue(job.is_open(self.root, record("20261031-1012", state="suspended")))
        self.write_status(state="running", pid=os.getpid())
        self.assertTrue(job.is_open(self.root, record("20261031-1012")))
        self.write_status(state="waiting", pid=os.getpid(), resets_at=1793460000)
        self.assertTrue(job.is_open(self.root, record("20261031-1012")))
        self.assertFalse(job.is_open(self.root, record("20261031-1012", state="merged")))
        self.assertFalse(job.is_open(self.root, record("20261031-1012", state="dropped")))

    def test_open_run_chooses_the_newest_open_record(self):
        self.assertIsNone(job.open_run(self.root))
        landed = {"state": "landed", "commit": "abc", "files": []}
        job.save(self.root, record("20261029-0900", state="ended", outcome="done", landing=landed))
        job.save(self.root, record("20261030-0900", state="merged", merge_commit="abc"))
        self.assertIsNone(job.open_run(self.root))
        job.save(self.root, record("20261030-1000", state="suspended"))
        job.save(self.root, record("20261031-0900", state="done"))  # 005, no run.json: ended, landing pending
        job.save(self.root, record("20261101-0900", state="dropped"))
        self.assertEqual(job.open_run(self.root)["id"], "20261031-0900")
        job.save(self.root, record("20261031-0900", state="ended", outcome="done", landing=landed))
        self.assertEqual(job.open_run(self.root)["id"], "20261030-1000")


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


JOB_ID = "20261031-1012"
JOB_TASKS = """# Tasks

## Phase 1: Work

- [ ] T001 One `a.txt`
"""


class GitProjectCase(unittest.TestCase):
    """A temp repo: `main` with one commit, the run branch `auto/<JOB_ID>` one commit ahead and checked out,
    and a `done` record for it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "project"
        self.root.mkdir()
        self.state_home = str(Path(self.tmp.name) / "state")
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.state_home})
        self.env.start()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "commit.gpgsign", "false")
        (self.root / ".gitignore").write_text(".sdd/jobs/\n.sdd/run.json\n")
        feature = self.root / "specs" / "003-plugin-redesign"
        feature.mkdir(parents=True)
        (feature / "tasks.md").write_text(JOB_TASKS)
        self.git("add", ".gitignore", "specs")
        self.write_commit("a.txt", "base\n", "base")
        self.base_commit = self.head()
        self.git("switch", "-q", "-c", f"auto/{JOB_ID}")
        self.write_commit("b.txt", "job\n", "T001 job work")
        self.job_commit = self.head()
        job.save(self.root, record(JOB_ID, state="done", feature="003-plugin-redesign",
                                   scope={"phases": ["1"]}, scope_tasks=["T001"],
                                   base={"branch": "main", "commit": self.base_commit[:7]}))

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, check=True).stdout

    def head(self, ref="HEAD"):
        return self.git("rev-parse", ref).strip()

    def branch(self):
        return self.git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def write_commit(self, name, text, message):
        (self.root / name).write_text(text)
        self.git("add", name)
        self.git("commit", "-q", "-m", message)

    def snapshot(self):
        return (self.branch(), self.head("main"), self.head(f"auto/{JOB_ID}"),
                self.git("status", "--porcelain"), job.load(self.root, JOB_ID))

    def assertRefused(self, call, message):
        before = self.snapshot()
        with self.assertRaises(job.Refused) as caught:
            call(self.root, JOB_ID)
        self.assertEqual(str(caught.exception), message)
        self.assertEqual(self.snapshot(), before)


class RunCliTest(GitProjectCase):
    """The 009 CLI on temp repos: merge = land, drop only a suspended run, list/show give the shown state."""

    def cli(self, *args):
        env = {**os.environ, "XDG_STATE_HOME": self.state_home}
        return subprocess.run([sys.executable, str(HERE.parent / "bin" / "sdd-job.py"), *args],
                              capture_output=True, text=True, env=env)

    def set_record(self, **fields):
        job.save(self.root, {**job.load(self.root, JOB_ID), **fields})

    def test_cli_merge_lands_an_ended_run_with_landing_pending(self):
        """009 FR-023: merge = land, the same JSON."""
        self.set_record(state="ended", outcome="done", landing=PENDING)
        merged = self.cli("merge", JOB_ID, str(self.root))
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
        self.assertEqual(json.loads(merged.stdout),
                         {"contract": "1.0", "landed": True, "commit": self.job_commit, "fast_forward": True,
                          "landing": {"state": "landed", "commit": self.job_commit, "files": []},
                          "summary": f".sdd/runs/{JOB_ID}.json"})
        self.assertEqual(self.branch(), "main")
        saved = job.load(self.root, JOB_ID)
        self.assertEqual((saved["state"], saved["landing"]["state"]), ("ended", "landed"))
        self.assertEqual(self.cli().returncode, 2)

    def test_cli_merge_refuses_a_working_run(self):
        self.set_record(state="working")
        status = job.sdd_supervise.state_dir(self.root) / "status.json"
        status.write_text(json.dumps({"state": "running", "pid": os.getpid()}))
        before = self.snapshot()
        refused = self.cli("merge", JOB_ID, str(self.root))
        self.assertEqual((refused.returncode, json.loads(refused.stdout)),
                         (1, {"contract": "1.0", "error": "this run has not ended: it is working"}))
        self.assertEqual(self.snapshot(), before)

    def test_cli_drop_refuses_an_ended_run(self):
        self.set_record(state="ended", outcome="done", landing=PENDING)
        before = self.snapshot()
        refused = self.cli("drop", JOB_ID, str(self.root))
        self.assertEqual((refused.returncode, json.loads(refused.stdout)),
                         (1, {"contract": "1.0", "error": "this run cannot be dropped: it is ended"}))
        self.assertEqual(self.snapshot(), before)

    def test_cli_list_and_show_give_the_shown_state(self):
        """A 005 stopped record with its own run.json reads suspended (shown_state, not display_state)."""
        self.set_record(state="stopped")
        (self.root / ".sdd").mkdir(exist_ok=True)
        (self.root / ".sdd" / "run.json").write_text(
            json.dumps({"run_id": "20261031-1013", "job": JOB_ID, "branch": f"auto/{JOB_ID}"}))
        listed = self.cli("list", str(self.root))
        self.assertEqual(listed.returncode, 0, listed.stderr)
        jobs = json.loads(listed.stdout)
        self.assertEqual([j["id"] for j in jobs], [JOB_ID])
        self.assertEqual(jobs[0]["shown"]["state"], "suspended")
        self.assertEqual(jobs[0]["progress"], {"done": 0, "total": 1, "waiting_on_you": 0, "left": 1})

        shown = self.cli("show", JOB_ID, str(self.root))
        self.assertEqual(shown.returncode, 0, shown.stderr)
        one = json.loads(shown.stdout)
        self.assertEqual((one["contract"], one["id"], one["shown"]["state"]), ("1.0", JOB_ID, "suspended"))

        missing = self.cli("show", "20261031-0000", str(self.root))
        self.assertEqual((missing.returncode, json.loads(missing.stdout)),
                         (1, {"contract": "1.0", "error": "no such job: 20261031-0000"}))


class LandTest(GitProjectCase):
    """009 `land`, `land_ready`, `end` and `suspend` on temp repos (plan "Engine changes" 2, FR-011)."""

    def setUp(self):
        super().setUp()
        self.set_record(state="ended", outcome="done", landing=PENDING)

    def set_record(self, **fields):
        job.save(self.root, {**job.load(self.root, JOB_ID), **fields})

    def move_base(self, name="c.txt", text="elsewhere\n"):
        """A commit on `main` that the run branch lacks; the checkout goes back to the run branch."""
        self.git("switch", "-q", "main")
        self.write_commit(name, text, "base moved")
        moved = self.head()
        self.git("switch", "-q", f"auto/{JOB_ID}")
        return moved

    def on_base(self, path):
        return subprocess.run(["git", "cat-file", "-e", f"main:{path}"], cwd=self.root).returncode == 0

    # land

    def test_fast_forward(self):
        result = job.land(self.root, JOB_ID)
        landing = {"state": "landed", "commit": self.job_commit, "files": []}
        self.assertEqual(result, {"landed": True, "commit": self.job_commit, "fast_forward": True,
                                  "landing": landing, "summary": f".sdd/runs/{JOB_ID}.json"})
        self.assertEqual(self.branch(), "main")
        self.assertEqual(self.head("main^"), self.job_commit)
        self.assertEqual(self.head(f"auto/{JOB_ID}"), self.job_commit)  # branch kept
        saved = job.load(self.root, JOB_ID)
        self.assertEqual((saved["state"], saved["landing"], saved["merge_commit"]),
                         ("ended", landing, self.job_commit))
        self.assertIsInstance(saved["ended"], int)

    def test_a_005_done_record_lands(self):
        old = {k: v for k, v in job.load(self.root, JOB_ID).items() if k not in ("landing", "outcome")}
        job.save(self.root, {**old, "state": "done", "reason": "every task in the scope is done or skipped"})
        self.assertTrue(job.land(self.root, JOB_ID)["landed"])
        saved = job.load(self.root, JOB_ID)
        self.assertEqual((saved["state"], saved["outcome"], saved["landing"]["state"]), ("ended", "done", "landed"))

    def test_merge_commit_with_the_message(self):
        moved = self.move_base()
        result = job.land(self.root, JOB_ID)
        self.assertFalse(result["fast_forward"])
        self.assertTrue(result["landed"])
        self.assertEqual(self.branch(), "main")
        self.assertEqual(result["commit"], self.head("main^"))
        self.assertEqual(self.git("log", "-1", "--format=%s", "main^").strip(), "merge: run Phase 11")
        self.assertEqual(self.git("log", "-1", "--format=%P", "main^").split(), [moved, self.job_commit])
        saved = job.load(self.root, JOB_ID)
        self.assertEqual((saved["landing"], saved["merge_commit"]),
                         ({"state": "landed", "commit": result["commit"], "files": []}, result["commit"]))

    def test_conflict_stores_needs_merge_and_goes_back_to_the_branch(self):
        self.git("switch", "-q", "main")
        self.write_commit("b.txt", "other\n", "base edits b")
        base = self.head()
        self.git("switch", "-q", f"auto/{JOB_ID}")
        with self.assertRaises(job.Refused) as caught:
            job.land(self.root, JOB_ID)
        self.assertEqual(str(caught.exception), "the merge conflicts in: b.txt")
        self.assertEqual(self.branch(), f"auto/{JOB_ID}")
        self.assertEqual(self.head("main"), base)
        self.assertEqual((self.root / "b.txt").read_text(), "job\n")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertFalse((self.root / ".git" / "MERGE_HEAD").exists())
        saved = job.load(self.root, JOB_ID)
        self.assertEqual((saved["state"], saved["landing"]),
                         ("ended", {"state": "needs-merge", "commit": None, "files": ["b.txt"]}))
        # A needs-merge landing can be tried again (after the person merged by hand, say).
        with self.assertRaises(job.Refused):
            job.land(self.root, JOB_ID)

    def test_nothing_to_land(self):
        self.git("switch", "-q", "main")
        self.git("merge", "-q", "--ff-only", f"auto/{JOB_ID}")
        self.git("switch", "-q", f"auto/{JOB_ID}")
        result = job.land(self.root, JOB_ID)
        nothing = {"state": "nothing", "commit": None, "files": []}
        self.assertEqual(result, {"landed": False, "commit": None, "fast_forward": False, "landing": nothing,
                                  "summary": f".sdd/runs/{JOB_ID}.json"})
        self.assertEqual(self.branch(), "main")
        self.assertEqual(self.head("main^"), self.job_commit)
        self.assertEqual(job.load(self.root, JOB_ID)["landing"], nothing)

    def test_refuses_a_run_that_has_not_ended(self):
        self.set_record(state="working")
        self.assertRefused(job.land, "this run has not ended: it is working")
        self.set_record(state="suspended")
        self.assertRefused(job.land, "this run has not ended: it is suspended")

    def test_refuses_a_landing_that_has_ended(self):
        for state in ("landed", "nothing", "not-landed"):
            self.set_record(landing={"state": state, "commit": None, "files": []})
            self.assertRefused(job.land, "this run has already ended its landing")
        self.set_record(state="merged", landing=None)  # a merged 005 Job reads as landed
        self.assertRefused(job.land, "this run has already ended its landing")

    def test_refuses_an_unknown_run(self):
        with self.assertRaises(job.Refused) as caught:
            job.land(self.root, "20261031-0000")
        self.assertEqual(str(caught.exception), "no such run: 20261031-0000")

    def test_refuses_while_a_run_is_active(self):
        run_json = self.root / ".sdd" / "run.json"
        run_json.write_text("{}")
        self.assertRefused(job.land, "a run is active")
        run_json.unlink()
        status = job.sdd_supervise.state_dir(self.root) / "status.json"
        status.write_text(json.dumps({"state": "running", "pid": os.getpid()}))
        self.assertRefused(job.land, "a run is active")

    def test_refuses_a_dirty_tree(self):
        (self.root / "b.txt").write_text("edited\n")
        (self.root / "new.txt").write_text("new\n")
        self.assertRefused(job.land, "the working tree has changes; commit or stash them first: b.txt, new.txt")

    def test_refuses_outside_a_git_repository(self):
        plain = Path(self.tmp.name).resolve() / "plain"
        plain.mkdir()
        job.save(plain, job.load(self.root, JOB_ID))
        with mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": self.tmp.name}):
            with self.assertRaises(job.Refused) as caught:
                job.land(plain, JOB_ID)
        self.assertEqual(str(caught.exception), "the project is not a git repository")
        self.assertEqual(job.load(plain, JOB_ID)["landing"], PENDING)

    # land_ready

    def test_land_ready_moves_the_base_and_leaves_a_dirty_tree(self):
        self.set_record(state="suspended", landing=None)
        (self.root / "b.txt").write_text("half done\n")
        (self.root / "new.txt").write_text("new\n")
        dirty = self.git("status", "--porcelain")
        self.assertEqual(job.land_ready(self.root, JOB_ID), self.job_commit)
        self.assertEqual(self.head("main"), self.job_commit)
        self.assertEqual(self.branch(), f"auto/{JOB_ID}")
        self.assertEqual(self.git("status", "--porcelain"), dirty)
        self.assertEqual((self.root / "b.txt").read_text(), "half done\n")
        self.assertEqual(job.load(self.root, JOB_ID)["landed_commit"], self.job_commit)
        self.assertIsNone(job.land_ready(self.root, JOB_ID))  # nothing new: they are the same commit

    def test_land_ready_leaves_a_base_that_is_not_an_ancestor(self):
        moved = self.move_base()
        self.assertIsNone(job.land_ready(self.root, JOB_ID))
        self.assertEqual(self.head("main"), moved)
        self.assertNotIn("landed_commit", job.load(self.root, JOB_ID))

    def test_land_ready_never_raises(self):
        self.assertIsNone(job.land_ready(self.root, "20261031-0000"))
        self.set_record(branch=None)
        self.assertIsNone(job.land_ready(self.root, JOB_ID))

    # end and suspend

    def test_end_commits_histories_before_landing_and_the_summary_after(self):
        """009 research R2: histories on the run branch before landing, the summary on the base after it."""
        self.set_record(state="working", outcome=None, landing=None)
        job.sdd_history.record_step(self.root, job.load(self.root, JOB_ID), "T001", "suspended", reason="cut off")
        result = job.end(self.root, JOB_ID, "done", None)
        self.assertEqual(result["finalize"]["files"], [".sdd/tasks/003-plugin-redesign/T001.jsonl"])
        self.assertTrue(result["land"]["landed"])
        self.assertEqual(result["land"]["summary"], f".sdd/runs/{JOB_ID}.json")
        self.assertEqual(self.branch(), "main")
        self.assertEqual(self.git("log", "-1", "--format=%s", "main").strip(), f"chore(run): {JOB_ID} summary")
        self.assertEqual(self.git("show", "--name-only", "--format=", "main").split(), [f".sdd/runs/{JOB_ID}.json"])
        landed = self.head("main^")
        self.assertEqual(landed, self.head(f"auto/{JOB_ID}"))
        self.assertEqual(self.git("log", "-1", "--format=%s", "main^").strip(), f"chore(run): {JOB_ID} history")
        self.assertNotIn(f".sdd/runs/{JOB_ID}.json", self.git("ls-tree", "-r", "--name-only", f"auto/{JOB_ID}"))
        saved = job.load(self.root, JOB_ID)
        self.assertEqual((saved["state"], saved["outcome"], saved["reason"], saved["landing"]["state"]),
                         ("ended", "done", None, "landed"))
        self.assertEqual(saved["merge_commit"], landed)
        summary = json.loads((self.root / ".sdd" / "runs" / f"{JOB_ID}.json").read_text())
        self.assertEqual(summary["landing"], {"state": "landed", "commit": landed, "files": []})
        self.assertEqual(summary["outcome"], "done")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_a_conflict_writes_no_summary_and_merge_after_resolving_writes_it(self):
        self.git("switch", "-q", "main")
        self.write_commit("b.txt", "other\n", "base edits b")
        self.git("switch", "-q", f"auto/{JOB_ID}")
        result = job.end(self.root, JOB_ID, "stopped", "stopped by you")
        self.assertEqual(result["land"]["error"], "the merge conflicts in: b.txt")
        self.assertNotIn("summary", result["land"])
        summary_file = self.root / ".sdd" / "runs" / f"{JOB_ID}.json"
        self.assertFalse(summary_file.exists())
        self.assertFalse(self.on_base(f".sdd/runs/{JOB_ID}.json"))
        # The person resolves the conflict on the run branch, then presses Merge (= land).
        self.write_commit("b.txt", "other\n", "resolve b")
        merged = job.land(self.root, JOB_ID)
        self.assertTrue(merged["landed"])
        self.assertFalse(merged["fast_forward"])
        self.assertEqual(merged["summary"], f".sdd/runs/{JOB_ID}.json")
        self.assertEqual(self.git("log", "-1", "--format=%s", "main").strip(), f"chore(run): {JOB_ID} summary")
        self.assertEqual(merged["commit"], self.head("main^"))
        summary = json.loads(summary_file.read_text())
        self.assertEqual(summary["landing"], {"state": "landed", "commit": merged["commit"], "files": []})
        self.assertEqual(summary["reason"], "stopped by you")

    def test_end_keeps_a_conflict_as_needs_merge(self):
        self.git("switch", "-q", "main")
        self.write_commit("b.txt", "other\n", "base edits b")
        self.git("switch", "-q", f"auto/{JOB_ID}")
        result = job.end(self.root, JOB_ID, "stopped", "stopped by you")
        self.assertEqual(result["land"]["error"], "the merge conflicts in: b.txt")
        self.assertEqual(self.branch(), f"auto/{JOB_ID}")
        saved = job.load(self.root, JOB_ID)
        self.assertEqual((saved["outcome"], saved["reason"]), ("stopped", "stopped by you"))
        self.assertEqual(saved["landing"], {"state": "needs-merge", "commit": None, "files": ["b.txt"]})

    def test_end_stores_another_refusal_as_pending(self):
        (self.root / ".sdd" / "run.json").write_text("{}")
        result = job.end(self.root, JOB_ID, "failed", "no progress in two runs")
        self.assertEqual(result["land"]["error"], "a run is active")
        self.assertEqual(self.head("main"), self.base_commit)
        saved = job.load(self.root, JOB_ID)
        self.assertEqual((saved["state"], saved["outcome"]), ("ended", "failed"))
        self.assertEqual(saved["landing"], {**PENDING, "reason": "a run is active"})

    def test_end_never_raises(self):
        self.assertEqual(job.end(self.root, "20261031-0000", "done", None)["land"]["error"],
                         "no such run: 20261031-0000")
        with mock.patch.object(job.sdd_history, "finalize", side_effect=RuntimeError("boom")):
            result = job.end(self.root, JOB_ID, "done", None)
        self.assertEqual(result["finalize"]["error"], "boom")
        self.assertTrue(result["land"]["landed"])

    def test_suspend_keeps_the_tree(self):
        self.set_record(state="working", outcome=None, landing=None)
        (self.root / "b.txt").write_text("half done\n")
        (self.root / "new.txt").write_text("new\n")
        dirty = self.git("status", "--porcelain")
        self.assertEqual(job.suspend(self.root, JOB_ID, "the run was cut off"), {"landed_commit": self.job_commit})
        self.assertEqual(self.branch(), f"auto/{JOB_ID}")
        self.assertEqual(self.git("status", "--porcelain"), dirty)
        self.assertEqual(self.head("main"), self.job_commit)
        saved = job.load(self.root, JOB_ID)
        self.assertEqual((saved["state"], saved["reason"], saved["landed_commit"]),
                         ("suspended", "the run was cut off", self.job_commit))

    def test_suspend_records_suspended_for_the_tasks_in_flight(self):
        """009 research R10: step `suspended` with the session and the reason for each task in flight."""
        self.set_record(state="working", outcome=None, landing=None)
        workflow_dir = Path(self.tmp.name).resolve() / "wf_suspend"
        workflow_dir.mkdir()
        (workflow_dir / "journal.jsonl").write_text(
            json.dumps({"type": "started", "key": "k1", "label": "impl:T002"}) + "\n")
        (self.root / ".sdd" / "run.json").write_text(json.dumps(
            {"run_id": "20261031-1203", "job": JOB_ID, "branch": f"auto/{JOB_ID}",
             "workflow_dir": str(workflow_dir)}))
        job.suspend(self.root, JOB_ID, "the run was cut off")
        entries = job.sdd_history.read(self.root, "003-plugin-redesign", "T002")
        self.assertEqual([(e["step"], e["run"], e["session"], e["reason"]) for e in entries],
                         [("suspended", JOB_ID, "20261031-1203", "the run was cut off")])

    def test_suspend_without_its_run_json_records_nothing(self):
        self.set_record(state="working", outcome=None, landing=None)
        job.suspend(self.root, JOB_ID, "the run was cut off")
        self.assertFalse((self.root / ".sdd" / "tasks").exists())

    def test_cli_land(self):
        env = {**os.environ, "XDG_STATE_HOME": self.state_home}
        landed = subprocess.run([sys.executable, str(HERE.parent / "bin" / "sdd-job.py"), "land", JOB_ID,
                                 str(self.root)], capture_output=True, text=True, env=env)
        self.assertEqual(landed.returncode, 0, landed.stdout + landed.stderr)
        self.assertEqual(json.loads(landed.stdout),
                         {"contract": "1.0", "landed": True, "commit": self.job_commit, "fast_forward": True,
                          "landing": {"state": "landed", "commit": self.job_commit, "files": []},
                          "summary": f".sdd/runs/{JOB_ID}.json"})
        again = subprocess.run([sys.executable, str(HERE.parent / "bin" / "sdd-job.py"), "land", JOB_ID,
                                str(self.root)], capture_output=True, text=True, env=env)
        self.assertEqual((again.returncode, json.loads(again.stdout)),
                         (1, {"contract": "1.0", "error": "this run has already ended its landing"}))


SESSION_ID = "20261031-1203"


class DropSuspendedTest(GitProjectCase):
    """009 `drop_suspended` on a temp repo: a suspended run with T001 in flight and half-done work in the tree."""

    def setUp(self):
        super().setUp()
        # As `/sdd-init` sets it up: the per-session directories under `.sdd/runs/` are git-ignored.
        (self.root / ".gitignore").write_text(".sdd/jobs/\n.sdd/run.json\n.sdd/runs/*/\n")
        self.write_commit(".gitignore", (self.root / ".gitignore").read_text(), "ignore run dirs")
        self.job_commit = self.head()
        self.workflow_dir = Path(self.tmp.name).resolve() / "wf_test"
        self.workflow_dir.mkdir()
        (self.workflow_dir / "journal.jsonl").write_text(
            json.dumps({"type": "started", "key": "k1", "label": "impl:T001"}) + "\n")
        (self.root / ".sdd" / "run.json").write_text(json.dumps(
            {"run_id": SESSION_ID, "job": JOB_ID, "branch": f"auto/{JOB_ID}",
             "workflow_dir": str(self.workflow_dir)}))
        job.save(self.root, {**job.load(self.root, JOB_ID), "state": "suspended", "reason": "the run was cut off"})
        (self.root / "b.txt").write_text("half done\n")
        (self.root / "new.txt").write_text("new\n")

    def test_drop_stashes_the_work_and_lands_the_finished_commits(self):
        result = job.drop_suspended(self.root, JOB_ID)
        message = f"sdd-run {JOB_ID}: dropped T001"
        self.assertEqual((result["dropped"], result["tasks"], result["stash"]), (True, ["T001"], message))
        self.assertTrue(result["end"]["land"]["landed"])
        # The tree is clean, and the stash holds both the edit and the new file.
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("stash", "list", "--format=%s").strip(), f"On auto/{JOB_ID}: {message}")
        self.assertEqual(self.git("show", "stash@{0}:b.txt"), "half done\n")
        self.assertEqual(self.git("show", "stash@{0}^3:new.txt"), "new\n")
        # The task's history has `dropped` with both files in the diff, committed and landed.
        entries = job.sdd_history.read(self.root, "003-plugin-redesign", "T001")
        dropped = [e for e in entries if e.get("step") == "dropped"]
        self.assertEqual(len(dropped), 1)
        self.assertEqual((dropped[0]["run"], dropped[0]["session"]), (JOB_ID, SESSION_ID))
        self.assertIn("+++ b/b.txt", dropped[0]["diff"])
        self.assertIn("+half done", dropped[0]["diff"])
        self.assertIn("+++ b/new.txt", dropped[0]["diff"])
        self.assertTrue(self.on_base(".sdd/tasks/003-plugin-redesign/T001.jsonl"))
        # run.json is archived under its session, and the session joined the record's runs.
        self.assertFalse((self.root / ".sdd" / "run.json").exists())
        archived = json.loads((self.root / ".sdd" / "runs" / SESSION_ID / "run.json").read_text())
        self.assertEqual(archived["run_id"], SESSION_ID)
        saved = job.load(self.root, JOB_ID)
        self.assertEqual(saved["runs"], [SESSION_ID])
        self.assertEqual((saved["state"], saved["outcome"], saved["reason"]),
                         ("ended", "dropped", "dropped by you during T001"))
        self.assertEqual(saved["landing"]["state"], "landed")
        # The finished commits landed on the base; the half-done edit did not.
        self.assertEqual(self.branch(), "main")
        self.assertEqual(subprocess.run(["git", "merge-base", "--is-ancestor", self.job_commit, "main"],
                                        cwd=self.root).returncode, 0)
        self.assertEqual((self.root / "b.txt").read_text(), "job\n")
        self.assertFalse((self.root / "new.txt").exists())
        # The real index was not touched by the patch's temporary one.
        self.assertFalse(list((self.root / ".git").glob("sdd-drop-index-*")))

    def on_base(self, path):
        return subprocess.run(["git", "cat-file", "-e", f"main:{path}"], cwd=self.root).returncode == 0

    def test_no_task_in_flight_records_nothing(self):
        run_json = self.root / ".sdd" / "run.json"
        run_json.write_text(json.dumps({**json.loads(run_json.read_text()), "workflow_dir": None}))
        result = job.drop_suspended(self.root, JOB_ID)
        self.assertEqual((result["tasks"], result["stash"]), ([], f"sdd-run {JOB_ID}: dropped partial work"))
        self.assertEqual(job.sdd_history.read(self.root, "003-plugin-redesign", "T001"), [])
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(job.load(self.root, JOB_ID)["reason"], "dropped by you")

    def snapshot(self):
        return (*super().snapshot(), (self.root / ".sdd" / "run.json").read_text(),
                self.git("stash", "list"), (self.root / ".sdd" / "tasks").exists())

    def test_refuses_a_run_that_is_not_suspended(self):
        job.save(self.root, {**job.load(self.root, JOB_ID), "state": "ended", "outcome": "done",
                             "landing": PENDING})
        self.assertRefused(job.drop_suspended, "this run cannot be dropped: it is ended")
        status = job.sdd_supervise.state_dir(self.root) / "status.json"
        status.write_text(json.dumps({"state": "waiting", "pid": os.getpid()}))
        job.save(self.root, {**job.load(self.root, JOB_ID), "state": "working"})
        self.assertRefused(job.drop_suspended, "this run cannot be dropped: it is paused")

    def test_refuses_while_a_supervisor_is_alive(self):
        status = job.sdd_supervise.state_dir(self.root) / "status.json"
        status.write_text(json.dumps({"state": "stopped", "pid": os.getpid()}))
        self.assertRefused(job.drop_suspended, "a run is active")

    def test_cli_drop_calls_drop_suspended(self):
        env = {**os.environ, "XDG_STATE_HOME": self.state_home}
        dropped = subprocess.run([sys.executable, str(HERE.parent / "bin" / "sdd-job.py"), "drop", JOB_ID,
                                  str(self.root)], capture_output=True, text=True, env=env)
        self.assertEqual(dropped.returncode, 0, dropped.stdout + dropped.stderr)
        out = json.loads(dropped.stdout)
        self.assertEqual((out["dropped"], out["tasks"], out["stash"]),
                         (True, ["T001"], f"sdd-run {JOB_ID}: dropped T001"))
        self.assertEqual(job.load(self.root, JOB_ID)["outcome"], "dropped")


SETTLE_TASKS = """# Tasks

## Phase 1: Work

- [x] T001 Done `a.txt`
- [ ] T002 Runnable `b.txt`
- [ ] T003 [manual] Record audio
"""


class AfterSettleTest(unittest.TestCase):
    """after_settle() (009 FR-024, research R6) on a temp repo with the launcher's `_spawn` replaced. The setUp
    record is a 005 `waiting` Job; `ended_waiting` makes it a 009 run that ended `waiting` and landed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "project"
        self.root.mkdir()
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(self.tmp.name) / "state")})
        self.env.start()
        self.feature = self.root / "specs" / "003-plugin-redesign"
        self.feature.mkdir(parents=True)
        (self.feature / "tasks.md").write_text(SETTLE_TASKS)
        (self.root / ".gitignore").write_text(".sdd/\n")
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "Test"),
                     ("config", "user.email", "test@example.com"), ("config", "commit.gpgsign", "false"),
                     ("add", "."), ("commit", "-q", "-m", "base"), ("switch", "-q", "-c", f"auto/{JOB_ID}")):
            subprocess.run(["git", *args], cwd=self.root, capture_output=True, check=True)
        job.save(self.root, record(JOB_ID, state="waiting", reason="only items that need you are left",
                                   scope={"phases": ["1"]}, scope_tasks=["T001", "T002", "T003"]))
        self.spawn = mock.patch.object(job._launcher(), "_spawn")
        self.popen = self.spawn.start()
        self.popen.return_value.pid = 4444

    def tearDown(self):
        self.spawn.stop()
        self.env.stop()
        self.tmp.cleanup()

    def set(self, **fields):
        job.save(self.root, {**job.load(self.root, JOB_ID), **fields})

    def not_started(self, reason):
        before = job.load(self.root, JOB_ID)
        self.assertEqual(job.after_settle(self.root), {"started": False, "reason": reason})
        self.popen.assert_not_called()
        self.assertEqual(job.load(self.root, JOB_ID), before)

    # 009 FR-024, research R6: only the newest run, when it ended `waiting`, is followed by a new run with its
    # scope, limits and title.

    def ended_waiting(self, landing="landed"):
        subprocess.run(["git", "switch", "-q", "main"], cwd=self.root, capture_output=True, check=True)
        self.set(state="ended", outcome="waiting", title="Phase 1 work",
                 limits={"max_tasks": 5, "until": None, "max_runs": 3},
                 landing={"state": landing, "commit": None, "files": []})

    def test_a_run_ended_waiting_starts_a_new_run_with_its_scope_limits_and_title(self):
        self.ended_waiting()
        self.assertEqual(job.after_settle(self.root), {"started": True, "reason": None})
        self.popen.assert_called_once()
        new_id = self.popen.call_args.args[0][-1]
        self.assertNotEqual(new_id, JOB_ID)
        new = job.load(self.root, new_id)
        self.assertEqual((new["state"], new["title"], new["scope"]), ("working", "Phase 1 work", {"phases": ["1"]}))
        self.assertEqual(new["limits"], {"max_tasks": 5, "until": None, "max_runs": 3})
        self.assertEqual(new["base"]["branch"], "main")
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.root, capture_output=True,
                                text=True).stdout.strip()
        self.assertEqual(branch, f"auto/{new_id}")
        self.assertEqual(job.load(self.root, JOB_ID)["state"], "ended", "the ended run is left as it was")

    def test_a_run_ended_waiting_starts_nothing_while_a_run_is_open(self):
        self.ended_waiting(landing="needs-merge")
        self.not_started("a run is open: Phase 1 work")

    def test_a_run_ended_waiting_starts_nothing_without_a_runnable_task(self):
        self.ended_waiting()
        (self.feature / "tasks.md").write_text(SETTLE_TASKS.replace("- [ ] T002", "- [x] T002"))
        subprocess.run(["git", "commit", "-qam", "tick"], cwd=self.root, capture_output=True, check=True)
        self.not_started("no task in the scope is runnable")

    def test_a_run_ended_otherwise_starts_nothing(self):
        subprocess.run(["git", "switch", "-q", "main"], cwd=self.root, capture_output=True, check=True)
        self.set(state="ended", outcome="done", landing={"state": "landed", "commit": None, "files": []})
        self.not_started("the newest run did not end waiting")

    def test_a_newest_run_not_ended_starts_nothing(self):
        self.ended_waiting()
        for state in ("working", "suspended"):
            self.set(state=state)
            self.not_started("the newest run did not end waiting")

    def test_no_runs_yet(self):
        job.jobs_dir(self.root).joinpath(f"{JOB_ID}.json").unlink()
        self.assertEqual(job.after_settle(self.root), {"started": False, "reason": "no runs yet"})
        self.popen.assert_not_called()
        self.assertEqual(job.after_settle(self.root / "missing"), {"started": False, "reason": "no runs yet"})

    def test_an_older_run_ended_waiting_does_not_restart_when_a_newer_run_ended_otherwise(self):
        self.ended_waiting()
        newer = "20261031-1013"
        job.save(self.root, record(newer, state="ended", outcome="done", scope={"phases": ["1"]},
                                   landing={"state": "landed", "commit": None, "files": []}))
        self.not_started("the newest run did not end waiting")
        self.assertEqual(job.load(self.root, newer)["state"], "ended")
        self.assertEqual([r["id"] for r in job.list_jobs(self.root)], [newer, JOB_ID])

    def test_a_005_waiting_job_without_run_json_is_open_and_starts_nothing(self):
        """A 005 record stored `waiting` without its run.json reads as ended `waiting` with landing `pending`:
        it is the open run, so nothing starts until it is landed (FR-025)."""
        self.assertEqual(job.load(self.root, JOB_ID)["state"], "waiting")
        self.not_started("a run is open: Phase 11")

    def test_the_stop_by_time_has_passed(self):
        self.ended_waiting()
        self.set(limits={"max_tasks": None, "until": time.time() - 60, "max_runs": 3})
        self.not_started("the run's stop-by time has passed")

    def test_a_launcher_refusal_comes_back_as_the_reason(self):
        self.ended_waiting()
        (self.root / "stray.txt").write_text("x\n")
        self.not_started("the working tree has changes; commit or stash them first: stray.txt")
        (self.root / "stray.txt").unlink()
        (self.root / ".sdd" / "run.json").write_text("{}")
        self.not_started("a run is active")

    def test_an_unexpected_error_comes_back_as_the_reason(self):
        self.ended_waiting()
        with mock.patch.object(job._launcher(), "launch_job", side_effect=RuntimeError("boom")):
            self.assertEqual(job.after_settle(self.root),
                             {"started": False, "reason": "could not start a run: boom"})
        with mock.patch.object(job, "list_jobs", side_effect=RuntimeError("bad")):
            self.assertEqual(job.after_settle(self.root), {"started": False, "reason": "could not start a run: bad"})


class UsagePeakTest(unittest.TestCase):
    """note_usage() (research R9): the highest utilization per window, only ever raised."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        job.save(self.root, record("20261031-1012"))

    def tearDown(self):
        self.tmp.cleanup()

    def path(self):
        return self.root / ".sdd" / "jobs" / "20261031-1012.json"

    def test_only_raises(self):
        self.assertEqual(job.note_usage(self.root, "20261031-1012", {"five_hour": 0.91}), {"five_hour": 0.91})
        self.assertEqual(job.note_usage(self.root, "20261031-1012", {"five_hour": 0.95, "seven_day": 0.4}),
                         {"five_hour": 0.95, "seven_day": 0.4})
        self.assertEqual(job.note_usage(self.root, "20261031-1012", {"five_hour": 0.92, "seven_day": 0.41}),
                         {"five_hour": 0.95, "seven_day": 0.41})
        self.assertEqual(job.load(self.root, "20261031-1012")["usage_peak"], {"five_hour": 0.95, "seven_day": 0.41})

    def test_saves_only_when_one_rose(self):
        job.note_usage(self.root, "20261031-1012", {"five_hour": 0.95})
        before = self.path().read_text()
        with mock.patch.object(job, "save", side_effect=AssertionError("saved")) as save:
            self.assertEqual(job.note_usage(self.root, "20261031-1012", {"five_hour": 0.9}), {"five_hour": 0.95})
            self.assertEqual(job.note_usage(self.root, "20261031-1012", {}), {"five_hour": 0.95})
        save.assert_not_called()
        self.assertEqual(self.path().read_text(), before)

    def test_unknown_run_is_none_and_errors_never_raise(self):
        self.assertIsNone(job.note_usage(self.root, "20261031-9999", {"five_hour": 0.9}))
        self.assertIsNone(job.note_usage(self.root, "../x", {"five_hour": 0.9}))
        with mock.patch.object(job, "save", side_effect=OSError("disk full")):
            self.assertIsNone(job.note_usage(self.root, "20261031-1012", {"five_hour": 0.9}))


if __name__ == "__main__":
    unittest.main()
