#!/usr/bin/env python3
"""Tests for bin/sdd-harvest.py (data-model.md §8, FR-024).

Each test copies the script into a temporary plugin dir (`<tmp>/plugin/bin/`) so that
`logs/.harvested.json` and the default log land there, never in this repo.
"""

import datetime
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "sdd-harvest.py"


def entry(n, text="Guard refused a scratch write"):
    return {"id": f"20261028-0925-{n}", "date": "2026-10-28", "run_id": "20261028-0925",
            "project": "toy", "text": f"{text} {n}", "evidence": f"event e{n}"}


class HarvestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.plugin = self.tmp / "plugin"
        (self.plugin / "bin").mkdir(parents=True)
        shutil.copy(SCRIPT, self.plugin / "bin" / "sdd-harvest.py")
        self.project = self.tmp / "toy"
        (self.project / ".sdd").mkdir(parents=True)
        self.today = datetime.date.today().isoformat()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_feedback(self, lines):
        (self.project / ".sdd" / "plugin-feedback.jsonl").write_text(
            "".join(l + "\n" for l in lines), encoding="utf-8")

    def run_harvest(self, *extra):
        proc = subprocess.run(
            [sys.executable, str(self.plugin / "bin" / "sdd-harvest.py"), str(self.project), *extra],
            capture_output=True, text=True, cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_first_run_harvests_second_run_harvests_zero(self):
        self.write_feedback([json.dumps(entry(1)), json.dumps(entry(2))])
        first = self.run_harvest()
        log = self.plugin / "logs" / f"{self.today}-toy-feedback.md"
        self.assertEqual(first, {"contract": "1.0", "harvested": 2, "skipped": 0, "log": str(log)})
        text = log.read_text(encoding="utf-8")
        self.assertIn(f"## Harvested {self.today}", text)
        self.assertIn("- 2026-10-28 20261028-0925: Guard refused a scratch write 1 (evidence: event e1)", text)
        self.assertIn("- 2026-10-28 20261028-0925: Guard refused a scratch write 2 (evidence: event e2)", text)
        ids = json.loads((self.plugin / "logs" / ".harvested.json").read_text(encoding="utf-8"))
        self.assertEqual(ids, {"ids": ["20261028-0925-1", "20261028-0925-2"]})

        second = self.run_harvest()
        self.assertEqual(second["harvested"], 0)
        self.assertEqual(second["skipped"], 2)
        self.assertEqual(log.read_text(encoding="utf-8"), text)

    def test_new_entry_after_harvest_is_appended_once(self):
        self.write_feedback([json.dumps(entry(1))])
        self.run_harvest()
        self.write_feedback([json.dumps(entry(1)), json.dumps(entry(2))])
        result = self.run_harvest()
        self.assertEqual((result["harvested"], result["skipped"]), (1, 1))
        text = Path(result["log"]).read_text(encoding="utf-8")
        self.assertEqual(text.count("scratch write 1 "), 1)
        self.assertEqual(text.count("scratch write 2 "), 1)
        self.assertEqual(text.count(f"## Harvested {self.today}"), 1)

    def test_bad_line_is_skipped(self):
        self.write_feedback([json.dumps(entry(1)), "{not json", json.dumps(["no", "id"]), json.dumps(entry(2))])
        result = self.run_harvest()
        self.assertEqual((result["harvested"], result["skipped"]), (2, 2))
        text = Path(result["log"]).read_text(encoding="utf-8")
        self.assertNotIn("not json", text)

    def test_log_option(self):
        self.write_feedback([json.dumps(entry(1))])
        custom = self.tmp / "out" / "custom.md"
        result = self.run_harvest("--log", str(custom))
        self.assertEqual(result["log"], str(custom))
        self.assertIn("scratch write 1", custom.read_text(encoding="utf-8"))
        self.assertFalse((self.plugin / "logs" / f"{self.today}-toy-feedback.md").exists())

    def test_missing_feedback_file_harvests_nothing(self):
        result = self.run_harvest()
        self.assertEqual((result["harvested"], result["skipped"]), (0, 0))
        self.assertFalse((self.plugin / "logs" / ".harvested.json").exists())


if __name__ == "__main__":
    unittest.main()
