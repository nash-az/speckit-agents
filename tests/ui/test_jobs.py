"""Browser tests of the Runs view (009 FR-021, FR-022, T009; 005 FR-011, US4): a run's card holds its sessions,
sessions without a run record are under "Earlier runs", a session inside a run opens the run detail, and each
card offers the buttons of its shown state: Continue and Drop when suspended (Drop asks for confirmation before
it posts), Merge when its landing needs a merge (with the files), Land when its landing is still to do, and
nothing otherwise.

The toy is the harness's (`specs/001-only` with the status fixture's tasks.md) plus run records in
`.sdd/jobs/`: a suspended run (its own `.sdd/run.json`, no supervisor) with two sessions, and three ended runs
(landing `pending`, `needs-merge` and `landed`). Three sessions (`.sdd/runs/<id>/workflows.json`, the fixture of
`tests/test_sdd_dashboard.py`): one names the suspended run in its `workflows.json`, one is listed in its
`runs`, one belongs to no run.

The dashboard runs in this test process (on an OS-chosen port, in a thread) so that `sdd_job.drop_suspended`
and `sdd_job.land` can be replaced by recorders: a confirmed Drop reaches `drop_suspended` once, a dismissed
one never does. Nothing touches git. The supervisor's state directory is under a temporary `XDG_STATE_HOME`.

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
JOB_TITLE = "Toy run"
JOB_RUNS = ("20261031-1530", "20261031-1012")  # newest first, as /api/runs lists them
EARLIER_RUN = "20261030-0800"
PENDING_ID, PENDING_TITLE = "20261029-0900", "Not landed run"
MERGE_ID, MERGE_TITLE = "20261028-0900", "Conflicting run"
LANDED_ID, LANDED_TITLE = "20261027-0900", "Landed run"
CONFLICTS = ["src/a.py", "README.md"]
# The landed run's summary (009 data-model §6): its card shows the past-run line (FR-021, US4).
LANDED_SUMMARY = {"contract": "1.0", "id": LANDED_ID, "title": LANDED_TITLE, "duration_s": 4800,
                  "outcome": "done", "tasks": {"done": ["T001", "T006"], "blocked": [], "dropped": ["T012"]},
                  "attempts": 4, "approved_first_time": 1,
                  "models": {"implementer": "claude-opus-5-5", "verifier": "claude-opus-5-5"},
                  "usage_peak": {"five_hour": 0.93, "seven_day": 0.41}}
LANDED_LINE = ("1 h 20 min · 2 done, 1 dropped, 0 blocked · 4 attempts, 1 approved first time · "
               "implementer claude-opus-5-5, verifier claude-opus-5-5 · peak usage 93% (5 h), 41% (7 d)")


CHIP_TASK = "T012"  # an open task of the toy that the run's newest session worked on

# The run's newest session: the implementer and the verifier of CHIP_TASK (the journal form of RUN_JOURNAL).
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


def record(run_id, title, state, **fields):
    return {"id": run_id, "title": title, "feature": "001-only", "scope": {"tasks": ["T001", "T006", "T012"]},
            "scope_tasks": ["T001", "T006", "T012"], "blocked_at_start": 0,
            "limits": {"max_tasks": None, "until": None, "max_runs": 20}, "branch": "auto/" + run_id,
            "base": {"branch": "main", "commit": "abc1234"}, "state": state, "reason": None,
            "runs": [], "no_progress_runs": 0, "created": 1, "ended": None, **fields}


def build_jobs_project(root):
    """The harness toy plus a suspended run with two sessions, one session without a run, and three ended
    runs. The suspended run's newest session worked on CHIP_TASK, so that task's card carries the run's chip
    (005 FR-013)."""
    root = harness.build_toy_project(root)
    wf = harness.fixtures.write_workflow_dir(root.resolve().parent / "transcripts" / "wf_jobs")
    chip_wf = harness.fixtures.write_workflow_dir(root.resolve().parent / "transcripts" / "wf_chip", CHIP_JOURNAL)
    for run_id in JOB_RUNS + (EARLIER_RUN,):
        harness.fixtures.write_run(root, run_id, [wf])
        (root / ".sdd" / "runs" / (run_id + ".md")).write_text("# Run Report\n", encoding="utf-8")
    listing = root / ".sdd" / "runs" / JOB_RUNS[0] / "workflows.json"
    listing.write_text(json.dumps({"workflow_dirs": [str(chip_wf)], "feature": "001-only", "job": JOB_ID}), encoding="utf-8")
    save = sdd_dashboard.sdd_job.save
    save(root, record(JOB_ID, JOB_TITLE, "suspended", reason="stopped by you", runs=[JOB_RUNS[1]]))
    (root / ".sdd" / "run.json").write_text(json.dumps({"job": JOB_ID, "branch": "auto/" + JOB_ID}), encoding="utf-8")
    save(root, record(PENDING_ID, PENDING_TITLE, "ended", outcome="stopped", reason="stopped by you",
                      landing={"state": "pending", "commit": None, "files": []}))
    save(root, record(MERGE_ID, MERGE_TITLE, "ended", outcome="done",
                      landing={"state": "needs-merge", "commit": None, "files": CONFLICTS}))
    save(root, record(LANDED_ID, LANDED_TITLE, "ended", outcome="done",
                      landing={"state": "landed", "commit": "1234567890abcdef", "files": []}))
    (root / ".sdd" / "runs" / (LANDED_ID + ".json")).write_text(json.dumps(LANDED_SUMMARY), encoding="utf-8")
    return root


class Recorder:
    """Stands in for an sdd-job.py action: records the run ids, changes nothing and answers `answer`."""

    def __init__(self, answer):
        self.calls = []
        self.answer = answer
        self.lock = threading.Lock()

    def __call__(self, project, job_id):
        with self.lock:
            self.calls.append(job_id)
        return self.answer


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
        cls.drop = Recorder({"dropped": True, "tasks": [CHIP_TASK], "stash": "sdd-run %s: dropped %s" % (JOB_ID, CHIP_TASK),
                             "end": {}})
        cls.land = Recorder({"landed": True, "commit": "abcdef1234567890", "fast_forward": True,
                             "landing": {"state": "landed", "commit": "abcdef1234567890", "files": []}})
        cls._patches = [mock.patch.object(sdd_dashboard.sdd_job, "drop_suspended", cls.drop),
                        mock.patch.object(sdd_dashboard.sdd_job, "land", cls.land)]
        for patch in cls._patches:
            patch.start()
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
        for patch in cls._patches:
            patch.stop()
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
        self.land.calls.clear()

    @contextlib.contextmanager
    def runs_page(self):
        """The dashboard at `#runs`. Every drop POST is in `ui.drop_posts`."""
        with harness.browser_page(self.url + "#runs", name=self.id()) as ui:
            ui.drop_posts = []
            ui.page.on("request", lambda r: ui.drop_posts.append(r.url)
                       if r.method == "POST" and r.url.endswith("/drop") else None)
            ui.page.locator('#jobs-list .job-card[data-job="%s"]' % JOB_ID).wait_for(state="visible", timeout=TIMEOUT_MS)
            yield ui
            ui.page.wait_for_load_state("networkidle")

    @contextlib.contextmanager
    def open_job(self):
        """The dashboard at `#runs` with the suspended run's card opened."""
        with self.runs_page() as ui:
            card = ui.page.locator('#jobs-list .job-card[data-job="%s"]' % JOB_ID)
            card.locator(".job-head").click()
            card.locator(".job-detail").wait_for(state="visible", timeout=TIMEOUT_MS)
            ui.card = card
            yield ui

    def buttons(self, card):
        return [b.get_attribute("data-job-action") for b in card.locator("button[data-job-action]").all()]

    def test_the_view_is_runs_and_jobs_redirects(self):
        with harness.browser_page(self.url + "#jobs", name=self.id()) as ui:
            ui.page.wait_for_function("location.hash === '#runs'", timeout=TIMEOUT_MS)
            ui.page.locator("#section-runs").wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertEqual(ui.page.locator('.nav-item[data-section="runs"]').inner_text().split()[-1], "Runs")
            self.assertEqual(ui.page.locator("#start-job-card .card-title").inner_text().strip(), "Start run")
            ui.page.wait_for_load_state("networkidle")

    def test_a_run_holds_both_of_its_sessions(self):
        with self.open_job() as ui:
            card = ui.card
            self.assertIn(JOB_TITLE, card.locator(".job-title").inner_text())
            self.assertEqual(card.locator(".job-state").inner_text().strip(), "Suspended")
            self.assertIn("auto/" + JOB_ID, card.locator(".job-branch").inner_text())
            self.assertIn("1 of 3 done", card.locator(".job-counts").inner_text())
            self.assertIn("Tasks in this run", card.locator(".job-detail").inner_text())
            runs = card.locator(".job-detail .run-item .run-id")
            self.assertEqual([r.inner_text().strip() for r in runs.all()], ["#" + r for r in JOB_RUNS])
            self.assertGreater(card.locator(".job-detail .job-tasks .run-task").count(), 0)
            earlier = ui.page.locator("#earlier-runs .run-item .run-id")
            self.assertEqual([r.inner_text().strip() for r in earlier.all()], ["#" + EARLIER_RUN])
            # A session inside the run opens the run detail under itself.
            card.locator(".job-detail .run-item").first.click()
            detail = card.locator(".job-detail .run-detail")
            detail.wait_for(state="visible", timeout=TIMEOUT_MS)
            detail.locator("#run-tasks .run-task").first.wait_for(state="visible", timeout=TIMEOUT_MS)

    def test_open_runs_come_first(self):
        with self.runs_page() as ui:
            ids = [c.get_attribute("data-job") for c in ui.page.locator("#jobs-list .job-card").all()]
            self.assertEqual(ids, [JOB_ID, PENDING_ID, MERGE_ID, LANDED_ID])

    def test_each_card_offers_the_buttons_of_its_state(self):
        with self.runs_page() as ui:
            card = lambda run_id: ui.page.locator('#jobs-list .job-card[data-job="%s"]' % run_id)
            self.assertEqual(self.buttons(card(JOB_ID)), ["continue", "drop"])
            self.assertEqual(self.buttons(card(PENDING_ID)), ["land"])
            self.assertEqual(self.buttons(card(MERGE_ID)), ["merge"])
            self.assertEqual(self.buttons(card(LANDED_ID)), [])
            self.assertIn("conflicts in " + ", ".join(CONFLICTS), card(MERGE_ID).inner_text())
            self.assertEqual(card(MERGE_ID).locator(".job-state").inner_text().strip(), "Needs merge")
            self.assertEqual(card(LANDED_ID).locator(".job-state").inner_text().strip(), "Done")
            self.assertIn("landed as 1234567", " ".join(card(LANDED_ID).locator(".job-branch").inner_text().split()))

    def test_a_past_run_shows_its_summary_line(self):
        """009 T025 (FR-021, US4): an ended run with a summary shows the past-run line; the others none."""
        with self.runs_page() as ui:
            card = lambda run_id: ui.page.locator('#jobs-list .job-card[data-job="%s"]' % run_id)
            line = card(LANDED_ID).locator(".job-summary")
            line.wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertEqual(line.inner_text().strip(), LANDED_LINE)
            for run_id in (JOB_ID, PENDING_ID, MERGE_ID):
                self.assertEqual(card(run_id).locator(".job-summary").count(), 0, run_id)

    def test_land_and_merge_post_to_land(self):
        with self.runs_page() as ui:
            for run_id in (PENDING_ID, MERGE_ID):
                button = ui.page.locator('#jobs-list .job-card[data-job="%s"] button[data-job-action]' % run_id)
                with ui.page.expect_response(lambda r: r.url.endswith("/api/jobs/%s/land" % run_id),
                                             timeout=TIMEOUT_MS) as res:
                    button.click()
                self.assertEqual(res.value.status, 200, res.value.text())
            ui.page.locator("#toasts .toast", has_text="Landed on main (abcdef1)").wait_for(state="visible", timeout=TIMEOUT_MS)
            ui.page.locator("#toasts .toast", has_text="Merged into main (abcdef1)").wait_for(state="visible", timeout=TIMEOUT_MS)
        self.assertEqual(self.land.calls, [PENDING_ID, MERGE_ID])

    def test_a_task_chip_names_its_run_and_opens_it(self):
        """005 FR-013, 009 FR-022: the chip of a task a run's session did says "done in run <title>" and opens
        that run's card."""
        with harness.browser_page(self.url + "#tasks", name=self.id()) as ui:
            chip = ui.page.locator('#tasks-list .task-card[data-task-card="%s"] .run-chip' % CHIP_TASK)
            chip.wait_for(state="visible", timeout=TIMEOUT_MS)
            self.assertIn("done in run " + JOB_TITLE, " ".join(chip.inner_text().split()))
            self.assertEqual(chip.get_attribute("data-open-job"), JOB_ID)
            chip.click()
            ui.page.wait_for_function("location.hash === '#runs'", timeout=TIMEOUT_MS)
            card = ui.page.locator('#jobs-list .job-card[data-job="%s"]' % JOB_ID)
            card.locator(".job-detail").wait_for(state="visible", timeout=TIMEOUT_MS)
            # The session is one click deeper: the run's sessions are listed, none is opened yet.
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
            # the toy project is not a git repository and its run.json has no journal: no files, no tasks named
            self.assertEqual(asked[0][1], "Drop %s? The half-done work on its last task is stashed and saved in the"
                                          " task's history: no changed files. Finished tasks land on main." % JOB_TITLE)
            self.assertEqual(ui.drop_posts, [])
            self.assertEqual(self.drop.calls, [])

            ui.page.once("dialog", lambda dialog: dialog.accept())
            with ui.page.expect_response(lambda r: r.url.endswith("/api/jobs/%s/drop" % JOB_ID), timeout=TIMEOUT_MS) as res:
                ui.card.locator('button[data-job-action="drop"]').click()
            self.assertEqual(res.value.status, 200, res.value.text())
            ui.page.locator("#toasts .toast", has_text="Run dropped").wait_for(state="visible", timeout=TIMEOUT_MS)
        self.assertEqual(self.drop.calls, [JOB_ID])


if __name__ == "__main__":
    unittest.main()
