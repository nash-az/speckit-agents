"""Browser smoke test for the dashboard (FR-031): the page opens cleanly and lists the toy's phases.

Run it with `python3 -m unittest discover -s tests/ui` (or `python3 -m unittest tests/ui/test_smoke.py`).
It skips without Playwright or a Chromium; `SDD_UI_TESTS=1` makes that a failure. Not part of
the default `checks.commands`.
"""
import importlib.util
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_ui_harness", HERE / "harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

TIMEOUT_MS = 10000


def toy_phase_titles():
    """The `## ...` headings of the toy's tasks.md, in file order."""
    return re.findall(r"^## (.+?)\s*$", harness.fixtures.STATUS_TASKS, flags=re.M)


class SmokeTest(harness.UITestCase):
    def test_page_opens_without_errors(self):
        with harness.browser_page(self.dashboard.url, name=self.id()) as ui:
            ui.page.wait_for_load_state("networkidle")

    def test_tasks_view_lists_the_toy_phases(self):
        expected = toy_phase_titles()
        self.assertTrue(expected, "the toy tasks.md has no phases")
        with harness.browser_page(self.dashboard.url + "#tasks", name=self.id()) as ui:
            headings = ui.page.locator("#section-tasks #tasks-list .phase-heading")
            headings.first.wait_for(state="visible", timeout=TIMEOUT_MS)
            shown = [t.strip() for t in headings.all_inner_texts()]
            for title in expected:
                self.assertTrue(any(title in s for s in shown),
                                "phase %r not in the Tasks view headings %r" % (title, shown))
            ui.page.wait_for_load_state("networkidle")


if __name__ == "__main__":
    unittest.main()
