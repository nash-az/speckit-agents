"""Browser tests of the run banner (FR-029, data-model.md §12.4): which buttons show for each
supervisor state, and what a click writes to `control.json`.

No real supervisor runs. `FakeSupervisor` writes the `status.json` that `bin/sdd-supervise.py`
would write (its `pid` is this test process, so `supervisor_alive()` sees a live one) into the
state directory that `state_dir()` gives for the toy project, under a temporary `XDG_STATE_HOME`
that the dashboard process inherits. It reads back the `control.json` the dashboard writes and
deletes it between tests, as the supervisor would.

The page loads Google Fonts from the network (`research.md` `## First browser run`, fixed by T065);
`open_page()` answers that stylesheet with an empty one, as `test_views.py` does.

Run it with `python3 -m unittest discover -s tests/ui`. It skips without Playwright or a Chromium;
`SDD_UI_TESTS=1` makes that a failure. Not part of the default `checks.commands`.
"""
import contextlib
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_ui_harness", HERE / "harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

_spec = importlib.util.spec_from_file_location("sdd_supervise", harness.ROOT / "bin" / "sdd-supervise.py")
sdd_supervise = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sdd_supervise)

TIMEOUT_MS = 10000
WEB_FONTS = "https://fonts.googleapis.com/**"  # the known finding above; remove with T065
STOP_NOW_QUESTION = "Stop the run now?"


class FakeSupervisor:
    """The supervisor's side of the state directory: `status.json` written, `control.json` read."""

    def __init__(self, state_home, project):
        self.state_home = Path(state_home)
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state_home)}):
            self.dir = sdd_supervise.state_dir(Path(project))
        self.status_file = self.dir / "status.json"
        self.control_file = self.dir / "control.json"

    def write_status(self, state, **fields):
        """`status.json` as `write_status()` in `bin/sdd-supervise.py` writes it, with a live `pid`."""
        status = {"state": state, **fields, "pid": os.getpid(), "updated": int(time.time())}
        tmp = self.dir / "status.json.tmp"
        tmp.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.status_file)

    def remove_status(self):
        with contextlib.suppress(FileNotFoundError):
            self.status_file.unlink()

    def read_control(self):
        """The pending request as a dict, or None when the dashboard wrote none."""
        try:
            return json.loads(self.control_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def clear_control(self):
        with contextlib.suppress(FileNotFoundError):
            self.control_file.unlink()


class BannerTestCase(harness.UITestCase):
    """One dashboard on the harness toy project, started with `XDG_STATE_HOME` in a temp dir."""

    @classmethod
    def setUpClass(cls):
        harness.require_browser()
        cls._tmp = tempfile.TemporaryDirectory(prefix="sdd-ui-banner-")
        base = Path(cls._tmp.name)
        project = harness.build_toy_project(base / "toy")
        cls.supervisor = FakeSupervisor(base / "state", project)
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(cls.supervisor.state_home)}):
            cls.dashboard = harness.Dashboard(project).start()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._tmp.cleanup()

    def setUp(self):
        self.supervisor.clear_control()
        self.supervisor.remove_status()

    @contextlib.contextmanager
    def open_page(self):
        """The dashboard's Overview with the web-font stylesheet answered empty; returns once the
        run banner is visible. The harness opens a same-origin JSON page first, so the route is in
        place before the dashboard's own first load."""
        with harness.browser_page(self.dashboard.url + "api/features", name=self.id()) as ui:
            ui.page.route(WEB_FONTS, lambda route: route.fulfill(status=200, content_type="text/css", body=""))
            ui.page.goto(self.dashboard.url)
            ui.page.locator("#run-banner").wait_for(state="visible", timeout=TIMEOUT_MS)
            yield ui
            ui.page.wait_for_load_state("networkidle")

    @staticmethod
    def button(ui, label):
        return ui.page.locator("#run-banner button[data-control]", has_text=label)


class RunningBannerTest(BannerTestCase):
    def setUp(self):
        super().setUp()
        self.supervisor.write_status("running", cycle=1)

    def test_running_shows_both_stop_buttons(self):
        with self.open_page() as ui:
            after = self.button(ui, "Stop after current task")
            now = self.button(ui, "Stop now")
            after.wait_for(state="visible", timeout=TIMEOUT_MS)
            now.wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertEqual(after.count(), 1)
            self.assertEqual(now.count(), 1)
            self.assertTrue(after.is_enabled(), "Stop after current task is disabled while running")
            self.assertTrue(now.is_enabled(), "Stop now is disabled while running")
            self.assertEqual(after.get_attribute("data-mode"), "after-task")
            self.assertEqual(now.get_attribute("data-mode"), "now")
            self.assertEqual(ui.page.locator("#run-banner button[data-control]").count(), 2)

    def test_stop_now_dismissed_writes_nothing(self):
        with self.open_page() as ui:
            now = self.button(ui, "Stop now")
            now.wait_for(state="visible", timeout=TIMEOUT_MS)
            asked = []

            def dismiss(dialog):
                asked.append((dialog.type, dialog.message))
                dialog.dismiss()

            ui.page.once("dialog", dismiss)
            now.click()
            ui.page.wait_for_timeout(500)  # a POST, had one been sent, would have landed by now
            self.assertEqual(len(asked), 1, "Stop now did not ask for confirmation")
            self.assertEqual(asked[0][0], "confirm")
            self.assertIn(STOP_NOW_QUESTION, asked[0][1])
            self.assertIsNone(self.supervisor.read_control(), "a dismissed Stop now wrote control.json")

    def test_stop_now_accepted_writes_stop_now(self):
        with self.open_page() as ui:
            now = self.button(ui, "Stop now")
            now.wait_for(state="visible", timeout=TIMEOUT_MS)
            asked = []

            def accept(dialog):
                asked.append((dialog.type, dialog.message))
                dialog.accept()

            ui.page.once("dialog", accept)
            with ui.page.expect_response(lambda r: r.url.endswith("/api/run/stop"), timeout=TIMEOUT_MS) as res:
                now.click()
            self.assertEqual(res.value.status, 202, res.value.text())
            self.assertEqual(len(asked), 1, "Stop now did not ask for confirmation")
            self.assertEqual(asked[0][0], "confirm")
            self.assertIn(STOP_NOW_QUESTION, asked[0][1])
            control = self.supervisor.read_control()
            self.assertIsNotNone(control, "a confirmed Stop now wrote no control.json")
            self.assertEqual({k: control.get(k) for k in ("command", "mode")}, {"command": "stop", "mode": "now"})
            self.assertEqual(control.get("id"), res.value.json().get("id"))


if __name__ == "__main__":
    unittest.main()
