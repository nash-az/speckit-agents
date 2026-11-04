"""bin/sdd-launch.py (FR-034): what a dashboard-started run would do, and when it refuses."""
import importlib.util
import json
import os
import subprocess
import sys
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


class LaunchJobTest(unittest.TestCase):
    """launch_job() (005 FR-003, data-model §8): the refusals, the record, the branch, the command line."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.root = base / "project"
        self.feature = self.root / "specs" / "001-a"
        self.feature.mkdir(parents=True)
        (self.root / ".sdd").mkdir()
        (self.root / ".gitignore").write_text(".sdd/jobs/\n", encoding="utf-8")
        (self.feature / "tasks.md").write_text(TASKS, encoding="utf-8")
        (self.feature / "BLOCKED.md").write_text("## T009\n- status: resolved\n- reason: old\n", encoding="utf-8")
        for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@example.com"),
                     ("config", "user.name", "T"), ("add", "."), ("commit", "-q", "-m", "init")):
            git(self.root, *args)
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(base / "state")})
        self.env.start()
        self.spawn = mock.patch.object(launch, "_spawn")
        self.popen = self.spawn.start()
        self.popen.return_value.pid = 4343

    def tearDown(self):
        self.spawn.stop()
        self.env.stop()
        self.tmp.cleanup()

    def start(self, scope=None, limits=None, title=None, dry_run=False):
        return launch.launch_job(self.root, self.feature, scope, limits, title, dry_run)

    def branch(self):
        return git(self.root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    def test_creates_the_record_the_branch_and_starts_the_supervisor(self):
        head = git(self.root, "rev-parse", "--short", "HEAD").strip()
        result = self.start({"phases": ["2"]}, {"max_tasks": 3})
        job_id = result["job"]
        self.assertEqual((result["branch"], result["pid"]), (f"auto/{job_id}", 4343))
        self.assertEqual(self.branch(), f"auto/{job_id}")
        record = launch.sdd_job.load(self.root, job_id)
        self.assertEqual(record["state"], "working")
        self.assertEqual(record["title"], "Phase 2")
        self.assertEqual(record["feature"], "001-a")
        self.assertEqual(record["scope"], {"phases": ["2"]})
        self.assertEqual(record["scope_tasks"], ["T003", "T004"])
        self.assertEqual(record["blocked_at_start"], 1)
        self.assertEqual(record["limits"], {"max_tasks": 3, "until": None, "max_runs": 20})
        self.assertEqual(record["branch"], f"auto/{job_id}")
        self.assertEqual(record["base"], {"branch": "main", "commit": head})
        self.assertEqual((record["runs"], record["reason"], record["ended"], record["merge_commit"]),
                         ([], None, None, None))
        self.assertIsInstance(record["created"], int)
        self.assertEqual(launch.sdd_job.open_job(self.root)["id"], job_id)

    def test_the_command_line_has_the_job_and_no_wait_cap(self):
        self.start()
        cmd = self.popen.call_args.args[0]
        job_id = launch.sdd_job.open_job(self.root)["id"]
        self.assertTrue(cmd[1].endswith("sdd-supervise.py"))
        self.assertEqual(cmd[2:], ["--project", str(self.root), "--job", job_id])
        self.assertNotIn("--max-wait-hours", cmd)
        kwargs = self.popen.call_args.kwargs
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["cwd"], str(self.root.parent))

    def test_title_and_limits_given_by_the_person(self):
        result = self.start({"tasks": ["T001", "T003"]}, {"until": 1793450000, "max_runs": None}, title="Mine")
        record = launch.sdd_job.load(self.root, result["job"])
        self.assertEqual(record["title"], "Mine")
        self.assertEqual(record["scope"], {"tasks": ["T001", "T003"]})
        self.assertEqual(record["scope_tasks"], ["T001", "T003"])
        self.assertEqual(record["limits"], {"max_tasks": None, "until": 1793450000, "max_runs": None})

    def test_default_scope_is_all_open_tasks(self):
        record = launch.sdd_job.load(self.root, self.start()["job"])
        self.assertEqual((record["scope"], record["title"]), ({"all": True}, "All open tasks"))
        self.assertEqual(sorted(record["scope_tasks"]), ["T001", "T003", "T004"])

    def test_dry_run_changes_nothing(self):
        result = self.start({"phases": ["1"]}, None, dry_run=True)
        self.assertIsNone(result["pid"])
        self.assertEqual(result["command"][-2:], ["--job", result["job"]])
        self.assertEqual(result["record"]["state"], "working")
        self.assertEqual(self.branch(), "main")
        self.assertIsNone(launch.sdd_job.open_job(self.root))
        self.popen.assert_not_called()

    def test_refuses_when_another_job_is_open(self):
        launch.sdd_job.save(self.root, {"id": "20261030-0900", "title": "Phase 9", "state": "waiting"})
        with self.assertRaises(launch.Refused) as caught:
            self.start()
        self.assertEqual(str(caught.exception), "another job is open: Phase 9")
        self.assertEqual(self.branch(), "main")
        self.popen.assert_not_called()

    def test_a_merged_or_dropped_job_does_not_block(self):
        launch.sdd_job.save(self.root, {"id": "20261030-0900", "title": "Old", "state": "merged"})
        launch.sdd_job.save(self.root, {"id": "20261030-0901", "title": "Older", "state": "dropped"})
        self.assertEqual(self.start()["pid"], 4343)

    def test_refuses_a_dirty_tree(self):
        (self.root / "stray.txt").write_text("mine")
        with self.assertRaises(launch.Refused) as caught:
            self.start()
        self.assertEqual(str(caught.exception), "the working tree has changes; commit or stash them first: stray.txt")
        self.assertEqual(self.branch(), "main")
        self.assertEqual(launch.sdd_job.list_jobs(self.root), [])

    def test_refuses_outside_a_git_repository(self):
        with tempfile.TemporaryDirectory() as other:
            feature = Path(other).resolve() / "f"
            feature.mkdir()
            (feature / "tasks.md").write_text(TASKS)
            with self.assertRaises(launch.Refused) as caught:
                launch.launch_job(Path(other), feature, None, None)
        self.assertEqual(str(caught.exception), "the project is not a git repository")

    def test_refuses_when_no_task_in_the_scope_is_runnable(self):
        for scope in ({"tasks": ["T002", "T004"]}, {"phases": ["9"]}):
            with self.assertRaises(launch.Refused) as caught:
                self.start(scope)
            self.assertEqual(str(caught.exception), "no task in the scope is runnable")
        self.assertEqual(self.branch(), "main")
        self.popen.assert_not_called()

    def test_refuses_while_a_run_is_active(self):
        (self.root / ".sdd" / "run.json").write_text(json.dumps({"run_id": "20261031-0800"}))
        with self.assertRaises(launch.Refused) as caught:
            self.start()
        self.assertEqual(str(caught.exception), "a run is active")

    def test_rejects_bad_limits_and_two_scopes(self):
        for limits in ({"max_tasks": 0}, {"max_runs": 0}, {"until": "soon"}):
            with self.assertRaises(launch.Refused):
                self.start(None, limits)
        with self.assertRaises(launch.Refused):
            self.start({"phases": ["1"], "tasks": ["T001"]})

    def test_the_id_skips_a_taken_branch(self):
        job_id = launch.sdd_job.new_id(self.root)
        git(self.root, "branch", f"auto/{job_id}")
        result = self.start()
        self.assertNotEqual(result["job"], job_id)
        self.assertEqual(self.branch(), result["branch"])

    def test_cli_job_flags(self):
        out = subprocess.run(
            [sys.executable, str(HERE.parent / "bin" / "sdd-launch.py"), str(self.root), str(self.feature),
             "--job", "--tasks", "T001,T003", "--max-runs", "5", "--until", "1793450000", "--title", "Two",
             "--dry-run"], capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(out.returncode, 0, out.stderr)
        result = json.loads(out.stdout)
        self.assertIsNone(result["pid"])
        record = result["record"]
        self.assertEqual((record["title"], record["scope"]), ("Two", {"tasks": ["T001", "T003"]}))
        self.assertEqual(record["limits"], {"max_tasks": None, "until": 1793450000, "max_runs": 5})

    def test_cli_refusal_and_flags_without_job(self):
        script = str(HERE.parent / "bin" / "sdd-launch.py")
        (self.root / "stray.txt").write_text("mine")
        out = subprocess.run([sys.executable, script, str(self.root), str(self.feature), "--job", "--dry-run"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 1)
        self.assertEqual(json.loads(out.stdout),
                         {"contract": "1.0",
                          "error": "the working tree has changes; commit or stash them first: stray.txt"})
        out = subprocess.run([sys.executable, script, str(self.root), str(self.feature), "--tasks", "T001"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)


class ContinueJobTest(unittest.TestCase):
    """continue_job() (005 FR-009, data-model §3 and §8): allowed and refused states, the limit rule,
    counts kept, and resuming an interrupted run with its work in the tree."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.root = base / "project"
        self.feature = self.root / "specs" / "001-a"
        self.feature.mkdir(parents=True)
        (self.root / ".sdd").mkdir()
        (self.root / ".gitignore").write_text(".sdd/jobs/\n", encoding="utf-8")
        (self.feature / "tasks.md").write_text(TASKS, encoding="utf-8")
        for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@example.com"),
                     ("config", "user.name", "T"), ("add", "."), ("commit", "-q", "-m", "init")):
            git(self.root, *args)
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(base / "state")})
        self.env.start()
        self.spawn = mock.patch.object(launch, "_spawn")
        self.popen = self.spawn.start()
        self.popen.return_value.pid = 4444
        self.job = launch.launch_job(self.root, self.feature, {"phases": ["1", "2"]}, None)["job"]
        self.job_branch = f"auto/{self.job}"
        self.popen.reset_mock()
        self.logs = launch.sdd_supervise.state_dir(self.root)

    def tearDown(self):
        self.spawn.stop()
        self.env.stop()
        self.tmp.cleanup()

    def set(self, **fields):
        record = launch.sdd_job.load(self.root, self.job)
        record.update(fields)
        launch.sdd_job.save(self.root, record)

    def record(self):
        return launch.sdd_job.load(self.root, self.job)

    def branch(self):
        return git(self.root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    def status(self, **fields):
        self.logs.mkdir(parents=True, exist_ok=True)
        (self.logs / "status.json").write_text(json.dumps(fields), encoding="utf-8")

    def refused(self, text, new_limits=None):
        with self.assertRaises(launch.Refused) as caught:
            launch.continue_job(self.root, self.job, new_limits)
        self.assertEqual(str(caught.exception), text)
        self.popen.assert_not_called()

    def tick(self, task_id):
        path = self.feature / "tasks.md"
        path.write_text(path.read_text().replace(f"- [ ] {task_id}", f"- [x] {task_id}"))
        git(self.root, "commit", "-qam", f"tick {task_id}")

    def test_each_allowed_state_continues_on_the_jobs_branch(self):
        for state, reason in (("waiting", "only items that need you are left"), ("working", None),
                              ("held", "kept paused at the usage reset"), ("stopped", "stopped by you")):
            with self.subTest(state=state):
                self.set(state=state, reason=reason)
                git(self.root, "switch", "-q", "main")
                result = launch.continue_job(self.root, self.job)
                self.assertEqual((result["job"], result["pid"], result["resumes"]), (self.job, 4444, None))
                self.assertEqual(self.branch(), self.job_branch)
                self.assertEqual((self.record()["state"], self.record()["reason"]), ("working", None))
                cmd = self.popen.call_args.args[0]
                self.assertEqual(cmd[2:], ["--project", str(self.root), "--job", self.job])
                self.assertNotIn("--max-wait-hours", cmd)
                self.assertTrue(self.popen.call_args.kwargs["start_new_session"])
                self.popen.reset_mock()

    def test_refused_states(self):
        for state in ("done", "failed", "merged", "dropped"):
            with self.subTest(state=state):
                self.set(state=state)
                self.refused(f"this job cannot be continued in state {state}")
                self.assertEqual(self.record()["state"], state)
        self.set(state="working")
        self.status(state="running", pid=os.getpid())
        self.refused("this job cannot be continued in state working")
        self.status(state="waiting", pid=os.getpid(), resets_at=1793450000)
        self.refused("this job cannot be continued in state paused")

    def test_unknown_job(self):
        with self.assertRaises(launch.Refused) as caught:
            launch.continue_job(self.root, "20990101-0000")
        self.assertEqual(str(caught.exception), "no such job: 20990101-0000")

    def test_refuses_while_a_supervisor_is_running_for_a_stored_state(self):
        self.set(state="stopped", reason="stopped by you")
        self.status(state="running", pid=os.getpid())
        self.refused("a run is active")

    def test_refuses_a_dirty_tree_when_not_resuming(self):
        self.set(state="stopped", reason="stopped by you")
        git(self.root, "switch", "-q", "main")
        (self.root / "stray.txt").write_text("mine")
        self.refused("the working tree has changes; commit or stash them first: stray.txt")
        self.assertEqual((self.branch(), self.record()["state"]), ("main", "stopped"))

    def test_refuses_when_no_scope_task_is_runnable_and_switches_back(self):
        git(self.root, "switch", "-q", self.job_branch)
        self.tick("T001")
        self.tick("T003")
        git(self.root, "switch", "-q", "main")
        self.set(state="waiting", reason="only items that need you are left")
        self.refused("no task in the scope is runnable")
        self.assertEqual((self.branch(), self.record()["state"]), ("main", "waiting"))

    def test_continue_with_nothing_left_makes_the_job_done(self):
        git(self.root, "switch", "-q", self.job_branch)
        for task_id in ("T001", "T003", "T004"):  # T002 is done in the fixture
            self.tick(task_id)
        git(self.root, "switch", "-q", "main")
        self.set(state="waiting", reason="only items that need you are left")
        self.refused("nothing is left to run: the job is now done, so Merge it")
        self.assertEqual((self.branch(), self.record()["state"]), ("main", "done"))

    def test_refuses_outside_a_git_repository(self):
        with tempfile.TemporaryDirectory() as other:
            project = Path(other).resolve()
            launch.sdd_job.save(project, {"id": "20261031-0900", "state": "stopped", "reason": "stopped by you",
                                          "branch": "auto/20261031-0900", "feature": "001-a"})
            with self.assertRaises(launch.Refused) as caught:
                launch.continue_job(project, "20261031-0900")
        self.assertEqual(str(caught.exception), "the project is not a git repository")

    def test_a_run_of_another_branch_is_active(self):
        self.set(state="waiting")
        (self.root / ".sdd" / "run.json").write_text(json.dumps({"run_id": "x", "branch": "auto/other"}))
        self.refused("a run is active")

    def test_a_limit_stopped_job_needs_a_new_value_for_that_limit(self):
        self.tick("T001")
        self.set(state="stopped", reason="max_tasks 1 reached", limits={"max_tasks": 1, "until": None, "max_runs": 20})
        self.refused("the limit max_tasks is still reached; give a new value")
        self.refused("the limit max_tasks is still reached; give a new value", {"max_runs": 30})
        self.refused("the limit max_tasks is still reached; give a new value", {"max_tasks": 1})
        self.assertEqual(self.record()["limits"]["max_tasks"], 1)
        launch.continue_job(self.root, self.job, {"max_tasks": 2})
        self.assertEqual(self.record()["limits"], {"max_tasks": 2, "until": None, "max_runs": 20})
        self.assertEqual(self.record()["state"], "working")

    def test_none_removes_the_limit(self):
        self.set(state="stopped", reason="max_runs 2 reached", runs=["r1", "r2"],
                 limits={"max_tasks": None, "until": None, "max_runs": 2})
        launch.continue_job(self.root, self.job, {"max_runs": None})
        self.assertEqual(self.record()["limits"]["max_runs"], None)

    def test_until_must_be_later_than_now(self):
        self.set(state="stopped", reason="until 09:00 reached",
                 limits={"max_tasks": None, "until": 1000, "max_runs": 20})
        self.refused("the limit until is still reached; give a new value", {"until": 2000})
        later = int(launch.time.time()) + 3600
        launch.continue_job(self.root, self.job, {"until": later})
        self.assertEqual(self.record()["limits"]["until"], later)

    def test_another_reached_limit_is_refused_too(self):
        self.set(state="stopped", reason="stopped by you", runs=["r1", "r2"],
                 limits={"max_tasks": None, "until": None, "max_runs": 2})
        self.refused("the limit max_runs is still reached; give a new value")

    def test_counts_are_not_reset(self):
        self.tick("T001")
        before = self.record()["scope_tasks"]
        self.set(state="stopped", reason="max_runs 2 reached", runs=["r1", "r2"], no_progress_runs=1,
                 limits={"max_tasks": 3, "until": None, "max_runs": 2})
        self.refused("the limit max_runs is still reached; give a new value", {"max_runs": 2})
        launch.continue_job(self.root, self.job, {"max_runs": 3})
        record = self.record()
        self.assertEqual((record["runs"], record["no_progress_runs"], record["scope_tasks"]),
                         (["r1", "r2"], 1, before))
        self.assertEqual(launch.sdd_job.tasks_done(self.root, record), 1)
        self.assertEqual(record["limits"], {"max_tasks": 3, "until": None, "max_runs": 3})

    def test_bad_new_limits(self):
        self.set(state="stopped", reason="stopped by you")
        for limits, text in (({"max_tasks": 0}, "max tasks must be a whole number from 1 to 500"),
                             ({"speed": 1}, "unknown limit: speed")):
            with self.subTest(limits=limits):
                self.refused(text, limits)

    def test_an_interrupted_job_with_a_dirty_tree_and_run_json_resumes(self):
        self.set(state="working")
        (self.root / "half-done.txt").write_text("interrupted work")
        (self.root / ".sdd" / "run.json").write_text(json.dumps({"run_id": "20261031-1100",
                                                                 "branch": self.job_branch}))
        result = launch.continue_job(self.root, self.job)
        self.assertEqual((result["pid"], result["resumes"]), (4444, "20261031-1100"))
        self.assertEqual(self.branch(), self.job_branch)
        self.assertTrue((self.root / "half-done.txt").exists())
        self.assertTrue((self.root / ".sdd" / "run.json").exists())
        self.assertEqual(self.record()["state"], "working")

    def test_resuming_with_the_checkout_on_another_branch_is_refused(self):
        self.set(state="held", reason="kept paused at the usage reset")
        (self.root / "half-done.txt").write_text("interrupted work")
        (self.root / ".sdd" / "run.json").write_text(json.dumps({"run_id": "20261031-1100",
                                                                 "branch": self.job_branch}))
        git(self.root, "switch", "-q", "main")
        self.refused("the checkout is not on the Job's branch")
        self.assertEqual((self.branch(), self.record()["state"]), ("main", "held"))

    def test_cli_continue(self):
        script = str(HERE.parent / "bin" / "sdd-launch.py")
        self.set(state="stopped", reason="max_runs 1 reached", runs=["r1"],
                 limits={"max_tasks": None, "until": None, "max_runs": 1})
        out = subprocess.run([sys.executable, script, str(self.root), str(self.feature), "--continue", self.job],
                             capture_output=True, text=True)
        self.assertEqual((out.returncode, json.loads(out.stdout)),
                         (1, {"contract": "1.0", "error": "the limit max_runs is still reached; give a new value"}))
        out = subprocess.run([sys.executable, script, str(self.root), str(self.feature), "--continue", self.job,
                              "--title", "x"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        out = subprocess.run([sys.executable, script, str(self.root), str(self.feature), "--continue", self.job,
                              "--max-runs", "many"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)

    def test_cli_passes_only_the_given_limits_and_none(self):
        with mock.patch.object(launch, "continue_job", return_value={"job": self.job, "pid": 1}) as cont:
            code = launch.main([str(self.root), str(self.feature), "--continue", self.job,
                                "--max-runs", "none", "--until", "1793450000"])
        self.assertEqual(code, 0)
        self.assertEqual(cont.call_args.args, (self.root, self.job, {"until": 1793450000, "max_runs": None}))


if __name__ == "__main__":
    unittest.main()
