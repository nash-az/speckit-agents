"""Browser test of the phase roll-up (FR-004, data-model.md §1, T062 answer (b)): the dropped statuses
`wontfix`, `moved` and `superseded` rank after `done`, so a phase of done plus dropped tasks shows
`done` in its heading, while a phase of only dropped tasks still shows the drop.

The toy has its own `tasks.md` and `BLOCKED.md`; the expected status of each phase is fixed here and
checked against both the API's phase `status` (Python `ROLLUP_ORDER`) and the heading badge the page
draws (its JavaScript copy, recomputed over the merged states).

The page loads Google Fonts from the network (`research.md` `## First browser run`, fixed by T065);
`open_tasks()` answers that stylesheet with an empty one, as `test_views.py` does.

Run it with `python3 -m unittest discover -s tests/ui`. It skips without Playwright or a Chromium;
`SDD_UI_TESTS=1` makes that a failure. Not part of the default `checks.commands`.
"""
import contextlib
import importlib.util
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_ui_harness", HERE / "harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

TIMEOUT_MS = 10000
WEB_FONTS = "https://fonts.googleapis.com/**"  # the known finding above; remove with T065

TASKS = """# Tasks

## Phase 1: Done and won't fix

- [x] T001 Built in `a.py`
- [x] T002 Built in `b.py`
- [ ] T003 Dropped in `c.py`

## Phase 2: Done, moved and superseded

- [x] T004 Built in `d.py`
- [ ] T005 Moved elsewhere in `e.py`
- [ ] T006 Old approach, superseded-by T004

## Phase 3: Only dropped

- [ ] T007 Dropped in `g.py`

## Phase 4: Done and open

- [x] T008 Built in `h.py`
- [ ] T009 Still to build in `i.py`
"""

BLOCKED = """# Blocked tasks

## T003
- status: wontfix
- reason: not needed

## T005
- status: moved
- reason: product work

## T007
- status: wontfix
- reason: not needed either
"""

# Phase titles as the API gives them (without the "Phase N:" prefix) → the expected roll-up.
EXPECTED = {
    "Done and won't fix": "done",
    "Done, moved and superseded": "done",
    "Only dropped": "wontfix",
    "Done and open": "queued",
}


def build_rollup_project(root):
    """The harness toy project with this module's tasks.md and BLOCKED.md."""
    root = harness.build_toy_project(root)
    feature = root / "specs" / "001-only"
    (feature / "tasks.md").write_text(TASKS, encoding="utf-8")
    (feature / "BLOCKED.md").write_text(BLOCKED, encoding="utf-8")
    return root


class PhaseRollupBrowserTest(harness.UITestCase):
    @classmethod
    def setUpClass(cls):
        harness.require_browser()
        cls._tmp = tempfile.TemporaryDirectory(prefix="sdd-ui-rollup-")
        project = build_rollup_project(Path(cls._tmp.name) / "toy")
        cls.dashboard = harness.Dashboard(project).start()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._tmp.cleanup()

    def api(self, path):
        with urllib.request.urlopen(self.dashboard.url + path.lstrip("/"), timeout=10) as res:
            return json.loads(res.read().decode("utf-8"))

    @contextlib.contextmanager
    def open_tasks(self):
        with harness.browser_page(self.dashboard.url + "api/features", name=self.id()) as ui:
            ui.page.route(WEB_FONTS, lambda route: route.fulfill(status=200, content_type="text/css", body=""))
            ui.page.goto(self.dashboard.url + "#tasks")
            ui.page.locator("#tasks-list .phase-heading").first.wait_for(state="visible", timeout=TIMEOUT_MS)
            yield ui
            ui.page.wait_for_load_state("networkidle")

    def test_api_phase_status(self):
        got = {p["title"]: p["status"] for p in self.api("/api/project/tasks")["phases"]}
        self.assertEqual(got, EXPECTED)

    def test_phase_headings_show_done_for_done_plus_dropped(self):
        with self.open_tasks() as ui:
            headings = ui.page.locator("#tasks-list .phase-heading")
            shown = {}
            for heading in headings.all():
                title = heading.locator("span").first.text_content().strip()
                badge = heading.locator(".status")
                self.assertEqual(badge.count(), 1, "no status badge on %r" % title)
                keys = [c[len("status-"):] for c in (badge.get_attribute("class") or "").split()
                        if c.startswith("status-") and c != "status-label"]
                shown[title] = keys[0] if keys else None
                self.assertTrue(badge.locator(".status-label").text_content().strip(), title)
        self.assertEqual(shown, EXPECTED)


if __name__ == "__main__":
    unittest.main()
