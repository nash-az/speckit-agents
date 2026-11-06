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

    def write_manual(self, record_line):
        (self.feature / "manual.md").write_text(
            "# Manual steps\n\n## T001 Look at the dashboard\n\n1. Look.\n\n" + record_line + "\n\n"
            "## T002 Record audio\n\n1. Record.\n", encoding="utf-8")
        (self.feature / "research.md").write_text(
            "# Research\n\n## R1. First\n\nText.\n\n## Validation\n\nEarlier.\n\n## R9. Last\n\nEnd.\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "manual")

    def test_a_record_line_saves_the_note_under_that_heading_in_the_same_commit(self):
        self.write_manual("Record: `research.md` `## Validation`")
        result = manual.settle(self.root, self.feature, "T001", "done", "line one\nline two")
        self.assertEqual(sorted(result["files"]), ["specs/001-a/manual-notes.md", "specs/001-a/research.md",
                                                   "specs/001-a/tasks.md"])
        text = (self.feature / "research.md").read_text()
        validation = text.split("## Validation", 1)[1].split("## R9. Last", 1)[0]
        self.assertIn("Earlier.", validation)
        self.assertIn("From T001 (", validation)
        self.assertIn("```\nline one\nline two\n```", validation)  # verbatim, pasted output keeps its lines
        self.assertTrue(text.rstrip().endswith("End."))
        self.assertEqual(git(self.root, "status", "--porcelain").strip(), "")
        self.assertEqual(len(git(self.root, "log", "--format=%h").split()), 3)  # init, manual, one settle commit

    def test_a_missing_heading_is_added_at_the_end(self):
        self.write_manual("Record: research.md ## R2 evidence (headless)")
        manual.settle(self.root, self.feature, "T001", "done", "CLAUDE_PID=1")
        text = (self.feature / "research.md").read_text()
        self.assertTrue(text.rstrip().endswith("## R2 evidence (headless)\n\nFrom T001 (%s), written in the dashboard:\n\n"
                                               "> CLAUDE_PID=1" % text.split("From T001 (")[1][:10]))

    def test_a_record_line_needs_a_note(self):
        self.write_manual("Record: research.md ## Validation")
        with self.assertRaises(manual.Refused) as caught:
            manual.settle(self.root, self.feature, "T001", "done")
        self.assertEqual(str(caught.exception), "write what you found: it is saved under ## Validation in research.md")
        self.assertIn("- [ ] T001", (self.feature / "tasks.md").read_text())

    def test_record_lines_cannot_name_protected_files_or_another_tasks_section(self):
        for line in ("Record: spec.md ## Notes", "Record: tasks.md ## Notes", "Record: ../x.md ## Notes"):
            with self.subTest(line=line):
                self.write_manual(line)
                self.assertIsNone(manual.record_target(self.feature, "T001"))
        self.write_manual("Record: research.md ## Validation")
        self.assertIsNone(manual.record_target(self.feature, "T002"))
        self.assertEqual(manual.record_target(self.feature, "T001"), (self.feature / "research.md", "## Validation"))

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
            self.assertEqual(result["job"], {"started": False, "reason": "no runs yet"})


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
        """009 FR-024: the newest run ended `waiting` and landed, the checkout is on main; the settle commit
        goes on main and a new run starts with the old run's scope."""
        git(self.root, "switch", "-q", "main")
        record = manual.sdd_job.load(self.root, JOB_ID)
        manual.sdd_job.save(self.root, {**record, "state": "ended", "outcome": "waiting",
                                        "landing": {"state": "landed", "commit": None, "files": []}})
        result = manual.settle(self.root, self.feature, "T001", "done")
        self.assertEqual(result["job"], {"started": True, "reason": None})
        self.popen.assert_called_once()
        self.assertEqual(git(self.root, "rev-parse", "--short", "main").strip(), result["commit"])
        self.assertIn("main", git(self.root, "branch", "--contains", result["commit"]))
        newest = manual.sdd_job.list_jobs(self.root)[0]
        self.assertNotEqual(newest["id"], JOB_ID)
        self.assertEqual((newest["state"], newest["scope"]), ("working", record["scope"]))

    def test_skip_reports_why_the_job_did_not_continue(self):
        manual.sdd_job.save(self.root, {**manual.sdd_job.load(self.root, JOB_ID), "state": "stopped",
                                        "reason": "stopped by you"})
        result = manual.settle(self.root, self.feature, "T002", "skip", "no microphone here")
        self.assertEqual(result["job"], {"started": False, "reason": "the newest run did not end waiting"})
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
