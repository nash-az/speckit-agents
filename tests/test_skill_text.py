"""Skill text and repo ignore lines that other code relies on (005 research R1, data-model §11)."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class JobsIgnoreLineTest(unittest.TestCase):
    def test_sdd_init_lists_jobs_line_as_fifth(self):
        text = read("skills/sdd-init/SKILL.md")
        self.assertIn(".sdd/jobs/", text)
        self.assertIn("these five lines", text)
        self.assertIn("any of the five lines", text)
        self.assertNotIn("four lines", text)

    def test_sdd_run_preflight_requires_jobs_line(self):
        text = read("skills/sdd-run/SKILL.md")
        self.assertIn(".sdd/jobs/", text)
        self.assertIn("all five of these lines", text)

    def test_gitignore_has_jobs_line(self):
        lines = read(".gitignore").splitlines()
        self.assertIn(".sdd/jobs/", lines)


if __name__ == "__main__":
    unittest.main()
