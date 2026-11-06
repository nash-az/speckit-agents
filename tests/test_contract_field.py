"""008 T008 (data-model §1): every engine command prints `"contract": "1.0"` at the top level of its JSON.

Each command runs as `python3 bin/<script> …` on a toy project, with temporary HOME, XDG_STATE_HOME and
XDG_DATA_HOME. The one exception is `sdd-job.py list`, which prints a JSON array (005 contracts/jobs-api.md),
so it has no top level to carry the field.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "bin"

TASKS = ("## Phase 1: A\n\n"
         "- [ ] T001 [decision] Pick a port. Options: (a) 80; (b) 8080\n"
         "- [ ] T002 [manual] Record audio\n"
         "- [ ] T003 Plain in `a.py`\n"
         "- [ ] T004 Blocked in `b.py`\n")
BLOCKED = ("# Blocked tasks\n\n## T004\n- status: open\n- tag: failed\n- reason: checks failed\n"
           "- question: Which API?\n- answer:\n")
JOB_ID = "20261031-1012"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


class ContractFieldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.root = base / "project"
        self.feature = self.root / "specs" / "001-a"
        self.feature.mkdir(parents=True)
        (self.root / ".sdd").mkdir()
        (self.root / ".sdd" / "config.toml").write_text("", encoding="utf-8")
        (self.root / ".gitignore").write_text(".sdd/\n", encoding="utf-8")
        (self.feature / "tasks.md").write_text(TASKS, encoding="utf-8")
        (self.feature / "BLOCKED.md").write_text(BLOCKED, encoding="utf-8")
        (self.feature / "spec.md").write_text("# Spec\n", encoding="utf-8")
        (self.root / ".specify" / "memory").mkdir(parents=True)
        (self.root / ".specify" / "memory" / "constitution.md").write_text("# Constitution\n", encoding="utf-8")
        for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@example.com"),
                     ("config", "user.name", "T"), ("add", "."), ("commit", "-q", "-m", "init")):
            git(self.root, *args)
        # Files the sdd-tasks commands may change, outside the repository.
        self.scratch = base / "scratch"
        self.scratch.mkdir()
        shutil.copy(self.feature / "tasks.md", self.scratch / "tasks.md")
        shutil.copy(self.feature / "BLOCKED.md", self.scratch / "BLOCKED.md")
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("SDD_SUPERVISED", "CLAUDECODE", "CLAUDE_PID") and not k.startswith("CLAUDE_CODE_")}
        self.env.update(HOME=str(base / "home"), XDG_STATE_HOME=str(base / "state"),
                        XDG_DATA_HOME=str(base / "data"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_json(self, script, *args):
        out = subprocess.run([sys.executable, str(BIN / script), *map(str, args)], cwd=self.root,
                             capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(out.returncode, 0, f"{script} {args}: {out.stdout}{out.stderr}")
        result = json.loads(out.stdout)
        self.assertIsInstance(result, dict, f"{script} {args}")
        self.assertEqual(result.get("contract"), "1.0", f"{script} {args}: {out.stdout}")
        return result

    def run_refused(self, script, *args):
        """A refusal exits 1 and prints `{"contract", "error"}` (contracts/engine-cli.md)."""
        out = subprocess.run([sys.executable, str(BIN / script), *map(str, args)], cwd=self.root,
                             capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(out.returncode, 1, f"{script} {args}: {out.stdout}{out.stderr}")
        result = json.loads(out.stdout)
        self.assertEqual(result.get("contract"), "1.0", f"{script} {args}: {out.stdout}")
        self.assertIn("error", result)
        self.assertTrue(result["error"])
        return result

    def test_refusals(self):
        self.run_refused("sdd-decide.py", self.root, self.feature, "T003", "(a) 80")
        self.run_refused("sdd-manual.py", "done", self.root, self.feature, "T003", "recorded")
        self.run_refused("sdd-unblock.py", "answer", self.root, self.feature, "T003", "use the v2 API")
        self.run_refused("sdd-edit.py", "commit", self.root, self.feature, "spec", "docs: nothing changed")
        self.assertEqual(self.run_refused("sdd-job.py", "show", "20261031-0000", self.root)["error"],
                         "no such job: 20261031-0000")
        (self.root / "stray.txt").write_text("mine", encoding="utf-8")
        self.run_refused("sdd-launch.py", self.root, self.feature, "--dry-run")

    def test_sdd_tasks_commands(self):
        tasks, blocked = self.scratch / "tasks.md", self.scratch / "BLOCKED.md"
        config = self.root / ".sdd" / "config.toml"
        self.run_json("sdd-tasks.py", "parse", tasks, "--blocked", blocked)
        self.run_json("sdd-tasks.py", "plan", tasks, "--blocked", blocked, "--config", config)
        self.run_json("sdd-tasks.py", "config", "--config", config)
        self.run_json("sdd-tasks.py", "checkpoint", self.scratch / "journal.jsonl")
        snapshot = self.run_json("sdd-tasks.py", "snapshot", tasks)
        run_json = self.scratch / "run.json"
        run_json.write_text(json.dumps({"run_id": "20261104-0000", "task_snapshot": snapshot}), encoding="utf-8")
        self.assertEqual(self.run_json("sdd-tasks.py", "changed", tasks, "--run-json", run_json)["changed"], [])
        self.run_json("sdd-tasks.py", "events", self.root, "20261104-0000")
        self.run_json("sdd-tasks.py", "session", "--run-json", run_json)
        self.run_json("sdd-tasks.py", "state", "--project", self.root)
        self.run_json("sdd-tasks.py", "tick", tasks, "T003")
        self.run_json("sdd-tasks.py", "block", blocked, "--id", "T003", "--reason", "r", "--question", "q")

    def test_control_controls(self):
        self.run_json("sdd-control.py", "controls", "--project", self.root)

    def test_settle_helpers(self):
        self.run_json("sdd-decide.py", self.root, self.feature, "T001", "(a) 80")
        self.run_json("sdd-manual.py", "done", self.root, self.feature, "T002", "recorded")
        self.run_json("sdd-unblock.py", "answer", self.root, self.feature, "T004", "use the v2 API")

    def test_manual_skip_and_unblock_drop(self):
        self.run_json("sdd-manual.py", "skip", self.root, self.feature, "T002", "not needed")
        self.run_json("sdd-unblock.py", "drop", self.root, self.feature, "T004", "covered elsewhere")

    def test_edit_state_and_commit(self):
        self.assertEqual(self.run_json("sdd-edit.py", "state", self.root, self.feature, "spec")["dirty"], False)
        (self.feature / "spec.md").write_text("# Spec\n\nMore.\n", encoding="utf-8")
        self.run_json("sdd-edit.py", "commit", self.root, self.feature, "spec", "docs: spec")

    def test_launch_dry_run(self):
        self.run_json("sdd-launch.py", self.root, self.feature, "--dry-run")
        self.run_json("sdd-launch.py", self.root, self.feature, "--job", "--dry-run")

    def test_harvest(self):
        plugin = Path(self.tmp.name) / "plugin"
        (plugin / "bin").mkdir(parents=True)
        shutil.copy(BIN / "sdd-harvest.py", plugin / "bin" / "sdd-harvest.py")
        out = subprocess.run([sys.executable, str(plugin / "bin" / "sdd-harvest.py"), str(self.root)],
                             capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["contract"], "1.0")

    def test_job_show_and_drop_and_saved_record(self):
        record = {"id": JOB_ID, "title": "Phase 1", "feature": "001-a", "scope": {"phases": ["1"]},
                  "scope_tasks": ["T003"], "blocked_at_start": 0,
                  "limits": {"max_tasks": None, "until": None, "max_runs": 20}, "branch": f"auto/{JOB_ID}",
                  "base": {"branch": "main", "commit": git(self.root, "rev-parse", "--short", "HEAD").strip()},
                  "state": "stopped", "reason": None, "runs": [], "no_progress_runs": 0, "created": 1793450000,
                  "ended": None, "merge_commit": None}
        jobs = self.root / ".sdd" / "jobs"
        jobs.mkdir(parents=True)
        (jobs / f"{JOB_ID}.json").write_text(json.dumps(record), encoding="utf-8")
        # Its own run.json makes the 005 stopped record read suspended, so Drop takes it (009 drop_suspended).
        (self.root / ".sdd" / "run.json").write_text(
            json.dumps({"run_id": "20261031-1013", "job": JOB_ID, "branch": f"auto/{JOB_ID}"}), encoding="utf-8")
        self.assertEqual(self.run_json("sdd-job.py", "show", JOB_ID, self.root)["shown"]["state"], "suspended")
        self.run_json("sdd-job.py", "drop", JOB_ID, self.root)
        # save() wrote the record again, with the field (data-model §1, "Job records").
        saved = json.loads((jobs / f"{JOB_ID}.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["state"], saved["outcome"], saved["contract"]), ("ended", "dropped", "1.0"))

    def test_harvest_keeps_its_contract_in_step_with_sdd_tasks(self):
        """sdd-harvest.py runs on its own, so it has its own copy of CONTRACT; the two change together."""
        text = (BIN / "sdd-tasks.py").read_text(encoding="utf-8")
        ours = next(line for line in text.splitlines() if line.startswith("CONTRACT = "))
        self.assertIn(ours, (BIN / "sdd-harvest.py").read_text(encoding="utf-8").splitlines())


if __name__ == "__main__":
    unittest.main()
