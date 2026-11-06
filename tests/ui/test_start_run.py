"""Browser tests of the Start run panel in the Runs view (009 FR-022; 005 FR-003, FR-011; it replaced 003's
Start run panel, FR-034): it lists the runnable phases with their task counts, shows the reason when a run cannot
start, asks for confirmation, and a confirmed click calls `POST /api/jobs/start` once.

No Job starts. The dashboard runs in this test process (on an OS-chosen port, in a thread) so that
`sdd_launch.launch_job` can be replaced by `RecordingLauncher`, a stub that records its arguments and
answers as the real one would. Its dry runs (what `GET /api/jobs/plan` asks) go to the real
`launch_job`, so the panel shows what the plan says for the toy: a clean git repository can start, a
project that is not a git repository cannot. The supervisor's state directory is under a temporary
`XDG_STATE_HOME`.

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
CONFIRM_START = "Start the run"


def build_toy(root, git):
    """A one-feature toy (`specs/001-only/tasks.md`, `.sdd/config.toml`); with `git`, a clean repository."""
    root = Path(root)
    feature = root / "specs" / "001-only"
    feature.mkdir(parents=True)
    (feature / "tasks.md").write_text(TASKS, encoding="utf-8")
    (root / ".sdd").mkdir(parents=True, exist_ok=True)
    (root / ".sdd" / "config.toml").write_text("[run]\nmax_retries = 2\n", encoding="utf-8")
    (root / ".gitignore").write_text(".sdd/\n", encoding="utf-8")
    if git:
        identity = ["-c", "user.name=sdd-ui", "-c", "user.email=sdd-ui@example.invalid", "-c", "commit.gpgsign=false"]
        for args in (["init", "-q"], ["add", "-A"], identity + ["commit", "-q", "-m", "toy"]):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


class RecordingLauncher:
    """Stands in for `sdd_launch.launch_job`: records each real call's arguments and starts nothing. Dry runs
    (the plan's "can it start?") go to the real function, which changes nothing."""

    def __init__(self, real):
        self.real = real
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, project, feature, scope, limits, title=None, dry_run=False):
        if dry_run:
            return self.real(project, feature, scope, limits, title, dry_run=True)
        with self.lock:
            self.calls.append({"project": Path(project), "feature": Path(feature), "scope": scope,
                               "limits": limits, "title": title})
        return {"job": "20261031-1012", "branch": "auto/20261031-1012", "pid": 4242}

    def reset(self):
        with self.lock:
            self.calls.clear()


class StartJobTestCase(unittest.TestCase):
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
        cls.launcher = RecordingLauncher(sdd_dashboard.sdd_launch.launch_job)
        cls._launch = mock.patch.object(sdd_dashboard.sdd_launch, "launch_job", cls.launcher)
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
    def open_jobs(self, element, hash_="#runs"):
        """The dashboard at `#runs`; returns once `element` in the Start run panel is visible. Every
        `POST /api/jobs/start` the page sends is in `ui.start_posts`."""
        with harness.browser_page(self.url + hash_, name=self.id()) as ui:
            ui.start_posts = []
            ui.page.on("request", lambda r: ui.start_posts.append(r.post_data_json)
                       if r.method == "POST" and r.url.endswith("/api/jobs/start") else None)
            ui.page.locator("#section-runs").wait_for(state="visible", timeout=TIMEOUT_MS)
            ui.page.locator("#start-job-body " + element).first.wait_for(state="visible", timeout=TIMEOUT_MS)
            yield ui
            ui.page.wait_for_load_state("networkidle")


class CanStartTest(StartJobTestCase):
    def phase_rows(self, ui):
        boxes = ui.page.locator("#start-job-body .start-phase")
        return {boxes.nth(i).locator(".start-phase-box").get_attribute("value"):
                boxes.nth(i).locator(".start-count").inner_text() for i in range(boxes.count())}

    def test_panel_lists_runnable_phases_and_counts(self):
        plan = sdd_dashboard.job_plan(self.project, self.feature)
        self.assertTrue(plan["can_start"], plan["reason"])
        self.assertEqual({p["id"]: p["runnable"] for p in plan["phases"]}, PHASES)
        with self.open_jobs("#start-job-btn") as ui:
            self.assertEqual(self.phase_rows(ui), {"1": "2 tasks", "2": "1 task"})
            self.assertIn("Phase 1: Setup", ui.page.locator("#start-job-body .start-phase").first.inner_text())
            boxes = ui.page.locator("#start-job-body .start-phase-box")
            self.assertTrue(all(boxes.nth(i).is_checked() for i in range(boxes.count())))
            summary = ui.page.locator("#start-job-body .start-summary").inner_text()
            self.assertIn("%d runnable, %d held." % (sum(PHASES.values()), HELD), summary)
            self.assertEqual(ui.page.locator("#start-job-body .start-reason").count(), 0)
            for field in ("#start-max-tasks", "#start-until", "#start-max-runs", "#start-title"):
                self.assertEqual(ui.page.locator(field).count(), 1, field)
            self.assertTrue(ui.page.locator("#start-job-btn").is_enabled())
        self.assertEqual(self.launcher.calls, [])  # opening the panel starts nothing

    def test_jobs_hash_redirects_to_runs(self):
        """009 FR-022: the view is Runs (`#runs`); the old `#jobs` redirects to it."""
        with self.open_jobs("#start-job-btn", hash_="#jobs") as ui:
            self.assertTrue(ui.page.url.endswith("#runs"), ui.page.url)
            self.assertEqual(ui.page.locator("#page-title").inner_text().strip(), "Runs")
            self.assertEqual(ui.page.locator("#start-job-btn").inner_text().strip(), "Start run")

    def test_dismissed_confirmation_starts_nothing(self):
        with self.open_jobs("#start-job-btn") as ui:
            asked = []

            def dismiss(dialog):
                asked.append((dialog.type, dialog.message))
                dialog.dismiss()

            ui.page.once("dialog", dismiss)
            ui.page.locator("#start-job-btn").click()
            ui.page.wait_for_timeout(500)  # a POST, had one been sent, would have landed by now
            self.assertEqual(len(asked), 1, "Start job did not ask for confirmation")
            self.assertEqual(asked[0][0], "confirm")
            self.assertIn(CONFIRM_START + ' "Phases 1–2" (3 tasks)', asked[0][1])
            self.assertEqual(ui.start_posts, [])
        self.assertEqual(self.launcher.calls, [])

    def test_confirmed_click_calls_start_once(self):
        with self.open_jobs("#start-job-btn") as ui:
            ui.page.locator('#start-job-body .start-phase-box[value="2"]').uncheck()
            ui.page.locator("#start-max-tasks").fill("3")
            asked = []

            def accept(dialog):
                asked.append((dialog.type, dialog.message))
                dialog.accept()

            ui.page.once("dialog", accept)
            with ui.page.expect_response(lambda r: r.url.endswith("/api/jobs/start"), timeout=TIMEOUT_MS) as res:
                ui.page.locator("#start-job-btn").click()
            self.assertEqual(res.value.status, 202, res.value.text())
            self.assertEqual(res.value.json(), {"ok": True, "job": "20261031-1012"})
            self.assertEqual(len(asked), 1, "Start job did not ask for confirmation")
            self.assertEqual(asked[0][0], "confirm")
            self.assertIn(CONFIRM_START + ' "Phase 1" (2 tasks)', asked[0][1])
            ui.page.locator("#toasts .toast", has_text="Run started").wait_for(state="visible", timeout=TIMEOUT_MS)
            ui.page.wait_for_timeout(500)  # a second POST, had one been sent, would have landed by now
            self.assertEqual(ui.start_posts, [{"phases": ["1"], "max_tasks": 3}])
        self.assertEqual(self.launcher.calls, [{"project": self.project, "feature": self.feature,
                                                "scope": {"phases": ["1"], "tasks": []},
                                                "limits": {"max_tasks": 3}, "title": None}])

    def test_tasks_scope_and_title(self):
        with self.open_jobs("#start-job-btn") as ui:
            ui.page.locator("#start-none-btn").click()
            for phase in ("1", "2"):  # open each phase to reach its tasks
                ui.page.locator('#start-job-body .start-phase[data-phase="%s"] summary .start-count' % phase).click()
            for task in ("T001", "T005"):
                ui.page.locator('#start-job-body .start-task-box[value="%s"]' % task).check()
            ui.page.locator("#start-title").fill("Two tasks")
            ui.page.once("dialog", lambda dialog: dialog.accept())
            with ui.page.expect_response(lambda r: r.url.endswith("/api/jobs/start"), timeout=TIMEOUT_MS) as res:
                ui.page.locator("#start-job-btn").click()
            self.assertEqual(res.value.status, 202, res.value.text())
            self.assertEqual(ui.start_posts, [{"tasks": ["T001", "T005"], "title": "Two tasks"}])
        self.assertEqual([c["scope"] for c in self.launcher.calls], [{"phases": [], "tasks": ["T001", "T005"]}])

    def test_select_all_and_none_and_phase_expansion(self):
        with self.open_jobs("#start-job-btn") as ui:
            page = ui.page
            phase = page.locator('#start-job-body .start-phase[data-phase="1"]')
            self.assertFalse(phase.evaluate("d => d.open"))
            phase.locator("summary .start-count").click()
            self.assertTrue(phase.evaluate("d => d.open"))
            tasks = phase.locator(".start-task-box")
            self.assertEqual(tasks.count(), PHASES["1"])
            page.locator("#start-none-btn").click()
            self.assertEqual(page.locator("#start-job-body input:checked").count(), 0)
            page.locator("#start-all-btn").click()
            boxes = page.locator("#start-job-body .start-phase-box, #start-job-body .start-task-box")
            self.assertTrue(all(boxes.nth(i).is_checked() for i in range(boxes.count())))
            tasks.first.uncheck()  # one task off: its phase box shows mixed, not ticked
            self.assertTrue(phase.locator(".start-phase-box").evaluate("b => b.indeterminate && !b.checked"))
            phase.locator(".start-phase-box").check()  # the phase box ticks all its tasks again
            self.assertTrue(all(tasks.nth(i).is_checked() for i in range(tasks.count())))
            self.assertEqual(page.locator("#start-job-body #start-tasks").count(), 0)


class CannotStartTest(StartJobTestCase):
    GIT = False

    def test_panel_shows_the_reason_and_no_button(self):
        with self.open_jobs(".start-reason") as ui:
            reason = ui.page.locator("#start-job-body .start-reason").inner_text()
            self.assertIn("Cannot start: " + NOT_GIT_REASON, reason)
            self.assertEqual(ui.page.locator("#start-job-btn").count(), 0)
            self.assertEqual(ui.page.locator("#start-job-body .start-phase-box").count(), 0)
            self.assertIn("%d runnable, %d held." % (sum(PHASES.values()), HELD),
                          ui.page.locator("#start-job-body .start-summary").inner_text())
            self.assertEqual(ui.start_posts, [])
        self.assertEqual(self.launcher.calls, [])


if __name__ == "__main__":
    unittest.main()
