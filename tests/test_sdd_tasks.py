import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE.parent / "bin" / "sdd-tasks.py"
spec = importlib.util.spec_from_file_location("sdd_tasks", SCRIPT)
sdd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdd)

TASKS = HERE / "fixtures" / "tasks.md"
BLOCKED = HERE / "fixtures" / "BLOCKED.md"


def tasks_by_id(parsed):
    return {t["id"]: t for p in parsed["phases"] for t in p["tasks"]}


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.parsed = sdd.parse_tasks(TASKS)
        self.tasks = tasks_by_id(self.parsed)

    def test_phases_and_ids(self):
        self.assertEqual([p["id"] for p in self.parsed["phases"]], ["1", "2", "3", "3a"])
        self.assertNotIn("T999", self.tasks)  # after the last phase

    def test_flags_tags_and_files(self):
        t = self.tasks["T009"]
        self.assertTrue(t["parallel"])
        self.assertEqual(t["story"], "US1")
        self.assertEqual(t["tags"], ["eval"])
        self.assertEqual(t["files"], ["src/eval/acc.py"])  # `svx eval accuracy` is not a path
        self.assertEqual(self.tasks["T008"]["tags"], ["manual"])
        self.assertTrue(self.tasks["T001"]["done"])

    def test_comma_tags_and_arrow_paths(self):
        t = self.tasks["T012"]
        self.assertTrue(t["parallel"])
        self.assertEqual(t["story"], "US2")
        self.assertEqual(t["files"], ["src/x.py", "src/y.py"])

    def test_blocked(self):
        self.assertEqual(sdd.parse_blocked(BLOCKED), {"T006": "open", "T003": "resolved"})

    def test_parse_command_output_unchanged(self):
        out = subprocess.run([sys.executable, str(SCRIPT), "parse", str(TASKS), "--blocked", str(BLOCKED)],
                             capture_output=True, text=True, check=True)
        result = json.loads(out.stdout)
        self.assertEqual(result["blocked"], {"T006": "open", "T003": "resolved"})
        self.assertEqual(result["phases"], json.loads(json.dumps(self.parsed["phases"])))


class BlockedEntriesTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.blocked = self.dir / "BLOCKED.md"

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_fixture_fields(self):
        entries = sdd.parse_blocked_entries(BLOCKED)
        self.assertEqual(entries["T006"], {"status": "open", "tag": "decision", "reason": "r",
                                           "question": "q", "answer": ""})
        self.assertEqual(entries["T003"], {"status": "resolved", "tag": "", "reason": "",
                                           "question": "", "answer": "done"})

    def test_last_entry_per_id_wins(self):
        self.blocked.write_text(
            "# Blocked tasks\n\n"
            "## T008\n- status: Resolved\n- tag: manual\n- reason: old\n- question: old q\n- answer: yes\n\n"
            "## T008\n- status: open\n- tag: failed\n- reason: new\n- question: new q\n- answer:\n",
            encoding="utf-8")
        entries = sdd.parse_blocked_entries(self.blocked)
        self.assertEqual(entries["T008"], {"status": "open", "tag": "failed", "reason": "new",
                                           "question": "new q", "answer": ""})
        self.assertEqual(sdd.parse_blocked(self.blocked), {"T008": "open"})

    def test_multi_line_answer(self):
        self.blocked.write_text(
            "## T008\n- status: resolved\n- tag: decision\n- question: Which one?\n- answer:\n"
            "Use the second option.\n\n  Keep the old API as well.\n"
            "- note: not part of the answer\n"
            "## Notes\nnot part of any entry\n",
            encoding="utf-8")
        entry = sdd.parse_blocked_entries(self.blocked)["T008"]
        self.assertEqual(entry["answer"], "Use the second option.\n\n  Keep the old API as well.")
        self.assertEqual(entry["status"], "resolved")
        self.assertEqual(entry["question"], "Which one?")

    def test_status_defaults_to_open(self):
        self.blocked.write_text("## T008\n- tag: manual\n", encoding="utf-8")
        self.assertEqual(sdd.parse_blocked_entries(self.blocked)["T008"]["status"], "open")

    def test_missing_file(self):
        self.assertEqual(sdd.parse_blocked_entries(self.dir / "nope.md"), {})
        self.assertEqual(sdd.parse_blocked_entries(None), {})
        self.assertEqual(sdd.parse_blocked(self.dir / "nope.md"), {})


class PlanTest(unittest.TestCase):
    def plan(self, **allow):
        config = sdd.load_config(None)
        config["allow"].update(allow)
        return sdd.plan(sdd.parse_tasks(TASKS), sdd.parse_blocked(BLOCKED), config, None, None)

    def test_holds(self):
        held = {h["id"]: h["tag"] for h in self.plan()["held"]}
        self.assertEqual(held, {"T003": "resolved", "T006": "blocked", "T008": "manual", "T010": "dep"})

    def test_wontfix_stays_held(self):
        # An entry dropped with `status: wontfix` must not send its open task back to the agents.
        config = sdd.load_config(None)
        result = sdd.plan(sdd.parse_tasks(TASKS), {"T006": "wontfix"}, config, None, None)
        self.assertIn({"id": "T006", "tag": "wontfix", "reason": "dropped in BLOCKED.md (wontfix)"}, result["held"])

    def test_allow_dependencies(self):
        held = {h["id"] for h in self.plan(dependencies=True)["held"]}
        self.assertNotIn("T010", held)

    def test_groups_split_on_shared_files_and_non_parallel(self):
        phases = {p["id"]: p["groups"] for p in self.plan()["phases"]}
        self.assertEqual(phases["1"], [["T002"]])  # T003 is held (resolved in BLOCKED.md)
        self.assertEqual(phases["2"], [["T004"], ["T005"]])  # T005 also touches a.py
        self.assertEqual(phases["3"], [["T007", "T009"], ["T011"]])

    def test_phase_filter_and_cap(self):
        config = sdd.load_config(None)
        out = sdd.plan(sdd.parse_tasks(TASKS), {}, config, ["3"], 1)
        self.assertEqual([p["id"] for p in out["phases"]], ["3"])
        self.assertEqual(out["runnable"], 1)


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.tasks = self.dir / "tasks.md"
        shutil.copy(TASKS, self.tasks)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_tick_only_named_open_tasks(self):
        out = sdd.tick(self.tasks, ["T002", "T001", "T404"])
        self.assertEqual(out["ticked"], ["T002"])
        self.assertEqual(out["missing"], ["T001", "T404"])
        text = self.tasks.read_text()
        self.assertIn("- [x] T002 [P] Add lint config", text)
        self.assertIn("- [ ] T003", text)

    def test_block_appends_once(self):
        blocked = self.dir / "BLOCKED.md"
        self.assertTrue(sdd.block(blocked, "T008", "manual", "needs a person", "Record it")["appended"])
        self.assertFalse(sdd.block(blocked, "T008", "manual", "again", "again")["appended"])
        self.assertEqual(sdd.parse_blocked(blocked), {"T008": "open"})

    def test_cli_prints_json(self):
        out = subprocess.run([sys.executable, str(SCRIPT), "plan", str(self.tasks), "--config", "/nonexistent"],
                             capture_output=True, text=True, check=True)
        self.assertIn("phases", json.loads(out.stdout))


class CheckpointTest(unittest.TestCase):
    def setUp(self):
        self.cp = sdd.checkpoint(HERE / "fixtures" / "journal.jsonl")

    def test_committed_tasks_are_left_out(self):
        self.assertNotIn("T001", self.cp["tasks"])

    def test_approved_but_uncommitted_keeps_verdict(self):
        # The commit agent for T002 was started but never returned.
        t = self.cp["tasks"]["T002"]
        self.assertEqual(t["verdict"]["verdict"], "approve")
        self.assertEqual(t["impl"]["files_changed"], ["b.py"])
        self.assertIn("commit:T002", self.cp["interrupted_agents"])

    def test_implemented_not_verified(self):
        self.assertEqual(self.cp["tasks"]["T003"], {"attempt": 0, "impl": {"status": "done", "summary": "three", "files_changed": ["c.py"]}})

    def test_rejection_is_kept_for_the_next_attempt(self):
        self.assertEqual(self.cp["tasks"]["T004"]["verdict"]["problems"], ["missing test"])

    def test_cut_off_attempt(self):
        self.assertEqual(self.cp["tasks"]["T005"], {"attempt": 1, "interrupted": "implement"})

    def test_earlier_attempts_files_are_kept(self):
        d = Path(tempfile.mkdtemp())
        j = d / "journal.jsonl"
        ev = [{"type": "started", "key": "1", "label": "impl:T1"},
              {"type": "result", "key": "1", "result": {"status": "done", "summary": "", "files_changed": ["a.py", "pyproject.toml"]}},
              {"type": "started", "key": "2", "label": "verify:T1"},
              {"type": "result", "key": "2", "result": {"verdict": "reject", "problems": ["x"], "checks_run": []}},
              {"type": "started", "key": "3", "label": "impl:T1#2"},
              {"type": "result", "key": "3", "result": {"status": "done", "summary": "", "files_changed": ["a.py"]}}]
        j.write_text("\n".join(json.dumps(e) for e in ev))
        self.assertEqual(sdd.checkpoint(j)["tasks"]["T1"]["files"], ["a.py", "pyproject.toml"])
        shutil.rmtree(d)

    def test_missing_journal(self):
        self.assertEqual(sdd.checkpoint(Path("/nonexistent/journal.jsonl"))["found"], False)


if __name__ == "__main__":
    unittest.main()
