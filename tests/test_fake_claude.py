"""The scripted stand-in for `claude -p` (tests/fake/claude): the tick and finish steps (005 data-model §11)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
FAKE = HERE / "fake" / "claude"


class FakeClaudeTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.fake_dir = self.root / "fake"
        self.fake_dir.mkdir()
        self.project = self.root / "project"
        (self.project / ".sdd").mkdir(parents=True)
        self.run_json = self.project / ".sdd" / "run.json"
        self.run_json.write_text('{"run_id": "x"}')
        self.tasks = self.project / "tasks.md"
        self.tasks.write_text("# Tasks\n\n- [ ] T001 First\n- [ ] T0010 Not this one\n- [ ] T002 Second\n")

    def play(self, steps):
        (self.fake_dir / "scenario.json").write_text(json.dumps({"cycles": [steps]}))
        env = dict(os.environ, FAKE_DIR=str(self.fake_dir), FAKE_TASKS=str(self.tasks))
        return subprocess.run([sys.executable, str(FAKE), "-p", "/sdd-run"], cwd=self.project, env=env,
                              capture_output=True, text=True, timeout=30)

    def test_tick_and_finish(self):
        proc = self.play([{"tick": "T001"}, {"finish": True}, {"exit": 0}])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.tasks.read_text(),
                         "# Tasks\n\n- [x] T001 First\n- [ ] T0010 Not this one\n- [ ] T002 Second\n")
        self.assertFalse(self.run_json.exists())

    def test_existing_scenario_runs_unchanged(self):
        event = {"type": "result", "subtype": "success"}
        proc = self.play([{"emit": event}, {"journal": {"type": "started", "key": "k"}}, {"exit": 3}])
        self.assertEqual(proc.returncode, 3)
        self.assertEqual([json.loads(line) for line in proc.stdout.splitlines()], [event])
        journal = (self.fake_dir / "wf" / "journal.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(line) for line in journal], [{"type": "started", "key": "k"}])
        self.assertEqual(json.loads((self.fake_dir / "calls").read_text()), ["-p", "/sdd-run"])
        self.assertTrue(self.run_json.exists())
        self.assertIn("- [ ] T001 First", self.tasks.read_text())


if __name__ == "__main__":
    unittest.main()
