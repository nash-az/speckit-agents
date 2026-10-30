"""Browser-test harness for the dashboard (FR-031).

`Dashboard` starts `bin/sdd-dashboard.py --port 0` on a temporary toy project and reads the real
port from its first output line. `browser_page()` opens the page in Chromium through Playwright,
serves the four CDN files from `tests/ui/vendor/` so a run is offline, records console errors,
uncaught exceptions and failed same-origin requests, and saves a screenshot and the page HTML
under `.sdd/scratch/ui/` when the test fails.

    class SmokeTest(UITestCase):
        def test_tasks(self):
            with browser_page(self.dashboard.url, name=self.id()) as ui:
                ui.page.wait_for_selector("text=Phase 1")

Leaving the `with` block normally fails the test when anything was recorded; `ui.errors` holds
the records, and `ignore=` takes regexes for records a test expects.

When `playwright` cannot be imported or neither Chromium nor Google Chrome launches, `UITestCase`
and `browser_page()` skip with a one-line reason naming the install command; `UITestCase` also
skips when the dashboard cannot start (a sandbox that refuses a local port). `SDD_UI_TESTS=1`
turns either skip into a failure (`require_browser()`, `skip_or_fail()`).
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import re
import select
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DASHBOARD = ROOT / "bin" / "sdd-dashboard.py"
VENDOR = HERE / "vendor"
SCRATCH = ROOT / ".sdd" / "scratch" / "ui"
START_TIMEOUT = 15  # seconds to wait for the dashboard's first output line
INSTALL = "python3 -m pip install playwright && python3 -m playwright install chromium"
STRICT_ENV = "SDD_UI_TESTS"  # "1": a missing browser fails the test instead of skipping it

# The pinned CDN files in `build_html()` → their vendored copies (tests/ui/vendor/README.md).
CDN = {
    "https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js":
        ("marked.min.js", "application/javascript"),
    "https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/highlight.min.js":
        ("highlight.min.js", "application/javascript"),
    "https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/styles/github-dark.min.css":
        ("github-dark.min.css", "text/css"),
    "https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js":
        ("purify.min.js", "application/javascript"),
}

# The toy project is the fixture of tests/test_sdd_dashboard.py (its HandlerTest layout).
_spec = importlib.util.spec_from_file_location("test_sdd_dashboard", ROOT / "tests" / "test_sdd_dashboard.py")
fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixtures)


def build_toy_project(root):
    """One feature `specs/001-only` with the status fixture's tasks.md and BLOCKED.md."""
    root = Path(root)
    fixtures.write_project(root / "specs" / "001-only", fixtures.STATUS_TASKS, fixtures.STATUS_BLOCKED)
    (root / ".sdd").mkdir(parents=True, exist_ok=True)
    (root / ".sdd" / "config.toml").write_text("[run]\nmax_retries = 2\n", encoding="utf-8")
    return root


class Dashboard:
    """`bin/sdd-dashboard.py` on an OS-chosen port; a context manager, or `start()` / `stop()`."""

    def __init__(self, project=None):
        self._tmp = None
        if project is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="sdd-ui-")
            project = build_toy_project(Path(self._tmp.name) / "toy")
        self.project = Path(project)
        self.proc = None
        self.port = None
        self.url = None
        self._log = None

    def start(self):
        self._log = tempfile.TemporaryFile(mode="w+", prefix="sdd-ui-dashboard-")
        self.proc = subprocess.Popen(
            [sys.executable, str(DASHBOARD), "--project", str(self.project), "--port", "0"],
            stdout=subprocess.PIPE, stderr=self._log, text=True)
        line = self._first_line()
        m = re.search(r"http://127\.0\.0\.1:(\d+)", line)
        if not m:
            message = "dashboard did not report its port: %r%s" % (line, self._stderr())
            self.stop()
            raise RuntimeError(message)
        self.port = int(m.group(1))
        self.url = "http://127.0.0.1:%d/" % self.port
        return self

    def _first_line(self):
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.proc.stdout], [], [], 0.2)
            if ready:
                return self.proc.stdout.readline()
            if self.proc.poll() is not None:
                return ""
        return ""

    def _stderr(self):
        if self._log is None:
            return ""
        self._log.seek(0)
        text = self._log.read().strip()
        return ("\n" + text) if text else ""

    def stop(self):
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait()
            self.proc.stdout.close()
            self.proc = None
        if self._log is not None:
            self._log.close()
            self._log = None
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def start_dashboard(project=None):
    """`Dashboard(project).start()`, skipping (or failing under `SDD_UI_TESTS=1`) when it cannot start,
    e.g. in a sandbox that refuses to bind a local port."""
    try:
        return Dashboard(project).start()
    except RuntimeError as exc:
        lines = [l.strip() for l in str(exc).strip().splitlines() if l.strip()]
        cause = "; ".join(dict.fromkeys(lines[:1] + lines[-1:])) or type(exc).__name__  # first line, stderr's last
        skip_or_fail("the dashboard cannot start (%s)" % cause)


class UITestCase(unittest.TestCase):
    """Starts one dashboard on the toy project per test class; `self.dashboard.url` is the page."""

    dashboard = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        require_browser()
        cls.dashboard = start_dashboard()

    @classmethod
    def tearDownClass(cls):
        if cls.dashboard is not None:
            cls.dashboard.stop()
            cls.dashboard = None
        super().tearDownClass()


class BrowserSession:
    """What `browser_page()` yields: the Playwright `page` and the `errors` recorded on it."""

    def __init__(self, page, origin, ignore=()):
        self.page = page
        self.origin = origin
        self.errors = []
        self._ignore = [re.compile(p) for p in ignore]

    def record(self, kind, text):
        if not any(p.search(text) for p in self._ignore):
            self.errors.append("%s: %s" % (kind, text))

    def same_origin(self, url):
        p = urllib.parse.urlsplit(url)
        return "%s://%s" % (p.scheme, p.netloc) == self.origin

    def assert_no_errors(self):
        if self.errors:
            raise AssertionError("browser errors:\n  " + "\n  ".join(self.errors))


def launch_chromium(playwright):
    """The bundled Chromium when it is installed, else the installed Google Chrome.

    Inside the run sandbox (macOS Seatbelt) Chromium dies at start: the sandbox refuses the Mach service
    it registers for its child processes. In one process it needs none, so that is the second try."""
    from playwright.sync_api import Error
    try:
        return playwright.chromium.launch()
    except Error as bundled:
        try:
            return playwright.chromium.launch(args=["--single-process"])
        except Error:
            pass
        try:
            return playwright.chromium.launch(channel="chrome")
        except Error as chrome:
            raise RuntimeError("cannot launch Chromium (%s) or Google Chrome (%s)"
                               % (bundled.message.splitlines()[0], chrome.message.splitlines()[0])) from chrome


def _one_line(exc):
    lines = str(exc).strip().splitlines()
    return (lines[0].strip() if lines else "") or type(exc).__name__


_UNPROBED = object()
_problem = _UNPROBED


def browser_problem(refresh=False):
    """None when Playwright imports and a Chromium launches, else a one-line reason with `INSTALL`.

    The launch is tried once per process; `refresh=True` probes again.
    """
    global _problem
    if _problem is not _UNPROBED and not refresh:
        return _problem
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        _problem = "Playwright for Python is not installed (%s); install it: %s" % (_one_line(exc), INSTALL)
        return _problem
    try:
        with sync_playwright() as p:
            launch_chromium(p).close()
    except Exception as exc:  # noqa: BLE001 - any launch failure means no browser to test with
        _problem = "Chromium cannot be launched (%s); install it: %s" % (_one_line(exc), INSTALL)
        return _problem
    _problem = None
    return None


def require_browser():
    """Skip (raise `unittest.SkipTest`) when there is no browser; fail instead when `SDD_UI_TESTS=1`."""
    problem = browser_problem()
    if problem is not None:
        skip_or_fail(problem)


def skip_or_fail(problem):
    """Raise `unittest.SkipTest(problem)`, or an `AssertionError` when `SDD_UI_TESTS=1`."""
    if os.environ.get(STRICT_ENV) == "1":
        raise AssertionError("%s (%s=1, so this fails instead of skipping)" % (problem, STRICT_ENV))
    raise unittest.SkipTest(problem)


def _route(session):
    def handle(route):
        url = route.request.url
        if url in CDN:
            name, content_type = CDN[url]
            route.fulfill(path=str(VENDOR / name), content_type=content_type)
        elif session.same_origin(url):
            route.continue_()
        else:
            session.record("offline", "blocked request to " + url)
            route.abort()
    return handle


def _on_console(session):
    def handle(msg):
        if msg.type == "error":
            session.record("console", msg.text)
    return handle


def _on_response(session):
    def handle(response):
        if response.status >= 400 and session.same_origin(response.url):
            session.record("request", "%s %s -> %d" % (response.request.method, response.url, response.status))
    return handle


def _on_request_failed(session):
    def handle(request):
        if session.same_origin(request.url):
            session.record("request", "%s %s failed: %s" % (request.method, request.url, request.failure))
    return handle


def artifact_name(name):
    """A file-safe name for the screenshot and HTML (a test id works)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name or "page").strip("_") or "page"


def save_artifacts(page, name, out_dir=None):
    """Screenshot and page HTML as `<name>.png` / `<name>.html`; returns the two paths."""
    out_dir = Path(out_dir) if out_dir is not None else SCRATCH
    out_dir.mkdir(parents=True, exist_ok=True)
    base = artifact_name(name)
    png, html = out_dir / (base + ".png"), out_dir / (base + ".html")
    try:
        page.screenshot(path=str(png), full_page=True)
    except Exception:  # noqa: BLE001 - a broken page must still leave its HTML
        png = None
    try:
        html.write_text(page.content(), encoding="utf-8")
    except Exception:  # noqa: BLE001
        html = None
    return png, html


@contextlib.contextmanager
def browser_page(url, name=None, ignore=(), check=True, out_dir=None):
    """Open `url` in Chromium and yield a `BrowserSession`.

    The four CDN URLs are served from `tests/ui/vendor/`; any other non-local request is aborted
    and recorded. When the block raises, or (`check=True`) leaves with recorded errors, the
    screenshot and HTML go to `out_dir` (default `.sdd/scratch/ui/`) and the test fails.
    Without a browser it skips, or fails under `SDD_UI_TESTS=1` (`require_browser()`).
    """
    require_browser()
    from playwright.sync_api import sync_playwright

    parts = urllib.parse.urlsplit(url)
    origin = "%s://%s" % (parts.scheme, parts.netloc)
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            context = browser.new_context()
            page = context.new_page()
            session = BrowserSession(page, origin, ignore)
            context.route("**/*", _route(session))
            page.on("console", _on_console(session))
            page.on("pageerror", lambda exc: session.record("pageerror", str(exc)))
            page.on("response", _on_response(session))
            page.on("requestfailed", _on_request_failed(session))
            page.goto(url)
            try:
                yield session
                if check:
                    session.assert_no_errors()
            except BaseException as exc:
                png, html = save_artifacts(page, name, out_dir)
                if isinstance(exc, AssertionError):
                    saved = ", ".join(str(f) for f in (png, html) if f)
                    raise AssertionError("%s\n(saved %s)" % (exc, saved)) from exc
                raise
        finally:
            browser.close()
