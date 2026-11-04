"""008 data-model §5: bin/sdd-control.py, the run controls and the one writer of control.json
(003 data-model §12.1 to §12.3 plus the in-session rows), its command line, and its lock."""
import fcntl
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
BIN = HERE.parent / "bin"
SCRIPT = BIN / "sdd-control.py"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, BIN / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


control = load("sdd_control", "sdd-control.py")
dash = load("sdd_dashboard", "sdd-dashboard.py")
supervise = load("sdd_supervise", "sdd-supervise.py")

RUN_ID = "20261104-1200"
RESETS_AT = 1793000000
OWN_ORIGIN = "http://127.0.0.1:8765"


def controls(**fields):
    """A `controls` object: everything false and no reason, unless given."""
    base = {"stop": False, "resume": False, "hold": False, "release": False,
            "adopt": False, "confirm_closed": False, "reason": None}
    return {**base, **fields}


class ControlCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.base = base
        self.root = base / "project"
        (self.root / ".sdd").mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(base / "state"),
                                                "HOME": str(base / "home"),
                                                "XDG_DATA_HOME": str(base / "data")})
        self.env.start()
        self.logs = control.sdd_tasks.state_dir(self.root)
        self.hhmm = time.strftime("%H:%M", time.localtime(RESETS_AT))

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write_status(self, state, pid=None, **fields):
        data = {"state": state, "pid": os.getpid() if pid is None else pid, **fields}
        (self.logs / "status.json").write_text(json.dumps(data), encoding="utf-8")

    def write_run(self, running=False, **fields):
        """`.sdd/run.json`; `running=True` gives it a workflow folder whose journal just changed,
        else the run is stale (no workflow folder)."""
        data = {"run_id": RUN_ID, **fields}
        if running:
            wf = self.base / "wf_test"
            wf.mkdir(exist_ok=True)
            (wf / "journal.jsonl").write_text("{}\n", encoding="utf-8")
            data["workflow_dir"] = str(wf)
        (self.root / ".sdd" / "run.json").write_text(json.dumps(data), encoding="utf-8")

    def write_attach(self, pid):
        attach = {"contract": "1.0", "run_id": RUN_ID, "session": None, "state": "watching",
                  "since": int(time.time()), "pid": pid}
        (self.logs / "attach.json").write_text(json.dumps(attach), encoding="utf-8")

    def session(self, alive):
        """Patch the session probe current_state() uses to answer `alive`."""
        return mock.patch.object(control.sdd_tasks, "session_alive", return_value=alive)

    def dead_pid(self):
        proc = subprocess.Popen(["true"])
        proc.wait()
        return proc.pid


class ControlsTableTest(ControlCase):
    """Every row of 003 §12.3 (a live supervisor) and every new row of 008 §5."""

    def test_running(self):
        self.write_status("running")
        self.assertEqual(control.controls(self.root), controls(stop=True))

    def test_stopping(self):
        self.write_status("stopping", mode="after-task")
        self.assertEqual(control.controls(self.root),
                         controls(stop=True, reason="stopping after the current task"))

    def test_held(self):
        self.write_status("held", reason="stopped by operator")
        self.assertEqual(control.controls(self.root), controls(resume=True))

    def test_waiting_not_kept_paused(self):
        self.write_status("waiting", resets_at=RESETS_AT, keep_paused=False)
        self.assertEqual(control.controls(self.root), controls(
            hold=True, reason=f"waiting for the usage window to reset at {self.hhmm}"))

    def test_waiting_kept_paused(self):
        self.write_status("waiting", resets_at=RESETS_AT, keep_paused=True)
        self.assertEqual(control.controls(self.root), controls(
            release=True, reason=f"will stay paused after the reset at {self.hhmm}"))

    def test_no_run_and_no_supervisor(self):
        self.assertEqual(control.controls(self.root), controls())
        self.write_status("running", pid=self.dead_pid())
        self.assertEqual(control.controls(self.root), controls())

    def test_old_reason_is_gone(self):
        self.assertNotIn("no supervisor is running", SCRIPT.read_text(encoding="utf-8"))
        self.assertNotIn("no supervisor is running", (BIN / "sdd-dashboard.py").read_text(encoding="utf-8"))
        self.assertFalse(hasattr(dash, "NO_SUPERVISOR"))

    def test_in_session_alive(self):
        self.write_run(running=True, started_by="session")
        with self.session(True):
            self.assertEqual(control.controls(self.root),
                             controls(adopt=True, reason="running in a Claude session"))

    def test_in_session_ended(self):
        for running in (True, False):
            self.write_run(running=running, started_by="session")
            with self.session(False):
                self.assertEqual(control.controls(self.root), controls(
                    adopt=True,
                    reason="the session that started this run has ended; take it over to finish it"), running)

    def test_in_session_unknown_and_stale(self):
        self.write_run()  # no started_by: an in-session run
        with self.session(None):
            self.assertEqual(control.controls(self.root), controls(
                adopt=True, confirm_closed=True,
                reason="the session that started this run is unknown; confirm it is closed to take it over"))

    def test_in_session_unknown_but_running(self):
        self.write_run(running=True)
        with self.session(None):
            self.assertEqual(control.controls(self.root),
                             controls(adopt=True, reason="running in a Claude session"))

    def test_in_session_alive_but_stale(self):
        self.write_run(started_by="session")
        with self.session(True):
            self.assertEqual(control.controls(self.root), controls(
                adopt=True, confirm_closed=True,
                reason="the session is still open but its run has done nothing for 30 minutes; "
                       "confirm the session no longer runs it to take it over"))

    def test_supervised_run_without_a_live_supervisor(self):
        self.write_run(running=True, started_by="supervisor")
        self.assertEqual(control.controls(self.root),
                         controls(reason="the supervisor stopped; Start run resumes it"))
        self.write_status("done", pid=self.dead_pid())
        self.assertEqual(control.controls(self.root),
                         controls(reason="the supervisor stopped; Start run resumes it"))

    def test_live_attachment(self):
        self.write_run(started_by="session")
        self.write_attach(os.getpid())
        for alive in (True, False, None):
            with self.session(alive):
                self.assertEqual(control.controls(self.root), controls(
                    reason="watching the session's run; takes over if the session ends"), alive)

    def test_dead_attachment_is_ignored(self):
        self.write_run(started_by="session")
        self.write_attach(self.dead_pid())
        with self.session(False):
            self.assertTrue(control.controls(self.root)["adopt"])

    def test_a_live_supervisor_wins_over_the_run(self):
        self.write_run(started_by="session")
        self.write_status("running")
        with self.session(False):
            self.assertEqual(control.controls(self.root), controls(stop=True))


def raw_post(path, body):
    """One POST through DashboardHandler without a socket; returns (status, parsed body)."""
    handler = dash.DashboardHandler.__new__(dash.DashboardHandler)
    handler.path, handler.command = path, "POST"
    handler.request_version, handler.requestline = "HTTP/1.1", f"POST {path} HTTP/1.1"
    raw = json.dumps(body).encode()
    handler.wfile, handler.rfile = io.BytesIO(), io.BytesIO(raw)
    handler.headers = {"Content-Length": str(len(raw)), "Host": "127.0.0.1:8765",
                       "Origin": OWN_ORIGIN, "Content-Type": "application/json"}
    handler.do_POST()
    head, _, out = handler.wfile.getvalue().partition(b"\r\n\r\n")
    return int(head.decode().split("\r\n")[0].split()[1]), json.loads(out)


class CommandLineTest(ControlCase):
    def setUp(self):
        super().setUp()
        handler = dash.DashboardHandler
        self.saved = (handler.project, handler.feature, handler.port, handler.allow_ports)
        handler.project, handler.feature, handler.port, handler.allow_ports = self.root, None, 8765, ()

    def tearDown(self):
        handler = dash.DashboardHandler
        handler.project, handler.feature, handler.port, handler.allow_ports = self.saved
        super().tearDown()

    def cli(self, *args):
        out = subprocess.run([sys.executable, str(SCRIPT), *args, "--project", str(self.root)],
                             capture_output=True, text=True, env=dict(os.environ))
        return out.returncode, json.loads(out.stdout)

    def read_control(self):
        data = json.loads((self.logs / "control.json").read_text(encoding="utf-8"))
        (self.logs / "control.json").unlink()  # as the supervisor consumes it
        return data

    def test_cli_and_route_write_the_same_control_json(self):
        for args, route, body, state, fields in (
                (("stop",), "/api/run/stop", {}, "running", {}),
                (("stop", "--now"), "/api/run/stop", {"mode": "now"}, "running", {}),
                (("resume",), "/api/run/resume", {}, "held", {}),
                (("hold",), "/api/run/hold", {}, "waiting", {"keep_paused": False, "resets_at": RESETS_AT}),
                (("release",), "/api/run/release", {}, "waiting", {"keep_paused": True, "resets_at": RESETS_AT})):
            self.write_status(state, **fields)
            code, out = self.cli(*args)
            self.assertEqual(code, 0, out)
            by_cli = self.read_control()
            self.assertEqual(out, {"contract": "1.0", "ok": True, "id": by_cli["id"]})
            status, answer = raw_post(route, body)
            self.assertEqual(status, 202, answer)
            by_route = self.read_control()
            self.assertEqual(answer, {"ok": True, "id": by_route["id"]})
            strip = lambda c: {k: v for k, v in c.items() if k not in ("id", "requested_at")}  # noqa: E731
            self.assertEqual(strip(by_cli), strip(by_route), args)

    def test_controls_command(self):
        self.write_status("running")
        code, out = self.cli("controls")
        self.assertEqual(code, 0)
        self.assertEqual(out, {"contract": "1.0", **controls(stop=True)})

    def test_pending_request_exits_1(self):
        self.write_status("running")
        self.assertEqual(self.cli("stop")[0], 0)
        before = (self.logs / "control.json").read_bytes()
        code, out = self.cli("stop", "--now")
        self.assertEqual((code, out), (1, {"contract": "1.0", "error": "a request is already pending"}))
        self.assertEqual((self.logs / "control.json").read_bytes(), before)

    def test_refusal_exits_1_with_the_route_text(self):
        self.write_status("stopping", mode="after-task")
        code, out = self.cli("stop")
        self.assertEqual((code, out), (1, {"contract": "1.0", "error": "stopping after the current task"}))
        self.assertEqual(raw_post("/api/run/stop", {}), (409, {"error": "stopping after the current task"}))
        code, out = self.cli("resume")
        self.assertEqual((code, out), (1, {"contract": "1.0", "error": "stopping after the current task"}))
        (self.logs / "status.json").unlink()
        code, out = self.cli("stop")
        self.assertEqual((code, out), (1, {"contract": "1.0", "error": "not available now"}))
        self.assertFalse((self.logs / "control.json").exists())


class WriteControlTest(ControlCase):
    def test_refuses_when_pending(self):
        first = control.write_control(self.logs, "hold")
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertIs(control.write_control(self.logs, "release"), False)
        self.assertIs(control.send_control(self.logs, {"id": "until", "command": "stop", "mode": "after-task"}),
                      False)
        self.assertEqual(json.loads((self.logs / "control.json").read_text())["command"], "hold")

    def test_send_control_writes_the_given_request(self):
        self.assertEqual(control.send_control(self.logs, {"id": "until", "command": "stop", "mode": "after-task",
                                                          "requested_at": 1}), "until")
        data = control.read_control(self.logs)
        self.assertEqual(data["id"], "until")
        self.assertEqual((data["command"], data["mode"]), ("stop", "after-task"))
        self.assertFalse((self.logs / "control.json").exists())  # read_control deletes it

    def test_read_control_skips_bad_files(self):
        self.assertIsNone(control.read_control(self.logs))
        (self.logs / "control.json").write_text("[1]", encoding="utf-8")
        self.assertIsNone(control.read_control(self.logs))
        (self.logs / "control.json").write_text("{bad", encoding="utf-8")
        self.assertIsNone(control.read_control(self.logs))

    def test_old_callers_use_this_module(self):
        self.assertIs(supervise.read_control, supervise.sdd_control.read_control)
        self.assertIs(supervise.send_control, supervise.sdd_control.send_control)
        for module in (supervise.sdd_control, dash.sdd_control):
            self.assertEqual(Path(module.__file__).resolve(), SCRIPT.resolve())
        source = (BIN / "sdd-supervise.py").read_text(encoding="utf-8")
        self.assertNotIn("def read_control", source)
        self.assertNotIn("def send_control", source)
        self.assertNotIn("def run_controls(status)", (BIN / "sdd-dashboard.py").read_text(encoding="utf-8"))

    def test_two_processes_at_once(self):
        """Both wait on the lock the test holds; released together, one writes and one is refused."""
        code = ("import importlib.util, json, sys\n"
                "spec = importlib.util.spec_from_file_location('c', sys.argv[1])\n"
                "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
                "from pathlib import Path\n"
                "print(json.dumps(m.write_control(Path(sys.argv[2]), 'stop', 'now')))\n")
        with open(self.logs / "control.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            procs = [subprocess.Popen([sys.executable, "-c", code, str(SCRIPT), str(self.logs)],
                                      stdout=subprocess.PIPE, text=True) for _ in range(2)]
            time.sleep(0.5)
            self.assertFalse((self.logs / "control.json").exists())  # both are waiting on the lock
            fcntl.flock(lock, fcntl.LOCK_UN)
        results = [json.loads(p.communicate(timeout=30)[0]) for p in procs]
        written = [r for r in results if r]
        self.assertEqual(len(written), 1, results)
        self.assertIn(False, results)
        self.assertEqual(json.loads((self.logs / "control.json").read_text())["id"], written[0])


if __name__ == "__main__":
    unittest.main()
