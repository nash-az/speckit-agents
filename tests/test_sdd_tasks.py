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


class PlanTest(unittest.TestCase):
    def plan(self, **allow):
        config = sdd.load_config(None)
        config["allow"].update(allow)
        return sdd.plan(sdd.parse_tasks(TASKS), sdd.parse_blocked(BLOCKED), config, None, None)

    def test_holds(self):
        held = {h["id"]: h["tag"] for h in self.plan()["held"]}
        self.assertEqual(held, {"T006": "blocked", "T008": "manual", "T010": "dep"})

    def test_allow_dependencies(self):
        held = {h["id"] for h in self.plan(dependencies=True)["held"]}
        self.assertNotIn("T010", held)

    def test_groups_split_on_shared_files_and_non_parallel(self):
        phases = {p["id"]: p["groups"] for p in self.plan()["phases"]}
        self.assertEqual(phases["1"], [["T002", "T003"]])
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


if __name__ == "__main__":
    unittest.main()
