"""Browser tests of the run banner (FR-029, data-model.md §12.4): which buttons show for each
supervisor state, and what a click writes to `control.json`.

No real supervisor runs. `FakeSupervisor` writes the `status.json` that `bin/sdd-supervise.py`
would write (its `pid` is this test process, so `supervisor_alive()` sees a live one) into the
state directory that `state_dir()` gives for the toy project, under a temporary `XDG_STATE_HOME`
that the dashboard process inherits. It reads back the `control.json` the dashboard writes and
deletes it between tests, as the supervisor would.

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
            cls.dashboard = harness.start_dashboard(project)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._tmp.cleanup()

    def setUp(self):
        self.supervisor.clear_control()
        self.supervisor.remove_status()

    @contextlib.contextmanager
    def open_page(self):
        """The dashboard's Overview; returns once the run banner is visible."""
        with harness.browser_page(self.dashboard.url, name=self.id()) as ui:
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


class PausedBannerTestCase(BannerTestCase):
    """The states with one enabled control: clicking it sends one POST and writes one command."""

    def click_and_read(self, ui, label, command):
        """Click the enabled `label` button, wait for its `POST /api/run/<command>` answer and
        return the `control.json` it wrote."""
        btn = self.button(ui, label)
        btn.wait_for(state="visible", timeout=TIMEOUT_MS)
        self.assertEqual(btn.count(), 1)
        self.assertTrue(btn.is_enabled(), f"{label} is disabled")
        with ui.page.expect_response(lambda r: r.url.endswith("/api/run/" + command), timeout=TIMEOUT_MS) as res:
            btn.click()
        self.assertEqual(res.value.status, 202, res.value.text())
        control = self.supervisor.read_control()
        self.assertIsNotNone(control, f"{label} wrote no control.json")
        self.assertEqual(control.get("command"), command)
        self.assertNotIn("mode", control)
        self.assertEqual(control.get("id"), res.value.json().get("id"))
        return control

    def enabled_labels(self, ui):
        buttons = ui.page.locator("#run-banner button[data-control]")
        return [buttons.nth(i).inner_text().strip() for i in range(buttons.count()) if buttons.nth(i).is_enabled()]


class HeldBannerTest(PausedBannerTestCase):
    def setUp(self):
        super().setUp()
        self.supervisor.write_status("held", cycle=1)

    def test_held_shows_resume_and_writes_resume(self):
        with self.open_page() as ui:
            self.button(ui, "Resume").wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertIn("Stopped by you", ui.page.locator("#run-banner .run-banner-text").inner_text())
            self.assertEqual(ui.page.locator("#run-banner button[data-control]").count(), 1)
            self.assertEqual(self.button(ui, "Stop now").count(), 0)
            self.click_and_read(ui, "Resume", "resume")


class WaitingBannerTest(PausedBannerTestCase):
    def setUp(self):
        super().setUp()
        self.resets_at = int(time.time()) + 3600
        self.reset_hhmm = time.strftime("%H:%M", time.localtime(self.resets_at))

    def test_waiting_shows_keep_paused_and_writes_hold(self):
        self.supervisor.write_status("waiting", cycle=1, resets_at=self.resets_at)
        with self.open_page() as ui:
            self.button(ui, "Keep paused").wait_for(state="visible", timeout=TIMEOUT_MS)
            text = ui.page.locator("#run-banner .run-banner-text").inner_text()
            self.assertIn("Waiting for credits until " + self.reset_hhmm, text)
            self.assertEqual(self.enabled_labels(ui), ["Keep paused"])
            self.assertEqual(self.button(ui, "Let it resume").count(), 0)
            self.click_and_read(ui, "Keep paused", "hold")

    def test_waiting_kept_paused_shows_let_it_resume_and_writes_release(self):
        self.supervisor.write_status("waiting", cycle=1, resets_at=self.resets_at, keep_paused=True)
        with self.open_page() as ui:
            self.button(ui, "Let it resume").wait_for(state="visible", timeout=TIMEOUT_MS)
            text = ui.page.locator("#run-banner .run-banner-text").inner_text()
            self.assertIn("Will stay paused after the reset at " + self.reset_hhmm, text)
            self.assertEqual(self.enabled_labels(ui), ["Let it resume"])
            self.assertEqual(self.button(ui, "Keep paused").count(), 0)
            self.click_and_read(ui, "Let it resume", "release")


class PollingBannerTest(BannerTestCase):
    """The banner follows `status.json` by polling (`setInterval(refreshAll, 3000)` in `build_html()`):
    a supervisor that goes from `running` to `held` shows as held within 5 seconds, and every banner
    the page draws on the way is either the running one or the held one, never an error."""

    FOLLOW_MS = 5000  # FR-029: the banner follows the supervisor within 5 seconds
    HELD_TEXT = "Stopped by you"
    RUNNING_PREFIX = "Working on"

    # Records the banner after every change to it (and once now): shown or hidden, its text, whether
    # an error or a reason line is in it, and its button labels. The stale-run banner is recorded too.
    WATCH_BANNER = """() => {
      window.__bannerSeen = [];
      const banner = document.getElementById('run-banner');
      const stale = document.getElementById('stale-banner');
      const snap = () => {
        const text = banner.querySelector('.run-banner-text');
        window.__bannerSeen.push({
          shown: banner.style.display !== 'none',
          text: text ? text.textContent.trim() : '',
          error: !!banner.querySelector('.run-banner-error, [role=alert]'),
          reason: !!banner.querySelector('.run-banner-reason'),
          stale: !!stale && stale.style.display !== 'none',
          buttons: Array.from(banner.querySelectorAll('button[data-control]')).map(b => b.textContent.trim()),
        });
      };
      new MutationObserver(snap).observe(banner, {childList: true, subtree: true, characterData: true, attributes: true});
      if (stale) new MutationObserver(snap).observe(stale, {childList: true, attributes: true});
      snap();
    }"""

    def setUp(self):
        super().setUp()
        self.supervisor.write_status("running", cycle=1)

    def test_banner_follows_running_to_held_within_5_seconds(self):
        with self.open_page() as ui:
            self.button(ui, "Stop now").wait_for(state="visible", timeout=TIMEOUT_MS)
            ui.page.evaluate(self.WATCH_BANNER)
            self.supervisor.write_status("held", cycle=1)
            started = time.monotonic()
            ui.page.wait_for_function(
                "text => { const t = document.querySelector('#run-banner .run-banner-text');"
                " return !!t && t.textContent.trim() === text; }",
                arg=self.HELD_TEXT, timeout=self.FOLLOW_MS)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, self.FOLLOW_MS / 1000, f"the banner took {elapsed:.1f} s to show held")
            self.assertEqual(self.button(ui, "Resume").all_inner_texts(), ["Resume"])
            self.assertEqual(self.button(ui, "Stop now").count(), 0)

            seen = ui.page.evaluate("() => window.__bannerSeen")
            self.assertTrue(seen, "the banner watcher recorded nothing")
            for snap in seen:
                self.assertTrue(snap["shown"], f"the banner was hidden on the way to held: {seen}")
                self.assertFalse(snap["error"], f"the banner showed an error on the way to held: {snap}")
                self.assertFalse(snap["reason"], f"the banner showed a disabled-control reason: {snap}")
                self.assertFalse(snap["stale"], f"the stale-run banner showed on the way to held: {snap}")
                if snap["text"] == self.HELD_TEXT:
                    self.assertEqual(snap["buttons"], ["Resume"], snap)
                else:
                    self.assertTrue(snap["text"].startswith(self.RUNNING_PREFIX),
                                    f"the banner showed neither running nor held: {snap}")
                    self.assertEqual(snap["buttons"], ["Stop after current task", "Stop now"], snap)
            held_from = next(i for i, s in enumerate(seen) if s["text"] == self.HELD_TEXT)
            self.assertTrue(all(s["text"] == self.HELD_TEXT for s in seen[held_from:]),
                            f"the banner went back to running after held: {seen}")


class NoSupervisorBannerTest(BannerTestCase):
    """A `.sdd/run.json` and no supervisor `status.json`: the banner shows the run, every control
    is disabled, and the reason (`NO_SUPERVISOR` in `bin/sdd-dashboard.py`) is shown."""

    RUN_ID = "20261030-0000"

    def setUp(self):
        super().setUp()
        self.run_file = self.dashboard.project / ".sdd" / "run.json"
        self.run_file.write_text(json.dumps({"run_id": self.RUN_ID}), encoding="utf-8")

    def tearDown(self):
        with contextlib.suppress(FileNotFoundError):
            self.run_file.unlink()
        super().tearDown()

    def test_no_supervisor_disables_every_control_and_shows_the_reason(self):
        with self.open_page() as ui:
            buttons = ui.page.locator("#run-banner button[data-control]")
            buttons.first.wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertGreater(buttons.count(), 0)
            for i in range(buttons.count()):
                label = buttons.nth(i).inner_text().strip()
                self.assertFalse(buttons.nth(i).is_enabled(), f"{label} is enabled without a supervisor")
                self.assertIn("no supervisor is running", buttons.nth(i).get_attribute("title") or "")
            reason = ui.page.locator("#run-banner .run-banner-reason")
            reason.wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertIn("no supervisor is running", reason.inner_text())
            self.assertIn("python3 bin/sdd-supervise.py", reason.locator("code").inner_text())
            buttons.first.click(force=True)  # a disabled button sends nothing
            ui.page.wait_for_timeout(500)
            self.assertIsNone(self.supervisor.read_control(), "a disabled control wrote control.json")


if __name__ == "__main__":
    unittest.main()
