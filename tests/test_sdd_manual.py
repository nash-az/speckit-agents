"""bin/sdd-manual.py (FR-036): settle an open [manual] task and commit just the files it touched."""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_manual", HERE.parent / "bin" / "sdd-manual.py")
manual = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(manual)

TASKS = ("## Phase 1: A\n\n"
         "- [ ] T001 [manual] Look at the dashboard\n"
         "- [ ] T002 [manual] Record audio\n"
         "- [ ] T003 [decision] Pick a port. Options: (a) 80; (b) 8080\n"
         "- [ ] T004 Plain\n")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


class SettleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.feature = self.root / "specs" / "001-a"
        self.feature.mkdir(parents=True)
        (self.feature / "tasks.md").write_text(TASKS, encoding="utf-8")
        for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "T"),
                     ("add", "."), ("commit", "-q", "-m", "init")):
            git(self.root, *args)

    def tearDown(self):
        self.tmp.cleanup()

    def test_done_ticks_and_commits_only_that_file(self):
        (self.root / "other.txt").write_text("unrelated\n")
        result = manual.settle(self.root, self.feature, "T001", "done")
        self.assertEqual(result["files"], ["specs/001-a/tasks.md"])
        text = (self.feature / "tasks.md").read_text()
        self.assertIn("- [x] T001", text)
        self.assertIn("- [ ] T002", text)
        self.assertEqual(git(self.root, "status", "--porcelain").strip(), "?? other.txt")
        self.assertIn("chore(manual): T001 done", git(self.root, "log", "-1", "--format=%s"))

    def test_done_with_a_note_records_it(self):
        result = manual.settle(self.root, self.feature, "T001", "done", "colours look off\non mobile")
        self.assertIn("specs/001-a/manual-notes.md", result["files"])
        notes = (self.feature / "manual-notes.md").read_text()
        self.assertIn("- T001 (", notes)
        self.assertIn("colours look off on mobile", notes)
        self.assertEqual(git(self.root, "status", "--porcelain").strip(), "")

    def test_skip_adds_a_wontfix_entry_and_leaves_the_task_open(self):
        with self.assertRaises(manual.Refused):
            manual.settle(self.root, self.feature, "T002", "skip", "  ")
        manual.settle(self.root, self.feature, "T002", "skip", "no microphone here")
        self.assertIn("- [ ] T002", (self.feature / "tasks.md").read_text())
        blocked = (self.feature / "BLOCKED.md").read_text()
        self.assertIn("## T002\n- status: wontfix\n- tag: manual", blocked)
        self.assertIn("no microphone here", blocked)
        self.assertEqual(git(self.root, "status", "--porcelain").strip(), "")

    def test_refusals_change_nothing(self):
        for task in ("T003", "T004", "T999"):
            with self.assertRaises(manual.Refused):
                manual.settle(self.root, self.feature, task, "done")
        (self.feature / "tasks.md").write_text(TASKS + "\n- [ ] T005 [manual] Added\n")
        with self.assertRaises(manual.Refused) as ctx:
            manual.settle(self.root, self.feature, "T001", "done")
        self.assertIn("other changes", str(ctx.exception))
        self.assertIn(" specs/001-a/tasks.md", " " + str(ctx.exception).split(": ", 1)[1])  # first path is whole
        git(self.root, "checkout", "--", ".")
        (self.root / ".sdd").mkdir()
        (self.root / ".sdd" / "run.json").write_text("{}")
        with self.assertRaises(manual.Refused) as ctx:
            manual.settle(self.root, self.feature, "T001", "done")
        self.assertIn("a run is active", str(ctx.exception))
        self.assertEqual((self.feature / "tasks.md").read_text(), TASKS)

    def test_a_task_cannot_be_settled_twice(self):
        manual.settle(self.root, self.feature, "T001", "done")
        with self.assertRaises(manual.Refused):
            manual.settle(self.root, self.feature, "T001", "done")

    def test_job_key_without_an_open_job(self):
        for task, action, note in (("T001", "done", ""), ("T002", "skip", "no microphone")):
            result = manual.settle(self.root, self.feature, task, action, note)
            self.assertEqual(result["job"], {"started": False, "reason": "no open job"})


JOB_ID = "20261031-1012"


class JobTest(unittest.TestCase):
    """With an open Job (spec 005): the settle commit lands on `auto/<id>` (FR-008) and the helper calls
    `sdd_job.after_settle` (FR-016, data-model §10); the launcher's `_spawn` is replaced."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "project"
        self.feature = self.root / "specs" / "001-a"
        self.feature.mkdir(parents=True)
        (self.feature / "tasks.md").write_text(TASKS, encoding="utf-8")
        (self.root / ".gitignore").write_text(".sdd/\n")
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(self.tmp.name) / "state")})
        self.env.start()
        for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@example.com"),
                     ("config", "user.name", "T"), ("config", "commit.gpgsign", "false"),
                     ("add", "."), ("commit", "-q", "-m", "init"), ("switch", "-q", "-c", f"auto/{JOB_ID}")):
            git(self.root, *args)
        manual.sdd_job.save(self.root, {
            "id": JOB_ID, "title": "Phase 1", "feature": "001-a", "scope": {"phases": ["1"]},
            "scope_tasks": ["T001", "T002", "T003", "T004"], "blocked_at_start": 0,
            "limits": {"max_tasks": None, "until": None, "max_runs": 20},
            "branch": f"auto/{JOB_ID}", "base": {"branch": "main", "commit": "0000000"}, "state": "waiting",
            "reason": "only items that need you are left", "runs": [], "no_progress_runs": 0,
            "created": 1793450000, "ended": None, "merge_commit": None})
        self.spawn = mock.patch.object(manual.sdd_job._launcher(), "_spawn")
        self.popen = self.spawn.start()
        self.popen.return_value.pid = 4444

    def tearDown(self):
        self.spawn.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_done_commits_on_the_job_branch_and_continues_the_job(self):
        result = manual.settle(self.root, self.feature, "T001", "done")
        self.assertEqual(result["job"], {"started": True, "reason": None})
        self.popen.assert_called_once()
        self.assertEqual(git(self.root, "branch", "--show-current").strip(), f"auto/{JOB_ID}")
        self.assertIn(f"auto/{JOB_ID}", git(self.root, "branch", "--contains", result["commit"]))
        self.assertNotIn("main", git(self.root, "branch", "--contains", result["commit"]))
        self.assertEqual(manual.sdd_job.load(self.root, JOB_ID)["state"], "working")

    def test_skip_reports_why_the_job_did_not_continue(self):
        manual.sdd_job.save(self.root, {**manual.sdd_job.load(self.root, JOB_ID), "state": "stopped"})
        result = manual.settle(self.root, self.feature, "T002", "skip", "no microphone here")
        self.assertEqual(result["job"], {"started": False, "reason": "the job is stopped, not waiting"})
        self.popen.assert_not_called()
        self.assertIn(f"auto/{JOB_ID}", git(self.root, "branch", "--contains", result["commit"]))

    def test_without_a_commit_after_settle_is_not_called(self):
        with mock.patch.object(manual.sdd_job, "after_settle") as after:
            result = manual.settle(self.root, self.feature, "T001", "done", commit=False)
        after.assert_not_called()
        self.assertFalse(result["job"]["started"])
        self.popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
