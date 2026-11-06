"""bin/sdd-decide.py (FR-033): apply a decision answer and commit exactly the decision files."""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_decide", HERE.parent / "bin" / "sdd-decide.py")
decide = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(decide)

TASKS = ("## Phase 1: A\n\n"
         "- [ ] T001 [decision] Pick a port. Options: (a) 80; (b) 8080. Record the answer under "
         "`## Port` in `research.md`, per FR-001\n"
         "- [ ] T002 [decision] Pick a colour. Options: (a) red; (b) blue\n"
         "- [ ] T003 [manual] Record audio\n"
         "- [ ] T004 Plain\n")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.feature = self.root / "specs" / "001-a"
        self.feature.mkdir(parents=True)
        (self.feature / "tasks.md").write_text(TASKS, encoding="utf-8")
        (self.feature / "research.md").write_text("# Research\n\n## Other\n\nnotes\n", encoding="utf-8")
        for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "T"),
                     ("add", "."), ("commit", "-q", "-m", "init")):
            git(self.root, *args)

    def tearDown(self):
        self.tmp.cleanup()

    def tracked_clean(self):
        return git(self.root, "status", "--porcelain").strip() == ""

    def test_records_ticks_and_commits_only_those_files(self):
        result = decide.apply(self.root, self.feature, "T001", "(b) 8080, it is free")
        self.assertEqual(sorted(result["files"]),
                         ["specs/001-a/decisions.md", "specs/001-a/research.md", "specs/001-a/tasks.md"])
        self.assertTrue(self.tracked_clean())
        self.assertIn("chore(decisions): T001 (b) 8080", git(self.root, "log", "-1", "--format=%s"))
        decisions = (self.feature / "decisions.md").read_text()
        self.assertIn("T001", decisions)
        self.assertIn("→ (b) 8080, it is free", decisions)
        research = (self.feature / "research.md").read_text()
        self.assertIn("## Port\n\nDecision", research)
        self.assertIn("(b) 8080, it is free", research)
        self.assertIn("- [x] T001", (self.feature / "tasks.md").read_text())
        self.assertIn("- [ ] T002", (self.feature / "tasks.md").read_text())

    def test_without_a_research_heading_only_decisions_and_tasks_change(self):
        result = decide.apply(self.root, self.feature, "T002", "(a) red")
        self.assertEqual(sorted(result["files"]), ["specs/001-a/decisions.md", "specs/001-a/tasks.md"])

    def test_adds_under_an_existing_heading_without_touching_the_rest(self):
        (self.feature / "research.md").write_text("# R\n\n## Port\n\nold line\n\n## After\n\nkeep\n")
        git(self.root, "commit", "-qam", "research")
        decide.apply(self.root, self.feature, "T001", "(a)")
        text = (self.feature / "research.md").read_text()
        self.assertLess(text.index("old line"), text.index("Decision"))
        self.assertLess(text.index("Decision"), text.index("## After"))
        self.assertIn("keep", text)

    def test_second_decision_appends_to_the_register(self):
        decide.apply(self.root, self.feature, "T001", "(a)")
        decide.apply(self.root, self.feature, "T002", "(b)")
        lines = [l for l in (self.feature / "decisions.md").read_text().splitlines() if l.startswith("- T")]
        self.assertEqual([l[:6] for l in lines], ["- T001", "- T002"])

    def test_refuses_other_changes_and_changes_nothing(self):
        (self.root / "stray.txt").write_text("mine")
        before = (self.feature / "tasks.md").read_text()
        with self.assertRaisesRegex(decide.Refused, "other changes"):
            decide.apply(self.root, self.feature, "T001", "(b)")
        self.assertEqual((self.feature / "tasks.md").read_text(), before)
        self.assertFalse((self.feature / "decisions.md").exists())

    def test_refuses_while_a_run_is_active(self):
        (self.root / ".sdd").mkdir()
        (self.root / ".sdd" / "run.json").write_text("{}")
        with self.assertRaisesRegex(decide.Refused, "run is active"):
            decide.apply(self.root, self.feature, "T001", "(b)")

    def test_refuses_tasks_that_are_not_open_decisions(self):
        for task in ("T003", "T004", "T999"):
            with self.assertRaisesRegex(decide.Refused, "not an open decision"):
                decide.apply(self.root, self.feature, task, "x")
        decide.apply(self.root, self.feature, "T001", "(a)")
        with self.assertRaisesRegex(decide.Refused, "not an open decision"):
            decide.apply(self.root, self.feature, "T001", "(b)")  # already ticked

    def test_empty_answer_is_refused(self):
        with self.assertRaisesRegex(decide.Refused, "empty"):
            decide.apply(self.root, self.feature, "T001", "   ")

    def test_failed_commit_restores_the_files(self):
        hook = self.root / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        before = {p.name: p.read_text() for p in self.feature.iterdir()}
        with self.assertRaises(decide.Refused):
            decide.apply(self.root, self.feature, "T001", "(b)")
        self.assertEqual({p.name: p.read_text() for p in self.feature.iterdir()}, before)
        self.assertTrue(self.tracked_clean())

    def test_outside_a_git_repo_is_refused(self):
        with tempfile.TemporaryDirectory() as other:
            feature = Path(other).resolve() / "f"
            feature.mkdir()
            (feature / "tasks.md").write_text(TASKS)
            with self.assertRaisesRegex(decide.Refused, "not a git repository"):
                decide.apply(Path(other), feature, "T001", "(b)")

    def test_result_has_a_job_key_not_started_without_an_open_job(self):
        result = decide.apply(self.root, self.feature, "T002", "(a) red")
        self.assertEqual(result["job"], {"started": False, "reason": "no runs yet"})

    def test_without_a_commit_the_job_is_not_continued(self):
        result = decide.apply(self.root, self.feature, "T002", "(a) red", commit=False)
        self.assertEqual(result["job"], {"started": False, "reason": "nothing was committed"})


JOB_ID = "20261031-1012"


class OpenJobTest(unittest.TestCase):
    """With an open Job (spec 005): the decision commit lands on `auto/<id>` (FR-008) and the result carries
    `after_settle`'s answer (FR-016, data-model §10)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "project"
        self.feature = self.root / "specs" / "001-a"
        self.feature.mkdir(parents=True)
        (self.feature / "tasks.md").write_text(TASKS, encoding="utf-8")
        (self.feature / "research.md").write_text("# Research\n", encoding="utf-8")
        (self.root / ".gitignore").write_text(".sdd/\n")
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(self.tmp.name) / "state")})
        self.env.start()
        for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@example.com"),
                     ("config", "user.name", "T"), ("config", "commit.gpgsign", "false"),
                     ("add", "."), ("commit", "-q", "-m", "init"), ("switch", "-q", "-c", f"auto/{JOB_ID}")):
            git(self.root, *args)
        self.base = git(self.root, "rev-parse", "main").strip()
        decide.sdd_job.save(self.root, {
            "id": JOB_ID, "title": "Phase 1", "feature": "001-a", "scope": {"phases": ["1"]},
            "scope_tasks": ["T001", "T002", "T003", "T004"], "blocked_at_start": 0,
            "limits": {"max_tasks": None, "until": None, "max_runs": 20}, "branch": f"auto/{JOB_ID}",
            "base": {"branch": "main", "commit": self.base}, "state": "waiting",
            "reason": "only items that need you are left", "runs": [], "no_progress_runs": 0,
            "created": 1793450000, "ended": None, "merge_commit": None})
        self.spawn = mock.patch.object(decide.sdd_job._launcher(), "_spawn")
        self.popen = self.spawn.start()
        self.popen.return_value.pid = 4444

    def tearDown(self):
        self.spawn.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_decision_commit_is_on_the_job_branch(self):
        result = decide.apply(self.root, self.feature, "T002", "(b) blue")
        self.assertEqual(git(self.root, "branch", "--show-current").strip(), f"auto/{JOB_ID}")
        self.assertEqual(git(self.root, "rev-parse", "--short", f"auto/{JOB_ID}").strip(), result["commit"])
        self.assertIn("chore(decisions): T002", git(self.root, "log", "-1", "--format=%s", f"auto/{JOB_ID}"))
        self.assertEqual(git(self.root, "rev-parse", "main").strip(), self.base)  # main untouched

    def test_a_waiting_job_with_a_runnable_task_is_continued(self):
        """009 FR-024: the newest run ended `waiting` and landed, so the settle starts a new run with its scope."""
        git(self.root, "switch", "-q", "main")
        record = decide.sdd_job.load(self.root, JOB_ID)
        decide.sdd_job.save(self.root, {**record, "state": "ended", "outcome": "waiting",
                                        "landing": {"state": "landed", "commit": None, "files": []}})
        result = decide.apply(self.root, self.feature, "T002", "(b) blue")
        self.assertEqual(result["job"], {"started": True, "reason": None})
        self.popen.assert_called_once()
        self.assertIn("chore(decisions): T002", git(self.root, "log", "-1", "--format=%s", "main"))
        self.assertEqual(git(self.root, "rev-parse", "--short", "main").strip(), result["commit"])
        newest = decide.sdd_job.list_jobs(self.root)[0]
        self.assertNotEqual(newest["id"], JOB_ID)
        self.assertEqual((newest["state"], newest["scope"]), ("working", record["scope"]))

    def test_a_job_that_is_not_waiting_is_not_continued(self):
        record = decide.sdd_job.load(self.root, JOB_ID)
        decide.sdd_job.save(self.root, {**record, "state": "stopped", "reason": "stopped by you"})
        result = decide.apply(self.root, self.feature, "T002", "(b) blue")
        self.assertEqual(result["job"], {"started": False, "reason": "the newest run did not end waiting"})
        self.popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
