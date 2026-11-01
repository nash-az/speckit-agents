"""Browser tests of the Jobs view (005 FR-011, US4): a Job's card holds its runs, runs without a Job are under
"Earlier runs", a run inside a Job opens the run detail, and Drop asks for confirmation before it posts.

The toy is the harness's (`specs/001-only` with the status fixture's tasks.md) plus one `done` Job record in
`.sdd/jobs/` and three runs (`.sdd/runs/<id>/workflows.json`, the fixture of `tests/test_sdd_dashboard.py`):
one names the Job in its `workflows.json`, one is listed in the Job's `runs`, one belongs to no Job.

The dashboard runs in this test process (on an OS-chosen port, in a thread) so that `sdd_job.drop` can be
replaced by a recorder: a confirmed Drop reaches it once, a dismissed one never does. Nothing touches git.
The supervisor's state directory is under a temporary `XDG_STATE_HOME`.

Run it with `python3 -m unittest discover -s tests/ui`. It skips without Playwright or a Chromium, or when
the sandbox refuses a local port; `SDD_UI_TESTS=1` makes that a failure. Not part of the default
`checks.commands`.
"""
import contextlib
import http.server
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sdd_ui_harness", HERE / "harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

_spec = importlib.util.spec_from_file_location("sdd_dashboard", harness.DASHBOARD)
sdd_dashboard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sdd_dashboard)

TIMEOUT_MS = 10000
JOB_ID = "20261031-1012"
JOB_TITLE = "Toy job"
JOB_RUNS = ("20261031-1530", "20261031-1012")  # newest first, as /api/runs lists them
EARLIER_RUN = "20261030-0800"


CHIP_TASK = "T012"  # an open task of the toy that the Job's newest run worked on

# The Job's newest run: the implementer and the verifier of CHIP_TASK (the journal form of RUN_JOURNAL).
CHIP_JOURNAL = [
    {"type": "launched"},
    {"type": "started", "key": "v2:01", "agentId": "a01", "label": "impl:" + CHIP_TASK, "phase": "Build"},
    {"type": "result", "key": "v2:01", "agentId": "a01",
     "result": {"status": "done", "summary": "done", "files_changed": ["f.py"]}},
    {"type": "started", "key": "v2:02", "agentId": "a02", "label": "verify:" + CHIP_TASK, "phase": "Build"},
    {"type": "result", "key": "v2:02", "agentId": "a02",
     "result": {"verdict": "approve", "problems": [], "checks_run": []}},
    {"type": "started", "key": "v2:03", "agentId": "a03", "label": "commit:" + CHIP_TASK, "phase": "Build"},
    {"type": "result", "key": "v2:03", "agentId": "a03",
     "result": {"checks_passed": True, "commits": ["c0ffee1"]}},
]


def build_jobs_project(root):
    """The harness toy plus a `done` Job with two runs and one run without a Job. The Job's newest run
    worked on CHIP_TASK, so that task's card carries the Job's chip (005 FR-013)."""
    root = harness.build_toy_project(root)
    wf = harness.fixtures.write_workflow_dir(root.resolve().parent / "transcripts" / "wf_jobs")
    chip_wf = harness.fixtures.write_workflow_dir(root.resolve().parent / "transcripts" / "wf_chip", CHIP_JOURNAL)
    for run_id in JOB_RUNS + (EARLIER_RUN,):
        harness.fixtures.write_run(root, run_id, [wf])
        (root / ".sdd" / "runs" / (run_id + ".md")).write_text("# Run Report\n", encoding="utf-8")
    listing = root / ".sdd" / "runs" / JOB_RUNS[0] / "workflows.json"
    listing.write_text(json.dumps({"workflow_dirs": [str(chip_wf)], "feature": "001-only", "job": JOB_ID}), encoding="utf-8")
    sdd_dashboard.sdd_job.save(root, {
        "id": JOB_ID, "title": JOB_TITLE, "feature": "001-only", "scope": {"tasks": ["T001", "T006", "T012"]},
        "scope_tasks": ["T001", "T006", "T012"], "blocked_at_start": 0,
        "limits": {"max_tasks": None, "until": None, "max_runs": 20}, "branch": "auto/" + JOB_ID,
        "base": {"branch": "main", "commit": "abc1234"}, "state": "done", "reason": None,
        "runs": [JOB_RUNS[1]], "no_progress_runs": 0, "created": 1, "ended": None, "merge_commit": None})
    return root


class RecordingDrop:
    """Stands in for `sdd_job.drop`: records the Job ids and changes nothing."""

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, project, job_id):
        with self.lock:
            self.calls.append(job_id)
        return {"dropped": True}


class JobsViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        harness.require_browser()
        cls._tmp = tempfile.TemporaryDirectory(prefix="sdd-ui-jobs-")
        base = Path(cls._tmp.name)
        cls._env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(base / "state")})
        cls._env.start()
        cls.project = build_jobs_project(base / "toy").resolve()
        cls.drop = RecordingDrop()
        cls._drop = mock.patch.object(sdd_dashboard.sdd_job, "drop", cls.drop)
        cls._drop.start()
        handler = sdd_dashboard.DashboardHandler
        handler.project = handler.started_project = cls.project
        handler.feature = None
        handler.allow_ports = ()
        handler.html = sdd_dashboard.build_html()
        try:
            cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        except OSError as exc:
            cls._cleanup()
            harness.skip_or_fail("the dashboard cannot start (%s)" % exc)
        handler.port = cls.server.server_address[1]
        cls.url = "http://127.0.0.1:%d/" % handler.port
        cls._thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def _cleanup(cls):
        cls._drop.stop()
        cls._env.stop()
        cls._tmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._thread.join(timeout=5)
        cls._cleanup()
        super().tearDownClass()

    def setUp(self):
        self.drop.calls.clear()

    @contextlib.contextmanager
    def open_job(self):
        """The dashboard at `#jobs` with the Job's card opened. Every drop POST is in `ui.drop_posts`."""
        with harness.browser_page(self.url + "#jobs", name=self.id()) as ui:
            ui.drop_posts = []
            ui.page.on("request", lambda r: ui.drop_posts.append(r.url)
                       if r.method == "POST" and r.url.endswith("/drop") else None)
            card = ui.page.locator('#jobs-list .job-card[data-job="%s"]' % JOB_ID)
            card.wait_for(state="visible", timeout=TIMEOUT_MS)
            card.locator(".job-head").click()
            card.locator(".job-detail").wait_for(state="visible", timeout=TIMEOUT_MS)
            ui.card = card
            yield ui
            ui.page.wait_for_load_state("networkidle")

    def test_a_job_holds_both_of_its_runs(self):
        with self.open_job() as ui:
            card = ui.card
            self.assertIn(JOB_TITLE, card.locator(".job-title").inner_text())
            self.assertEqual(card.locator(".job-state").inner_text().strip(), "Done")
            self.assertIn("auto/" + JOB_ID, card.locator(".job-branch").inner_text())
            self.assertIn("1 of 3 done", card.locator(".job-counts").inner_text())
            runs = card.locator(".job-detail .run-item .run-id")
            self.assertEqual([r.inner_text().strip() for r in runs.all()], ["#" + r for r in JOB_RUNS])
            self.assertGreater(card.locator(".job-detail .job-tasks .run-task").count(), 0)
            earlier = ui.page.locator("#earlier-runs .run-item .run-id")
            self.assertEqual([r.inner_text().strip() for r in earlier.all()], ["#" + EARLIER_RUN])
            # A run inside the Job opens the run detail under itself.
            card.locator(".job-detail .run-item").first.click()
            detail = card.locator(".job-detail .run-detail")
            detail.wait_for(state="visible", timeout=TIMEOUT_MS)
            detail.locator("#run-tasks .run-task").first.wait_for(state="visible", timeout=TIMEOUT_MS)
            # Merge and Drop are offered for a done Job; Stop and Continue are not.
            for action, enabled in (("stop", False), ("continue", False), ("merge", True), ("drop", True)):
                self.assertEqual(card.locator('button[data-job-action="%s"]' % action).is_enabled(), enabled, action)

    def test_a_task_chip_names_its_job_and_opens_it(self):
        """005 FR-013: the chip of a task a Job's run did says "done in job <title>" and opens that Job's card."""
        with harness.browser_page(self.url + "#tasks", name=self.id()) as ui:
            chip = ui.page.locator('#tasks-list .task-card[data-task-card="%s"] .run-chip' % CHIP_TASK)
            chip.wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertIn("done in job " + JOB_TITLE, " ".join(chip.inner_text().split()))
            self.assertEqual(chip.get_attribute("data-open-job"), JOB_ID)
            chip.click()
            ui.page.wait_for_function("location.hash === '#jobs'", timeout=TIMEOUT_MS)
            card = ui.page.locator('#jobs-list .job-card[data-job="%s"]' % JOB_ID)
            card.locator(".job-detail").wait_for(state="visible", timeout=TIMEOUT_MS)
            # The run is one click deeper: the Job's runs are listed, none is opened yet.
            self.assertEqual(card.locator(".job-detail .run-item").count(), len(JOB_RUNS))
            self.assertEqual(card.locator(".job-detail .run-detail").count(), 0)
            ui.page.wait_for_load_state("networkidle")

    def test_drop_asks_for_confirmation(self):
        with self.open_job() as ui:
            asked = []

            def dismiss(dialog):
                asked.append((dialog.type, dialog.message))
                dialog.dismiss()

            ui.page.once("dialog", dismiss)
            ui.card.locator('button[data-job-action="drop"]').click()
            ui.page.wait_for_timeout(500)  # a POST, had one been sent, would have landed by now
            self.assertEqual(len(asked), 1, "Drop did not ask for confirmation")
            self.assertEqual(asked[0][0], "confirm")
            self.assertIn('Drop the job "%s"?' % JOB_TITLE, asked[0][1])
            self.assertEqual(ui.drop_posts, [])
            self.assertEqual(self.drop.calls, [])

            ui.page.once("dialog", lambda dialog: dialog.accept())
            with ui.page.expect_response(lambda r: r.url.endswith("/api/jobs/%s/drop" % JOB_ID), timeout=TIMEOUT_MS) as res:
                ui.card.locator('button[data-job-action="drop"]').click()
            self.assertEqual(res.value.status, 200, res.value.text())
            ui.page.locator("#toasts .toast", has_text="Job dropped").wait_for(state="visible", timeout=TIMEOUT_MS)
        self.assertEqual(self.drop.calls, [JOB_ID])


if __name__ == "__main__":
    unittest.main()
