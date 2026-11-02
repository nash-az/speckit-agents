"""Browser test: "Waiting on you" lists only what the person can act on now.

A held [manual] step whose earlier phases are still open (for example "look at the view" before the view is
built) is not doable yet: it shows in its phase as "Not yet" without buttons, and appears in Waiting on you
once every earlier phase is finished. Open tasks in its own phase do not count. Run it with
`python3 -m unittest tests/ui/test_waiting.py`; it skips without Playwright or a Chromium, like the other
browser tests.
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_ui_harness", HERE / "harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

TIMEOUT_MS = 10000

BUILD_OPEN = """# Tasks

## Phase 1: Build

- [ ] T001 Build the view in `view.py`

## Phase 2: Checks by a person

- [ ] T002 [manual] Look at the view in your browser
"""
BUILD_DONE = BUILD_OPEN.replace("- [ ] T001", "- [x] T001")
# A setup step in the middle of a phase: open tasks in its own phase do not make it "not yet".
SAME_PHASE = """# Tasks

## Phase 1: Setup

- [ ] T001 Build the view in `view.py`
- [ ] T002 [manual] Install the browser on your machine
"""


class WaitingTest(harness.UITestCase):
    @classmethod
    def setUpClass(cls):
        harness.require_browser()
        cls._tmp = tempfile.TemporaryDirectory(prefix="sdd-ui-waiting-")
        cls.project = harness.build_toy_project(Path(cls._tmp.name) / "toy")
        cls.tasks = cls.project / "specs" / "001-only" / "tasks.md"
        cls.dashboard = harness.start_dashboard(cls.project)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._tmp.cleanup()

    def open_tasks(self, ui):
        ui.page.goto(self.dashboard.url + "#tasks")
        ui.page.locator('#tasks-list [data-task-card="T002"]').wait_for(state="attached", timeout=TIMEOUT_MS)
        ui.page.wait_for_load_state("networkidle")

    def test_manual_step_waits_until_the_tasks_before_it_are_finished(self):
        self.tasks.write_text(BUILD_OPEN, encoding="utf-8")
        with harness.browser_page(self.dashboard.url, name=self.id()) as ui:
            self.open_tasks(ui)
            self.assertEqual(ui.page.locator("#waiting-count").inner_text().strip(), "0")
            self.assertNotIn("T002", ui.page.locator("#waiting-list").inner_text())
            card = ui.page.locator('#tasks-list [data-task-card="T002"]')
            self.assertIn("Not yet", card.inner_text())
            self.assertEqual(card.locator("button").count(), 0, "a not-yet step has buttons")

        self.tasks.write_text(BUILD_DONE, encoding="utf-8")
        with harness.browser_page(self.dashboard.url, name=self.id()) as ui:
            self.open_tasks(ui)
            self.assertEqual(ui.page.locator("#waiting-count").inner_text().strip(), "1")
            item = ui.page.locator("#waiting-list .blocked-item", has_text="T002")
            self.assertEqual(item.count(), 1)
            self.assertIn("a step you do yourself", item.inner_text())
            self.assertNotIn("Not yet", ui.page.locator('#tasks-list [data-task-card="T002"]').inner_text())

    def test_manual_step_in_the_same_phase_is_doable(self):
        self.tasks.write_text(SAME_PHASE, encoding="utf-8")
        with harness.browser_page(self.dashboard.url, name=self.id()) as ui:
            self.open_tasks(ui)
            self.assertEqual(ui.page.locator("#waiting-count").inner_text().strip(), "1")
            self.assertNotIn("Not yet", ui.page.locator('#tasks-list [data-task-card="T002"]').inner_text())


if __name__ == "__main__":
    unittest.main()
