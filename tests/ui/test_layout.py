"""Browser test for the dashboard layout: no view is wider than the window, on a desktop or a phone.

A task card with an open log once grew past its column (the log's lines sized the task grid), and at
phone width the sidebar filled the first screen. Run it with `python3 -m unittest tests/ui/test_layout.py`;
it skips without Playwright or a Chromium, like the other browser tests.
"""
import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_ui_harness", HERE / "harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

TIMEOUT_MS = 10000
WIDTHS = (1700, 1024, 390)
SECTIONS = ("overview", "docs", "tasks", "blocked", "decisions", "config", "jobs", "status")

# A log panel under the first task card, as a running task's live output looks (liveOutputHtml()), holding
# a line that cannot wrap (an unbroken 2500px box stands in for whatever a real log holds). The log must
# scroll inside the card; the card must stay in its column.
ADD_LONG_LOG = """() => {
  const card = document.querySelector('#section-tasks .task-card');
  card.insertAdjacentHTML('beforeend', '<div class="live-output"><div class="live-output-title">Live output</div>' +
    '<div class="live-output-body"><pre class="live-output-lines">short line\\n' +
    '<span style="display: inline-block; width: 2500px">unbreakable</span></pre></div></div>');
}"""

# Elements whose right edge is past the window, outside a box that scrolls on purpose (pre, the nav row).
WIDER_THAN_WINDOW = """() => {
  const vw = document.documentElement.clientWidth, out = [];
  document.querySelectorAll('.app *').forEach(e => {
    if (e.closest('pre, .nav, .events-list')) return;
    const r = e.getBoundingClientRect();
    if (r.width && r.right > vw + 1) out.push(e.tagName.toLowerCase() + '.' + String(e.className).split(' ')[0] + ' right=' + Math.round(r.right));
  });
  return {vw: vw, page: document.documentElement.scrollWidth, wide: out.slice(0, 5)};
}"""


class LayoutTest(harness.UITestCase):
    def assert_fits(self, ui, where):
        r = ui.page.evaluate(WIDER_THAN_WINDOW)
        self.assertLessEqual(r["page"], r["vw"], "%s: the page scrolls sideways" % where)
        self.assertEqual(r["wide"], [], "%s: wider than the %spx window" % (where, r["vw"]))

    def test_every_view_fits_the_window(self):
        with harness.browser_page(self.dashboard.url, name=self.id()) as ui:
            for width in WIDTHS:
                ui.page.set_viewport_size({"width": width, "height": 900})
                for section in SECTIONS:
                    ui.page.goto(self.dashboard.url + "#" + section)
                    ui.page.locator(".content section:visible").first.wait_for(timeout=TIMEOUT_MS)
                    ui.page.wait_for_load_state("networkidle")
                    self.assert_fits(ui, "%s at %spx" % (section, width))

    def test_an_open_log_keeps_its_card_in_the_column(self):
        with harness.browser_page(self.dashboard.url + "#tasks", name=self.id()) as ui:
            ui.page.locator("#section-tasks .task-card").first.wait_for(state="visible", timeout=TIMEOUT_MS)
            ui.page.evaluate(ADD_LONG_LOG)
            for width in WIDTHS:
                ui.page.set_viewport_size({"width": width, "height": 900})
                card = ui.page.locator("#section-tasks .task-card").first.bounding_box()
                column = ui.page.locator("#section-tasks .phase-tasks").first.bounding_box()
                self.assertLessEqual(card["x"] + card["width"], column["x"] + column["width"] + 1,
                                     "a card with a long log is wider than its column at %spx" % width)
                self.assert_fits(ui, "tasks with a long log at %spx" % width)

    def test_phone_shows_the_content_on_the_first_screen(self):
        with harness.browser_page(self.dashboard.url + "#tasks", name=self.id()) as ui:
            ui.page.set_viewport_size({"width": 390, "height": 800})
            ui.page.locator("#section-tasks").wait_for(state="visible", timeout=TIMEOUT_MS)
            top = ui.page.locator(".content").bounding_box()["y"]
            self.assertLess(top, 200, "on a phone the content starts %dpx down, below the nav" % top)


if __name__ == "__main__":
    unittest.main()
