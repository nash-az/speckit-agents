"""tests/ui/harness.py skip logic (FR-031): no browser skips with the install command; SDD_UI_TESTS=1 fails.

These tests fake the missing import or launch, so they need no Playwright and run in the default suite.
"""
import contextlib
import importlib.util
import io
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_ui_harness", HERE / "ui" / "harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

NO_PLAYWRIGHT = {"playwright": None, "playwright.sync_api": None}  # None in sys.modules: import fails


def fake_playwright(launch_error):
    """A `playwright.sync_api` whose Chromium and Chrome launches both raise `launch_error`."""
    class Error(Exception):
        @property
        def message(self):
            return str(self)

    class Chromium:
        def launch(self, **kwargs):
            raise Error(launch_error)

    @contextlib.contextmanager
    def sync_playwright():
        yield types.SimpleNamespace(chromium=Chromium())

    api = types.ModuleType("playwright.sync_api")
    api.sync_playwright = sync_playwright
    api.Error = Error
    pkg = types.ModuleType("playwright")
    pkg.sync_api = api
    return {"playwright": pkg, "playwright.sync_api": api}


def env_without_strict():
    env = dict(os.environ)
    env.pop(harness.STRICT_ENV, None)
    return env


class SkipLogicTest(unittest.TestCase):
    def setUp(self):
        harness._problem = harness._UNPROBED

    def tearDown(self):
        harness._problem = harness._UNPROBED

    def test_missing_import_skips_with_install_command(self):
        with mock.patch.dict(sys.modules, NO_PLAYWRIGHT), mock.patch.dict(os.environ, env_without_strict(), clear=True):
            with self.assertRaises(unittest.SkipTest) as cm:
                harness.require_browser()
        reason = str(cm.exception)
        self.assertIn("Playwright for Python is not installed", reason)
        self.assertIn(harness.INSTALL, reason)
        self.assertNotIn("\n", reason)

    def test_missing_import_fails_when_strict(self):
        with mock.patch.dict(sys.modules, NO_PLAYWRIGHT), mock.patch.dict(os.environ, {harness.STRICT_ENV: "1"}):
            with self.assertRaises(AssertionError) as cm:
                harness.require_browser()
        self.assertNotIsInstance(cm.exception, unittest.SkipTest)
        self.assertIn(harness.INSTALL, str(cm.exception))
        self.assertIn("SDD_UI_TESTS=1", str(cm.exception))

    def test_strict_needs_exactly_one(self):
        with mock.patch.dict(sys.modules, NO_PLAYWRIGHT), mock.patch.dict(os.environ, {harness.STRICT_ENV: "0"}):
            with self.assertRaises(unittest.SkipTest):
                harness.require_browser()

    def test_launch_failure_skips_with_one_line_reason(self):
        fake = fake_playwright("Executable doesn't exist at /x/chrome\nLooks like Playwright was just installed")
        with mock.patch.dict(sys.modules, fake), mock.patch.dict(os.environ, env_without_strict(), clear=True):
            with self.assertRaises(unittest.SkipTest) as cm:
                harness.require_browser()
        reason = str(cm.exception)
        self.assertIn("Chromium cannot be launched", reason)
        self.assertIn("Executable doesn't exist", reason)
        self.assertIn(harness.INSTALL, reason)
        self.assertNotIn("\n", reason)

    def test_launch_failure_fails_when_strict(self):
        with mock.patch.dict(sys.modules, fake_playwright("no browser")), \
                mock.patch.dict(os.environ, {harness.STRICT_ENV: "1"}):
            with self.assertRaises(AssertionError) as cm:
                harness.require_browser()
        self.assertNotIsInstance(cm.exception, unittest.SkipTest)
        self.assertIn("Chromium cannot be launched", str(cm.exception))

    def test_probe_is_cached_until_refresh(self):
        with mock.patch.dict(sys.modules, NO_PLAYWRIGHT):
            first = harness.browser_problem()
        self.assertIsNotNone(first)
        self.assertEqual(harness.browser_problem(), first)
        harness._problem = None
        self.assertIsNone(harness.browser_problem())

    def test_ui_test_case_skips_whole_class_without_browser(self):
        started = []

        class Probe(harness.UITestCase):
            def test_page(self):
                pass

        with mock.patch.dict(sys.modules, NO_PLAYWRIGHT), \
                mock.patch.dict(os.environ, env_without_strict(), clear=True), \
                mock.patch.object(harness.Dashboard, "start", lambda self: started.append(self)):
            result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(Probe))
        self.assertEqual(len(result.skipped), 1)
        self.assertIn(harness.INSTALL, result.skipped[0][1])
        self.assertEqual(result.errors + result.failures, [])
        self.assertEqual(started, [])  # no dashboard is started for a skipped class

    def test_ui_test_case_fails_whole_class_when_strict(self):
        class Probe(harness.UITestCase):
            def test_page(self):
                pass

        with mock.patch.dict(sys.modules, NO_PLAYWRIGHT), mock.patch.dict(os.environ, {harness.STRICT_ENV: "1"}):
            result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(Probe))
        self.assertEqual(result.skipped, [])
        self.assertEqual(len(result.errors) + len(result.failures), 1)

    def test_browser_page_skips_without_browser(self):
        with mock.patch.dict(sys.modules, NO_PLAYWRIGHT), mock.patch.dict(os.environ, env_without_strict(), clear=True):
            with self.assertRaises(unittest.SkipTest):
                with harness.browser_page("http://127.0.0.1:1/"):
                    self.fail("the block must not run")


if __name__ == "__main__":
    unittest.main()
