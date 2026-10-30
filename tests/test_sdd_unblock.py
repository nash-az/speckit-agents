"""bin/sdd-unblock.py (FR-037): settle an open BLOCKED.md entry and commit just the files it touched."""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_unblock", HERE.parent / "bin" / "sdd-unblock.py")
unblock = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(unblock)
_spec = importlib.util.spec_from_file_location("sdd_tasks", HERE.parent / "bin" / "sdd-tasks.py")
sdd_tasks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sdd_tasks)

TASKS = "## Phase 1: A\n\n- [ ] T001 Old\n- [ ] T053 Browser run\n- [ ] T054 Plain\n"
BLOCKED = ("# Blocked tasks\n\n## T001\n- status: wontfix\n- tag: decision\n- reason: gone\n- question: Drop?\n"
           "- answer: Yes.\n\n## T053\n- status: open\n- tag: manual\n- reason: sandbox\n"
           "- question: Run the smoke test outside the sandbox.\n- answer:\n\n## T054\n- status: open\n"
           "- tag: failed\n- reason: checks failed\n- question: Which API?\n- answer:\n")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


class SettleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.feature = self.root / "specs" / "001-a"
        self.feature.mkdir(parents=True)
        (self.feature / "tasks.md").write_text(TASKS, encoding="utf-8")
        (self.feature / "BLOCKED.md").write_text(BLOCKED, encoding="utf-8")
        for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "T"),
                     ("add", "."), ("commit", "-q", "-m", "init")):
            git(self.root, *args)

    def tearDown(self):
        self.tmp.cleanup()

    def entries(self):
        return sdd_tasks.parse_blocked_entries(self.feature / "BLOCKED.md")

    def test_done_ticks_and_resolves(self):
        result = unblock.settle(self.root, self.feature, "T053", "done", "smoke test passed")
        self.assertEqual(sorted(result["files"]), ["specs/001-a/BLOCKED.md", "specs/001-a/tasks.md"])
        self.assertIn("- [x] T053", (self.feature / "tasks.md").read_text())
        e = self.entries()["T053"]
        self.assertEqual(e["status"], "resolved")
        self.assertIn("smoke test passed", e["answer"])
        self.assertEqual(self.entries()["T054"]["status"], "open")  # the other entries stay
        self.assertEqual(self.entries()["T001"]["answer"], "Yes.")
        self.assertEqual(git(self.root, "status", "--porcelain").strip(), "")

    def test_answer_resolves_and_leaves_the_task_open(self):
        unblock.settle(self.root, self.feature, "T054", "answer", "use the v2 API")
        self.assertIn("- [ ] T054", (self.feature / "tasks.md").read_text())
        e = self.entries()["T054"]
        self.assertEqual(e["status"], "resolved")
        self.assertTrue(e["answer"].startswith("use the v2 API"))
        self.assertIn("answered", git(self.root, "log", "-1", "--format=%s"))

    def test_drop_is_wontfix_with_the_reason(self):
        unblock.settle(self.root, self.feature, "T053", "drop", "covered by T061")
        e = self.entries()["T053"]
        self.assertEqual(e["status"], "wontfix")
        self.assertIn("covered by T061", e["answer"])

    def test_refusals_change_nothing(self):
        for task, action, text in (("T001", "done", ""), ("T099", "done", ""), ("T054", "answer", " "),
                                   ("T054", "drop", "")):
            with self.assertRaises(unblock.Refused):
                unblock.settle(self.root, self.feature, task, action, text)
        (self.root / ".sdd").mkdir()
        (self.root / ".sdd" / "run.json").write_text("{}")
        with self.assertRaises(unblock.Refused) as ctx:
            unblock.settle(self.root, self.feature, "T053", "done")
        self.assertIn("a run is active", str(ctx.exception))
        (self.root / ".sdd" / "run.json").unlink()
        (self.feature / "BLOCKED.md").write_text(BLOCKED + "\n")
        with self.assertRaises(unblock.Refused) as ctx:
            unblock.settle(self.root, self.feature, "T053", "done")
        self.assertIn(": specs/001-a/BLOCKED.md", str(ctx.exception))
        self.assertEqual((self.feature / "tasks.md").read_text(), TASKS)

    def test_an_entry_cannot_be_settled_twice(self):
        unblock.settle(self.root, self.feature, "T053", "done")
        with self.assertRaises(unblock.Refused):
            unblock.settle(self.root, self.feature, "T053", "drop", "again")


if __name__ == "__main__":
    unittest.main()
