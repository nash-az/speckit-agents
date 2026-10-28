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


class SleepUntilTest(unittest.TestCase):
    def test_wakes_by_the_wall_clock(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("sup", SCRIPT)
        sup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sup)
        t0 = time.time()
        sup.sleep_until(t0 + 0.3)
        self.assertGreaterEqual(time.time(), t0 + 0.3)
        self.assertLess(time.time(), t0 + 2)


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
        cycle1 = [{"transcript": True}, started("a", "impl:T1"), rate(utilization=0.96), rate(utilization=0.97),
                  rate(utilization=0.99), {"sleep": 30}, *DONE]
        out, calls, took = self.supervise([cycle1, [*DONE]])
        self.assertEqual(len(calls), 2)
        self.assertLess(took, 25)
        self.assertIn("usage five_hour 97%", out.stdout)
        self.assertIn("before the agent finished: stopping now", out.stdout)
        self.assertNotIn("safe point", out.stdout)

    def test_each_window_has_its_own_threshold(self):
        # Defaults: five_hour pauses at 95 %, seven_day only at 99 %.
        cycle1 = [{"transcript": True}, started("a", "impl:T1"), rate(utilization=0.97, window="seven_day"),
                  {"sleep": 0.5}, finished("a"), started("b", "verify:T1"), {"sleep": 1.0}, *DONE]
        out, calls, _ = self.supervise([cycle1])
        self.assertEqual(len(calls), 1, "97 % of the week is below its threshold: no pause")
        self.assertNotIn("pausing", out.stdout)
        cycle1 = [{"transcript": True}, started("a", "impl:T1"), rate(utilization=0.99, window="seven_day"),
                  {"sleep": 0.5}, finished("a"), started("b", "verify:T1"), {"sleep": 30}, *DONE]
        shutil.rmtree(self.dir / "wf"); (self.dir / "count").unlink(); (self.dir / "calls").unlink()
        out, calls, _ = self.supervise([cycle1, [*DONE]])
        self.assertEqual(len(calls), 2)
        self.assertIn("seven_day at 99%: pausing", out.stdout)
        self.assertIn("safe point", out.stdout, "the seven-day hard stop is above 99 %, so it waits for the agent")

    def test_threshold_option_per_window(self):
        cycle1 = [{"transcript": True}, started("a", "impl:T1"), rate(utilization=0.96), {"sleep": 0.5},
                  finished("a"), started("b", "verify:T1"), {"sleep": 1.0}, *DONE]
        out, calls, _ = self.supervise([cycle1], "--threshold", "five_hour=0.98")
        self.assertEqual(len(calls), 1, out.stdout)
        self.assertNotIn("pausing", out.stdout)

    def test_follows_a_relaunched_workflow_to_its_safe_point(self):
        # The first workflow is left with an agent that never finishes; the second one carries on.
        cycle1 = [{"transcript": True}, started("a", "verify:T1"), {"transcript": "wf2"}, started("b", "impl:T2"),
                  rate(utilization=0.96), {"sleep": 0.5}, finished("b"), started("c", "verify:T2"), {"sleep": 30}, *DONE]
        out, calls, took = self.supervise([cycle1, [*DONE]])
        self.assertEqual(len(calls), 2, out.stdout)
        self.assertLess(took, 25)
        self.assertIn("workflow wf2", out.stdout)
        self.assertIn("safe point", out.stdout)

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
        cycle1 = [{"transcript": True}, rate(utilization=0.99, window="seven_day", resets_in=3 * 86400), {"sleep": 30}, *DONE]
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

    def test_usage_goes_into_status_and_progress_lines(self):
        week = {"emit": {"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "rateLimitType": "five_hour",
            "unifiedWindows": {"five_hour": {"utilization": 0.42, "resetsAt": int(time.time()) + 3600},
                               "seven_day": {"utilization": 0.51, "resetsAt": int(time.time()) + 86400}}}}}
        out, _, _ = self.supervise([[{"transcript": True}, week, {"sleep": 0.5}, *DONE]])
        self.assertIn("usage five_hour 42%", out.stdout)
        self.assertIn("usage seven_day 51%", out.stdout)

    def test_restart_keeps_waiting_for_a_reset(self):
        state = self.dir / "state"
        sup_dir = next(iter([None]), None)
        out, calls, _ = self.supervise([[*DONE]])  # creates the state dir
        status = next(state.rglob("status.json"))
        status.write_text(json.dumps({"state": "waiting", "window": "five_hour", "resets_at": int(time.time()) + 600}))
        (self.dir / "count").unlink()
        (self.dir / "calls").unlink()
        out, calls, _ = self.supervise([[*DONE]])
        self.assertIn("an earlier supervisor was waiting for the five_hour window", out.stdout)
        self.assertEqual(len(calls), 1)

    PROFILES = ('[profiles.local]\nmodel = "qwen3-coder"\n'
                'env = { ANTHROPIC_BASE_URL = "http://localhost:4000" }\n')

    def test_profile_sets_the_model_env_and_prompt(self):
        (self.project / ".sdd" / "config.toml").write_text(self.PROFILES)
        out, calls, _ = self.supervise([[{"transcript": True}, *DONE]], "--profile", "local")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(len(calls), 1)
        argv = calls[0]
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "qwen3-coder")
        self.assertTrue(argv[1].startswith("/sdd-run --yes --profile local"), argv[1])
        env = json.loads((self.dir / "env0.json").read_text())
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "http://localhost:4000")
        self.assertEqual(env["CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"], "0")

    def test_without_a_profile_no_model_is_passed(self):
        out, calls, _ = self.supervise([[{"transcript": True}, *DONE]])
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertNotIn("--model", calls[0])
        self.assertNotIn("--profile", calls[0][1])

    def test_unknown_profile_exits_2(self):
        (self.project / ".sdd" / "config.toml").write_text(self.PROFILES)
        out, calls, _ = self.supervise([[*DONE]], "--profile", "cloud")
        self.assertEqual(out.returncode, 2, out.stdout)
        self.assertIn("unknown profile cloud", out.stdout)
        self.assertEqual(calls, [])

    def test_config_errors_exit_2_before_starting(self):
        (self.project / ".sdd" / "config.toml").write_text(
            self.PROFILES + '[profiles.bad]\nenv = { API_KEY = "x" }\n')
        out, calls, _ = self.supervise([[*DONE]], "--profile", "local")
        self.assertEqual(out.returncode, 2, out.stdout)
        self.assertIn("profiles.bad.model: missing", out.stdout)
        self.assertIn("profiles.bad.env.API_KEY", out.stdout)
        self.assertEqual(calls, [])

    def test_needs_an_initialised_project(self):
        (self.project / ".sdd" / "config.toml").unlink()
        out, calls, _ = self.supervise([[*DONE]])
        self.assertEqual(out.returncode, 2)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
