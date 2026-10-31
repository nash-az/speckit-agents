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


class SddRunJobArgsTest(unittest.TestCase):
    """`/sdd-run --tasks` and `--job` (005 data-model §5)."""

    def setUp(self):
        self.text = read("skills/sdd-run/SKILL.md")

    def argument_hint(self):
        for line in self.text.splitlines():
            if line.startswith("argument-hint:"):
                return line
        self.fail("no argument-hint line")

    def test_argument_hint_names_tasks_and_job(self):
        hint = self.argument_hint()
        self.assertIn("--tasks", hint)
        self.assertIn("--job", hint)

    def test_tasks_goes_to_plan_and_skips_converge(self):
        self.assertIn("`--tasks a,b`", self.text)
        self.assertIn("[--tasks <the --tasks ids>]", self.text)
        self.assertIn("Skip this step with `--phases`, `--tasks` or `--no-converge`.", self.text)

    def test_job_key_in_run_json_description(self):
        start = self.text.index("2. Write `.sdd/run.json`")
        end = self.text.index("3. Call **Workflow**")
        section = self.text[start:end]
        self.assertIn("--job <id>", section)
        self.assertIn('"job": "<id>"', section)

    def test_job_key_in_workflows_json_description(self):
        start = self.text.index("`.sdd/runs/<run id>/workflows.json`, `{")
        section = self.text[start:self.text.index("4. Tell the user", start)]
        self.assertIn("--job <id>", section)
        self.assertIn('"job": "<id>"', section)
        self.assertIn("beside `feature`", section)


if __name__ == "__main__":
    unittest.main()
