"""bin/sdd-supervise.py against a scripted stand-in for `claude -p` (tests/fake/claude)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE.parent / "bin" / "sdd-supervise.py"
FAKE = HERE / "fake" / "claude"


def rate(status="allowed_warning", utilization=None, window="five_hour", resets_in=60):
    info = {"status": status, "rateLimitType": window, "resetsAt": int(time.time()) + resets_in}
    if utilization is not None:
        info["utilization"] = utilization
        info["unifiedWindows"] = {window: {"utilization": utilization, "resetsAt": info["resetsAt"]}}
    return {"emit": {"type": "rate_limit_event", "rate_limit_info": info}}


def started(key, label):
    return {"journal": {"type": "started", "key": key, "label": label}}


def finished(key):
    return {"journal": {"type": "result", "key": key, "result": {}}}


DONE = [{"emit": {"type": "result", "subtype": "success"}}, {"exit": 0}]


class SuperviseTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.project = self.dir / "project"
        (self.project / ".sdd").mkdir(parents=True)
        (self.project / ".sdd" / "config.toml").write_text("")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def supervise(self, cycles, *extra):
        (self.dir / "scenario.json").write_text(json.dumps({"cycles": cycles}))
        env = dict(os.environ, FAKE_DIR=str(self.dir), XDG_STATE_HOME=str(self.dir / "state"))
        t0 = time.time()
        out = subprocess.run([sys.executable, str(SCRIPT), "--project", str(self.project), "--claude", str(FAKE),
                              "--poll", "0.1", "--no-sleep", *extra],
                             env=env, capture_output=True, text=True, timeout=60)
        calls = (self.dir / "calls").read_text().splitlines() if (self.dir / "calls").exists() else []
        return out, [json.loads(c) for c in calls], time.time() - t0

    def test_finished_run_ends_the_supervisor(self):
        out, calls, _ = self.supervise([[{"transcript": True}, *DONE]])
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(len(calls), 1)
        self.assertIn("/sdd-run --yes", calls[0])

    def test_pauses_after_the_next_agent_finishes_and_resumes(self):
        cycle1 = [{"transcript": True}, started("a", "impl:T1"), rate(utilization=0.96), {"sleep": 0.5},
                  finished("a"), started("b", "verify:T1"), {"sleep": 30}, *DONE]
        out, calls, took = self.supervise([cycle1, [{"transcript": True}, *DONE]])
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(len(calls), 2)
        self.assertLess(took, 25, "cycle 1 should be stopped at the safe point, not run to its end")
        self.assertIn("pausing after the next agent finishes", out.stdout)
        self.assertIn("safe point", out.stdout)

    def test_does_not_stop_mid_agent_below_the_hard_stop(self):
        cycle1 = [{"transcript": True}, started("a", "impl:T1"), rate(utilization=0.96), {"sleep": 1.0}, *DONE]
        out, calls, _ = self.supervise([cycle1])
        self.assertEqual(len(calls), 1, "no safe point came, so the run was allowed to finish")
        self.assertEqual(out.returncode, 0, out.stdout)

    def test_hard_stop_does_not_wait_for_a_safe_point(self):
        cycle1 = [{"transcript": True}, started("a", "impl:T1"), rate(utilization=0.99), {"sleep": 30}, *DONE]
        out, calls, took = self.supervise([cycle1, [*DONE]])
        self.assertEqual(len(calls), 2)
        self.assertLess(took, 25)

    def test_rejected_stops_at_once_and_resumes(self):
        cycle1 = [{"transcript": True}, started("a", "impl:T1"), rate(status="rejected"), {"sleep": 30}, *DONE]
        out, calls, took = self.supervise([cycle1, [{"transcript": True}, *DONE]])
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(len(calls), 2)
        self.assertLess(took, 25)
        self.assertIn("limit was hit before a safe point", out.stdout)

    def test_waits_only_for_the_window_that_crossed_the_threshold(self):
        # The seven-day window has a far reset but is not the one near its limit.
        week = {"emit": {"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "rateLimitType": "five_hour",
            "unifiedWindows": {"seven_day": {"utilization": 0.64, "resetsAt": int(time.time()) + 3 * 86400}}}}}
        cycle1 = [{"transcript": True}, week, started("a", "impl:T1"), rate(utilization=0.96, resets_in=60),
                  {"sleep": 0.5}, finished("a"), {"sleep": 30}, *DONE]
        out, calls, _ = self.supervise([cycle1, [*DONE]])
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(len(calls), 2)
        self.assertIn("five_hour window to reset", out.stdout)

    def test_test_pause_restarts_at_once(self):
        week = {"emit": {"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "rateLimitType": "five_hour",
            "unifiedWindows": {"seven_day": {"utilization": 0.64, "resetsAt": int(time.time()) + 3 * 86400}}}}}
        cycle1 = [{"transcript": True}, week, started("a", "impl:T1"), finished("a"), {"sleep": 30}, *DONE]
        out, calls, _ = self.supervise([cycle1, [*DONE]], "--pause-after", "1")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(len(calls), 2)

    def test_reset_too_far_stops_the_supervisor(self):
        cycle1 = [{"transcript": True}, rate(utilization=0.97, window="seven_day", resets_in=3 * 86400), {"sleep": 30}, *DONE]
        out, calls, _ = self.supervise([cycle1])
        self.assertEqual(out.returncode, 3, out.stdout)
        self.assertEqual(len(calls), 1)
        status = json.loads(next((self.dir / "state").rglob("status.json")).read_text())
        self.assertEqual(status["state"], "stopped")

    def test_cut_off_without_a_limit_is_not_reported_as_finished(self):
        (self.project / ".sdd" / "run.json").write_text("{}")
        out, calls, _ = self.supervise([[{"transcript": True}, *DONE]])
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("cut off by something other than the usage limit", out.stdout)

    def test_terminating_the_supervisor_stops_its_run(self):
        (self.dir / "scenario.json").write_text(json.dumps({"cycles": [[{"transcript": True}, {"sleep": 60}, *DONE]]}))
        env = dict(os.environ, FAKE_DIR=str(self.dir), XDG_STATE_HOME=str(self.dir / "state"))
        sup = subprocess.Popen([sys.executable, str(SCRIPT), "--project", str(self.project), "--claude", str(FAKE),
                                "--poll", "0.1", "--no-sleep"], env=env, stdout=subprocess.PIPE, text=True)
        pid_file = self.dir / "pid0"
        for _ in range(100):
            if pid_file.exists():
                break
            time.sleep(0.1)
        child = int(pid_file.read_text())
        time.sleep(0.5)
        sup.terminate()
        sup.wait(timeout=40)
        time.sleep(0.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)

    def test_needs_an_initialised_project(self):
        (self.project / ".sdd" / "config.toml").unlink()
        out, calls, _ = self.supervise([[*DONE]])
        self.assertEqual(out.returncode, 2)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
