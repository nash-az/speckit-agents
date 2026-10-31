"""bin/sdd-supervise.py against a scripted stand-in for `claude -p` (tests/fake/claude)."""
import json
import os
import re
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


class HardStopTest(unittest.TestCase):
    """The hard stop is per window (FR-028, data-model §10)."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("sup", SCRIPT)
        self.sup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.sup)

    def test_defaults_are_each_windows_threshold_plus_one_point(self):
        stops = self.sup.parse_args([]).hard_stops
        self.assertEqual(stops["five_hour"], 0.96)
        self.assertEqual(stops["seven_day"], 1.0)

    def test_explicit_hard_stop_applies_to_every_window(self):
        stops = self.sup.parse_args(["--hard-stop", "0.97"]).hard_stops
        self.assertEqual(stops["five_hour"], 0.97)
        self.assertEqual(stops["seven_day"], 0.97)

    def test_hard_stop_is_capped_at_one(self):
        stops = self.sup.parse_args(["--hard-stop", "1.5"]).hard_stops
        self.assertEqual(set(stops.values()), {1.0})


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
        # 95 %: at the five-hour threshold, below its hard stop (threshold + 1 point).
        cycle1 = [{"transcript": True}, started("a", "impl:T1"), rate(utilization=0.95), {"sleep": 0.5},
                  finished("a"), started("b", "verify:T1"), {"sleep": 30}, *DONE]
        out, calls, took = self.supervise([cycle1, [{"transcript": True}, *DONE]])
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(len(calls), 2)
        self.assertLess(took, 25, "cycle 1 should be stopped at the safe point, not run to its end")
        self.assertIn("pausing after the next agent finishes", out.stdout)
        self.assertIn("safe point", out.stdout)

    def test_does_not_stop_mid_agent_below_the_hard_stop(self):
        cycle1 = [{"transcript": True}, started("a", "impl:T1"), rate(utilization=0.95), {"sleep": 1.0}, *DONE]
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
                  rate(utilization=0.95), {"sleep": 0.5}, finished("b"), started("c", "verify:T2"), {"sleep": 30}, *DONE]
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

    def test_config_errors_exit_2_even_without_a_profile(self):
        (self.project / ".sdd" / "config.toml").write_text(
            '[agents]\ncoder = "haiku"\n[run]\nisolation = "worktree"\n')
        out, calls, _ = self.supervise([[*DONE]])
        self.assertEqual(out.returncode, 2, out.stdout)
        self.assertIn("agents.coder", out.stdout)
        self.assertIn("worktree isolation was removed", out.stdout)
        self.assertEqual(calls, [])

    def test_needs_an_initialised_project(self):
        (self.project / ".sdd" / "config.toml").unlink()
        out, calls, _ = self.supervise([[*DONE]])
        self.assertEqual(out.returncode, 2)
        self.assertEqual(calls, [])


class RunControlTest(unittest.TestCase):
    """Stop, resume, hold and release from the dashboard's control.json (FR-029, data-model §12)."""

    MARGIN = 120  # sdd-supervise.RESET_MARGIN_S: a reset this far in the past is due now

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.project = self.dir / "project"
        (self.project / ".sdd").mkdir(parents=True)
        (self.project / ".sdd" / "config.toml").write_text("")
        slug = re.sub(r"[^A-Za-z0-9]+", "-", str(self.project.resolve())).strip("-")
        self.logs = self.dir / "state" / "speckit-agents" / slug
        self.env = dict(os.environ, FAKE_DIR=str(self.dir), XDG_STATE_HOME=str(self.dir / "state"),
                        FAKE_CONTROL=str(self.logs / "control.json"))
        self.procs = []

    def tearDown(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        shutil.rmtree(self.dir)

    def start(self, cycles, *extra):
        (self.dir / "scenario.json").write_text(json.dumps({"cycles": cycles}))
        proc = subprocess.Popen([sys.executable, str(SCRIPT), "--project", str(self.project), "--claude", str(FAKE),
                                 "--poll", "0.1", *extra], env=self.env, stdout=subprocess.PIPE, text=True)
        self.procs.append(proc)
        return proc

    def finish(self, proc):
        out, _ = proc.communicate(timeout=60)
        calls = (self.dir / "calls").read_text().splitlines() if (self.dir / "calls").exists() else []
        return out, calls

    def status(self):
        try:
            return json.loads((self.logs / "status.json").read_text())
        except (OSError, ValueError):
            return {}

    def wait_for(self, predicate, what, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate(self.status()):
                return self.status()
            time.sleep(0.05)
        self.fail(f"timed out waiting for {what}; status {self.status()}")

    def control(self, command, **fields):
        """Write a request like the dashboard does, then wait until the supervisor has consumed it."""
        path = self.logs / "control.json"
        tmp = self.logs / "control.json.tmp"
        tmp.write_text(json.dumps({"id": "x", "command": command, "requested_at": int(time.time()), **fields}))
        os.replace(tmp, path)
        deadline = time.time() + 10
        while path.exists() and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(path.exists(), f"the supervisor did not consume {command}")

    def waits_briefly(self):
        """A cycle that pauses at a safe point for a five-hour window resetting in about 4 s."""
        return [{"transcript": True}, started("a", "impl:T1"), rate(utilization=0.95, resets_in=4 - self.MARGIN),
                {"sleep": 0.3}, finished("a"), {"sleep": 30}, *DONE]

    def test_hold_during_the_wait_ends_held_at_the_reset_and_times_out(self):
        proc = self.start([self.waits_briefly(), [*DONE]], "--max-wait-hours", "0.002")
        self.wait_for(lambda s: s.get("state") == "waiting", "waiting")
        self.assertFalse(self.status()["keep_paused"])
        self.control("hold")
        self.assertTrue(self.wait_for(lambda s: s.get("keep_paused"), "keep_paused")["state"] == "waiting")
        held = self.wait_for(lambda s: s.get("state") == "held", "held")
        self.assertEqual(held["reason"], "kept paused; the usage window has reset")
        self.assertEqual(held["pid"], proc.pid)
        out, calls = self.finish(proc)
        self.assertEqual(proc.returncode, 3, out)
        self.assertEqual(len(calls), 1, "a kept pause starts no cycle at the reset")
        self.assertEqual(self.status()["state"], "stopped")
        self.assertEqual(self.status()["reason"], "held too long")

    def test_release_before_the_reset_lets_the_cycle_start(self):
        proc = self.start([self.waits_briefly(), [*DONE]])
        self.wait_for(lambda s: s.get("state") == "waiting", "waiting")
        self.control("hold")
        self.wait_for(lambda s: s.get("keep_paused") is True, "keep_paused")
        self.control("release")
        self.wait_for(lambda s: s.get("keep_paused") is False, "released")
        out, calls = self.finish(proc)
        self.assertEqual(proc.returncode, 0, out)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("held", out)

    def test_stop_and_resume_during_the_wait_are_discarded(self):
        proc = self.start([self.waits_briefly(), [*DONE]])
        self.wait_for(lambda s: s.get("state") == "waiting", "waiting")
        self.control("stop", mode="now")
        self.control("resume")
        self.assertEqual(self.status()["state"], "waiting")
        self.assertFalse(self.status()["keep_paused"])
        out, calls = self.finish(proc)
        self.assertEqual(proc.returncode, 0, out)
        self.assertEqual(len(calls), 2)
        self.assertIn("ignoring operator request 'stop' while waiting", out)
        self.assertIn("ignoring operator request 'resume' while waiting", out)

    def test_after_task_stop_waits_for_the_agent_then_resume_starts_a_second_cycle(self):
        cycle1 = [{"transcript": True}, started("a", "impl:T1"), {"control": {"command": "stop", "mode": "after-task"}},
                  {"sleep": 1.5}, finished("a"), started("b", "verify:T1"), {"sleep": 30}, *DONE]
        t0 = time.time()
        proc = self.start([cycle1, [{"transcript": True}, *DONE]])
        stopping = self.wait_for(lambda s: s.get("state") == "stopping", "stopping")
        self.assertEqual(stopping["mode"], "after-task")
        held = self.wait_for(lambda s: s.get("state") == "held", "held")
        self.assertGreaterEqual(time.time() - t0, 1.5, "the running agent was allowed to finish")
        self.assertEqual(held["reason"], "stopped by operator")
        self.assertEqual(len((self.dir / "calls").read_text().splitlines()), 1)
        self.control("resume")
        out, calls = self.finish(proc)
        self.assertEqual(proc.returncode, 0, out)
        self.assertEqual(len(calls), 2)
        self.assertIn("safe point", out)

    def test_stop_now_stops_at_once_and_a_stop_while_held_is_discarded(self):
        cycle1 = [{"transcript": True}, started("a", "impl:T1"), {"control": {"command": "stop", "mode": "now"}},
                  {"sleep": 30}, *DONE]
        t0 = time.time()
        proc = self.start([cycle1, [*DONE]])
        self.wait_for(lambda s: s.get("state") == "held", "held")
        self.assertLess(time.time() - t0, 25, "stop now does not wait for the agent")
        self.control("stop", mode="after-task")
        self.assertEqual(self.status()["state"], "held")
        self.control("resume")
        out, calls = self.finish(proc)
        self.assertEqual(proc.returncode, 0, out)
        self.assertEqual(len(calls), 2)
        self.assertIn("stopping now", out)
        self.assertIn("ignoring operator request 'stop' while held", out)
        self.assertNotIn("safe point", out)

    def test_a_stale_control_file_is_deleted_at_startup(self):
        self.logs.mkdir(parents=True)
        (self.logs / "control.json").write_text(json.dumps({"id": "old", "command": "stop", "mode": "now"}))
        proc = self.start([[{"transcript": True}, {"sleep": 1.0}, *DONE]], "--no-sleep")
        out, calls = self.finish(proc)
        self.assertEqual(proc.returncode, 0, out)
        self.assertEqual(len(calls), 1)
        self.assertFalse((self.logs / "control.json").exists())
        self.assertIn("deleted a leftover control.json", out)
        self.assertEqual(self.status()["state"], "done")


JOB_ID = "20261031-1012"


def job_run(run_id, tick=None):
    """One run of a Job as the fake plays it: run.json appears, a task may be ticked, the run finishes."""
    steps = [{"transcript": True}, {"run": run_id}, {"sleep": 0.4}]
    if tick:
        steps.append({"tick": tick})
    return [*steps, {"finish": True}, *DONE]


class JobTest(unittest.TestCase):
    """--job: the Job loop over runs (spec 005, data-model §2 and §6)."""

    TASKS = ("# Tasks\n\n## Phase 1: Work\n\n"
             "- [ ] T001 First `a.py`\n- [ ] T002 Second `b.py`\n- [ ] T003 Third `c.py`\n")

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.project = self.dir / "project"
        (self.project / ".sdd").mkdir(parents=True)
        (self.project / ".sdd" / "config.toml").write_text("")
        self.tasks = self.project / "specs" / "005-jobs" / "tasks.md"
        self.tasks.parent.mkdir(parents=True)
        self.tasks.write_text(self.TASKS)
        slug = re.sub(r"[^A-Za-z0-9]+", "-", str(self.project.resolve())).strip("-")
        self.logs = self.dir / "state" / "speckit-agents" / slug
        self.git("init", "-q", "-b", "main")
        self.git("add", "specs")
        self.git("commit", "-q", "-m", "init")
        self.git("switch", "-q", "-c", f"auto/{JOB_ID}")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def git(self, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=self.project,
                       check=True, capture_output=True)

    def save_job(self, **extra):
        record = {"id": JOB_ID, "title": "Phase 1", "feature": "005-jobs", "scope": {"phases": ["1"]},
                  "scope_tasks": ["T001", "T002", "T003"], "blocked_at_start": 0,
                  "limits": {"max_tasks": None, "until": None, "max_runs": 20}, "branch": f"auto/{JOB_ID}",
                  "base": {"branch": "main", "commit": "x"}, "state": "working", "reason": None, "runs": [],
                  "no_progress_runs": 0, "created": int(time.time()), "ended": None, "merge_commit": None}
        record.update(extra)
        (self.project / ".sdd" / "jobs").mkdir(exist_ok=True)
        (self.project / ".sdd" / "jobs" / f"{JOB_ID}.json").write_text(json.dumps(record))

    def job(self):
        return json.loads((self.project / ".sdd" / "jobs" / f"{JOB_ID}.json").read_text())

    def supervise(self, cycles, *extra):
        (self.dir / "scenario.json").write_text(json.dumps({"cycles": cycles}))
        env = dict(os.environ, FAKE_DIR=str(self.dir), XDG_STATE_HOME=str(self.dir / "state"),
                   FAKE_TASKS=str(self.tasks), FAKE_CONTROL=str(self.logs / "control.json"))
        t0 = time.time()
        out = subprocess.run([sys.executable, str(SCRIPT), "--project", str(self.project), "--claude", str(FAKE),
                              "--poll", "0.1", "--no-sleep", "--job", JOB_ID, *extra],
                             env=env, capture_output=True, text=True, timeout=90)
        calls = (self.dir / "calls").read_text().splitlines() if (self.dir / "calls").exists() else []
        return out, [json.loads(c) for c in calls], time.time() - t0

    def test_three_runs_end_done_with_three_run_ids(self):
        self.save_job()
        out, calls, _ = self.supervise([job_run("r1", "T001"), job_run("r2", "T002"), job_run("r3", "T003")])
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(len(calls), 3)
        for argv in calls:
            self.assertEqual(argv[1], f"/sdd-run --yes --no-converge --phases 1 --job {JOB_ID}")
        record = self.job()
        self.assertEqual(record["state"], "done")
        self.assertEqual(record["runs"], ["r1", "r2", "r3"])
        status = json.loads((self.logs / "status.json").read_text())
        self.assertEqual(status["job"], JOB_ID)
        self.assertEqual(status["state"], "done")

    def test_only_person_items_left_is_waiting(self):
        self.tasks.write_text(self.TASKS.replace("T002 Second", "T002 [manual] Second")
                              .replace("T003 Third `c.py`", "T003 Third, after T002 `c.py`"))
        self.save_job()
        out, calls, _ = self.supervise([job_run("r1", "T001"), job_run("r2")])
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(len(calls), 1)
        record = self.job()
        self.assertEqual(record["state"], "waiting")
        self.assertEqual(record["runs"], ["r1"])

    def test_two_runs_without_progress_fail(self):
        self.save_job()
        out, calls, _ = self.supervise([job_run("r1"), job_run("r2"), job_run("r3")])
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertEqual(len(calls), 2)
        record = self.job()
        self.assertEqual(record["state"], "failed")
        self.assertEqual(record["reason"], "no progress in two runs")
        self.assertEqual(record["no_progress_runs"], 2)

    def test_progress_resets_the_no_progress_count(self):
        self.save_job()
        out, calls, _ = self.supervise([job_run("r1"), job_run("r2", "T001"), job_run("r3", "T002"),
                                        job_run("r4", "T003")])
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(len(calls), 4)
        self.assertEqual(self.job()["state"], "done")

    def test_max_runs_stops_the_job(self):
        self.save_job(limits={"max_tasks": None, "until": None, "max_runs": 2})
        out, calls, _ = self.supervise([job_run("r1", "T001"), job_run("r2", "T002"), job_run("r3", "T003")])
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(len(calls), 2)
        record = self.job()
        self.assertEqual(record["state"], "stopped")
        self.assertEqual(record["reason"], "max_runs 2 reached")
        self.assertEqual(record["runs"], ["r1", "r2"])

    def test_checkout_on_another_branch_stops_and_runs_nothing(self):
        self.save_job()
        self.git("switch", "-q", "main")
        out, calls, _ = self.supervise([job_run("r1", "T001")])
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(calls, [])
        record = self.job()
        self.assertEqual(record["state"], "stopped")
        self.assertEqual(record["reason"], "the checkout is not on the Job's branch")
        self.assertEqual(record["runs"], [])
        self.assertEqual(self.tasks.read_text(), self.TASKS)

    def test_max_tasks_passes_the_tasks_left_to_the_run(self):
        self.tasks.write_text(self.TASKS.replace("- [ ] T001", "- [x] T001"))
        self.save_job(limits={"max_tasks": 2, "until": None, "max_runs": 20})
        out, calls, _ = self.supervise([job_run("r1", "T002"), job_run("r2", "T003")])
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][1].endswith(f"--job {JOB_ID} --max-tasks 1"), calls[0][1])
        record = self.job()
        self.assertEqual(record["state"], "stopped")
        self.assertEqual(record["reason"], "max_tasks 2 reached")

    def test_until_passing_mid_run_stops_after_the_current_task(self):
        until = int(time.time()) + 2
        self.save_job(limits={"max_tasks": None, "until": until, "max_runs": 20})
        run = [{"transcript": True}, {"run": "r1"}, started("a", "impl:T001"), {"sleep": 3.5}, finished("a"),
               started("b", "verify:T001"), {"sleep": 30}, {"tick": "T001"}, {"finish": True}, *DONE]
        t0 = time.time()
        out, calls, took = self.supervise([run, job_run("r2", "T002")])
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(len(calls), 1)
        self.assertGreaterEqual(time.time() - t0, 3.0, "the running agent was allowed to finish")
        self.assertLess(took, 25, "the run was stopped at the safe point, not run to its end")
        self.assertIn("safe point", out.stdout)
        record = self.job()
        self.assertEqual(record["state"], "stopped")
        self.assertEqual(record["reason"], f"until {time.strftime('%H:%M', time.localtime(until))} reached")
        self.assertEqual(record["runs"], ["r1"])

    def test_max_cycles_counts_per_run(self):
        self.save_job()
        out, calls, _ = self.supervise([job_run("r1", "T001"), job_run("r2", "T002"), job_run("r3", "T003")],
                                       "--max-cycles", "1")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.job()["state"], "done")

    def test_tasks_added_to_a_scoped_phase_join_the_job(self):
        self.save_job()
        added = [{"transcript": True}, {"run": "r1"}, {"sleep": 0.4}, {"tick": "T001"}, {"finish": True}, *DONE]
        self.tasks.write_text(self.TASKS + "- [ ] T004 Added `d.py`\n")
        out, calls, _ = self.supervise([added, job_run("r2", "T002"), job_run("r3", "T003"), job_run("r4", "T004")])
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(len(calls), 4)
        record = self.job()
        self.assertEqual(record["scope_tasks"], ["T001", "T002", "T003", "T004"])
        self.assertEqual(record["state"], "done")

    def test_a_job_that_is_not_working_is_refused(self):
        self.save_job(state="waiting")
        out, calls, _ = self.supervise([job_run("r1", "T001")])
        self.assertEqual(out.returncode, 2, out.stdout)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
