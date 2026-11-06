"""bin/sdd-history.py (spec 009, plan "Task history" and "Run summary")."""
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_history", HERE.parent / "bin" / "sdd-history.py")
history = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(history)

RUN = "20261106-1014"


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


class HistoryTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.wfroot = Path(tempfile.mkdtemp()).resolve()
        self.record = {"id": RUN, "title": "Phase 3", "feature": "008-standalone-server", "branch": f"auto/{RUN}",
                       "scope": {"phases": ["3"]}, "runs": ["20261106-1015"], "created": 1, "outcome": "stopped",
                       "reason": "stopped by you"}

    def tearDown(self):
        shutil.rmtree(self.root)
        shutil.rmtree(self.wfroot)

    def workflow(self, session, name, lines, metas=None):
        wf = self.wfroot / name
        wf.mkdir()
        (wf / "journal.jsonl").write_text("\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")
        for agent_id, model in (metas or {}).items():
            (wf / f"agent-{agent_id}.meta.json").write_text(json.dumps({"pickedEffortModel": model}))
            (wf / f"agent-{agent_id}.jsonl").write_text('{"timestamp": "2026-11-06T09:00:00Z"}\n')
        listing = self.root / ".sdd" / "runs" / session
        listing.mkdir(parents=True, exist_ok=True)
        path = listing / "workflows.json"
        dirs = json.loads(path.read_text())["workflow_dirs"] if path.exists() else []
        path.write_text(json.dumps({"workflow_dirs": dirs + [str(wf)], "feature": "008-standalone-server", "job": RUN}))
        return wf

    def retry_journal(self):
        return [
            {"type": "started", "key": "1", "agentId": "a1", "label": "impl:T012"},
            {"type": "result", "key": "1", "agentId": "a1", "result": {"status": "done", "summary": "s", "files_changed": ["x"]}},
            {"type": "started", "key": "2", "agentId": "a2", "label": "verify:T012"},
            {"type": "result", "key": "2", "agentId": "a2", "result": {"verdict": "reject", "problems": ["no test"], "checks_run": []}},
            {"type": "started", "key": "3", "agentId": "a3", "label": "impl:T012#2"},
            {"type": "result", "key": "3", "agentId": "a3", "result": {"status": "done", "summary": "s", "files_changed": ["x"]}},
            {"type": "started", "key": "4", "agentId": "a4", "label": "verify:T012"},
            {"type": "result", "key": "4", "agentId": "a4", "result": {"verdict": "approve", "problems": [], "checks_run": []}},
            {"type": "started", "key": "5", "agentId": "a5", "label": "commit:T012"},
            {"type": "result", "key": "5", "agentId": "a5", "result": {"checks_ran": True, "checks_passed": True, "failing_output": "", "commits": ["b882551"], "blocked_recorded": []}},
            {"type": "started", "key": "6", "agentId": "a6", "label": "impl:T013"},
        ]


class HarvestTest(HistoryTestCase):
    def test_retry_then_approve_keeps_both_attempts_and_problems(self):
        self.workflow("20261106-1015", "wf_one", self.retry_journal(), metas={"a1": "claude-opus-5-5"})
        added = history.harvest(self.root, self.record)
        self.assertEqual(added, {"T012": 7, "T013": 1})
        entries = history.read(self.root, "008-standalone-server", "T012")
        self.assertEqual([e["step"] for e in entries],
                         ["started", "implemented", "verified", "started", "implemented", "verified", "committed"])
        self.assertEqual([e.get("attempt") for e in entries if e["step"] == "started"], [1, 2])
        self.assertEqual(entries[2]["verdict"], "reject")
        self.assertEqual(entries[2]["problems"], ["no test"])
        self.assertEqual(entries[6]["commits"], ["b882551"])
        self.assertEqual(entries[0]["model"], "claude-opus-5-5")
        self.assertEqual(entries[0]["role"], "implementer")
        self.assertTrue(entries[0]["transcript"].endswith("agent-a1.jsonl"))
        self.assertTrue(all(e["run"] == RUN and e["session"] == "20261106-1015" for e in entries))
        self.assertEqual(len({e["key"] for e in entries}), len(entries))

    def test_transcript_gives_duration_and_tokens(self):
        wf = self.workflow("20261106-1015", "wf_one", self.retry_journal())
        (wf / "agent-a1.jsonl").write_text("\n".join(json.dumps(line) for line in [
            {"type": "assistant", "timestamp": "2026-11-06T09:00:00Z",
             "message": {"usage": {"input_tokens": 100, "output_tokens": 20}}},
            {"type": "user", "timestamp": "2026-11-06T09:01:00Z", "message": {"usage": {"input_tokens": 999}}},
            {"type": "assistant", "timestamp": "2026-11-06T09:02:05.500Z",
             "message": {"usage": {"input_tokens": 300, "output_tokens": 45}}},
        ]) + "\n", encoding="utf-8")
        history.harvest(self.root, self.record)
        entries = history.read(self.root, "008-standalone-server", "T012")
        from_a1 = [e for e in entries if e["key"].split(":")[-2] == "a1"]
        self.assertEqual([e["step"] for e in from_a1], ["started", "implemented"])
        for entry in from_a1:
            self.assertEqual(entry["duration_s"], 125)
            self.assertEqual(entry["tokens"], 465)

    def test_missing_transcript_gives_neither_field(self):
        self.workflow("20261106-1015", "wf_one", self.retry_journal())
        history.harvest(self.root, self.record)
        for entry in history.read(self.root, "008-standalone-server", "T012"):
            self.assertNotIn("transcript", entry)
            self.assertNotIn("duration_s", entry)
            self.assertNotIn("tokens", entry)

    def test_harvest_twice_adds_nothing(self):
        self.workflow("20261106-1015", "wf_one", self.retry_journal())
        history.harvest(self.root, self.record)
        before = (self.root / ".sdd" / "tasks" / "008-standalone-server" / "T012.jsonl").read_text()
        self.assertEqual(history.harvest(self.root, self.record), {})
        self.assertEqual((self.root / ".sdd" / "tasks" / "008-standalone-server" / "T012.jsonl").read_text(), before)

    def test_blocked_and_failed_checks(self):
        self.workflow("20261106-1015", "wf_two", [
            {"type": "started", "key": "1", "agentId": "b1", "label": "impl:T020"},
            {"type": "result", "key": "1", "agentId": "b1", "result": {"status": "blocked", "reason": "needs a mic", "tag": "manual"}},
            {"type": "started", "key": "2", "agentId": "b2", "label": "commit:T020,T021"},
            {"type": "result", "key": "2", "agentId": "b2", "result": {"checks_ran": True, "checks_passed": False, "commits": [], "blocked_recorded": ["T020"]}},
        ])
        history.harvest(self.root, self.record)
        steps = [e["step"] for e in history.read(self.root, "008-standalone-server", "T020")]
        self.assertEqual(steps, ["started", "blocked", "checks failed", "blocked"])
        self.assertEqual([e["step"] for e in history.read(self.root, "008-standalone-server", "T021")], ["checks failed"])

    def test_sessions_include_the_live_run_json(self):
        (self.root / ".sdd").mkdir(parents=True)
        (self.root / ".sdd" / "run.json").write_text(json.dumps({"run_id": "20261106-1100", "job": RUN}))
        self.assertEqual(history.sessions(self.root, self.record), ["20261106-1015", "20261106-1100"])
        (self.root / ".sdd" / "run.json").write_text(json.dumps({"run_id": "20261106-1200", "job": "other"}))
        self.assertEqual(history.sessions(self.root, self.record), ["20261106-1015"])

    def test_bad_task_id_is_refused(self):
        with self.assertRaises(ValueError):
            history.history_path(self.root, "008", "../x")
        self.assertEqual(history.read(self.root, "008", "../x"), [])

    def test_record_step(self):
        self.assertTrue(history.record_step(self.root, self.record, "T015", "dropped", diff="--- a\n+++ b\n"))
        entries = history.read(self.root, "008-standalone-server", "T015")
        self.assertEqual(entries[0]["step"], "dropped")
        self.assertEqual(entries[0]["diff"], "--- a\n+++ b\n")


class SummaryAndFinalizeTest(HistoryTestCase):
    def setUp(self):
        super().setUp()
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@example.com")
        git(self.root, "config", "user.name", "T")
        (self.root / "app.txt").write_text("a\n")
        git(self.root, "add", "app.txt")
        git(self.root, "commit", "-q", "-m", "init")
        (self.root / ".gitignore").write_text(".sdd/runs/*/\n.sdd/jobs/\n.sdd/run.json\n")
        git(self.root, "add", ".gitignore")
        git(self.root, "commit", "-q", "-m", "ignore")

    def test_summary_counts(self):
        self.workflow("20261106-1015", "wf_one", self.retry_journal(), metas={"a1": "claude-opus-5-5", "a2": "claude-opus-5-5"})
        history.harvest(self.root, self.record)
        s = history.summary(self.root, self.record)
        self.assertEqual(s["tasks"], {"done": ["T012"], "blocked": [], "dropped": []})
        self.assertEqual(s["attempts"], 3)  # T012 twice, T013 once
        self.assertEqual(s["approved_first_time"], 0)
        self.assertEqual(s["models"], {"implementer": "claude-opus-5-5", "verifier": "claude-opus-5-5"})
        self.assertEqual(s["sessions"], ["20261106-1015"])
        self.assertEqual(s["outcome"], "stopped")

    def test_finalize_commits_histories_report_and_summary_only(self):
        self.workflow("20261106-1015", "wf_one", self.retry_journal())
        (self.root / "other.txt").write_text("not mine\n")  # an untracked file finalize must leave alone
        result = history.finalize(self.root, self.record)
        self.assertIsNotNone(result["commit"])
        committed = git(self.root, "show", "--name-only", "--format=", "HEAD").splitlines()
        self.assertEqual(sorted(committed), sorted([
            ".sdd/runs/20261106-1014.json", ".sdd/runs/20261106-1015.md",
            ".sdd/tasks/008-standalone-server/T012.jsonl", ".sdd/tasks/008-standalone-server/T013.jsonl"]))
        self.assertEqual(git(self.root, "log", "-1", "--format=%s"), f"chore(run): {RUN} history and summary")
        report = (self.root / ".sdd" / "runs" / "20261106-1015.md").read_text()
        self.assertIn("T012", report)
        self.assertIn("b882551", report)
        self.assertIn("T013: started", report)
        self.assertIn("?? other.txt", subprocess.run(["git", "status", "--porcelain"], cwd=self.root,
                                                     capture_output=True, text=True).stdout)

    def test_finalize_with_nothing_new_makes_no_commit(self):
        self.workflow("20261106-1015", "wf_one", self.retry_journal())
        history.finalize(self.root, self.record)
        head = git(self.root, "rev-parse", "HEAD")
        self.assertEqual(history.finalize(self.root, self.record), {"commit": None, "files": []})
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), head)

    def test_existing_report_and_summary_are_kept(self):
        self.workflow("20261106-1015", "wf_one", self.retry_journal())
        (self.root / ".sdd" / "runs" / "20261106-1015.md").write_text("# the skill's report\n")
        history.finalize(self.root, self.record)
        self.assertEqual((self.root / ".sdd" / "runs" / "20261106-1015.md").read_text(), "# the skill's report\n")
        first = (self.root / ".sdd" / "runs" / f"{RUN}.json").read_text()
        self.assertIsNone(history.write_summary(self.root, {**self.record, "outcome": "done"}))
        self.assertEqual((self.root / ".sdd" / "runs" / f"{RUN}.json").read_text(), first)


class CliTest(HistoryTestCase):
    def test_show_and_harvest(self):
        self.workflow("20261106-1015", "wf_one", self.retry_journal())
        jobs = self.root / ".sdd" / "jobs"
        jobs.mkdir(parents=True)
        (jobs / f"{RUN}.json").write_text(json.dumps(self.record))
        script = str(HERE.parent / "bin" / "sdd-history.py")
        out = json.loads(subprocess.run(["python3", script, "harvest", str(self.root), RUN],
                                        capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out["added"]["T012"], 7)
        shown = json.loads(subprocess.run(["python3", script, "show", str(self.root), "008-standalone-server", "T012"],
                                          capture_output=True, text=True, check=True).stdout)
        self.assertEqual(len(shown["entries"]), 7)
        bad = subprocess.run(["python3", script, "show", str(self.root), "008", "nope"], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)


if __name__ == "__main__":
    unittest.main()
