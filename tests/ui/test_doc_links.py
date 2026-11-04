"""Browser tests of document links in task text: a task that cites `quickstart.md §6`,
`specs/<feature>/research.md` or `§N` links to that document, and clicking the link opens the
document view at the cited section (the heading numbered N), from the cited feature when that is not
the one shown.

A held `[manual]` task shows its steps (the `## <task id>` section of the feature's `manual.md`) on its
"Waiting on you" card, or a line saying how to add them.

The toy has two features. `001-only` (the shown one, by `.specify/feature.json`) has the tasks and its
own quickstart.md and data-model.md; `002-other` has a research.md the first task points at.

Run it with `python3 -m unittest discover -s tests/ui`. It skips without Playwright or a Chromium;
`SDD_UI_TESTS=1` makes that a failure. Not part of the default `checks.commands`.
"""
import importlib.util
import json
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_ui_harness", HERE / "harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

TIMEOUT_MS = 10000

TASKS = """# Tasks: Toy links

## Phase 1: Setup

- [ ] T001 [manual] Run quickstart.md §6 for real and record it under `## Validation` in `specs/002-other/research.md`.
- [ ] T002 Implement §2's rules in `bin/x.py`, like `specs/999-gone/plan.md` says.
- [ ] T003 [manual] Look at the page yourself.
"""

MANUAL = """# Manual steps

## T001 Run the quickstart

1. Open a terminal.
2. Run `make check` and see `research.md` for the record.

## T0010 Not this one

1. Wrong section.
"""

FILLER = "\n".join("Line %d of filler so the page has to scroll." % i for i in range(80))
QUICKSTART = "# Quickstart\n\n" + "".join(
    "## %d. Step %d\n\n%s\n\n" % (n, n, FILLER) for n in range(1, 7))
DATA_MODEL = "# Data model\n\n## 1. One\n\n%s\n\n## 2. Two\n\nThe second section.\n\n## 12. Twelve\n\nNot this one.\n" % FILLER
RESEARCH = "# Research: other\n\n## Validation\n\nNothing yet.\n"


def build_links_project(root):
    root = harness.build_toy_project(root)
    own = root / "specs" / "001-only"
    (own / "tasks.md").write_text(TASKS, encoding="utf-8")
    (own / "quickstart.md").write_text(QUICKSTART, encoding="utf-8")
    (own / "data-model.md").write_text(DATA_MODEL, encoding="utf-8")
    (own / "manual.md").write_text(MANUAL, encoding="utf-8")
    other = root / "specs" / "002-other"
    other.mkdir(parents=True)
    (other / "research.md").write_text(RESEARCH, encoding="utf-8")
    (root / ".specify").mkdir(exist_ok=True)
    (root / ".specify" / "feature.json").write_text(json.dumps({"feature_directory": "specs/001-only"}), encoding="utf-8")
    return root


class DocLinksTest(harness.UITestCase):
    @classmethod
    def setUpClass(cls):
        harness.require_browser()
        cls._tmp = tempfile.TemporaryDirectory(prefix="sdd-ui-links-")
        cls.dashboard = harness.start_dashboard(build_links_project(Path(cls._tmp.name) / "toy"))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._tmp.cleanup()

    def card(self, ui, task):
        card = ui.page.locator('#tasks-list .task-card[data-task-card="%s"]' % task)
        card.wait_for(state="visible", timeout=TIMEOUT_MS)
        return card

    def test_a_cited_section_opens_at_its_heading(self):
        with harness.browser_page(self.dashboard.url + "#tasks", name=self.id()) as ui:
            link = self.card(ui, "T001").locator("a.doc-ref", has_text="quickstart.md §6")
            link.wait_for(state="visible", timeout=TIMEOUT_MS)
            link.click()
            heading = ui.page.locator("#doc-content h2", has_text="6. Step 6")
            heading.wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertEqual(ui.page.locator("#doc-title").inner_text().strip(), "Quickstart")
            self.assertIn("#quickstart", ui.page.url)
            ui.page.wait_for_function(
                "() => { var r = document.querySelector('#doc-content h2.doc-target'); "
                "return r && r.getBoundingClientRect().top < window.innerHeight; }", timeout=TIMEOUT_MS)

    def test_a_bare_section_opens_the_data_model_there(self):
        with harness.browser_page(self.dashboard.url + "#tasks", name=self.id()) as ui:
            self.card(ui, "T002").locator("a.doc-ref", has_text="§2").click()
            ui.page.locator("#doc-content h2.doc-target", has_text="2. Two").wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertEqual(ui.page.locator("#doc-content h2.doc-target").count(), 1)  # not "12. Twelve"
            self.assertEqual(ui.page.locator("#doc-title").inner_text().strip(), "Data model")

    def test_another_features_document_opens_from_that_feature(self):
        with harness.browser_page(self.dashboard.url + "#tasks", name=self.id()) as ui:
            card = self.card(ui, "T001")
            self.assertEqual(card.locator("a.doc-ref").count(), 2)
            card.locator("a.doc-ref", has_text="specs/002-other/research.md").click()
            ui.page.locator("#doc-content h1", has_text="Research: other").wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertEqual(ui.page.locator("#doc-title").inner_text().strip(), "Research · 002-other")
            self.assertEqual(ui.page.locator("#feature-select").input_value(), "001-only")  # the shown feature stays
            # Back in the nav, the documents are the shown feature's again.
            ui.page.locator('.nav-item[data-section="docs"]').click()
            ui.page.locator('.doc-tab[data-doc="quickstart"]').click()
            ui.page.locator("#doc-content h1", has_text="Quickstart").wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertEqual(ui.page.locator("#doc-title").inner_text().strip(), "Quickstart")

    def test_a_path_to_a_missing_feature_is_plain_text(self):
        with harness.browser_page(self.dashboard.url + "#tasks", name=self.id()) as ui:
            card = self.card(ui, "T002")
            self.assertIn("specs/999-gone/plan.md", card.locator(".task-text").inner_text())
            self.assertEqual(card.locator("a.doc-ref", has_text="plan.md").count(), 0)


    def test_a_manual_task_shows_its_steps_on_its_waiting_card(self):
        with harness.browser_page(self.dashboard.url + "#tasks", name=self.id()) as ui:
            card = ui.page.locator("#waiting-list .blocked-item", has_text="T001")
            steps = card.locator(".manual-steps-fold[open] ol > li")
            steps.first.wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertEqual(steps.count(), 2)
            self.assertIn("Open a terminal.", steps.nth(0).inner_text())
            self.assertNotIn("Wrong section", card.inner_text())
            self.assertEqual(card.locator(".manual-steps a.doc-ref", has_text="research.md").count(), 1)

    def test_a_manual_task_without_steps_says_how_to_add_them(self):
        with harness.browser_page(self.dashboard.url + "#tasks", name=self.id()) as ui:
            note = ui.page.locator("#waiting-list .blocked-item", has_text="T003").locator(".manual-steps-none")
            note.wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertIn("## T003", note.inner_text())
