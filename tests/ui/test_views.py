"""Browser tests of the dashboard's main views (FR-001): Tasks, Blocked, Documents and Runs each open
without a console error and show their content.

The Documents view also renders a table and highlighted code and drops a `<script>`, and a task
whose text is markup shows as text.

The toy project is the harness's (`specs/001-only` with the status fixture's tasks.md and BLOCKED.md,
plus one task whose text is an `<img onerror=...>`), a `spec.md` for the Documents view and one run (`.sdd/runs/<id>/workflows.json` pointing at a
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

## Formats

| Field | Meaning |
|-------|---------|
| id    | The task id |

```python
def answer():
    return 42
```

<script>window.sddScriptRan = true;</script>
"""

# A task whose text is markup: the page must show it as text, never as an element (FR-001).
MARKUP_TASK_ID = "T014"
MARKUP_TEXT = '<img src="x" onerror="window.sddImgRan = true">'
MARKUP_TASK = "- [ ] %s Show %s as text\n" % (MARKUP_TASK_ID, MARKUP_TEXT)


def build_views_project(root):
    """The harness toy project with a task whose text is markup, a spec.md and one finished run."""
    root = harness.build_toy_project(root)
    feature = root / "specs" / "001-only"
    (feature / "tasks.md").write_text(harness.fixtures.STATUS_TASKS + MARKUP_TASK, encoding="utf-8")
    (feature / "spec.md").write_text(SPEC, encoding="utf-8")
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

    def test_documents_view_renders_tables_and_code_and_drops_scripts(self):
        with self.open_view("spec", "#section-doc") as ui:
            doc = ui.page.locator("#doc-content")
            doc.locator("h1").wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertEqual(doc.locator("h1").count(), 1)
            self.assertEqual([h.inner_text().strip() for h in doc.locator("h2").all()], ["Summary", "Formats"])
            table = doc.locator("table")
            self.assertEqual(table.count(), 1, "the markdown table was not rendered")
            self.assertEqual([c.inner_text().strip() for c in table.locator("th").all()], ["Field", "Meaning"])
            self.assertEqual([c.inner_text().strip() for c in table.locator("td").all()], ["id", "The task id"])
            code = doc.locator("pre code")
            self.assertEqual(code.count(), 1)
            self.assertIn("hljs", code.get_attribute("class") or "", "the code block was not highlighted")
            self.assertGreater(code.locator("span[class^='hljs-']").count(), 0, "the code block has no highlighted tokens")
            self.assertIn("return 42", code.inner_text())
            self.assertEqual(doc.locator("script").count(), 0, "the document's <script> reached the page")
            self.assertIsNone(ui.page.evaluate("window.sddScriptRan"), "the document's <script> ran")
            self.assertNotIn("sddScriptRan", doc.inner_text())

    def test_task_text_with_markup_shows_as_text(self):
        tasks = {t["id"]: t for p in self.api("/api/project/tasks")["phases"] for t in p["tasks"]}
        self.assertIn(MARKUP_TEXT, tasks[MARKUP_TASK_ID]["text"])
        with self.open_view("tasks") as ui:
            card = ui.page.locator('#tasks-list .task-card[data-task-card="%s"]' % MARKUP_TASK_ID)
            card.wait_for(state="visible", timeout=TIMEOUT_MS)
            text = card.locator(".task-text")
            self.assertIn(MARKUP_TEXT, text.inner_text(), "the markup in the task text is not shown as text")
            self.assertEqual(text.locator("img").count(), 0, "the task text became an element")
            self.assertEqual(ui.page.locator("#tasks-list img").count(), 0)
            self.assertIsNone(ui.page.evaluate("window.sddImgRan"), "the task text's onerror ran")

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
