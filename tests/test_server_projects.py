"""008 data-model §6 and §10: server/projects.py — `GET /api/projects`, `POST /api/projects/add`,
`POST /api/projects/<id>/remove` and `POST /api/p/<id>/feature`, with §6's project tests (5 projects,
the same one again and through a symlink, a moved folder shows as missing, the list survives a
restart).

Every server here runs with temporary HOME, XDG_STATE_HOME and XDG_DATA_HOME and without the Claude
session's variables (the `TempHome` base of tests/test_server_core.py).
"""
import hashlib
import importlib.util
import json
import subprocess
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load("test_server_core_for_projects", HERE / "test_server_core.py")
request = core.request


def make_project(path: Path, sdd=True, git=True, features=("001-a", "002-b")):
    """A toy project: a git repository with `.sdd/config.toml` and feature folders under specs/."""
    path.mkdir(parents=True)
    if git:
        subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True, timeout=30)
    if sdd:
        (path / ".sdd").mkdir()
        (path / ".sdd" / "config.toml").write_text("[checks]\ncommands = []\n", encoding="utf-8")
    for name in features:
        (path / "specs" / name).mkdir(parents=True)
    return path


class ProjectsServer(core.TempHome):
    """An in-process server with the repository as its engine."""

    def setUp(self):
        super().setUp()
        self.home = self.base / "home"
        self.projects_json = self.base / "state" / "speckit-agents" / "server" / "projects.json"
        self.serve()

    def serve(self):
        self.holder = core.app.EngineHolder(REPO)
        self.holder.refresh()
        self.server = core.app.make_server(0, self.holder)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop_serving(self):
        self.server.shutdown()
        self.server.server_close()

    def tearDown(self):
        self.stop_serving()
        super().tearDown()

    def post(self, path, body):
        return request(self.port, "POST", path, {"Origin": self.origin, "Content-Type": "application/json"},
                       json.dumps(body).encode("utf-8"))

    def listed(self):
        status, _, data = request(self.port, "GET", "/api/projects")
        self.assertEqual(status, 200)
        return data["projects"]


class AddAndListTest(ProjectsServer):
    def test_five_projects_are_added_and_listed(self):
        paths = [make_project(self.home / "work" / f"p{i}") for i in range(5)]
        for path in paths:
            status, _, entry = self.post("/api/projects/add", {"path": str(path)})
            self.assertEqual(status, 200, entry)
            self.assertEqual(entry["path"], str(path.resolve()))
            self.assertEqual(entry["id"], hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12])
            self.assertEqual(entry["name"], path.name)
            self.assertIsNone(entry["feature"])
            self.assertIsInstance(entry["added"], int)
        rows = self.listed()
        self.assertEqual([r["path"] for r in rows], [str(p) for p in paths])
        for row in rows:
            self.assertEqual(set(row), {"id", "path", "name", "missing", "state", "origin", "job"})
            self.assertFalse(row["missing"])
            self.assertEqual(row["state"], "none")
            self.assertIsNone(row["origin"])
            self.assertIsNone(row["job"])
        data = json.loads(self.projects_json.read_text(encoding="utf-8"))
        self.assertEqual(data["contract"], "1.0")
        self.assertEqual(len(data["projects"]), 5)
        self.assertEqual(set(data["projects"][0]), {"id", "path", "name", "added", "feature"})

    def test_adding_again_or_through_a_symlink_gives_one_entry(self):
        path = make_project(self.home / "app")
        link = self.home / "link-to-app"
        link.symlink_to(path)
        first = self.post("/api/projects/add", {"path": str(path)})[2]
        again = self.post("/api/projects/add", {"path": str(path) + "/"})[2]
        through_link = self.post("/api/projects/add", {"path": str(link)})[2]
        self.assertEqual(first, again)
        self.assertEqual(first, through_link)
        self.assertEqual(len(self.listed()), 1)

    def test_a_shared_base_name_adds_the_parent(self):
        a = make_project(self.home / "private" / "app")
        b = make_project(self.home / "work" / "app")
        self.post("/api/projects/add", {"path": str(a)})
        self.assertEqual(self.listed()[0]["name"], "app")
        self.post("/api/projects/add", {"path": str(b)})
        self.assertEqual([r["name"] for r in self.listed()], ["app (private)", "app (work)"])
        self.post(f"/api/projects/{self.listed()[1]['id']}/remove", {})
        self.assertEqual([r["name"] for r in self.listed()], ["app"])

    def test_a_moved_folder_shows_as_missing_and_stays_listed(self):
        path = make_project(self.home / "app")
        pid = self.post("/api/projects/add", {"path": str(path)})[2]["id"]
        path.rename(self.home / "moved")
        rows = self.listed()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["missing"])
        self.assertIsNone(rows[0]["state"])
        status, _, data = self.post(f"/api/p/{pid}/feature", {"feature": None})
        self.assertEqual((status, data), (410, {"error": "project folder is missing"}))

    def test_a_folder_without_sdd_config_counts_as_missing(self):
        path = make_project(self.home / "app")
        self.post("/api/projects/add", {"path": str(path)})
        (path / ".sdd" / "config.toml").unlink()
        self.assertTrue(self.listed()[0]["missing"])

    def test_the_list_survives_a_restart(self):
        paths = [make_project(self.home / f"p{i}") for i in range(5)]
        for path in paths:
            self.post("/api/projects/add", {"path": str(path)})
        before = self.listed()
        self.stop_serving()
        self.serve()
        self.assertEqual(self.listed(), before)

    def test_the_list_survives_a_restart_of_the_server_process(self):
        self.stop_serving()
        proc, port = self.start("--port", "0", "--engine", str(REPO))
        origin = f"http://127.0.0.1:{port}"
        headers = {"Origin": origin, "Content-Type": "application/json"}
        paths = [make_project(self.home / f"p{i}") for i in range(5)]
        for path in paths:
            status = request(port, "POST", "/api/projects/add", headers, json.dumps({"path": str(path)}).encode())[0]
            self.assertEqual(status, 200)
        before = request(port, "GET", "/api/projects")[2]["projects"]
        proc.terminate()
        self.assertEqual(proc.wait(timeout=10), 0)
        proc, port = self.start("--port", "0", "--engine", str(REPO))
        self.assertEqual(request(port, "GET", "/api/projects")[2]["projects"], before)
        self.assertEqual(len(before), 5)
        self.serve()  # tearDown stops an in-process server

    def test_outside_home_is_403_and_nothing_is_added(self):
        outside = make_project(self.base / "outside")
        status, _, data = self.post("/api/projects/add", {"path": str(outside)})
        self.assertEqual((status, data), (403, {"error": "outside your home folder"}))
        link = self.home / "link-out"
        link.symlink_to(outside)
        self.assertEqual(self.post("/api/projects/add", {"path": str(link)})[0], 403)
        self.assertEqual(self.listed(), [])

    def test_not_a_git_repository_is_422(self):
        path = make_project(self.home / "plain", git=False)
        status, _, data = self.post("/api/projects/add", {"path": str(path)})
        self.assertEqual((status, data), (422, {"error": "not a git repository"}))
        self.assertEqual(self.post("/api/projects/add", {"path": str(self.home / "nowhere")})[0], 422)

    def test_a_git_folder_without_sdd_needs_setup_and_is_not_added(self):
        path = make_project(self.home / "fresh", sdd=False)
        status, _, data = self.post("/api/projects/add", {"path": str(path)})
        self.assertEqual(status, 200)
        self.assertIs(data["needs_setup"], True)
        self.assertEqual(data["path"], str(path.resolve()))
        self.assertIn("plan", data)
        self.assertEqual(self.listed(), [])

    def test_a_bad_body_is_400(self):
        self.assertEqual(self.post("/api/projects/add", {})[0], 400)
        self.assertEqual(self.post("/api/projects/add", {"path": 3})[0], 400)
        self.assertEqual(self.post("/api/projects/add", ["x"])[0], 400)

    def test_without_an_engine_the_list_answers_with_null_state(self):
        path = make_project(self.home / "app")
        self.post("/api/projects/add", {"path": str(path)})
        self.stop_serving()
        self.server = core.app.make_server(0, core.app.EngineHolder(self.base / "nowhere"))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        rows = self.listed()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["state"])
        self.assertFalse(rows[0]["missing"])


class RemoveTest(ProjectsServer):
    def test_remove_drops_the_entry_and_never_touches_the_project(self):
        path = make_project(self.home / "app")
        pid = self.post("/api/projects/add", {"path": str(path)})[2]["id"]
        before = sorted(str(p) for p in path.rglob("*"))
        status, _, data = self.post(f"/api/projects/{pid}/remove", {})
        self.assertEqual((status, data), (200, {"ok": True}))
        self.assertEqual(self.listed(), [])
        self.assertEqual(sorted(str(p) for p in path.rglob("*")), before)
        self.assertEqual(self.post(f"/api/projects/{pid}/remove", {})[0], 404)

    def test_a_missing_project_can_be_removed(self):
        path = make_project(self.home / "app")
        pid = self.post("/api/projects/add", {"path": str(path)})[2]["id"]
        path.rename(self.home / "moved")
        self.assertEqual(self.post(f"/api/projects/{pid}/remove", {})[0], 200)
        self.assertEqual(self.listed(), [])


class FeatureTest(ProjectsServer):
    def test_feature_is_set_and_cleared_in_projects_json(self):
        path = make_project(self.home / "app")
        pid = self.post("/api/projects/add", {"path": str(path)})[2]["id"]
        status, _, data = self.post(f"/api/p/{pid}/feature", {"feature": "002-b"})
        self.assertEqual((status, data), (200, {"ok": True, "feature": "002-b"}))
        stored = json.loads(self.projects_json.read_text(encoding="utf-8"))["projects"][0]
        self.assertEqual(stored["feature"], "002-b")
        self.assertEqual(self.post(f"/api/p/{pid}/feature", {"feature": None})[2]["feature"], None)
        stored = json.loads(self.projects_json.read_text(encoding="utf-8"))["projects"][0]
        self.assertIsNone(stored["feature"])

    def test_an_unknown_feature_is_400_and_an_unknown_project_404(self):
        path = make_project(self.home / "app")
        pid = self.post("/api/projects/add", {"path": str(path)})[2]["id"]
        status, _, data = self.post(f"/api/p/{pid}/feature", {"feature": "../etc"})
        self.assertEqual(status, 400)
        self.assertEqual(data["features"], ["001-a", "002-b"])
        self.assertEqual(self.post("/api/p/0123456789ab/feature", {"feature": None})[0], 404)


class StoreTest(core.TempHome):
    def test_an_unreadable_file_is_kept_before_it_is_replaced(self):
        projects = core.app.load_server_modules()["projects"]
        file = self.base / "state" / "projects.json"
        file.parent.mkdir(parents=True)
        file.write_text("{not json", encoding="utf-8")
        store = projects.Store(file, "1.0")
        self.assertEqual(store.entries(), [])
        path = make_project(self.base / "home" / "app")
        store.add(path)
        self.assertEqual(file.with_name("projects.json.bad").read_text(encoding="utf-8"), "{not json")
        self.assertEqual(len(store.entries()), 1)


if __name__ == "__main__":
    unittest.main()
