"""Browser test of the status system (SC-002, data-model.md §1): every status key renders with an
icon, a color and a label, and no two keys share the same icon-and-color pair.

The keys come from the dashboard's own `STATUS` object: the page's status code (from
`escapeHtml()` up to `loadProject()`, the slice `tests/test_sdd_dashboard.py` runs in node) is
evaluated in the open page, and each `statusBadge(key, ...)` is inserted into the dashboard's
document, so its real CSS decides the color.

Run it with `python3 -m unittest discover -s tests/ui`. It skips without Playwright or a Chromium;
`SDD_UI_TESTS=1` makes that a failure. Not part of the default `checks.commands`.
"""
import importlib.util
import json
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_ui_harness", HERE / "harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

EXPECTED_COUNT = 10  # data-model.md §1: ten status keys

# Values for the label templates (`Running: {step}`, `Retry {n}/{max}`, `Held: {tag}`, `Superseded by {by}`).
VARS = {"step": "Implementing", "n": 2, "max": 3, "tag": "manual", "by": "T020"}

# Evaluated in the page with the status code in scope; returns the keys and renders one badge per key
# into a fresh container appended to the dashboard's <body>, then measures each badge.
RENDER = """
(function () {
  var keys = Object.keys(STATUS);
  var box = document.createElement('div');
  box.id = 'sdd-status-test';
  document.body.appendChild(box);
  return keys.map(function (key) {
    var holder = document.createElement('div');
    holder.innerHTML = statusBadge(key, VARS);
    box.appendChild(holder);
    var badge = holder.querySelector('.status');
    var svg = badge && badge.querySelector('svg');
    var label = badge && badge.querySelector('.status-label');
    var rect = svg ? svg.getBoundingClientRect() : null;
    return {
      key: key,
      badgeClass: badge ? badge.className : null,
      color: badge ? getComputedStyle(badge).color : null,
      glyph: svg ? (svg.getAttribute('class') || '') : null,
      glyphShapes: svg ? svg.querySelectorAll('path, circle, line, polyline').length : 0,
      glyphStroke: svg ? getComputedStyle(svg).stroke : null,
      glyphSize: rect ? [rect.width, rect.height] : null,
      label: label ? label.textContent : null,
      labelVisible: label ? label.getBoundingClientRect().width > 0 : false
    };
  });
})()
"""


def status_code(url):
    """The dashboard's status functions and objects, as served (escapeHtml() up to loadProject())."""
    with urllib.request.urlopen(url, timeout=10) as res:
        html = res.read().decode("utf-8")
    start = html.index("function escapeHtml(")
    return html[start:html.index("function loadProject(")]


class StatusSystemBrowserTest(harness.UITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.code = status_code(cls.dashboard.url)

    def render_all(self):
        with harness.browser_page(self.dashboard.url, name=self.id()) as ui:
            ui.page.wait_for_load_state("networkidle")
            script = ("(function (VARS) {\n" + self.code + "\nreturn " + RENDER.strip() + ";\n})")
            got = ui.page.evaluate(script + "(" + json.dumps(VARS) + ")")
        return got

    def test_every_status_has_icon_color_and_label(self):
        badges = self.render_all()
        self.assertEqual(len(badges), EXPECTED_COUNT,
                         "STATUS has %d keys, expected %d: %r" % (len(badges), EXPECTED_COUNT,
                                                                  [b["key"] for b in badges]))
        for b in badges:
            with self.subTest(status=b["key"]):
                self.assertIsNotNone(b["badgeClass"], "statusBadge(%r) rendered no .status element" % b["key"])
                self.assertIn("status-" + b["key"], b["badgeClass"].split())
                # icon: a visible inline SVG with drawn shapes, named after its glyph
                self.assertIsNotNone(b["glyph"], "no icon in the %r badge" % b["key"])
                self.assertTrue(b["glyph"].startswith("glyph-"), b["glyph"])
                self.assertGreater(b["glyphShapes"], 0)
                self.assertTrue(b["glyphSize"][0] > 0 and b["glyphSize"][1] > 0, b["glyphSize"])
                # color: set by the dashboard CSS, not the inherited body text color
                self.assertTrue(b["color"], "no color for %r" % b["key"])
                self.assertNotEqual(b["color"], "rgba(0, 0, 0, 0)")
                self.assertEqual(b["glyphStroke"], b["color"], "the icon does not take the badge color")
                # label: visible, non-empty text with the templates filled in
                self.assertTrue(b["label"] and b["label"].strip(), "empty label for %r" % b["key"])
                self.assertNotIn("{", b["label"])
                self.assertTrue(b["labelVisible"], "label of %r is not visible" % b["key"])

    def test_no_two_statuses_share_icon_and_color(self):
        badges = self.render_all()
        seen = {}
        for b in badges:
            pair = (b["glyph"], b["color"])
            self.assertNotIn(pair, seen, "%r and %r share icon and color %r" % (seen.get(pair), b["key"], pair))
            seen[pair] = b["key"]
        self.assertEqual(len(seen), EXPECTED_COUNT)

    def test_colors_differ_from_body_text(self):
        """A status without its own CSS rule would inherit the page text color; none may."""
        with harness.browser_page(self.dashboard.url, name=self.id()) as ui:
            ui.page.wait_for_load_state("networkidle")
            body = ui.page.evaluate("getComputedStyle(document.body).color")
        for b in self.render_all():
            with self.subTest(status=b["key"]):
                self.assertNotEqual(b["color"], body, "%r has no color of its own" % b["key"])


if __name__ == "__main__":
    unittest.main()
