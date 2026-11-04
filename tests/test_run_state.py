"""008 data-model §3: the `session` command, `session_alive`, and the run-state helpers that moved
into bin/sdd-tasks.py."""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
BIN = HERE.parent / "bin"
SCRIPT = BIN / "sdd-tasks.py"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, BIN / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tasks = load("sdd_tasks", "sdd-tasks.py")


def _ps_started(pid):
    """The start time of `pid`, read the way §3 says, independently of the code under test,
    or None where `ps` may not run (a run's sandbox refuses it)."""
    import time
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                             env=dict(os.environ, LC_ALL="C"), check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return int(time.mktime(time.strptime(out.strip(), "%a %b %d %H:%M:%S %Y")))


# This process's real start time. Where `ps` cannot run, a fixed time stands in for it and the
# tests patch `process_started` to answer it for this pid, so the same rules are still checked.
REAL_START = _ps_started(os.getpid())
MY_START = REAL_START if REAL_START is not None else 1793616483


def ps_started(pid):
    return MY_START if pid == os.getpid() else _ps_started(pid)


def maybe_fake_ps():
    """A patch of `tasks.process_started` when `ps` is unavailable, else a no-op context."""
    import contextlib
    if REAL_START is not None:
        return contextlib.nullcontext()
    return mock.patch.object(tasks, "process_started", side_effect=ps_started)


class TempHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.env = mock.patch.dict(os.environ, {
            "HOME": str(self.root / "home"),
            "XDG_STATE_HOME": str(self.root / "state"),
            "XDG_DATA_HOME": str(self.root / "data"),
        })
        self.env.start()
        self.project = self.root / "project"
        (self.project / ".sdd").mkdir(parents=True)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


class SessionCommandTest(TempHome):
    def setUp(self):
        super().setUp()
        self.run_json = self.project / ".sdd" / "run.json"
        self.run_json.write_text(json.dumps({"run_id": "20261104-1200", "branch": "auto/x",
                                             "phases": ["2"], "workflow_dir": "/w"}))

    def session(self, fake_ps=True, **env):
        clean = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PID", "SDD_SUPERVISED")}
        clean.update(env)
        stdout = io.StringIO()
        ps = maybe_fake_ps() if fake_ps else contextlib.nullcontext()
        with mock.patch.dict(os.environ, clean, clear=True), ps, \
                contextlib.redirect_stdout(stdout):
            code = tasks.main(["session", "--run-json", str(self.run_json)])
        self.assertEqual(code, 0)
        return json.loads(stdout.getvalue()), json.loads(self.run_json.read_text())

    def test_command_line(self):
        out = subprocess.run([sys.executable, str(SCRIPT), "session", "--run-json", str(self.run_json)],
                             capture_output=True, text=True,
                             env={k: v for k, v in os.environ.items() if k != "CLAUDE_PID"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["session"], None)

    def test_missing_run_json(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = tasks.main(["session", "--run-json", str(self.project / ".sdd" / "nope.json")])
        self.assertEqual(code, 1)
        self.assertIn("error", json.loads(stdout.getvalue()))
        self.assertFalse((self.project / ".sdd" / "nope.json").exists())

    def test_records_this_session(self):
        printed, run = self.session(CLAUDE_PID=str(os.getpid()))
        session = {"pid": os.getpid(), "started": ps_started(os.getpid())}
        self.assertEqual(run["session"], session)
        self.assertEqual(run["started_by"], "session")
        self.assertEqual(run["contract"], tasks.CONTRACT)
        for key, value in {"run_id": "20261104-1200", "branch": "auto/x", "phases": ["2"],
                           "workflow_dir": "/w"}.items():
            self.assertEqual(run[key], value)
        self.assertEqual(printed, {"contract": tasks.CONTRACT, "session": session, "started_by": "session"})
        self.assertFalse(self.run_json.with_name("run.json.tmp").exists())

    def test_supervised_run(self):
        _, run = self.session(CLAUDE_PID=str(os.getpid()), SDD_SUPERVISED="1")
        self.assertEqual(run["started_by"], "supervisor")

    def test_without_claude_pid(self):
        printed, run = self.session()
        self.assertIsNone(run["session"])
        self.assertEqual(run["started_by"], "session")
        self.assertEqual(run["contract"], tasks.CONTRACT)
        self.assertNotIn("error", printed)

    def test_unreadable_pid_writes_null_and_an_error(self):
        with mock.patch.object(tasks.subprocess, "run", side_effect=PermissionError("ps")):
            printed, run = self.session(fake_ps=False, CLAUDE_PID=str(os.getpid()))
        self.assertIsNone(run["session"])
        self.assertEqual(run["started_by"], "session")
        self.assertEqual(run["contract"], tasks.CONTRACT)
        self.assertIn("error", printed)

    def test_dead_claude_pid_writes_null_and_an_error(self):
        printed, run = self.session(CLAUDE_PID=str(dead_pid()))
        self.assertIsNone(run["session"])
        self.assertEqual(run["started_by"], "session")
        self.assertEqual(run["contract"], tasks.CONTRACT)
        self.assertIn("error", printed)


def dead_pid():
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


class ProcessStartedTest(unittest.TestCase):
    def test_reads_lstart_in_the_c_locale(self):
        import time
        done = subprocess.CompletedProcess([], 0, stdout="Wed Nov  4 17:10:03 2026\n", stderr="")
        with mock.patch.object(tasks.subprocess, "run", return_value=done) as run:
            got = tasks.process_started(4242)
        self.assertEqual(run.call_args.args[0], ["ps", "-o", "lstart=", "-p", "4242"])
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")
        self.assertEqual(got, int(time.mktime((2026, 11, 4, 17, 10, 3, 0, 0, -1))))

    def test_failure_is_none(self):
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="")
        with mock.patch.object(tasks.subprocess, "run", return_value=failed):
            self.assertIsNone(tasks.process_started(4242))
        with mock.patch.object(tasks.subprocess, "run", side_effect=PermissionError("ps")):
            self.assertIsNone(tasks.process_started(4242))

    @unittest.skipIf(REAL_START is None, "ps cannot run here (sandbox)")
    def test_real_ps(self):
        self.assertEqual(tasks.process_started(os.getpid()), REAL_START)


class SessionAliveTest(unittest.TestCase):
    def test_this_process(self):
        run = {"session": {"pid": os.getpid(), "started": ps_started(os.getpid())}}
        with maybe_fake_ps():
            self.assertIs(tasks.session_alive(run), True)

    def test_wrong_start_time(self):
        run = {"session": {"pid": os.getpid(), "started": ps_started(os.getpid()) - 3600}}
        with maybe_fake_ps():
            self.assertIs(tasks.session_alive(run), False)

    def test_dead_pid(self):
        run = {"session": {"pid": dead_pid(), "started": MY_START}}
        self.assertIs(tasks.session_alive(run), False)

    def test_no_session(self):
        self.assertIsNone(tasks.session_alive({}))
        self.assertIsNone(tasks.session_alive({"session": None}))


class MovedHelpersTest(TempHome):
    def test_state_dir_get_status_supervisor_alive_through_old_names(self):
        dash = load("sdd_dashboard", "sdd-dashboard.py")
        supervise = load("sdd_supervise", "sdd-supervise.py")
        expected = tasks.state_dir(self.project)
        self.assertTrue(str(expected).startswith(str(self.root / "state" / "speckit-agents")))
        self.assertEqual(supervise.state_dir(self.project), expected)
        self.assertEqual(dash.logs_dir(self.project), expected)
        self.assertIsNone(dash.get_status(self.project))
        (expected / "status.json").write_text(json.dumps({"state": "running", "pid": os.getpid()}))
        status = dash.get_status(self.project)
        self.assertEqual(status, {"state": "running", "pid": os.getpid()})
        self.assertEqual(tasks.get_status(self.project), status)
        self.assertTrue(dash.supervisor_alive(status))
        self.assertFalse(dash.supervisor_alive({"pid": dead_pid()}))
        self.assertFalse(dash.supervisor_alive({"pid": True}))
        self.assertFalse(tasks.supervisor_alive(None))


class CurrentStateTest(TempHome):
    """§4: `current_state()` and the `state` command (the 003 §4.5 rules, `origin`, `session_alive`)."""

    RUN_ID = "20261104-1200"

    def setUp(self):
        super().setUp()
        (self.project / ".sdd" / "runs").mkdir()
        self.wf = self.root / "transcripts" / "wf_current"
        self.wf.mkdir(parents=True)
        (self.wf / "journal.jsonl").write_text("{}\n", encoding="utf-8")
        (self.wf / "agent-a01.jsonl").write_text("{}\n", encoding="utf-8")
        self.status_file = tasks.state_dir(self.project) / "status.json"

    def age(self, minutes):
        import time
        t = time.time() - minutes * 60
        for f in (self.wf / "journal.jsonl", self.wf / "agent-a01.jsonl"):
            os.utime(f, (t, t))

    def write_run(self, **extra):
        data = {"run_id": self.RUN_ID, "workflow_dir": str(self.wf), **extra}
        (self.project / ".sdd" / "run.json").write_text(json.dumps(data), encoding="utf-8")

    def write_status(self, **status):
        self.status_file.write_text(json.dumps(status), encoding="utf-8")

    def state(self):
        return tasks.current_state(self.project)

    def test_none_without_run_json(self):
        self.assertEqual(self.state(), {"contract": tasks.CONTRACT, "state": "none", "run_id": None,
                                        "workflow_dir": None, "origin": None, "session_alive": None})

    def test_paused_when_report_exists(self):
        self.write_run()
        self.age(120)
        (self.project / ".sdd" / "runs" / (self.RUN_ID + ".md")).write_text("# Report\n")
        got = self.state()
        self.assertEqual(got["state"], "paused")
        self.assertEqual((got["run_id"], got["workflow_dir"]), (self.RUN_ID, str(self.wf)))
        self.age(0)
        self.assertEqual(self.state()["state"], "paused")  # the report beats recent activity

    def test_dead_supervisor_status_is_ignored(self):
        self.write_run()
        for state in ("done", "waiting", "running", "held"):
            self.write_status(state=state, pid=dead_pid())
            self.assertEqual(self.state()["state"], "running", state)
        self.age(120)
        for state in ("done", "waiting", "running", "held"):
            self.write_status(state=state)
            self.assertEqual(self.state()["state"], "stale", state)

    def test_live_supervisor(self):
        self.write_run()
        self.age(120)
        for state, want in (("running", "running"), ("stopping", "running"), ("held", "paused"),
                            ("waiting", "paused")):
            self.write_status(state=state, pid=os.getpid())
            self.assertEqual(self.state()["state"], want, state)

    def test_stopped_supervisor_does_not_pause(self):
        self.write_run()
        self.write_status(state="stopped", pid=os.getpid())
        self.assertEqual(self.state()["state"], "running")
        self.age(120)
        self.assertEqual(self.state()["state"], "stale")

    def test_running_window(self):
        self.write_run()
        self.age(120)
        os.utime(self.wf / "agent-a01.jsonl")  # only an agent file is recent
        self.assertEqual(self.state()["state"], "running")
        self.age(31)
        (self.wf / "notes.txt").write_text("not an agent file\n")  # recent, ignored
        self.assertEqual(self.state()["state"], "stale")
        self.age(29)
        self.assertEqual(self.state()["state"], "running")

    def test_stale_when_workflow_dir_invalid(self):
        bad = self.root / "transcripts" / "not-a-wf"
        bad.mkdir()
        (bad / "journal.jsonl").write_text("{}\n")
        self.write_run(workflow_dir=str(bad))
        self.assertEqual(self.state()["state"], "stale")
        self.write_run(workflow_dir=str(self.root / "gone" / "wf_gone"))
        self.assertEqual(self.state()["state"], "stale")

    def test_stale_when_run_json_unreadable(self):
        (self.project / ".sdd" / "run.json").write_text("not json")
        got = self.state()
        self.assertEqual((got["state"], got["run_id"], got["workflow_dir"]), ("stale", None, None))
        self.assertEqual(got["origin"], "session")

    def test_origin_live_supervisor(self):
        self.write_run(started_by="session")
        self.write_status(state="running", pid=os.getpid())
        self.assertEqual(self.state()["origin"], "supervisor")

    def test_origin_live_supervisor_with_a_job(self):
        self.write_run(started_by="supervisor")
        self.write_status(state="waiting", pid=os.getpid(), job="20261104-1100")
        self.assertEqual(self.state()["origin"], "job")

    def test_origin_from_started_by(self):
        self.write_run(started_by="session")
        self.assertEqual(self.state()["origin"], "session")
        self.write_run(started_by="supervisor")
        self.assertEqual(self.state()["origin"], "supervisor")

    def test_origin_defaults_to_session(self):
        self.write_run()
        self.assertEqual(self.state()["origin"], "session")

    def test_leftover_done_status_with_a_live_journal(self):
        self.write_run()
        self.write_status(state="done", pid=dead_pid(), job="20261104-1100")
        got = self.state()
        self.assertEqual((got["state"], got["origin"]), ("running", "session"))

    def test_session_alive(self):
        self.write_run()
        self.assertIsNone(self.state()["session_alive"])
        self.write_run(session={"pid": os.getpid(), "started": ps_started(os.getpid())})
        with maybe_fake_ps():
            self.assertIs(self.state()["session_alive"], True)
        self.write_run(session={"pid": dead_pid(), "started": MY_START})
        self.assertIs(self.state()["session_alive"], False)

    def test_state_command(self):
        self.write_run(started_by="session")
        out = subprocess.run([sys.executable, str(SCRIPT), "state", "--project", str(self.project)],
                             capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), {
            "contract": tasks.CONTRACT, "state": "running", "run_id": self.RUN_ID,
            "workflow_dir": str(self.wf), "origin": "session", "session_alive": None})

    def test_dashboard_current_run_drops_the_new_keys(self):
        dash = load("sdd_dashboard", "sdd-dashboard.py")
        self.write_run(started_by="session")
        self.assertEqual(dash.current_run(self.project),
                         {"state": "running", "run_id": self.RUN_ID, "workflow_dir": str(self.wf)})
        (self.project / ".sdd" / "run.json").unlink()
        self.assertEqual(dash.current_run(self.project),
                         {"state": "none", "run_id": None, "workflow_dir": None})


if __name__ == "__main__":
    unittest.main()
