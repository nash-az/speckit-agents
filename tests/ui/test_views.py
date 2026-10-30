"""Browser tests of the dashboard's main views (FR-001): Tasks, Blocked, Documents and Runs each open
without a console error and show their content.

The toy project is the harness's (`specs/001-only` with the status fixture's tasks.md and BLOCKED.md),
plus a `spec.md` for the Documents view and one run (`.sdd/runs/<id>/workflows.json` pointing at a
`wf_…` journal, the fixture of `tests/test_sdd_dashboard.py`) for the Runs view. What each view must
show is read from the dashboard's own API, so the tests follow the fixtures.

The page loads Google Fonts from the network (`research.md` `## First browser run`, fixed by T065).
Until then `open_view()` answers that one stylesheet with an empty one, so the offline page records
no error for it; every other request and console error still fails the test.

Run it with `python3 -m unittest discover -s tests/ui`. It skips without Playwright or a Chromium;
`SDD_UI_TESTS=1` makes that a failure. Not part of the default `checks.commands`.
"""
import contextlib
import importlib.util
import json
import re
import tempfile
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_ui_harness", HERE / "harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

TIMEOUT_MS = 10000
RUN_ID = "20261030-1200"
WEB_FONTS = "https://fonts.googleapis.com/**"  # the known finding above; remove with T065

SPEC = """# Feature: Toy views

## Summary

The toy feature the browser tests open in the Documents view.
"""


def build_views_project(root):
    """The harness toy project, a spec.md and one finished run."""
    root = harness.build_toy_project(root)
    (root / "specs" / "001-only" / "spec.md").write_text(SPEC, encoding="utf-8")
    wf = harness.fixtures.write_workflow_dir(root.resolve().parent / "transcripts" / "wf_views")
    harness.fixtures.write_run(root, RUN_ID, [wf])
    (root / ".sdd" / "runs" / (RUN_ID + ".md")).write_text("# Run Report\n\n- approved: T1\n", encoding="utf-8")
    return root


def squash(text):
    """Text without whitespace, so a question split into sentences or lines still compares."""
    return re.sub(r"\s+", "", text or "")


class ViewsTest(harness.UITestCase):
    @classmethod
    def setUpClass(cls):
        harness.require_browser()
        cls._tmp = tempfile.TemporaryDirectory(prefix="sdd-ui-views-")
        project = build_views_project(Path(cls._tmp.name) / "toy")
        cls.dashboard = harness.Dashboard(project).start()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._tmp.cleanup()

    def api(self, path):
        with urllib.request.urlopen(self.dashboard.url + path.lstrip("/"), timeout=10) as res:
            return json.loads(res.read().decode("utf-8"))

    @contextlib.contextmanager
    def open_view(self, section, element=None):
        """The dashboard at `#<section>`, with the web-font stylesheet answered empty (see the module doc);
        returns once `element` (default `#section-<section>`) is visible.

        The harness opens a same-origin JSON page first, so the route is in place before the
        dashboard's own first load."""
        with harness.browser_page(self.dashboard.url + "api/features", name=self.id()) as ui:
            ui.page.route(WEB_FONTS, lambda route: route.fulfill(status=200, content_type="text/css", body=""))
            ui.page.goto(self.dashboard.url + "#" + section)
            ui.page.locator(element or "#section-" + section).wait_for(state="visible", timeout=TIMEOUT_MS)
            yield ui
            ui.page.wait_for_load_state("networkidle")

    def test_tasks_view_shows_task_cards_with_a_status_badge(self):
        expected = [t["id"] for p in self.api("/api/project/tasks")["phases"] for t in p["tasks"]]
        self.assertTrue(expected, "the toy has no tasks")
        with self.open_view("tasks") as ui:
            cards = ui.page.locator("#tasks-list .task-card[data-task-card]")
            cards.first.wait_for(state="attached", timeout=TIMEOUT_MS)
            shown = [c.get_attribute("data-task-card") for c in cards.all()]
            self.assertEqual(sorted(shown), sorted(expected))
            for card in cards.all():
                task = card.get_attribute("data-task-card")
                with self.subTest(task=task):
                    badge = card.locator(".task-head .status")
                    self.assertEqual(badge.count(), 1, "no status badge on %s" % task)
                    self.assertTrue(badge.locator("svg").count() == 1, "the %s badge has no icon" % task)
                    self.assertTrue(squash(badge.locator(".status-label").text_content()),
                                    "the %s badge has no label" % task)
            # A card that is not folded away under "done" is on screen with its badge.
            open_card = ui.page.locator("#tasks-list .phase-tasks > .task-card").first
            self.assertTrue(open_card.locator(".status").is_visible())

    def test_blocked_view_shows_entries_with_their_questions(self):
        entries = self.api("/api/project/blocked")["entries"]
        asked = [e for e in entries if e.get("question")]
        self.assertTrue(asked, "the toy BLOCKED.md has no questions")
        with self.open_view("blocked") as ui:
            items = ui.page.locator("#blocked-list .blocked-item")
            items.first.wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertEqual(items.count(), len(entries))
            for i, entry in enumerate(entries):
                item = items.nth(i)
                with self.subTest(entry=entry["id"], index=i):
                    self.assertEqual(item.locator(".blocked-id").text_content().strip(), entry["id"])
                    self.assertEqual(item.locator(".blocked-header .status").count(), 1)
                    if entry.get("question"):
                        question = item.locator(".blocked-question")
                        self.assertTrue(question.is_visible(), "%s shows no question" % entry["id"])
                        self.assertIn(squash(entry["question"]), squash(question.inner_text()))
            # The open entry with a question (T007 in the fixture) is shown as blocked, with its question.
            t007 = items.filter(has=ui.page.locator(".blocked-id", has_text="T007"))
            self.assertEqual(t007.count(), 1)
            self.assertEqual(t007.locator(".status.status-blocked").count(), 1)
            self.assertIn("Which API should it call?", t007.locator(".blocked-question").inner_text())

    def test_documents_view_renders_the_spec(self):
        with self.open_view("spec", "#section-doc") as ui:  # every document shares the doc section
            heading = ui.page.locator("#doc-content h1")
            heading.wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertIn("Feature: Toy views", heading.inner_text())
            self.assertIn("Summary", ui.page.locator("#doc-content h2").first.inner_text())
            self.assertIn("The toy feature the browser tests open", ui.page.locator("#doc-content p").first.inner_text())
            self.assertNotIn("# Feature", ui.page.locator("#doc-content").inner_text(), "the markdown was not rendered")
            self.assertEqual(ui.page.locator("#doc-title").inner_text().strip(), "Spec")

    def test_runs_view_lists_the_runs(self):
        runs = self.api("/api/runs")
        self.assertEqual([r["id"] for r in runs], [RUN_ID])
        with self.open_view("runs") as ui:
            items = ui.page.locator("#runs-list .run-item")
            items.first.wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertEqual(items.count(), len(runs))
            self.assertEqual(items.first.locator(".run-id").inner_text().strip(), "#" + RUN_ID)
            meta = items.first.locator(".run-meta").inner_text()
            self.assertIn("done", meta)  # the run has its report, so it is finished
            self.assertIn("%d approved" % runs[0]["approved"], meta)
            self.assertIn("%d blocked" % runs[0]["blocked"], meta)


if __name__ == "__main__":
    unittest.main()
