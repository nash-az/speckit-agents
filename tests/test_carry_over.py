"""009 carry-over (US5, FR-025, SC-006, research R12): this repository's own 005 records, copied into
`tests/fixtures/carry-over/`, read as runs in a temp git repository. The live `.sdd/` is never read."""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "carry-over"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE.parent / "bin" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


job = _load("sdd_job", "sdd-job.py")
views = _load("sdd_views", "sdd-views.py")

# The three merged Jobs and the session each one names (their `runs`).
MERGED = {"20261104-1602": "20261104-1605", "20261104-1710": "20261104-1710", "20261106-1014": "20261106-1014"}
STOPPED = "20261101-0900"  # 005 `stopped`, with its run.json: shown suspended
DONE = "20261101-1000"  # 005 `done`, not merged, no run.json: ended, landing pending


class CarryOverTest(unittest.TestCase):
    """A temp repo: `main` with the session reports committed, `auto/20261101-1000` and `auto/20261101-0900`
    one commit each ahead of it, the latter checked out with an uncommitted edit; the fixture records in
    `.sdd/jobs/`, the workflows listings under `.sdd/runs/<session>/` and the stopped run's `.sdd/run.json`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "project"
        self.root.mkdir()
        # No supervisor status: the state folder is a fresh temp one.
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(self.tmp.name) / "state")})
        self.env.start()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "commit.gpgsign", "false")
        # As `/sdd-init` sets it up (DropSuspendedTest): records, run.json and session folders are ignored.
        (self.root / ".gitignore").write_text(".sdd/jobs/\n.sdd/run.json\n.sdd/runs/*/\n")
        runs = self.root / ".sdd" / "runs"
        runs.mkdir(parents=True)
        for report in sorted((FIXTURE / "runs").glob("*.md")):
            shutil.copyfile(report, runs / report.name)
        self.git("add", ".gitignore", ".sdd/runs")
        self.git("commit", "-q", "-m", "base")
        self.base_commit = self.head()

        self.git("switch", "-q", "-c", f"auto/{DONE}")
        self.write_commit("done.txt", "done work\n", "T002 done work")
        self.done_commit = self.head()
        self.git("switch", "-q", "main")
        self.git("switch", "-q", "-c", f"auto/{STOPPED}")
        self.write_commit("stopped.txt", "finished work\n", "T001 finished work")
        self.stopped_commit = self.head()
        (self.root / "stopped.txt").write_text("half done\n")

        jobs = self.root / ".sdd" / "jobs"
        shutil.copytree(FIXTURE / "jobs", jobs)
        for listing in sorted((FIXTURE / "runs").glob("*/workflows.json")):
            (runs / listing.parent.name).mkdir()
            shutil.copyfile(listing, runs / listing.parent.name / "workflows.json")
        for name in (f"{STOPPED}.json", f"{DONE}.json"):
            shutil.copyfile(FIXTURE / "open" / name, jobs / name)
        shutil.copyfile(FIXTURE / "open" / "run.json", self.root / ".sdd" / "run.json")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, check=True).stdout

    def head(self, ref="HEAD"):
        return self.git("rev-parse", ref).strip()

    def write_commit(self, name, text, message):
        (self.root / name).write_text(text)
        self.git("add", name)
        self.git("commit", "-q", "-m", message)

    def record(self, run_id):
        return job.load(self.root, run_id)

    def assertMergedUnchanged(self):
        for run_id in MERGED:
            self.assertEqual((self.root / ".sdd" / "jobs" / f"{run_id}.json").read_bytes(),
                             (FIXTURE / "jobs" / f"{run_id}.json").read_bytes(), run_id)

    def test_us5_records_carry_over_and_the_open_ones_can_be_dropped_and_landed(self):
        # Scenario 1: merged Jobs are past runs, landed with their merge commit, with their sessions.
        for run_id, session in MERGED.items():
            record = self.record(run_id)
            shown = job.shown_state(self.root, record)
            self.assertEqual(shown["state"], "ended", run_id)
            self.assertEqual(shown["landing"], {"state": "landed", "commit": record["merge_commit"], "files": []})
            self.assertFalse(job.is_open(self.root, record), run_id)
            normalized = job.normalize(self.root, record)
            self.assertEqual((normalized["state"], normalized["landing"]["state"]), ("ended", "landed"))
            view = views.get_job(self.root, run_id)
            self.assertEqual(view["shown"]["state"], "ended")
            self.assertEqual([r["id"] for r in view["runs"]], [session], run_id)
            self.assertTrue(view["runs"][0]["finished"], run_id)  # its report is there
        self.assertMergedUnchanged()

        # Scenario 2: the stopped Job with its run.json is a suspended run.
        stopped = self.record(STOPPED)
        self.assertEqual(job.shown_state(self.root, stopped)["state"], "suspended")
        self.assertTrue(job.is_open(self.root, stopped))
        self.assertEqual(views.get_job(self.root, STOPPED)["changed_files"], ["stopped.txt"])

        # Scenario 3: the done Job without run.json, not merged, is ended and offered Land.
        done = self.record(DONE)
        shown = job.shown_state(self.root, done)
        self.assertEqual((shown["state"], shown["outcome"]), ("ended", "done"))
        self.assertEqual(shown["landing"], {"state": "pending", "commit": None, "files": []})
        self.assertTrue(job.is_open(self.root, done))

        # Drop the stopped one: the half-done edit is stashed, the finished commit lands on main.
        dropped = job.drop_suspended(self.root, STOPPED)
        self.assertEqual((dropped["dropped"], dropped["tasks"], dropped["stash"]),
                         (True, [], f"sdd-run {STOPPED}: dropped partial work"))
        self.assertTrue(dropped["end"]["land"]["landed"], dropped)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD").strip(), "main")
        self.assertEqual(subprocess.run(["git", "merge-base", "--is-ancestor", self.stopped_commit, "main"],
                                        cwd=self.root).returncode, 0)
        self.assertEqual((self.root / "stopped.txt").read_text(), "finished work\n")
        self.assertEqual(self.git("show", "stash@{0}:stopped.txt"), "half done\n")
        self.assertFalse((self.root / ".sdd" / "run.json").exists())
        saved = self.record(STOPPED)
        self.assertEqual((saved["state"], saved["outcome"], saved["landing"]["state"]),
                         ("ended", "dropped", "landed"))
        self.assertEqual(saved["runs"], ["20261101-0901"])

        # Land the done one: main has moved on, so it lands with a merge commit, then its summary is written.
        landed = job.land(self.root, DONE)
        self.assertTrue(landed["landed"])
        self.assertFalse(landed["fast_forward"])
        self.assertEqual(landed["summary"], f".sdd/runs/{DONE}.json")
        self.assertNotIn("summary_error", landed)
        merge = landed["commit"]
        self.assertEqual(len(self.git("rev-list", "--parents", "-n", "1", merge).split()), 3)  # two parents
        self.assertEqual(self.git("log", "-1", "--format=%s", merge).strip(), "merge: run Phase 2")
        self.assertEqual(self.head("main^"), merge)  # the summary commit sits on the merge
        self.assertEqual(subprocess.run(["git", "merge-base", "--is-ancestor", self.done_commit, "main"],
                                        cwd=self.root).returncode, 0)
        summary = json.loads((self.root / ".sdd" / "runs" / f"{DONE}.json").read_text())
        self.assertEqual((summary["id"], summary["landing"]["state"]), (DONE, "landed"))
        self.assertEqual(self.git("ls-tree", "--name-only", "main", f".sdd/runs/{DONE}.json").strip(),
                         f".sdd/runs/{DONE}.json")
        saved = self.record(DONE)
        self.assertEqual((saved["state"], saved["landing"], saved["merge_commit"]),
                         ("ended", {"state": "landed", "commit": merge, "files": []}, merge))
        self.assertFalse(job.is_open(self.root, saved))
        self.assertEqual(self.git("status", "--porcelain"), "")

        # The merged records were never written.
        self.assertMergedUnchanged()


if __name__ == "__main__":
    unittest.main()
