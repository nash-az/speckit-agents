"""bin/sdd-launch.py (FR-034): what a dashboard-started run would do, and when it refuses."""
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_launch", HERE.parent / "bin" / "sdd-launch.py")
launch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launch)

TASKS = ("## Phase 1: Setup\n\n- [ ] T001 Do a thing\n- [x] T002 Done thing\n\n"
         "## Phase 2: Build\n\n- [ ] T003 Build it\n- [ ] T004 [manual] Record audio\n")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


class LaunchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.root = base / "project"
        self.feature = self.root / "specs" / "001-a"
        self.feature.mkdir(parents=True)
        (self.root / ".sdd").mkdir()
        (self.feature / "tasks.md").write_text(TASKS, encoding="utf-8")
        for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "T"),
                     ("add", "."), ("commit", "-q", "-m", "init")):
            git(self.root, *args)
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(base / "state")})
        self.env.start()
        self.logs = launch.sdd_supervise.state_dir(self.root)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def status(self, **fields):
        self.logs.mkdir(parents=True, exist_ok=True)
        (self.logs / "status.json").write_text(json.dumps(fields), encoding="utf-8")

    def test_preview_lists_runnable_phases_and_held_count(self):
        info = launch.preview(self.root, self.feature)
        self.assertTrue(info["can_start"])
        self.assertEqual([(p["id"], p["runnable"]) for p in info["phases"]], [("1", 1), ("2", 1)])
        self.assertEqual((info["runnable"], info["held"]), (2, 1))
        self.assertIsNone(info["resumes"])

    def test_dry_run_builds_the_supervisor_command(self):
        result = launch.launch(self.root, self.feature, ["2"], True, 3, dry_run=True)
        self.assertEqual(result["run_args"], "--no-converge --phases 2 --max-tasks 3")
        cmd = result["command"]
        self.assertEqual(cmd[cmd.index("--project") + 1], str(self.root))
        self.assertEqual(cmd[cmd.index("--run-args") + 1], result["run_args"])
        self.assertEqual((cmd[cmd.index("--max-wait-hours") + 1], cmd[cmd.index("--max-cycles") + 1]), ("0", "1"))

    def test_launch_spawns_detached_with_the_model_settings_and_logs(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_MODEL", None)
            os.environ.pop("CLAUDE_CODE_SUBAGENT_MODEL", None)
            with mock.patch.object(launch, "_spawn") as popen:
                popen.return_value.pid = 4242
                result = launch.launch(self.root, self.feature, [], True, None)
        self.assertEqual(result["pid"], 4242)
        kwargs = popen.call_args.kwargs
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["env"]["ANTHROPIC_MODEL"], launch.DEFAULT_MODEL)
        self.assertEqual(kwargs["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], launch.DEFAULT_MODEL)
        self.assertEqual(Path(result["log"]).parent, self.logs)

    def test_a_model_chosen_by_the_caller_is_kept(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_MODEL": "my-model"}):
            with mock.patch.object(launch, "_spawn") as popen:
                popen.return_value.pid = 1
                launch.launch(self.root, self.feature)
        self.assertEqual(popen.call_args.kwargs["env"]["ANTHROPIC_MODEL"], "my-model")

    def test_refuses_while_a_supervisor_is_running(self):
        self.status(state="running", pid=os.getpid())
        with self.assertRaisesRegex(launch.Refused, "already in progress"):
            launch.launch(self.root, self.feature, dry_run=True)

    def test_a_finished_or_dead_supervisor_does_not_block(self):
        self.status(state="done", pid=os.getpid())
        self.assertTrue(launch.preview(self.root, self.feature)["can_start"])
        self.status(state="running", pid=2 ** 22 + 12345)  # no such process
        self.assertTrue(launch.preview(self.root, self.feature)["can_start"])

    def test_refuses_a_dirty_tree(self):
        (self.root / "stray.txt").write_text("mine")
        with self.assertRaisesRegex(launch.Refused, "working tree has changes"):
            launch.launch(self.root, self.feature, dry_run=True)

    def test_the_dirty_tree_message_names_a_modified_file_whole(self):
        (self.feature / "tasks.md").write_text((self.feature / "tasks.md").read_text() + "\n")
        with self.assertRaisesRegex(launch.Refused, r": specs/\S+/tasks\.md"):
            launch.launch(self.root, self.feature, dry_run=True)

    def test_an_interrupted_run_resumes_even_with_a_dirty_tree(self):
        (self.root / "stray.txt").write_text("interrupted work")
        (self.root / ".sdd" / "run.json").write_text(json.dumps({"run_id": "20261028-1005"}))
        info = launch.preview(self.root, self.feature)
        self.assertTrue(info["can_start"])
        self.assertEqual(info["resumes"], "20261028-1005")

    def test_refuses_when_nothing_is_runnable(self):
        (self.feature / "tasks.md").write_text("## Phase 1: A\n\n- [x] T001 done\n- [ ] T002 [manual] you\n")
        git(self.root, "commit", "-qam", "tasks")
        with self.assertRaisesRegex(launch.Refused, "no task is runnable"):
            launch.launch(self.root, self.feature, dry_run=True)

    def test_refuses_outside_a_git_repository(self):
        with tempfile.TemporaryDirectory() as other:
            feature = Path(other).resolve() / "f"
            feature.mkdir()
            (feature / "tasks.md").write_text(TASKS)
            with self.assertRaisesRegex(launch.Refused, "not a git repository"):
                launch.launch(Path(other), feature, dry_run=True)

    def test_rejects_unknown_phases_and_bad_caps(self):
        with self.assertRaisesRegex(launch.Refused, "phase 9"):
            launch.launch(self.root, self.feature, ["9"], dry_run=True)
        for cap in (0, 501, -1, True):
            with self.assertRaisesRegex(launch.Refused, "max tasks"):
                launch.launch(self.root, self.feature, [], True, cap, dry_run=True)

    def test_run_args(self):
        self.assertEqual(launch.build_run_args(None, False, None), "")
        self.assertEqual(launch.build_run_args(["1", "2"], True, 5), "--no-converge --phases 1,2 --max-tasks 5")


if __name__ == "__main__":
    unittest.main()
