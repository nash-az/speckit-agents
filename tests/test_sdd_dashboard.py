#!/usr/bin/env python3
"""Tests for bin/sdd-dashboard.py (D301).

The unittest classes cover task status mapping and phase rollup (data-model.md §1, §2) and the
tasks/blocked endpoints; `unittest discover` runs them. The `main()` script below
starts the dashboard server on a test port, creates sample .sdd/runs/ data
and status.json, then asserts the API endpoints return correct data.
"""

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import urllib.error
from pathlib import Path

PORT = 19765
PROJECT = Path("/tmp/speckit-dashboard-test")

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("sdd_dashboard", HERE.parent / "bin" / "sdd-dashboard.py")
dash = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dash)

STATUS_TASKS = """# Tasks

## Phase 1: Setup

- [x] T001 Done already in `a.py`
- [ ] T002 [manual] Record audio
- [ ] T003 [decision] Pick a database
- [ ] T004 [dep] Add a package
- [ ] T005 [download] Fetch the model
- [ ] T006 Needs work after T002
- [ ] T007 Ran and blocked in `b.py`
- [ ] T008 Dropped in `c.py`
- [ ] T009 Moved elsewhere in `d.py`
- [ ] T010 Answered and runnable in `e.py`
- [ ] T011 Old approach, superseded-by T012
- [ ] T012 New approach in `f.py`
- [ ] T013 [dep] [dep:ok] Approved package
"""

STATUS_BLOCKED = """# Blocked tasks

## T007
- status: open
- tag: failed
- reason: checks failed twice
- question: Which API should it call?
- answer:

## T008
- status: wontfix
- reason: not needed

## T009
- status: moved
- reason: product work

## T010
- status: open
- question: Old question?

## T010
- status: resolved
- question: Which port?
- answer: Use 8080.
  Not 80.
"""


def write_project(root, tasks, blocked=None, config=None):
    (root / ".sdd").mkdir(parents=True, exist_ok=True)
    (root / "tasks.md").write_text(tasks, encoding="utf-8")
    if blocked is not None:
        (root / "BLOCKED.md").write_text(blocked, encoding="utf-8")
    if config is not None:
        (root / ".sdd" / "config.toml").write_text(config, encoding="utf-8")


class TaskStatusTest(unittest.TestCase):
    """data-model.md §2: each sdd-tasks.py hold maps to one status."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_project(self.root, STATUS_TASKS, STATUS_BLOCKED)
        self.result = dash.project_tasks(self.root / "tasks.md", self.root / "BLOCKED.md",
                                         self.root / ".sdd" / "config.toml")
        self.tasks = {t["id"]: t for p in self.result["phases"] for t in p["tasks"]}

    def tearDown(self):
        self.tmp.cleanup()

    def assertStatus(self, task_id, status, tag, question=None, answer=None):
        task = self.tasks[task_id]
        self.assertEqual((task["status"], task["status_tag"]), (status, tag), task_id)
        self.assertEqual(task["question"], question, task_id)
        self.assertEqual(task["answer"], answer, task_id)

    def test_shape(self):
        task = self.tasks["T002"]
        for key in ("id", "text", "tags", "parallel", "story", "files", "deps", "superseded_by",
                    "done", "line", "status", "status_tag", "status_reason", "question", "answer"):
            self.assertIn(key, task)
        self.assertEqual(self.result["text"], STATUS_TASKS)
        phase = self.result["phases"][0]
        self.assertEqual((phase["id"], phase["title"]), ("1", "Setup"))
        self.assertIn("status", phase)

    def test_done(self):
        self.assertStatus("T001", "done", None)
        self.assertIsNone(self.tasks["T001"]["status_reason"])

    def test_person_tags_are_held(self):
        for task_id, tag in (("T002", "manual"), ("T003", "decision"), ("T004", "dep"), ("T005", "download")):
            self.assertStatus(task_id, "held", tag)
            self.assertEqual(self.tasks[task_id]["status_reason"], dash.sdd_tasks.HOLD_TAGS[tag])

    def test_propagated_hold_is_held_by_dependency(self):
        self.assertStatus("T006", "held", "dependency")
        self.assertIn("T002", self.tasks["T006"]["status_reason"])

    def test_open_entry_is_blocked_with_question(self):
        self.assertStatus("T007", "blocked", "open", question="Which API should it call?")
        self.assertEqual(self.tasks["T007"]["status_reason"], "open entry in BLOCKED.md")

    def test_wontfix_and_moved(self):
        self.assertStatus("T008", "wontfix", "wontfix")
        self.assertStatus("T009", "moved", "moved")

    def test_resolved_entry_is_runnable_with_answer(self):
        self.assertStatus("T010", None, None, answer="Use 8080.\n  Not 80.")
        self.assertIsNone(self.tasks["T010"]["status_reason"])

    def test_explicit_superseded_names_successor(self):
        self.assertStatus("T011", "superseded", "T012")
        self.assertStatus("T012", None, None)

    def test_dep_ok_tag_is_runnable(self):
        self.assertStatus("T013", None, None)

    def test_allowed_dependencies_are_runnable(self):
        write_project(self.root, STATUS_TASKS, STATUS_BLOCKED, "[allow]\ndependencies = true\n")
        result = dash.project_tasks(self.root / "tasks.md", self.root / "BLOCKED.md",
                                    self.root / ".sdd" / "config.toml")
        tasks = {t["id"]: t for p in result["phases"] for t in p["tasks"]}
        self.assertIsNone(tasks["T004"]["status"])
        self.assertEqual(tasks["T005"]["status"], "held")

    def test_convergence_supersedes_older_phases(self):
        write_project(self.root, "# Tasks\n\n## Phase 1: Old\n\n- [ ] T020 [manual] Old work\n"
                                 "- [x] T021 Finished\n\n## Phase 2: Convergence\n\n- [ ] T090 Redo per T020\n")
        result = dash.project_tasks(self.root / "tasks.md", None, None)
        tasks = {t["id"]: t for p in result["phases"] for t in p["tasks"]}
        self.assertEqual((tasks["T020"]["status"], tasks["T020"]["status_tag"]), ("superseded", "Phase 2"))
        self.assertEqual(tasks["T021"]["status"], "done")
        self.assertIsNone(tasks["T090"]["status"])
        self.assertEqual([p["status"] for p in result["phases"]], ["superseded", "queued"])

    def test_missing_tasks_file(self):
        self.assertEqual(dash.project_tasks(self.root / "nope.md", None, None), {"text": "", "phases": []})
        self.assertEqual(dash.project_tasks(None, None, None), {"text": "", "phases": []})

    def test_phase_rollup_over_tasks(self):
        self.assertEqual(self.result["phases"][0]["status"], "blocked")


class RollupTest(unittest.TestCase):
    """data-model.md §1: the first key of the priority list that any task has."""

    ORDER = ["blocked", "retrying", "running", "held", "paused", "queued",
             "moved", "wontfix", "superseded", "done"]

    def test_order_is_exact(self):
        self.assertEqual(list(dash.ROLLUP_ORDER), self.ORDER)

    def test_each_key_beats_every_later_key(self):
        for i, key in enumerate(self.ORDER):
            for later in self.ORDER[i + 1:]:
                self.assertEqual(dash.rollup([later, key, later]), key, (key, later))

    def test_single_status(self):
        for key in self.ORDER:
            self.assertEqual(dash.rollup([key]), key)

    def test_open_runnable_task_counts_as_queued(self):
        self.assertEqual(dash.rollup(["done", None]), "queued")
        self.assertEqual(dash.rollup([None, "held"]), "held")

    def test_no_tasks_has_no_status(self):
        self.assertIsNone(dash.rollup([]))

    def test_all_done(self):
        self.assertEqual(dash.rollup(["done", "done"]), "done")


class BlockedEndpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_project(self.root, STATUS_TASKS, STATUS_BLOCKED)

    def tearDown(self):
        self.tmp.cleanup()

    def test_entries_from_parse_blocked_entries(self):
        result = dash.project_blocked(self.root / "BLOCKED.md")
        self.assertEqual(result["text"], STATUS_BLOCKED)
        by_id = {e["id"]: e for e in result["entries"]}
        self.assertEqual([e["id"] for e in result["entries"]], ["T007", "T008", "T009", "T010"])
        self.assertEqual(by_id["T007"], {"id": "T007", "status": "open", "tag": "failed",
                                         "reason": "checks failed twice",
                                         "question": "Which API should it call?", "answer": ""})
        self.assertEqual(by_id["T010"]["status"], "resolved")  # the last entry per id wins
        self.assertEqual(by_id["T010"]["question"], "Which port?")

    def test_missing_file(self):
        self.assertEqual(dash.project_blocked(self.root / "missing.md"), {"text": "", "entries": []})
        self.assertEqual(dash.project_blocked(None), {"text": "", "entries": []})

    def test_parsers_removed_from_dashboard(self):
        self.assertFalse(hasattr(dash, "parse_tasks"))
        self.assertFalse(hasattr(dash, "parse_blocked"))


class HandlerTest(unittest.TestCase):
    """The two endpoints through the request handler, without a socket (the sandbox may forbid
    binding a port)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_project(self.root / "specs" / "001-only", STATUS_TASKS, STATUS_BLOCKED)
        (self.root / ".sdd").mkdir()
        (self.root / ".sdd" / "config.toml").write_text("[run]\nmax_retries = 2\n", encoding="utf-8")
        self.saved =(dash.DashboardHandler.project, dash.DashboardHandler.feature)
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.root, None

    def tearDown(self):
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.saved
        self.tmp.cleanup()

    def get(self, path):
        return request(self, "GET", path)

    def test_tasks_endpoint(self):
        data = self.get("/api/project/tasks")
        tasks = {t["id"]: t for p in data["phases"] for t in p["tasks"]}
        self.assertEqual(tasks["T002"]["status"], "held")
        self.assertEqual(tasks["T007"]["question"], "Which API should it call?")
        self.assertEqual(data["phases"][0]["status"], "blocked")

    def test_blocked_endpoint(self):
        data = self.get("/api/project/blocked")
        self.assertEqual({e["id"] for e in data["entries"]}, {"T007", "T008", "T009", "T010"})


def request(test, method, path, body=None, status=200, host="127.0.0.1:8765"):
    """Run one request through DashboardHandler without a socket; returns the parsed body
    (JSON when it parses, else text). `host=None` sends no Host header."""
    handler = dash.DashboardHandler.__new__(dash.DashboardHandler)
    handler.path, handler.command = path, method
    handler.request_version, handler.requestline = "HTTP/1.1", f"{method} {path} HTTP/1.1"
    handler.wfile = io.BytesIO()
    raw = json.dumps(body).encode() if body is not None else b""
    handler.rfile = io.BytesIO(raw)
    handler.headers = {"Content-Length": str(len(raw))}
    if host is not None:
        handler.headers["Host"] = host
    handler.do_GET() if method == "GET" else handler.do_POST()
    head, _, out = handler.wfile.getvalue().partition(b"\r\n\r\n")
    test.assertEqual(head.split(b"\r\n")[0].split()[1], str(status).encode(), head)
    try:
        return json.loads(out)
    except ValueError:
        return out.decode()


class ActiveFeatureTest(unittest.TestCase):
    """data-model.md §4.2 (FR-027): config `run.feature`, then `.specify/feature.json`, then the
    only `specs/` folder, else 409 and the person chooses."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".sdd").mkdir()
        (self.root / ".specify").mkdir()
        for name in ("001-a", "002-b", "003-c"):
            (self.root / "specs" / name).mkdir(parents=True)
            (self.root / "specs" / name / "spec.md").write_text(f"# Spec {name}\n", encoding="utf-8")
        self.saved = (dash.DashboardHandler.project, dash.DashboardHandler.feature)
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.root, None

    def tearDown(self):
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.saved
        self.tmp.cleanup()

    def config(self, feature):
        (self.root / ".sdd" / "config.toml").write_text(f'[run]\nfeature = "{feature}"\n', encoding="utf-8")

    def feature_json(self, directory):
        (self.root / ".specify" / "feature.json").write_text(
            json.dumps({"feature_directory": directory}), encoding="utf-8")

    def test_config_wins(self):
        self.config("specs/001-a")
        self.feature_json("specs/002-b")
        self.assertEqual(dash.active_feature(self.root), self.root / "specs" / "001-a")
        self.assertEqual(request(self, "GET", "/api/project/spec"), "# Spec 001-a\n")

    def test_feature_json_when_config_empty(self):
        self.config("")
        self.feature_json("specs/002-b")
        self.assertEqual(dash.active_feature(self.root), self.root / "specs" / "002-b")
        self.assertEqual(request(self, "GET", "/api/project/spec"), "# Spec 002-b\n")

    def test_single_folder(self):
        for name in ("001-a", "003-c"):
            shutil.rmtree(self.root / "specs" / name)
        self.assertEqual(dash.active_feature(self.root), self.root / "specs" / "002-b")
        self.assertEqual(request(self, "GET", "/api/project/spec"), "# Spec 002-b\n")

    def test_409_with_several_folders(self):
        self.config("")
        self.assertIsNone(dash.active_feature(self.root))
        expected = {"error": "choose a feature", "features": ["001-a", "002-b", "003-c"]}
        for path in ("/api/project/spec", "/api/project/plan", "/api/project/tasks",
                     "/api/project/blocked", "/api/project/decisions"):
            self.assertEqual(request(self, "GET", path, status=409), expected, path)

    def test_config_is_not_per_feature(self):
        self.config("")
        self.assertIn("[run]", request(self, "GET", "/api/project/config"))

    def test_select_feature_in_memory(self):
        files = sorted(str(p) for p in self.root.rglob("*"))
        result = request(self, "POST", "/api/project/select", {"feature": "003-c"})
        self.assertEqual(result, {"ok": True, "path": str(self.root), "feature": "003-c"})
        self.assertEqual(request(self, "GET", "/api/project/spec"), "# Spec 003-c\n")
        self.assertEqual(sorted(str(p) for p in self.root.rglob("*")), files)  # nothing written

    def test_select_unknown_feature(self):
        result = request(self, "POST", "/api/project/select", {"feature": "../../etc"}, status=400)
        self.assertEqual(result["error"], "unknown feature")
        self.assertIsNone(dash.DashboardHandler.feature)

    def test_find_file_removed(self):
        self.assertFalse(hasattr(dash, "find_file"))


class DocEndpointTest(unittest.TestCase):
    """data-model.md §4.3 (FR-001): `/api/doc?name=` serves a fixed name set, nothing else."""

    FEATURE_FILES = {"spec": "spec.md", "plan": "plan.md", "research": "research.md",
                     "data-model": "data-model.md", "quickstart": "quickstart.md", "tasks": "tasks.md",
                     "blocked": "BLOCKED.md", "decisions": "decisions.md"}
    PROJECT_FILES = {"constitution": ".specify/memory/constitution.md",
                     "learnings": ".sdd/learnings.md", "config": ".sdd/config.toml"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.feature = self.root / "specs" / "001-only"
        self.feature.mkdir(parents=True)
        (self.root / ".specify" / "memory").mkdir(parents=True)
        (self.root / ".sdd" / "runs").mkdir(parents=True)
        self.saved = (dash.DashboardHandler.project, dash.DashboardHandler.feature)
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.root, None

    def tearDown(self):
        dash.DashboardHandler.project, dash.DashboardHandler.feature = self.saved
        self.tmp.cleanup()

    def write_all(self):
        for name, rel in self.FEATURE_FILES.items():
            (self.feature / rel).write_text(f"# {name}\n", encoding="utf-8")
        for name, rel in self.PROJECT_FILES.items():
            (self.root / rel).write_text(f"# {name}\n", encoding="utf-8")

    def test_name_set_is_exact(self):
        self.assertEqual(set(dash.DOC_NAMES), set(self.FEATURE_FILES) | set(self.PROJECT_FILES))

    def test_each_name(self):
        self.write_all()
        for name in list(self.FEATURE_FILES) + list(self.PROJECT_FILES):
            self.assertEqual(request(self, "GET", f"/api/doc?name={name}"), f"# {name}\n", name)

    def test_content_types(self):
        self.write_all()
        self.assertEqual(content_type(self, "/api/doc?name=config"), "text/plain")
        self.assertEqual(content_type(self, "/api/doc?name=spec"), "text/markdown")

    def test_each_name_missing(self):
        for name in list(self.FEATURE_FILES) + list(self.PROJECT_FILES):
            self.assertEqual(request(self, "GET", f"/api/doc?name={name}"), {"missing": True}, name)

    def test_unknown_names_are_404(self):
        for query in ("name=readme", "name=../../etc/passwd", "name=..%2F..%2Fetc%2Fpasswd",
                      "name=spec.md", "name=", "", "name=spec&name=plan"):
            request(self, "GET", f"/api/doc?{query}", status=404)

    def test_feature_doc_needs_a_feature(self):
        (self.root / "specs" / "002-other").mkdir()
        (self.root / ".sdd" / "config.toml").write_text("# cfg\n", encoding="utf-8")
        result = request(self, "GET", "/api/doc?name=research", status=409)
        self.assertEqual(result, {"error": "choose a feature", "features": ["001-only", "002-other"]})
        self.assertEqual(request(self, "GET", "/api/doc?name=config"), "# cfg\n")  # project-wide

    def test_aliases(self):
        self.write_all()
        for alias in ("spec", "plan", "decisions", "config"):
            self.assertEqual(request(self, "GET", f"/api/project/{alias}"), f"# {alias}\n", alias)
        (self.feature / "plan.md").unlink()
        self.assertEqual(request(self, "GET", "/api/project/plan"), {"missing": True})

    def test_report_run_id_validated(self):
        (self.root / ".sdd" / "runs" / "20261027-1431.md").write_text("# Report\n", encoding="utf-8")
        (self.root / ".sdd" / "secret.md").write_text("# Secret\n", encoding="utf-8")
        self.assertEqual(request(self, "GET", "/api/runs/20261027-1431/report"), "# Report\n")
        for bad in ("run-001", "..%2Fsecret", "..", "2026102-1431", "20261027-1431X", "20261027-1431.md"):
            request(self, "GET", f"/api/runs/{bad}/report", status=404)
        request(self, "GET", "/api/runs/20261027-0000/report", status=404)  # valid id, no report


def content_type(test, path):
    handler = dash.DashboardHandler.__new__(dash.DashboardHandler)
    handler.path, handler.command = path, "GET"
    handler.request_version, handler.requestline = "HTTP/1.1", f"GET {path} HTTP/1.1"
    handler.wfile, handler.rfile, handler.headers = io.BytesIO(), io.BytesIO(), {"Host": "localhost:8765"}
    handler.do_GET()
    head = handler.wfile.getvalue().partition(b"\r\n\r\n")[0].decode()
    return re.search(r"Content-Type: ([^\r\n]+)", head).group(1)


class HostAndDiscoveryTest(unittest.TestCase):
    """data-model.md §4.7 (FR-015, FR-022): localhost Host names only; one project."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "started"
        (self.root / ".sdd").mkdir(parents=True)
        self.saved = (dash.DashboardHandler.project, dash.DashboardHandler.started_project,
                      dash.DashboardHandler.feature)
        dash.DashboardHandler.project, dash.DashboardHandler.started_project = self.root, self.root
        dash.DashboardHandler.feature = None

    def tearDown(self):
        (dash.DashboardHandler.project, dash.DashboardHandler.started_project,
         dash.DashboardHandler.feature) = self.saved
        self.tmp.cleanup()

    def test_foreign_host_is_421(self):
        for host in ("evil.example", "evil.example:8765", "127.0.0.1.evil.example", None):
            request(self, "GET", "/api/projects", host=host, status=421)
            request(self, "POST", "/api/project/select", {"feature": "x"}, host=host, status=421)

    def test_localhost_names_are_allowed(self):
        for host in ("127.0.0.1", "127.0.0.1:8765", "localhost", "LOCALHOST:1", "[::1]", "[::1]:8765"):
            request(self, "GET", "/api/projects", host=host)

    def test_projects_has_only_the_started_project(self):
        other = Path(self.tmp.name) / "other"
        (other / ".specify").mkdir(parents=True)
        self.assertEqual(request(self, "GET", "/api/projects"),
                         [{"path": str(self.root), "name": "started"}])
        dash.DashboardHandler.project = other  # selecting another project does not add it
        self.assertEqual(len(request(self, "GET", "/api/projects")), 1)


class StatusSystemTest(unittest.TestCase):
    """data-model.md §1 (FR-003, SC-002): CSS colors, GLYPHS, STATUS and statusBadge() in build_html()."""

    COLORS = {"queued": "#8b93a1", "running": "#3b82f6", "retrying": "#eab308", "paused": "#64748b",
              "done": "#22c55e", "held": "#f59e0b", "blocked": "#ef4444", "wontfix": "#6b7280",
              "moved": "#a78bfa", "superseded": "#94a3b8"}
    GLYPH_OF = {"queued": "circle-outline", "running": "spinner", "retrying": "rotate-arrow",
                "paused": "pause-bars", "done": "check", "held": "clock", "blocked": "alert-triangle",
                "wontfix": "slash-circle", "moved": "arrow-up-right", "superseded": "arrow-right"}

    @classmethod
    def setUpClass(cls):
        cls.html = dash.build_html()
        start = cls.html.index("function escapeHtml(")
        cls.js = cls.html[start:cls.html.index("function loadProjects(")]

    def test_css_variable_per_key(self):
        self.assertEqual(set(self.COLORS), set(dash.ROLLUP_ORDER))
        for key, color in self.COLORS.items():
            self.assertIn("--st-%s: %s;" % (key, color), self.html)
            self.assertIn(".status-%s { color: var(--st-%s); }" % (key, key), self.html)

    def test_no_glyph_and_color_shared(self):
        pairs = {(self.GLYPH_OF[k], self.COLORS[k]) for k in self.COLORS}
        self.assertEqual(len(pairs), len(self.COLORS))

    def test_spinner_animates(self):
        self.assertRegex(self.html, r"svg\.glyph-spinner \{ animation: [a-z-]+ 1s linear infinite; \}")

    def test_no_icon_font_or_cdn(self):
        for needle in ("font-awesome", "fontawesome", "material-icons", "Material+Icons", "lucide", "feather"):
            self.assertNotIn(needle.lower(), self.html.lower())

    def run_js(self, expr):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        script = self.js + "\nprocess.stdout.write(JSON.stringify(" + expr + "));"
        out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_glyphs_and_status_objects(self):
        got = self.run_js("{glyphs: GLYPHS, status: STATUS}")
        self.assertEqual(set(got["glyphs"]), set(self.GLYPH_OF.values()))
        for svg in got["glyphs"].values():
            for attr in ('viewBox="0 0 24 24"', 'stroke="currentColor"', 'fill="none"', 'stroke-width="2"'):
                self.assertIn(attr, svg)
        self.assertEqual({k: v["glyph"] for k, v in got["status"].items()}, self.GLYPH_OF)

    def test_status_badge_markup(self):
        got = self.run_js("[statusBadge('held', {tag: 'manual'}), statusBadge('running', {step: 'Verifying'}),"
                          " statusBadge('retrying', {n: 1, max: 2}), statusBadge('superseded', {by: 'T020'}),"
                          " statusBadge('wontfix'), statusBadge('queued', {label: 'Answered — runs next'}),"
                          " statusBadge('held'), GLYPHS['clock']]")
        held, running, retry, sup, wontfix, answered, bare, clock = got
        self.assertEqual(held, '<span class="status status-held" title="Held: manual">' + clock +
                         '<span class="status-label">Held: manual</span></span>')
        self.assertIn('title="Running: Verifying"', running)
        self.assertIn('class="glyph-spinner"', running)
        self.assertIn('>Retry 1/2<', retry)
        self.assertIn('>Superseded by T020<', sup)
        self.assertIn('>Won&#39;t fix<', wontfix)
        self.assertIn('>Answered — runs next<', answered)
        self.assertIn('>Held<', bare)


class TasksViewTest(unittest.TestCase):
    """T018 (FR-004, FR-005): task cards, phase rollup badges, "Waiting on you", BLOCKED.md entry badges."""

    setUpClass = classmethod(StatusSystemTest.setUpClass.__func__)
    run_js = StatusSystemTest.run_js

    def test_task_status_key(self):
        got = self.run_js("[taskStatusKey({status: 'held'}), taskStatusKey({status: null, done: true}),"
                          " taskStatusKey({status: null, done: false})]")
        self.assertEqual(got, ["held", "done", "queued"])

    def test_task_card_badges(self):
        got = self.run_js("[taskCardHtml({id: 'T1', text: 'a', tags: ['manual'], status: 'held', status_tag: 'manual'}),"
                          " taskCardHtml({id: 'T2', text: 'b', tags: [], status: 'superseded', status_tag: 'T9'}),"
                          " taskCardHtml({id: 'T3', text: 'c', tags: [], status: null, done: true}),"
                          " taskCardHtml({id: 'T4', text: 'd', tags: [], status: null, done: false}),"
                          " statusBadge('done'), statusBadge('queued')]")
        held, sup, done, queued, done_badge, queued_badge = got
        self.assertIn('title="Held: manual"', held)
        self.assertIn('>Superseded by T9<', sup)
        self.assertIn(done_badge, done)
        self.assertIn("task-done", done)
        self.assertIn(queued_badge, queued)

    def test_phase_heading_rollup(self):
        got = self.run_js("[phaseHeadingHtml({title: 'Setup', status: 'blocked'}), phaseHeadingHtml({title: 'Empty', status: null}),"
                          " statusBadge('blocked')]")
        self.assertIn(got[2], got[0])
        self.assertNotIn('class="status ', got[1])

    def test_waiting_on_you(self):
        tasks = ("[{id: 'T1', text: 'a', tags: ['manual'], status: 'held', status_tag: 'manual',"
                 " status_reason: 'needs a person'},"
                 " {id: 'T2', text: 'b', tags: [], status: 'blocked', status_tag: 'open',"
                 " status_reason: 'stuck', question: 'Which port?'},"
                 " {id: 'T3', text: 'c', tags: [], status: 'wontfix'},"
                 " {id: 'T4', text: 'd', tags: [], status: null, done: false},"
                 " {id: 'T5', text: 'e', tags: [], status: 'done', done: true}]")
        got = self.run_js("(function(ts) { return {waiting: ts.filter(isWaiting).map(function(t) { return t.id; }),"
                          " held: waitingItemHtml(ts[0], 'Phase 1'), blocked: waitingItemHtml(ts[1], 'Phase 1'),"
                          " counts: countTasks([{tasks: ts}])}; })(" + tasks + ")")
        self.assertEqual(got["waiting"], ["T1", "T2"])
        self.assertIn("needs a person", got["held"])
        self.assertIn("Which port?", got["blocked"])
        self.assertIn('title="Blocked"', got["blocked"])
        self.assertEqual(got["counts"], {"total": 5, "done": 1, "waiting": 2, "blocked": 1})

    def test_entry_badges(self):
        got = self.run_js("[entryBadge({status: 'open'}), entryBadge({status: 'resolved'}), entryBadge({status: 'wontfix'}),"
                          " entryBadge({status: 'moved'}), statusBadge('blocked'), statusBadge('wontfix'), statusBadge('moved'),"
                          " entryItemHtml({id: 'T7', status: 'resolved', reason: 'r', question: 'q?', answer: 'yes', tag: 'decision'})]")
        open_, resolved, wontfix, moved, b_blocked, b_wontfix, b_moved, item = got
        self.assertEqual(open_, b_blocked)
        self.assertIn('class="status status-queued" title="Answered — runs next"', resolved)
        self.assertEqual(wontfix, b_wontfix)
        self.assertEqual(moved, b_moved)
        self.assertIn("Answered — runs next", item)
        self.assertIn(">yes<", item)

    def test_old_held_logic_gone(self):
        self.assertNotIn("Held / Waiting", self.html)
        self.assertNotIn("loadBlockedData", self.html)
        self.assertIn("Waiting on you", self.html)


class DocumentsViewTest(unittest.TestCase):
    """T019 (FR-001, data-model.md §4.3): pinned renderer URLs, sanitized markdown, TOC, offline fallback."""

    setUpClass = classmethod(StatusSystemTest.setUpClass.__func__)
    run_js = StatusSystemTest.run_js

    URLS = ('<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>',
            '<script src="https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/highlight.min.js"></script>',
            '<script src="https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js"></script>',
            '<link href="https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/styles/github-dark.min.css" rel="stylesheet">')

    def test_pinned_urls(self):
        for tag in self.URLS:
            self.assertIn(tag, self.html)
        self.assertNotIn("npm/highlight.js", self.html)

    def test_nav_entry_per_doc_name(self):
        got = self.run_js("DOC_TITLES")
        self.assertEqual(set(got), set(dash.DOC_NAMES))
        for name in dash.DOC_NAMES:
            if name in ("tasks", "blocked"):
                self.assertIn('data-raw="%s"' % name, self.html)
            else:
                self.assertIn('class="nav-item" data-section="%s"' % name, self.html)
        self.assertIn("Raw document", self.html)
        self.assertIn("'Not written yet'", self.html)

    def test_toc_needs_four_headings(self):
        got = self.run_js("[tocFor([{level: 'h2', text: 'A'}, {level: 'h2', text: 'B'}, {level: 'h3', text: 'C'}], 'doc'),"
                          " tocFor([{level: 'h2', text: 'One thing'}, {level: 'h3', text: 'One thing'},"
                          " {level: 'h2', text: '<b>x</b>'}, {level: 'h3', text: '§4.3 Docs'}], 'doc-content')]")
        none, toc = got
        self.assertIsNone(none)
        self.assertEqual(toc["ids"], ["doc-content-one-thing", "doc-content-one-thing-2",
                                      "doc-content-b-x-b", "doc-content-4-3-docs"])
        self.assertIn('<li class="toc-h3"><a href="#doc-content-one-thing-2">One thing</a></li>', toc["html"])
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", toc["html"])

    def test_fallback_without_libraries(self):
        got = self.run_js("(function() { var el = {innerHTML: ''}; renderDoc(el, '# T <script>', 'markdown');"
                          " return [docLibs(), el.innerHTML, fallbackHtml('x', DOC_NOTICE)]; })()")
        libs, html, plain = got
        self.assertIsNone(libs)
        self.assertIn('<div class="doc-notice">', html)
        self.assertIn('<pre class="doc-plain"># T &lt;script&gt;</pre>', html)
        self.assertEqual(html.count("<div"), 1)  # one notice line, then the old <pre> view

    def test_markdown_is_sanitized_then_highlighted(self):
        got = self.run_js(
            "(function() { var calls = [], codes = [{c: 1}, {c: 2}];"
            " var libs = {marked: {parse: function(t) { calls.push('parse'); return '<p>' + t + '</p>'; }},"
            " DOMPurify: {sanitize: function(h) { calls.push('sanitize'); return 'CLEAN' + h; }},"
            " hljs: {highlightElement: function(c) { calls.push('hl' + c.c); }}};"
            " var el = {id: 'doc-content', innerHTML: '', querySelectorAll: function(s) { return s === 'pre code' ? codes : []; }};"
            " renderDoc(el, 'hi', 'markdown', libs); var md = el.innerHTML;"
            " renderDoc(el, 'a = <1>', 'text', libs);"
            " return {calls: calls, md: md, text: el.innerHTML}; })()")
        self.assertEqual(got["calls"], ["parse", "sanitize", "hl1", "hl2", "hl1", "hl2"])
        self.assertEqual(got["md"], "CLEAN<p>hi</p>")
        self.assertEqual(got["text"], '<pre><code class="language-ini">a = &lt;1&gt;</code></pre>')

    def test_doc_helpers(self):
        got = self.run_js("[docUrl('data-model'), docKind('config'), docKind('spec')]")
        self.assertEqual(got, ["/api/doc?name=data-model", "text", "markdown"])

    def test_reports_render_as_documents(self):
        self.assertIn("showDoc(el, '/api/runs/' + encodeURIComponent(runId) + '/report', 'markdown')", self.html)
        self.assertNotIn("function loadFile(", self.html)


class FeaturePickerTest(unittest.TestCase):
    """T020 (FR-027, data-model.md §4.2): a 409 from a per-feature request shows a picker of `features`;
    choosing one posts `{"feature": name}` to /api/project/select and reloads the views."""

    setUpClass = classmethod(StatusSystemTest.setUpClass.__func__)
    run_js = StatusSystemTest.run_js

    def test_picker_features(self):
        got = self.run_js("[pickerFeatures(409, {error: 'choose a feature', features: ['001-a', '002-b']}),"
                          " pickerFeatures(404, {features: ['001-a']}), pickerFeatures(409, {error: 'x'}),"
                          " pickerFeatures(409, null)]")
        self.assertEqual(got, [["001-a", "002-b"], None, None, None])

    def test_picker_markup(self):
        got = self.run_js("[featurePickerHtml(['001-a', '<b>\"x']), featurePickerHtml([])]")
        buttons, empty = got
        self.assertEqual(buttons.count("<button"), 2)
        self.assertIn('data-feature="001-a">001-a</button>', buttons)
        self.assertIn('data-feature="&lt;b&gt;&quot;x">&lt;b&gt;&quot;x</button>', buttons)
        self.assertNotIn("<button", empty)

    def test_markup_and_wiring(self):
        self.assertIn('id="feature-picker"', self.html)
        self.assertIn('id="feature-picker-list"', self.html)
        self.assertIn("fetchJSON('/api/project/select', 'POST', { feature: name })", self.html)
        chooser = self.html[self.html.index("function chooseFeature("):self.html.index("function reloadViews(")]
        self.assertIn("reloadViews();", chooser)
        doc = self.html[self.html.index("function showDoc("):self.html.index("function docUrl(")]
        self.assertIn("showFeaturePicker(features)", doc)

    def test_fetch_json_409_shows_picker(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        fetch_json = self.html[self.html.index("function fetchJSON("):self.html.index("function escapeHtml(")]
        script = (
            "var shown = [];\n"
            "function showFeaturePicker(f) { shown.push(f); }\n"
            "function fakeRes(status, body) { return {status: status, ok: status < 300,"
            " json: function() { return Promise.resolve(body); }}; }\n"
            "var answers = {'/a': fakeRes(409, {error: 'choose a feature', features: ['001-a', '002-b']}),"
            " '/b': fakeRes(200, {phases: []}), '/c': fakeRes(404, {error: 'nope'})};\n"
            "function fetch(url) { return Promise.resolve(answers[url]); }\n"
            + self.html[self.html.index("function pickerFeatures("):self.html.index("function featurePickerHtml(")]
            + fetch_json +
            "\nPromise.all([fetchJSON('/a'), fetchJSON('/b'), fetchJSON('/c')]).then(function(r) {"
            " process.stdout.write(JSON.stringify({results: r, shown: shown})); });")
        out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
        got = json.loads(out.stdout)
        self.assertEqual(got["results"], [None, {"phases": []}, None])
        self.assertEqual(got["shown"], [["001-a", "002-b"]])


class ReadOnlyTest(unittest.TestCase):
    """data-model.md §4.8 (FR-020): the dashboard's source has no way to write or run anything."""

    FORBIDDEN = (r"write_text", r"\.unlink\(", r"subprocess", r"os\.remove", r"shutil\.", r"git ",
                 r"""open\([^)]*,\s*(mode\s*=\s*)?['"][rbt]*[wax+]""",  # a write-mode open(
                 r"""\.open\(\s*(mode\s*=\s*)?['"][rbt]*[wax+]""")  # a write-mode Path.open(

    def test_source_has_no_writes(self):
        source = (HERE.parent / "bin" / "sdd-dashboard.py").read_text(encoding="utf-8")
        for pattern in self.FORBIDDEN:
            self.assertIsNone(re.search(pattern, source), pattern)


def wait_for(url, timeout=10):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(0.2)
    raise last


def api(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5) as r:
        return json.loads(r.read())


def apiText(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5) as r:
        return r.read().decode()


def main():
    if PROJECT.exists():
        shutil.rmtree(PROJECT)
    (PROJECT / ".sdd" / "runs").mkdir(parents=True)

    # Sample feature files, in the folder `run.feature` names
    feature = PROJECT / "specs" / "001"
    feature.mkdir(parents=True)
    (feature / "spec.md").write_text("# Feature Spec\n\n## User Story 1\n\nDo something.\n")
    (feature / "plan.md").write_text("# Plan\n\n- [ ] T101 Design\n- [ ] T102 Implement\n")
    (feature / "tasks.md").write_text("# Tasks\n\n## Phase 1: Setup\n\n- [x] T001 [P] First task in `a.py`\n- [ ] T002 [manual] Needs a person in `b.py`\n")
    (feature / "BLOCKED.md").write_text("## T002\n- status: open\n- reason: needs recording\n- question: Who will record?\n")
    (feature / "decisions.md").write_text("# Decisions\n\n- T011: Use Playwright (2026-10-24)\n")
    (PROJECT / ".sdd" / "config.toml").write_text("[run]\nfeature = \"specs/001\"\n")

    # Sample events.jsonl for run-001
    events = [
        {"ts": 1000, "kind": "phase.started", "id": "1", "title": "Phase 1", "taskCount": 2},
        {"ts": 1001, "kind": "task.started", "id": "T1", "tags": ["P"], "files": ["a.py"], "deps": []},
        {"ts": 1002, "kind": "task.implemented", "id": "T1", "attempt": 0, "summary": "done", "files": ["a.py"]},
        {"ts": 1003, "kind": "task.verified", "id": "T1", "verdict": "approve", "problems": []},
        {"ts": 1004, "kind": "task.approved", "id": "T1", "files": ["a.py"]},
        {"ts": 1005, "kind": "task.started", "id": "T2", "tags": ["manual"], "files": ["b.py"], "deps": []},
        {"ts": 1006, "kind": "task.blocked", "id": "T2", "tag": "manual", "reason": "needs a person"},
        {"ts": 1007, "kind": "run.finished", "run_id": "run-001", "stopped": None, "approved": 1, "blocked": 1, "skipped": 0},
    ]
    (PROJECT / ".sdd" / "runs" / "run-001.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    (PROJECT / ".sdd" / "runs" / "20261027-1431.md").write_text("# Run Report\n\n- approved: T1\n- blocked: T2\n")

    # Sample status.json (same slug computation as the server)
    resolved = str(PROJECT.resolve())
    slug = re.sub(r"[^A-Za-z0-9]+", "-", resolved).strip("-")
    state_dir = Path(os.path.expanduser("~/.local/state/speckit-agents")) / slug
    state_dir.mkdir(parents=True, exist_ok=True)
    status = {
        "state": "running",
        "cycle": 1,
        "usage": {
            "five_hour": {"utilization": 0.87, "resets_at": int(time.time()) + 3600},
            "seven_day": {"utilization": 0.45, "resets_at": int(time.time()) + 86400},
        },
    }
    (state_dir / "status.json").write_text(json.dumps(status, indent=2))

    proc = subprocess.Popen([sys.executable, "bin/sdd-dashboard.py", "--project", str(PROJECT), "--port", str(PORT)])
    try:
        wait_for(f"http://127.0.0.1:{PORT}/")

        # /api/runs
        runs = api("/api/runs")
        assert len(runs) == 1, f"expected 1 run, got {len(runs)}"
        r = runs[0]
        assert r["id"] == "run-001", f"expected run-001, got {r['id']}"
        assert r["approved"] == 1, f"expected 1 approved, got {r['approved']}"
        assert r["blocked"] == 1, f"expected 1 blocked, got {r['blocked']}"
        assert r["finished"] is True
        assert r["stopped"] is None
        print("PASS: /api/runs")

        # /api/runs/<id>/events
        evs = api("/api/runs/run-001/events")
        assert len(evs) == 8, f"expected 8 events, got {len(evs)}"
        assert evs[0]["kind"] == "phase.started"
        assert evs[-1]["kind"] == "run.finished"
        print("PASS: /api/runs/run-001/events")

        # /api/runs/<id>/report
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/runs/20261027-1431/report")
        with urllib.request.urlopen(req, timeout=5) as resp:
            report = resp.read().decode()
        assert "# Run Report" in report
        assert "approved: T1" in report
        print("PASS: /api/runs/20261027-1431/report")

        # /api/status
        st = api("/api/status")
        assert st is not None
        assert st["state"] == "running"
        assert st["cycle"] == 1
        assert st["usage"]["five_hour"]["utilization"] == 0.87
        print("PASS: /api/status")

        # /api/project/spec
        spec = apiText("/api/project/spec")
        assert "# Feature Spec" in spec
        print("PASS: /api/project/spec")

        # /api/project/plan
        plan = apiText("/api/project/plan")
        assert "# Plan" in plan
        print("PASS: /api/project/plan")

        # /api/project/tasks
        tasks = api("/api/project/tasks")
        assert tasks is not None
        assert "phases" in tasks
        assert len(tasks["phases"]) == 1
        assert tasks["phases"][0]["title"] == "Setup"
        assert len(tasks["phases"][0]["tasks"]) == 2
        assert tasks["phases"][0]["tasks"][0]["id"] == "T001"
        assert tasks["phases"][0]["tasks"][0]["done"] is True
        assert tasks["phases"][0]["tasks"][1]["id"] == "T002"
        assert tasks["phases"][0]["tasks"][1]["done"] is False
        assert "manual" in tasks["phases"][0]["tasks"][1]["tags"]
        print("PASS: /api/project/tasks")

        # /api/project/blocked
        blocked = api("/api/project/blocked")
        assert len(blocked["entries"]) == 1
        assert blocked["entries"][0]["id"] == "T002"
        assert blocked["entries"][0]["status"] == "open"
        print("PASS: /api/project/blocked")

        # /api/project/decisions
        decisions = apiText("/api/project/decisions")
        assert "# Decisions" in decisions
        print("PASS: /api/project/decisions")

        # /api/project/config
        config = apiText("/api/project/config")
        assert "[run]" in config
        print("PASS: /api/project/config")

        # /api/runs/<id>/report not found
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/runs/20261027-0000/report", timeout=5)
            assert False, "expected 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
        print("PASS: 404 for missing report")

        print("\nAll dashboard tests passed.")
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        shutil.rmtree(PROJECT, ignore_errors=True)
        shutil.rmtree(state_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
