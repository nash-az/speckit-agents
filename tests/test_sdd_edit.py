"""bin/sdd-edit.py (FR-035): edit spec.md or the constitution, and commit just that file."""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_edit", HERE.parent / "bin" / "sdd-edit.py")
edit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(edit)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


class EditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.feature = self.root / "specs" / "001-a"
        self.feature.mkdir(parents=True)
        (self.feature / "spec.md").write_text("# Spec\n\nOne.\n", encoding="utf-8")
        (self.feature / "plan.md").write_text("# Plan\n", encoding="utf-8")
        (self.root / ".specify" / "memory").mkdir(parents=True)
        (self.root / ".specify" / "memory" / "constitution.md").write_text("# Constitution\n", encoding="utf-8")
        for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "T"),
                     ("add", "."), ("commit", "-q", "-m", "init")):
            git(self.root, *args)

    def tearDown(self):
        self.tmp.cleanup()

    def state(self, name="spec"):
        return edit.state(self.root, self.feature, name)

    def test_state_of_a_clean_file(self):
        st = self.state()
        self.assertEqual((st["path"], st["exists"], st["dirty"], st["locked"]), ("specs/001-a/spec.md", True, False, None))
        self.assertEqual(st["hash"], edit.digest("# Spec\n\nOne.\n"))

    def test_save_replaces_the_file_and_marks_it_dirty(self):
        base = self.state()["hash"]
        st = edit.save(self.root, self.feature, "spec", "# Spec\n\nTwo.\r\n\n\n", base)
        self.assertEqual((self.feature / "spec.md").read_text(encoding="utf-8"), "# Spec\n\nTwo.\n")
        self.assertTrue(st["dirty"])
        self.assertEqual(st["hash"], edit.digest("# Spec\n\nTwo.\n"))
        self.assertEqual(list(self.feature.glob("*.tmp")), [])

    def test_save_refused_when_the_file_changed_since_it_was_opened(self):
        base = self.state()["hash"]
        (self.feature / "spec.md").write_text("# Spec\n\nChanged elsewhere.\n", encoding="utf-8")
        with self.assertRaisesRegex(edit.Refused, "changed since you opened it"):
            edit.save(self.root, self.feature, "spec", "# Mine\n", base)
        self.assertIn("Changed elsewhere", (self.feature / "spec.md").read_text(encoding="utf-8"))

    def test_save_refused_for_empty_text_and_during_a_run(self):
        base = self.state()["hash"]
        with self.assertRaisesRegex(edit.Refused, "empty"):
            edit.save(self.root, self.feature, "spec", "  \n", base)
        (self.root / ".sdd").mkdir()
        (self.root / ".sdd" / "run.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.state()["locked"], edit.RUN_ACTIVE)
        with self.assertRaisesRegex(edit.Refused, "run is active"):
            edit.save(self.root, self.feature, "spec", "# New\n", base)
        with self.assertRaisesRegex(edit.Refused, "run is active"):
            edit.commit(self.root, self.feature, "spec", "docs: x")

    def test_only_spec_and_constitution_are_editable(self):
        for name in ("plan", "../plan", "tasks", ""):
            with self.assertRaises(edit.Refused):
                edit.target(self.root, self.feature, name)
        with self.assertRaisesRegex(edit.Refused, "choose a feature"):
            edit.target(self.root, None, "spec")
        with self.assertRaisesRegex(edit.Refused, "under specs/"):
            edit.target(self.root, self.root, "spec")

    def test_constitution_is_editable_without_a_feature(self):
        base = edit.state(self.root, None, "constitution")["hash"]
        st = edit.save(self.root, None, "constitution", "# Constitution\n\nNew rule.\n", base)
        self.assertEqual((st["path"], st["dirty"]), (".specify/memory/constitution.md", True))

    def test_commit_takes_only_that_file(self):
        edit.save(self.root, self.feature, "spec", "# Spec\n\nTwo.\n", self.state()["hash"])
        (self.feature / "plan.md").write_text("# Plan\n\nother change\n", encoding="utf-8")
        result = edit.commit(self.root, self.feature, "spec", "docs(spec): tighten the wording")
        self.assertEqual(result["file"], "specs/001-a/spec.md")
        self.assertEqual(git(self.root, "log", "-1", "--format=%s").strip(), "docs(spec): tighten the wording")
        self.assertEqual(git(self.root, "show", "--name-only", "--format=", "HEAD").split(), ["specs/001-a/spec.md"])
        self.assertIn("plan.md", git(self.root, "status", "--porcelain"))  # the other change is still there
        self.assertFalse(self.state()["dirty"])

    def test_commit_refusals(self):
        with self.assertRaisesRegex(edit.Refused, "no uncommitted change"):
            edit.commit(self.root, self.feature, "spec", "docs: x")
        edit.save(self.root, self.feature, "spec", "# Spec\n\nTwo.\n", self.state()["hash"])
        with self.assertRaisesRegex(edit.Refused, "message is empty"):
            edit.commit(self.root, self.feature, "spec", "   ")
        self.assertTrue(self.state()["dirty"])


if __name__ == "__main__":
    unittest.main()
