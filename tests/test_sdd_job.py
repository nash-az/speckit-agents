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


JOB_ID = "20261031-1012"
JOB_TASKS = """# Tasks

## Phase 1: Work

- [ ] T001 One `a.txt`
"""


class MergeDropTest(unittest.TestCase):
    """merge() and drop() on temp repos (research R9, FR-010, FR-017, SC-005)."""

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

    def test_fast_forward(self):
        result = job.merge(self.root, JOB_ID)
        self.assertEqual(result, {"merged": True, "commit": self.job_commit, "fast_forward": True})
        self.assertEqual(self.branch(), "main")
        self.assertEqual(self.head("main"), self.job_commit)
        self.assertEqual(self.head(f"auto/{JOB_ID}"), self.job_commit)  # branch kept
        saved = job.load(self.root, JOB_ID)
        self.assertEqual(saved["state"], "merged")
        self.assertEqual(saved["merge_commit"], self.job_commit)
        self.assertIsInstance(saved["ended"], int)

    def test_merge_commit_after_the_base_moved(self):
        self.git("switch", "-q", "main")
        self.write_commit("c.txt", "elsewhere\n", "base moved")
        moved = self.head()
        self.git("switch", "-q", f"auto/{JOB_ID}")
        job.save(self.root, {**job.load(self.root, JOB_ID), "state": "stopped"})
        result = job.merge(self.root, JOB_ID)
        self.assertFalse(result["fast_forward"])
        self.assertEqual(self.branch(), "main")
        self.assertEqual(result["commit"], self.head("main"))
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "merge: job Phase 11")
        parents = self.git("log", "-1", "--format=%P").split()
        self.assertEqual(parents, [moved, self.job_commit])
        # SC-002: every Job commit is on the base branch now.
        self.assertEqual(subprocess.run(["git", "merge-base", "--is-ancestor", self.job_commit, "main"],
                                        cwd=self.root).returncode, 0)
        saved = job.load(self.root, JOB_ID)
        self.assertEqual((saved["state"], saved["merge_commit"]), ("merged", result["commit"]))

    def test_conflict_leaves_base_branch_and_tree_unchanged(self):
        self.git("switch", "-q", "main")
        self.write_commit("b.txt", "other\n", "base edits b")
        self.write_commit("d.txt", "x\n", "base adds d")
        self.git("switch", "-q", f"auto/{JOB_ID}")
        job.save(self.root, {**job.load(self.root, JOB_ID), "state": "failed"})
        self.assertRefused(job.merge, "the merge conflicts in: b.txt")
        self.assertEqual(self.branch(), f"auto/{JOB_ID}")
        self.assertEqual((self.root / "b.txt").read_text(), "job\n")
        self.assertFalse((self.root / ".git" / "MERGE_HEAD").exists())

    def test_refuses_states_that_cannot_close(self):
        for state in ("working", "waiting", "held", "merged", "dropped"):
            job.save(self.root, {**job.load(self.root, JOB_ID), "state": state})
            self.assertRefused(job.merge, f"this job cannot be merged in state {state}")
            self.assertRefused(job.drop, f"this job cannot be dropped in state {state}")

    def test_refuses_an_unknown_job(self):
        for call in (job.merge, job.drop):
            with self.assertRaises(job.Refused) as caught:
                call(self.root, "20261031-0000")
            self.assertEqual(str(caught.exception), "no such job: 20261031-0000")

    def test_refuses_while_a_run_is_active(self):
        run_json = self.root / ".sdd" / "run.json"
        run_json.write_text("{}")
        self.assertRefused(job.merge, "a run is active")
        self.assertRefused(job.drop, "a run is active")
        run_json.unlink()
        status = job.sdd_supervise.state_dir(self.root) / "status.json"
        status.write_text(json.dumps({"state": "running", "pid": os.getpid()}))
        self.assertRefused(job.merge, "a run is active")
        self.assertRefused(job.drop, "a run is active")
        status.write_text(json.dumps({"state": "stopped", "pid": os.getpid()}))
        self.assertTrue(job.drop(self.root, JOB_ID)["dropped"])

    def test_refuses_a_dirty_tree(self):
        (self.root / "b.txt").write_text("edited\n")
        (self.root / "new.txt").write_text("new\n")
        message = "the working tree has changes; commit or stash them first: b.txt, new.txt"
        self.assertRefused(job.merge, message)
        self.assertRefused(job.drop, message)

    def test_drop_keeps_the_branch(self):
        self.assertEqual(job.drop(self.root, JOB_ID), {"dropped": True})
        self.assertEqual(self.branch(), "main")
        self.assertEqual(self.head("main"), self.base_commit)
        self.assertEqual(self.head(f"auto/{JOB_ID}"), self.job_commit)
        saved = job.load(self.root, JOB_ID)
        self.assertEqual(saved["state"], "dropped")
        self.assertIsNone(saved["merge_commit"])
        self.assertIsInstance(saved["ended"], int)

    def cli(self, *args):
        env = {**os.environ, "XDG_STATE_HOME": self.state_home}
        return subprocess.run([sys.executable, str(HERE.parent / "bin" / "sdd-job.py"), *args],
                              capture_output=True, text=True, env=env)

    def test_cli_json_output(self):
        listed = self.cli("list", str(self.root))
        self.assertEqual(listed.returncode, 0, listed.stderr)
        jobs = json.loads(listed.stdout)
        self.assertEqual([j["id"] for j in jobs], [JOB_ID])
        self.assertEqual(jobs[0]["shown"], {"state": "done"})
        self.assertEqual(jobs[0]["progress"], {"done": 0, "total": 1, "waiting_on_you": 0, "left": 1})

        shown = self.cli("show", JOB_ID, str(self.root))
        self.assertEqual(shown.returncode, 0, shown.stderr)
        one = json.loads(shown.stdout)
        self.assertEqual((one["id"], one["runs"], one["shown"]["state"]), (JOB_ID, [], "done"))

        missing = self.cli("show", "20261031-0000", str(self.root))
        self.assertEqual((missing.returncode, json.loads(missing.stdout)),
                         (1, {"contract": "1.0", "error": "no such job: 20261031-0000"}))

        (self.root / "b.txt").write_text("edited\n")
        dirty = self.cli("merge", JOB_ID, str(self.root))
        self.assertEqual(dirty.returncode, 1)
        self.assertEqual(json.loads(dirty.stdout),
                         {"contract": "1.0",
                          "error": "the working tree has changes; commit or stash them first: b.txt"})
        self.git("checkout", "--", "b.txt")

        merged = self.cli("merge", JOB_ID, str(self.root))
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
        self.assertEqual(json.loads(merged.stdout),
                         {"contract": "1.0", "merged": True, "commit": self.job_commit, "fast_forward": True})

        again = self.cli("drop", JOB_ID, str(self.root))
        self.assertEqual((again.returncode, json.loads(again.stdout)),
                         (1, {"contract": "1.0", "error": "this job cannot be dropped in state merged"}))
        self.assertEqual(self.cli().returncode, 2)

    def test_cli_drop(self):
        dropped = self.cli("drop", JOB_ID, str(self.root))
        self.assertEqual(dropped.returncode, 0, dropped.stdout + dropped.stderr)
        self.assertEqual(json.loads(dropped.stdout), {"contract": "1.0", "dropped": True})
        self.assertEqual(self.branch(), "main")


SETTLE_TASKS = """# Tasks

## Phase 1: Work

- [x] T001 Done `a.txt`
- [ ] T002 Runnable `b.txt`
- [ ] T003 [manual] Record audio
"""


class AfterSettleTest(unittest.TestCase):
    """after_settle() (FR-016, research R8) on a temp repo with the launcher's `_spawn` replaced."""

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

    def test_starts_when_a_scope_task_is_runnable(self):
        self.assertEqual(job.after_settle(self.root), {"started": True, "reason": None})
        self.popen.assert_called_once()
        self.assertEqual(self.popen.call_args.args[0][2:], ["--project", str(self.root), "--job", JOB_ID])
        saved = job.load(self.root, JOB_ID)
        self.assertEqual((saved["state"], saved["reason"]), ("working", None))

    def test_no_open_job(self):
        self.set(state="merged")
        self.not_started("no open job")

    def test_job_not_waiting(self):
        for state in ("stopped", "held", "done", "failed"):
            self.set(state=state)
            self.not_started(f"the job is {state}, not waiting")
        self.set(state="working")  # no live supervisor: shown as interrupted
        self.not_started("the job is interrupted, not waiting")

    def test_run_json_exists(self):
        (self.root / ".sdd" / "run.json").write_text("{}")
        self.not_started("a run is active")

    def test_supervisor_alive(self):
        status = job.sdd_supervise.state_dir(self.root) / "status.json"
        status.write_text(json.dumps({"state": "done", "pid": os.getpid()}))
        self.not_started("a supervisor is running")
        status.write_text(json.dumps({"state": "running", "pid": dead_pid()}))
        self.assertTrue(job.after_settle(self.root)["started"])

    def test_nothing_runnable_in_scope(self):
        (self.feature / "tasks.md").write_text(SETTLE_TASKS.replace("- [ ] T002", "- [x] T002"))
        self.not_started("no task in the scope is runnable")

    def test_nothing_left_after_a_skip_makes_the_job_done(self):
        """The person skipped the last item (wontfix): nothing waits on them and nothing is left, so the Job is
        done and can be merged, instead of staying "waiting" with a Continue that always refuses."""
        (self.feature / "tasks.md").write_text(SETTLE_TASKS.replace("- [ ] T002", "- [x] T002"))
        (self.feature / "BLOCKED.md").write_text("# Blocked\n\n## T003\n- status: wontfix\n- tag: manual\n"
                                                 "- reason: skipped\n- question:\n- answer:\n")
        self.assertEqual(job.after_settle(self.root),
                         {"started": False, "reason": "the job is done: every task in the scope is done or skipped"})
        self.popen.assert_not_called()
        saved = job.load(self.root, JOB_ID)
        self.assertEqual((saved["state"], saved["reason"]), ("done", job.COMPLETE_REASON))

    def test_a_manual_task_still_open_keeps_the_job_waiting(self):
        (self.feature / "tasks.md").write_text(SETTLE_TASKS.replace("- [ ] T002", "- [x] T002"))
        self.assertFalse(job.finish_if_complete(self.root, job.load(self.root, JOB_ID)))
        self.assertEqual(job.load(self.root, JOB_ID)["state"], "waiting")

    def test_a_launcher_refusal_comes_back_as_the_reason(self):
        (self.root / "stray.txt").write_text("x\n")
        self.not_started("the working tree has changes; commit or stash them first: stray.txt")
        (self.root / "stray.txt").unlink()
        self.set(limits={"max_tasks": 1, "until": None, "max_runs": 20})
        self.not_started("the limit max_tasks is still reached; give a new value")

    def test_an_unexpected_error_comes_back_as_the_reason(self):
        with mock.patch.object(job._launcher(), "continue_job", side_effect=RuntimeError("boom")):
            self.assertEqual(job.after_settle(self.root),
                             {"started": False, "reason": "could not continue the job: boom"})
        self.assertEqual(job.after_settle(self.root / "missing"), {"started": False, "reason": "no open job"})


if __name__ == "__main__":
    unittest.main()
