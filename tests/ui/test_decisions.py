"""Browser tests of the Decisions view (FR-033): an open `[decision]` task shows with its options and a
box for the answer, choosing an option fills the box, Save writes one answer file, the saved answer
shows after a reload, and with no open decisions the view says so.

Each toy project is one feature `specs/001-only` outside any git repository, so `sdd-decide.py`
refuses to apply the answer ("not a git repository") and the saved answer stays in
`<state dir>/answers/<task id>.json`, the way the view shows it until it is applied. The state
directory is under a temporary `XDG_STATE_HOME` that the dashboard process inherits.

Run it with `python3 -m unittest discover -s tests/ui`. It skips without Playwright or a Chromium;
`SDD_UI_TESTS=1` makes that a failure. Not part of the default `checks.commands`.
"""
import contextlib
import importlib.util
import json
import os
import tempfile
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
DECISION_ID = "T001"
OPEN_TASKS = ("# Tasks\n\n## Phase 1: Setup\n\n"
              "- [ ] T001 [decision] Pick a port. Options: (a) 80; (b) 8080. Recommend (b). "
              "Record the port in `decisions.md`.\n"
              "- [ ] T002 [manual] Record audio\n"
              "- [x] T003 [decision] Already settled\n"
              "- [ ] T004 Plain task in `a.py`\n")
NONE_OPEN_TASKS = ("# Tasks\n\n## Phase 1: Setup\n\n"
                   "- [x] T001 [decision] Already settled\n"
                   "- [ ] T002 Plain task in `a.py`\n")
EMPTY_TEXT = "No decisions are waiting on you."


def build_decisions_project(root, tasks):
    """A one-feature toy (`specs/001-only/tasks.md`) with a `.sdd/config.toml`, not a git repository."""
    root = Path(root)
    feature = root / "specs" / "001-only"
    feature.mkdir(parents=True)
    (feature / "tasks.md").write_text(tasks, encoding="utf-8")
    (root / ".sdd").mkdir(parents=True, exist_ok=True)
    (root / ".sdd" / "config.toml").write_text("[run]\nmax_retries = 2\n", encoding="utf-8")
    return root


class DecisionsTestCase(harness.UITestCase):
    """One dashboard on a toy with `TASKS`, started with `XDG_STATE_HOME` in a temp dir."""

    TASKS = OPEN_TASKS

    @classmethod
    def setUpClass(cls):
        harness.require_browser()
        cls._tmp = tempfile.TemporaryDirectory(prefix="sdd-ui-decisions-")
        base = Path(cls._tmp.name)
        cls.project = build_decisions_project(base / "toy", cls.TASKS)
        cls.state_home = base / "state"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(cls.state_home)}):
            cls.answers = sdd_supervise.state_dir(cls.project) / "answers"
            cls.dashboard = harness.start_dashboard(cls.project)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._tmp.cleanup()

    def setUp(self):
        """Each test starts with no saved answer, as the tests share one dashboard."""
        for name in self.answer_files():
            (self.answers / name).unlink()

    def answer_files(self):
        return sorted(p.name for p in self.answers.iterdir()) if self.answers.is_dir() else []

    @contextlib.contextmanager
    def open_decisions(self, element):
        """The dashboard at `#decisions`; returns once `element` is visible."""
        with harness.browser_page(self.dashboard.url + "#decisions", name=self.id()) as ui:
            ui.page.locator("#section-decisions").wait_for(state="visible", timeout=TIMEOUT_MS)
            ui.page.locator(element).first.wait_for(state="visible", timeout=TIMEOUT_MS)
            yield ui
            ui.page.wait_for_load_state("networkidle")


class OpenDecisionTest(DecisionsTestCase):
    ITEM = '#decisions-list .decision-item[data-task="%s"]' % DECISION_ID

    def test_open_decision_shows_with_its_options_and_a_box(self):
        with self.open_decisions(self.ITEM) as ui:
            items = ui.page.locator("#decisions-list .decision-item")
            self.assertEqual([items.nth(i).get_attribute("data-task") for i in range(items.count())],
                             [DECISION_ID])  # the done decision and the other tasks are not listed
            item = ui.page.locator(self.ITEM)
            self.assertIn("Pick a port.", item.locator(".decision-text").inner_text())
            choices = item.locator(".decision-choices li")
            self.assertEqual(choices.count(), 2)
            self.assertEqual(item.locator(".decision-choices li.recommended").count(), 1)
            self.assertIn("8080", item.locator(".decision-choices li.recommended").inner_text())
            box = item.locator("textarea.decision-answer")
            self.assertEqual(box.count(), 1)
            self.assertTrue(box.is_visible())
            self.assertEqual(box.input_value(), "")
            self.assertEqual(item.locator("button.decision-submit").count(), 1)
            options = item.locator("button.decision-option")
            self.assertEqual([options.nth(i).get_attribute("data-option") for i in range(options.count())],
                             ["a", "b"])

    def test_choosing_saving_and_reloading(self):
        self.assertEqual(self.answer_files(), [])
        with self.open_decisions(self.ITEM) as ui:
            item = ui.page.locator(self.ITEM)
            box = item.locator("textarea.decision-answer")
            item.locator("button.decision-option", has_text="Choose (a)").click()
            self.assertEqual(box.input_value().strip(), "(a)")
            item.locator("button.decision-option", has_text="Choose (b)").click()
            self.assertEqual(box.input_value().strip(), "(b)")  # a second choice replaces the first
            box.press("End")
            box.type("it is free")
            answer = box.input_value().strip()
            self.assertEqual(answer, "(b) it is free")

            with ui.page.expect_response(lambda r: r.url.endswith("/api/decisions/answer"),
                                         timeout=TIMEOUT_MS) as res:
                item.locator("button.decision-submit").click()
            self.assertEqual(res.value.status, 202, res.value.text())  # saved, not applied (no git repo)
            self.assertFalse(res.value.json().get("applied"))
            self.assertEqual(self.answer_files(), [DECISION_ID + ".json"])
            saved = json.loads((self.answers / (DECISION_ID + ".json")).read_text(encoding="utf-8"))
            self.assertEqual((saved.get("task"), saved.get("answer")), (DECISION_ID, answer))
            self.assertIn("- [ ] T001", (self.project / "specs" / "001-only" / "tasks.md").read_text())

            ui.page.reload()
            ui.page.locator(self.ITEM + " .decision-saved").wait_for(state="visible", timeout=TIMEOUT_MS)
            item = ui.page.locator(self.ITEM)
            self.assertIn(answer, item.locator(".decision-saved").inner_text())
            self.assertEqual(item.locator("button.decision-apply").count(), 1)
            self.assertEqual(item.locator("textarea.decision-answer").input_value(), answer)
        self.assertEqual(self.answer_files(), [DECISION_ID + ".json"])  # still exactly one file


class NoOpenDecisionsTest(DecisionsTestCase):
    TASKS = NONE_OPEN_TASKS

    def test_the_view_says_none_are_waiting(self):
        with self.open_decisions("#decisions-list .empty") as ui:
            self.assertIn(EMPTY_TEXT, ui.page.locator("#decisions-list .empty").inner_text())
            self.assertEqual(ui.page.locator("#decisions-list .decision-item").count(), 0)
            self.assertEqual(ui.page.locator("#decisions-list textarea").count(), 0)


if __name__ == "__main__":
    unittest.main()
