"""Browser tests of the Start run panel in the Runs view (FR-034): it lists the runnable phases with
their task counts, shows the reason when a run cannot start, asks for confirmation, and a confirmed
click calls `POST /api/run/start` once.

No run starts. The dashboard runs in this test process (on an OS-chosen port, in a thread) so that
`sdd_launch.launch` can be replaced by `RecordingLauncher`, a stub that records its arguments and
answers as the real one would. `sdd_launch.preview` stays real, so the panel shows what
`GET /api/run/plan` says for the toy: a clean git repository can start, a project that is not a git
repository cannot. The supervisor's state directory is under a temporary `XDG_STATE_HOME`.

Run it with `python3 -m unittest discover -s tests/ui`. It skips without Playwright or a Chromium, or
when the sandbox refuses a local port; `SDD_UI_TESTS=1` makes that a failure. Not part of the default
`checks.commands`.
"""
import contextlib
import http.server
import importlib.util
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_ui_harness", HERE / "harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

_spec = importlib.util.spec_from_file_location("sdd_dashboard", harness.DASHBOARD)
sdd_dashboard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sdd_dashboard)

TIMEOUT_MS = 10000
TASKS = ("# Tasks\n\n## Phase 1: Setup\n\n"
         "- [ ] T001 Plain task in `a.py`\n"
         "- [ ] T002 Another task in `b.py`\n"
         "- [ ] T003 [manual] Record audio\n"
         "- [x] T004 Done task in `c.py`\n\n"
         "## Phase 2: Core\n\n"
         "- [ ] T005 Core task in `d.py`\n")
# What `sdd_launch.preview()` makes of TASKS: phase id -> runnable tasks, and the held count.
PHASES = {"1": 2, "2": 1}
HELD = 1
NOT_GIT_REASON = "the project is not a git repository"
CONFIRM_START = "Start an unattended run of"


def build_toy(root, git):
    """A one-feature toy (`specs/001-only/tasks.md`, `.sdd/config.toml`); with `git`, a clean repository."""
    root = Path(root)
    feature = root / "specs" / "001-only"
    feature.mkdir(parents=True)
    (feature / "tasks.md").write_text(TASKS, encoding="utf-8")
    (root / ".sdd").mkdir(parents=True, exist_ok=True)
    (root / ".sdd" / "config.toml").write_text("[run]\nmax_retries = 2\n", encoding="utf-8")
    if git:
        identity = ["-c", "user.name=sdd-ui", "-c", "user.email=sdd-ui@example.invalid", "-c", "commit.gpgsign=false"]
        for args in (["init", "-q"], ["add", "-A"], identity + ["commit", "-q", "-m", "toy"]):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


class RecordingLauncher:
    """Stands in for `sdd_launch.launch`: records each call's arguments and starts nothing."""

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, project, feature, phases=None, no_converge=True, max_tasks=None, dry_run=False):
        with self.lock:
            self.calls.append({"project": Path(project), "feature": Path(feature), "phases": list(phases or []),
                               "no_converge": no_converge, "max_tasks": max_tasks})
        run_args = sdd_dashboard.sdd_launch.build_run_args(list(phases or []), no_converge, max_tasks)
        return {"pid": 4242, "run_args": run_args, "resumes": None}

    def reset(self):
        with self.lock:
            self.calls.clear()


class StartRunTestCase(unittest.TestCase):
    """One in-process dashboard per class on a toy (`GIT` says whether it is a git repository)."""

    GIT = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        harness.require_browser()
        cls._tmp = tempfile.TemporaryDirectory(prefix="sdd-ui-start-")
        base = Path(cls._tmp.name)
        cls.project = build_toy(base / "toy", cls.GIT).resolve()
        cls.feature = cls.project / "specs" / "001-only"
        cls._env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(base / "state")})
        cls._env.start()
        cls.launcher = RecordingLauncher()
        cls._launch = mock.patch.object(sdd_dashboard.sdd_launch, "launch", cls.launcher)
        cls._launch.start()
        handler = sdd_dashboard.DashboardHandler
        handler.project = handler.started_project = cls.project
        handler.feature = None
        handler.allow_ports = ()
        handler.html = sdd_dashboard.build_html()
        try:
            cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        except OSError as exc:
            cls._cleanup()
            harness.skip_or_fail("the dashboard cannot start (%s)" % exc)
        handler.port = cls.server.server_address[1]
        cls.url = "http://127.0.0.1:%d/" % handler.port
        cls._thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def _cleanup(cls):
        cls._launch.stop()
        cls._env.stop()
        cls._tmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._thread.join(timeout=5)
        cls._cleanup()
        super().tearDownClass()

    def setUp(self):
        self.launcher.reset()

    @contextlib.contextmanager
    def open_runs(self, element):
        """The dashboard at `#runs`; returns once `element` in the Start run panel is visible. Every
        `POST /api/run/start` the page sends is in `ui.start_posts`."""
        with harness.browser_page(self.url + "#runs", name=self.id()) as ui:
            ui.start_posts = []
            ui.page.on("request", lambda r: ui.start_posts.append(r.post_data_json)
                       if r.method == "POST" and r.url.endswith("/api/run/start") else None)
            ui.page.locator("#section-runs").wait_for(state="visible", timeout=TIMEOUT_MS)
            ui.page.locator("#start-run-body " + element).first.wait_for(state="visible", timeout=TIMEOUT_MS)
            yield ui
            ui.page.wait_for_load_state("networkidle")


class CanStartTest(StartRunTestCase):
    def phase_rows(self, ui):
        boxes = ui.page.locator("#start-run-body .start-phase")
        return {boxes.nth(i).locator(".start-phase-box").get_attribute("value"):
                boxes.nth(i).locator(".start-count").inner_text() for i in range(boxes.count())}

    def test_panel_lists_runnable_phases_and_counts(self):
        plan = sdd_dashboard.sdd_launch.preview(self.project, self.feature)
        self.assertTrue(plan["can_start"], plan["reason"])
        self.assertEqual({p["id"]: p["runnable"] for p in plan["phases"]}, PHASES)
        with self.open_runs("#start-run-btn") as ui:
            self.assertEqual(self.phase_rows(ui), {"1": "2 tasks", "2": "1 task"})
            self.assertIn("Phase 1: Setup", ui.page.locator("#start-run-body .start-phase").first.inner_text())
            boxes = ui.page.locator("#start-run-body .start-phase-box")
            self.assertTrue(all(boxes.nth(i).is_checked() for i in range(boxes.count())))
            summary = ui.page.locator("#start-run-body .start-summary").inner_text()
            self.assertIn("%d runnable, %d held." % (sum(PHASES.values()), HELD), summary)
            self.assertEqual(ui.page.locator("#start-run-body .start-reason").count(), 0)
            self.assertTrue(ui.page.locator("#start-run-btn").is_enabled())
        self.assertEqual(self.launcher.calls, [])  # opening the panel starts nothing

    def test_dismissed_confirmation_starts_nothing(self):
        with self.open_runs("#start-run-btn") as ui:
            asked = []

            def dismiss(dialog):
                asked.append((dialog.type, dialog.message))
                dialog.dismiss()

            ui.page.once("dialog", dismiss)
            ui.page.locator("#start-run-btn").click()
            ui.page.wait_for_timeout(500)  # a POST, had one been sent, would have landed by now
            self.assertEqual(len(asked), 1, "Start run did not ask for confirmation")
            self.assertEqual(asked[0][0], "confirm")
            self.assertIn(CONFIRM_START, asked[0][1])
            self.assertIn("(3 tasks)", asked[0][1])
            self.assertEqual(ui.start_posts, [])
        self.assertEqual(self.launcher.calls, [])

    def test_confirmed_click_calls_start_once(self):
        with self.open_runs("#start-run-btn") as ui:
            ui.page.locator('#start-run-body .start-phase-box[value="2"]').uncheck()
            ui.page.locator("#start-max-tasks").fill("3")
            asked = []

            def accept(dialog):
                asked.append((dialog.type, dialog.message))
                dialog.accept()

            ui.page.once("dialog", accept)
            with ui.page.expect_response(lambda r: r.url.endswith("/api/run/start"), timeout=TIMEOUT_MS) as res:
                ui.page.locator("#start-run-btn").click()
            self.assertEqual(res.value.status, 202, res.value.text())
            self.assertEqual(res.value.json().get("pid"), 4242)
            self.assertEqual(res.value.json().get("run_args"), "--no-converge --phases 1 --max-tasks 3")
            self.assertEqual(len(asked), 1, "Start run did not ask for confirmation")
            self.assertEqual(asked[0][0], "confirm")
            self.assertIn(CONFIRM_START + " phase 1 (2 tasks)", asked[0][1])
            ui.page.locator("#toasts .toast", has_text="Run started").wait_for(state="visible", timeout=TIMEOUT_MS)
            ui.page.wait_for_timeout(500)  # a second POST, had one been sent, would have landed by now
            self.assertEqual(ui.start_posts, [{"phases": ["1"], "no_converge": True, "max_tasks": 3}])
        self.assertEqual(self.launcher.calls, [{"project": self.project, "feature": self.feature, "phases": ["1"],
                                                "no_converge": True, "max_tasks": 3}])


class CannotStartTest(StartRunTestCase):
    GIT = False

    def test_panel_shows_the_reason_and_no_button(self):
        with self.open_runs(".start-reason") as ui:
            reason = ui.page.locator("#start-run-body .start-reason").inner_text()
            self.assertIn("Cannot start: " + NOT_GIT_REASON, reason)
            self.assertEqual(ui.page.locator("#start-run-btn").count(), 0)
            self.assertEqual(ui.page.locator("#start-run-body .start-phase-box").count(), 0)
            self.assertIn("%d runnable, %d held." % (sum(PHASES.values()), HELD),
                          ui.page.locator("#start-run-body .start-summary").inner_text())
            self.assertEqual(ui.start_posts, [])
        self.assertEqual(self.launcher.calls, [])


if __name__ == "__main__":
    unittest.main()
