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


ENGINE_INSTALL = "`python3 <plugin>/bin/sdd-engine.py install --from <plugin>`"


def section(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


class EngineStepZeroTest(unittest.TestCase):
    """Step 0 of `/sdd-run` and `/sdd-init` refreshes the engine copy (008 data-model §2)."""

    def check_step_zero(self, rel):
        text = read(rel)
        step = section(text, "## 0. ", "## 1. ")
        self.assertIn(ENGINE_INSTALL, step)
        self.assertIn("Ignore its result", step)
        self.assertLess(text.index("## 0. "), text.index("## 1. "))

    def test_sdd_run_step_zero(self):
        self.check_step_zero("skills/sdd-run/SKILL.md")

    def test_sdd_init_step_zero(self):
        self.check_step_zero("skills/sdd-init/SKILL.md")


class SddRunSessionTest(unittest.TestCase):
    """`/sdd-run` step 6 records the session and the Workflow task (008 data-model §3)."""

    def setUp(self):
        self.text = read("skills/sdd-run/SKILL.md")
        self.step6 = section(self.text, "## 6. Start", "## 7. ")

    def test_session_command_right_after_run_json(self):
        write = section(self.step6, "2. Write `.sdd/run.json`", "3. Call **Workflow**")
        command = "`python3 <helper> session --run-json .sdd/run.json`"
        self.assertIn(command, write)
        self.assertIn("as its own Bash call", write[write.index(command):])
        self.assertIn("Right after writing it", write)

    def test_workflow_task_beside_workflow_dir(self):
        call = section(self.step6, "3. Call **Workflow**", "4. Tell the user")
        self.assertIn("`workflow_dir`", call)
        self.assertIn('"workflow_task": "<the task id the Workflow tool returned>"', call)
        self.assertIn("beside `workflow_dir`", call)


class SddRunTrailersAndHistoryTest(unittest.TestCase):
    """`/sdd-run` passes `job` and `model` for the commit trailers and harvests task histories (009 FR-018)."""

    def setUp(self):
        self.text = read("skills/sdd-run/SKILL.md")

    def test_workflow_args_have_job_and_model(self):
        call = section(self.text, "3. Call **Workflow**", "4. Tell the user")
        self.assertIn('"job": "<the --job id, or null>"', call)
        self.assertIn('"model": "<your own model id>"', call)

    def test_harvest_before_commit_as_own_call(self):
        item = section(self.text, "4. **Commit**", "If `stopped` starts with")
        command = "`python3 <plugin>/bin/sdd-history.py harvest . <the --job id, else the run id>`"
        self.assertIn(command, item)
        self.assertIn("as its own Bash call", item[item.index(command):])
        self.assertLess(item.index(command), item.index("git add"))

    def test_git_add_includes_task_histories(self):
        adds = [line for line in self.text.splitlines() if "git add -- .sdd/runs/<run id>.md" in line]
        self.assertEqual(len(adds), 3)
        for line in adds:
            self.assertIn("git add -- .sdd/runs/<run id>.md .sdd/learnings.md .sdd/tasks`", line)


class SddRunSupervisedWarningTest(unittest.TestCase):
    """`/sdd-run` preflight warns when the supervisor did not start the run (009 T030)."""

    COMMAND = "python3 -c \"import os; print(os.environ.get('SDD_SUPERVISED', ''))\""
    SENTENCE = (
        "This run is not started through the supervisor, so it has no usage-limit protection. "
        "Runs are started with the launcher (bin/sdd-launch.py) or Start run in the dashboard."
    )

    def setUp(self):
        self.text = read("skills/sdd-run/SKILL.md")

    def test_holds_exact_command_and_sentence(self):
        self.assertIn(self.COMMAND, self.text)
        self.assertIn(self.SENTENCE, self.text)

    def test_no_printenv(self):
        self.assertNotIn("printenv", self.text)

    def test_in_preflight_before_confirm(self):
        confirm = self.text.index("## 5. Confirm once")
        preflight = self.text.index("## 1. Preflight")
        self.assertLess(preflight, self.text.index(self.COMMAND))
        self.assertLess(self.text.index(self.COMMAND), confirm)
        self.assertLess(self.text.index(self.SENTENCE), confirm)

    def test_warning_never_stops(self):
        para = section(self.text, self.COMMAND, "## 2. ")
        self.assertIn("never a stop", para)
        self.assertIn("denied or fails", para)


class SddStatusStateTest(unittest.TestCase):
    """`/sdd-status` step 4 reads the run state from the engine (008 data-model §4, §11)."""

    def setUp(self):
        text = read("skills/sdd-status/SKILL.md")
        self.step4 = section(text, "4. Give the run state", "5. Give the latest")

    def test_calls_state_command(self):
        self.assertIn("`python3 <helper> state --project .`", self.step4)
        self.assertIn("as its own Bash call", self.step4)

    def test_reports_state_and_origin(self):
        self.assertIn("report `state` and `origin`", self.step4)

    def test_contract_mismatch_line(self):
        self.assertIn("read `contract` from `.sdd/run.json`", self.step4)
        self.assertIn("When it is not the same as the `contract` in the `state` output, add one line", self.step4)


SUMMARY_PARAGRAPH = (
    "Every task gets a plain-language summary on the line right below it, indented by exactly two spaces: "
    "one sentence a person reads on the task card, with no file paths, function names or task ids "
    "(for example: The dashboard shows each run's state in words.). "
    "The task line itself stays exact, for the implementer."
)


class AgentTagsSummaryTest(unittest.TestCase):
    """The agent tags ask for a summary line under every task (009 T015)."""

    def test_template_holds_summary_paragraph(self):
        text = read("templates/agent-tags.md")
        self.assertIn("indented by exactly two spaces", text)
        self.assertIn(SUMMARY_PARAGRAPH, text)

    def test_override_holds_same_paragraph(self):
        self.assertIn(SUMMARY_PARAGRAPH, read(".specify/templates/overrides/tasks-template.md"))

    def test_override_section_matches_template(self):
        override = read(".specify/templates/overrides/tasks-template.md")
        part = section(override, "## Agent tags", "## Path Conventions")
        self.assertEqual(part.strip(), read("templates/agent-tags.md").strip())


if __name__ == "__main__":
    unittest.main()
