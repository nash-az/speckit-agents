"""008 data-model §6 and §10: server/sdd-server.py and server/app.py — the Claude-session refusal and
the cleaned child environment, one server per person, starting without an engine, the engine reload,
the 127.0.0.1 listener and the request checks; plus a guard that the server never names the
repository's bin/ and loads modules only from the engine folder (research R1).

Every server here runs with temporary HOME, XDG_STATE_HOME and XDG_DATA_HOME, and with CLAUDECODE,
CLAUDE_PID and every CLAUDE_CODE_* variable removed; only the refusal test sets CLAUDECODE.
"""
import ast
import http.client
import importlib.util
import json
import os
import re
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
REPO = HERE.parent
SERVER = REPO / "server"
SCRIPT = SERVER / "sdd-server.py"
RELOAD_WAIT = 15  # §6: the engine appears or reloads within 15 s
# The bin/ guard (NoRepositoryBinTest): where "bin" may be named, and what names an engine file.
ENGINE_PATH_FUNCTIONS = ("engine_module", "is_engine_folder")
ENGINE_FILE_RE = re.compile(r"(^|/)bin/sdd-[\w-]+\.py")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


app = load("test_server_app", SERVER / "app.py")
server_main = load("test_sdd_server", SCRIPT)


def is_session_var(name):
    return name in ("CLAUDECODE", "CLAUDE_PID") or name.startswith("CLAUDE_CODE_")


def request(port, method, path, headers=None, body=None):
    """(status, headers, parsed JSON or None) of one request to 127.0.0.1:`port`."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        raw = resp.read()
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            data = None
        return resp.status, dict(resp.getheaders()), data
    finally:
        conn.close()


class TempHome(unittest.TestCase):
    """Temporary HOME and XDG folders, the session variables removed, no SDD_SUPERVISED."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        (self.base / "home").mkdir()
        env = {key: value for key, value in os.environ.items()
               if not is_session_var(key) and key != "SDD_SUPERVISED"}
        env.update({"HOME": str(self.base / "home"), "XDG_STATE_HOME": str(self.base / "state"),
                    "XDG_DATA_HOME": str(self.base / "data")})
        self.env = env
        self.patch = mock.patch.dict(os.environ, env, clear=True)
        self.patch.start()
        self.engine_root = self.base / "data" / "speckit-agents" / "engine"
        self.server_json = self.base / "state" / "speckit-agents" / "server" / "server.json"
        self.procs = []

    def tearDown(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            for stream in (proc.stdout, proc.stderr):
                if stream:
                    stream.close()
        self.patch.stop()
        self.tmp.cleanup()

    def install_engine(self):
        """`bin/sdd-engine.py install --from <repo>` into the temporary engine root."""
        out = subprocess.run([sys.executable, str(REPO / "bin" / "sdd-engine.py"), "install", "--from", str(REPO)],
                             env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return Path(json.loads(out.stdout)["path"])

    def start(self, *args, env=None):
        """Start sdd-server.py; returns the process and, when it serves, its port (from line 1)."""
        proc = subprocess.Popen([sys.executable, str(SCRIPT), *args], env=env or self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.procs.append(proc)
        ready, _, _ = select.select([proc.stdout], [], [], 30)
        self.assertTrue(ready, "the server printed nothing within 30 s")
        line = proc.stdout.readline()
        self.assertIn("http://127.0.0.1:", line, line + (proc.stderr.read() if proc.poll() is not None else ""))
        return proc, int(line.rsplit(":", 1)[1].strip().split()[0])

    def wait_for(self, check, seconds):
        deadline = time.time() + seconds
        while time.time() < deadline:
            value = check()
            if value:
                return value
            time.sleep(0.25)
        return check()


class RefusalTest(TempHome):
    def test_claudecode_exits_3_with_the_message_and_writes_nothing(self):
        env = dict(self.env, CLAUDECODE="1")
        out = subprocess.run([sys.executable, str(SCRIPT), "--port", "0", "--engine", str(REPO)], env=env,
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 3)
        self.assertIn("Start sdd-server from a terminal, not from a Claude session: runs it starts would "
                      "inherit the session's sandbox.", out.stderr)
        self.assertFalse(self.server_json.exists())


class ChildEnvTest(unittest.TestCase):
    def test_child_env_drops_the_session_variables_and_keeps_the_rest(self):
        base = {"CLAUDECODE": "1", "CLAUDE_PID": "42", "CLAUDE_CODE_SESSION_ID": "x",
                "CLAUDE_CODE_SUBAGENT_MODEL": "m", "CLAUDE_CONFIG_DIR": "/c", "PATH": "/bin", "HOME": "/h"}
        self.assertEqual(app.child_env(base), {"CLAUDE_CONFIG_DIR": "/c", "PATH": "/bin", "HOME": "/h"})

    def test_scrub_environ_cleans_this_process_so_engine_spawns_are_clean(self):
        with mock.patch.dict(os.environ, {"CLAUDE_PID": "42", "CLAUDE_CODE_X": "1", "KEEP_ME": "1"}):
            app.scrub_environ()
            self.assertNotIn("CLAUDE_PID", os.environ)
            self.assertNotIn("CLAUDE_CODE_X", os.environ)
            self.assertEqual(os.environ["KEEP_ME"], "1")
            out = subprocess.run([sys.executable, "-c", "import os, json; print(json.dumps(sorted(os.environ)))"],
                                 capture_output=True, text=True, timeout=30)
            names = json.loads(out.stdout)
            self.assertFalse([name for name in names if is_session_var(name)])


class SingleInstanceTest(TempHome):
    def test_second_start_reports_the_running_one_and_exits_0(self):
        first, port = self.start("--port", "0", "--engine", str(REPO))
        info = json.loads(self.server_json.read_text(encoding="utf-8"))
        self.assertEqual(info["pid"], first.pid)
        self.assertEqual(info["port"], port)
        self.assertEqual(info["contract"], "1.0")
        self.assertEqual(info["engine_version"], "repo")
        self.assertIsInstance(info["started"], int)

        second = subprocess.run([sys.executable, str(SCRIPT), "--port", "0", "--engine", str(REPO)],
                                env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout.strip(), f"speckit-agents server already running at http://127.0.0.1:{port}")
        self.assertEqual(json.loads(self.server_json.read_text(encoding="utf-8"))["pid"], first.pid)

        first.terminate()
        self.assertEqual(first.wait(timeout=10), 0)
        self.assertFalse(self.server_json.exists(), "a clean exit removes server.json")

    def test_a_dead_or_reused_pid_in_server_json_does_not_count(self):
        self.server_json.parent.mkdir(parents=True)
        dead = subprocess.Popen(["true"])
        dead.wait()
        for info in ({"pid": dead.pid, "started": int(time.time()), "port": 1},
                     {"pid": os.getpid(), "started": 1, "port": 1}):  # live pid, other start time
            self.server_json.write_text(json.dumps(info), encoding="utf-8")
            proc, port = self.start("--port", "0", "--engine", str(REPO))
            self.assertEqual(json.loads(self.server_json.read_text(encoding="utf-8"))["pid"], proc.pid)
            proc.terminate()
            self.assertEqual(proc.wait(timeout=10), 0)


class ListenTest(TempHome):
    def test_listens_on_127_0_0_1_only(self):
        holder = app.EngineHolder(REPO)
        server = app.make_server(0, holder)
        try:
            self.assertIsInstance(server, app.http.server.ThreadingHTTPServer)
            self.assertEqual(server.server_address[0], "127.0.0.1")
        finally:
            server.server_close()

    def test_a_busy_default_port_tries_the_next_ones(self):
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        try:
            with mock.patch.object(server_main.app, "DEFAULT_PORT", port):
                server = server_main.bind(None, app.EngineHolder(REPO))
            try:
                got = server.server_address[1]
                self.assertNotEqual(got, port)
                self.assertTrue(port < got <= port + server_main.PORT_TRIES)
            finally:
                server.server_close()
        finally:
            busy.close()


class EngineLifecycleTest(TempHome):
    def test_start_without_an_engine_then_the_copy_appears(self):
        proc, port = self.start("--port", "0")  # default engine: <engine root>/current, not there yet
        status, headers, data = request(port, "GET", "/api/server")
        self.assertEqual(status, 200)
        self.assertIsNone(data["engine"])
        self.assertEqual(headers.get("X-SDD-Contract"), "1.0")
        status, headers, data = request(port, "GET", "/api/p/abc/tasks")
        self.assertEqual(status, 503)
        self.assertEqual(headers.get("X-SDD-Contract"), "1.0")
        self.assertNotEqual(request(port, "GET", "/api/projects")[0], 503)

        copy = self.install_engine()
        engine = self.wait_for(lambda: request(port, "GET", "/api/server")[2]["engine"], RELOAD_WAIT)
        self.assertIsNotNone(engine, "the engine was not loaded within 15 s")
        self.assertEqual(engine["path"], str(copy))
        self.assertEqual(engine["contract"], "1.0")
        self.assertNotEqual(request(port, "GET", "/api/p/abc/tasks")[0], 503)

    def test_a_new_version_json_is_reloaded(self):
        copy = self.install_engine()
        proc, port = self.start("--port", "0")
        first = request(port, "GET", "/api/server")[2]["engine"]
        self.assertEqual(first["path"], str(copy))

        info = json.loads((copy / "VERSION.json").read_text(encoding="utf-8"))
        info["plugin_version"] = "9.9.9"
        (copy / "VERSION.json").write_text(json.dumps(info), encoding="utf-8")
        reloaded = self.wait_for(
            lambda: request(port, "GET", "/api/server")[2]["engine"]["plugin_version"] == "9.9.9", RELOAD_WAIT)
        self.assertTrue(reloaded, "the new VERSION.json was not loaded within 15 s")

    def test_holder_loads_from_the_engine_folder_under_fresh_names(self):
        copy = self.install_engine()
        holder = app.EngineHolder()
        self.assertTrue(holder.refresh())
        engine = holder.get()
        self.assertEqual(engine.folder, copy)
        modules = [engine.views, engine.control, engine.manual, engine.unblock, engine.edit,
                   engine.tasks, engine.decide, engine.launch, engine.job]
        for module in modules:
            self.assertTrue(Path(module.__file__).resolve().is_relative_to(copy), module.__file__)
        self.assertFalse(holder.refresh(), "same folder and version: no reload")

        info = json.loads((copy / "VERSION.json").read_text(encoding="utf-8"))
        info["plugin_version"] = "9.9.9"
        (copy / "VERSION.json").write_text(json.dumps(info), encoding="utf-8")
        self.assertTrue(holder.refresh())
        again = holder.get()
        self.assertIsNot(again.views, engine.views)
        self.assertNotEqual(again.views.__name__, engine.views.__name__)
        self.assertEqual(again.plugin_version, "9.9.9")

    def test_holder_without_a_folder_has_no_engine(self):
        holder = app.EngineHolder(self.base / "nowhere")
        self.assertFalse(holder.refresh())
        self.assertIsNone(holder.get())


class RequestChecksTest(TempHome):
    """§10 on an in-process server with the repository as its engine (`--engine .`)."""

    def setUp(self):
        super().setUp()
        self.holder = app.EngineHolder(REPO)
        self.holder.refresh()
        self.server = app.make_server(0, self.holder)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def test_a_foreign_host_gets_421(self):
        status, headers, data = request(self.port, "GET", "/api/server", {"Host": "evil.example:80"})
        self.assertEqual(status, 421)
        self.assertEqual(headers.get("X-SDD-Contract"), "1.0")
        for host in ("127.0.0.1", f"localhost:{self.port}", f"[::1]:{self.port}"):
            self.assertEqual(request(self.port, "GET", "/api/server", {"Host": host})[0], 200, host)

    def test_post_needs_an_allowed_origin_and_json(self):
        body = b"{}"
        self.assertEqual(request(self.port, "POST", "/api/x", {"Content-Type": "application/json"}, body)[0], 403)
        self.assertEqual(request(self.port, "POST", "/api/x", {"Origin": "http://evil.example",
                                                               "Content-Type": "application/json"}, body)[0], 403)
        self.assertEqual(request(self.port, "POST", "/api/x", {"Origin": self.origin,
                                                               "Content-Type": "text/plain"}, body)[0], 415)
        status = request(self.port, "POST", "/api/x", {"Origin": self.origin,
                                                       "Content-Type": "application/json; charset=utf-8"}, body)[0]
        self.assertEqual(status, 404, "the checks passed; no such route")
        local = f"http://localhost:{self.port}"
        self.assertEqual(request(self.port, "POST", "/api/x", {"Origin": local,
                                                               "Content-Type": "application/json"}, body)[0], 404)

    def test_guarded_gets_check_origin_and_sec_fetch_site(self):
        for path in ("/api/fs/list", "/api/plugin"):
            self.assertEqual(request(self.port, "GET", path, {"Origin": "http://evil.example"})[0], 403, path)
            self.assertEqual(request(self.port, "GET", path, {"Sec-Fetch-Site": "cross-site"})[0], 403, path)
            self.assertEqual(request(self.port, "GET", path, {"Sec-Fetch-Site": "same-site"})[0], 403, path)
            for headers in ({}, {"Origin": self.origin}, {"Sec-Fetch-Site": "same-origin"},
                            {"Sec-Fetch-Site": "none"}):
                self.assertNotEqual(request(self.port, "GET", path, headers)[0], 403, (path, headers))

    def test_other_gets_ignore_origin(self):
        status, _, _ = request(self.port, "GET", "/api/server", {"Origin": "http://evil.example",
                                                                "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 200)

    def test_every_answer_carries_the_contract_header(self):
        for method, path in (("GET", "/api/server"), ("GET", "/nope"), ("POST", "/api/x")):
            _, headers, _ = request(self.port, method, path)
            self.assertEqual(headers.get("X-SDD-Contract"), "1.0", (method, path))

    def test_api_server_names_the_engine(self):
        status, _, data = request(self.port, "GET", "/api/server")
        self.assertEqual(status, 200)
        self.assertEqual(data["contract"], "1.0")
        self.assertEqual(data["engine"], {"plugin_version": "repo", "contract": "1.0", "path": str(REPO.resolve())})


class InstallCommandTest(TempHome):
    def test_writes_the_shim_and_never_overwrites_another_file(self):
        shim = self.base / "home" / ".local" / "bin" / "sdd-server"
        out = subprocess.run([sys.executable, str(SCRIPT), "--install-command"], env=self.env,
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        text = shim.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# written by sdd-server --install-command")
        self.assertIn(str(SCRIPT.resolve()), text)
        self.assertTrue(os.access(shim, os.X_OK))
        again = subprocess.run([sys.executable, str(SCRIPT), "--install-command"], env=self.env,
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(again.returncode, 0, "its own shim is rewritten")

        shim.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        refused = subprocess.run([sys.executable, str(SCRIPT), "--install-command"], env=self.env,
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(refused.returncode, 1)
        self.assertEqual(shim.read_text(encoding="utf-8"), "#!/bin/sh\necho mine\n")


class NoRepositoryBinTest(unittest.TestCase):
    """Fails when a file under server/ names the repository's bin/ or loads modules other than
    through the engine folder (`--engine`). Rules, checked on the syntax tree of every server file:
    - imports are standard-library modules only (server modules are loaded by file, from HERE);
    - no `sys.path`, `os.pardir`, `..` in a string, and no `.parent`, `.parents` or `dirname`
      climbing from `__file__` or HERE, except in HERE's own definition (`Path(__file__)...parent`);
    - outside docstrings, the string "bin" (or one starting with "bin/" or naming a
      `bin/sdd-*.py` file) appears only in `engine_module` and `is_engine_folder`, which take the
      engine folder as their argument;
    - `spec_from_file_location` gets either `HERE / <name>` or, inside `_exec_module`, its own
      parameter; `_exec_module` is called only by `server_module` and `engine_module`.
    """

    def files(self):
        found = sorted(p for p in SERVER.rglob("*.py") if "static" not in p.relative_to(SERVER).parts)
        self.assertTrue(found)
        return found

    @staticmethod
    def names_in(node):
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}

    def check(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        problems = []
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        def enclosing_function(node):
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return node
            return None

        def in_here_definition(node):
            while node in parents:
                node = parents[node]
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "HERE"
                                                        for t in node.targets):
                    return True
            return False

        for node in ast.walk(tree):
            where = f"{path.name}:{getattr(node, 'lineno', '?')}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in sys.stdlib_module_names:
                        problems.append(f"{where} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                top = (node.module or "").split(".")[0]
                if node.level or (top not in sys.stdlib_module_names and top != "__future__"):
                    problems.append(f"{where} imports from {node.module!r}")
            elif isinstance(node, ast.Attribute):
                if node.attr == "path" and isinstance(node.value, ast.Name) and node.value.id == "sys":
                    problems.append(f"{where} uses sys.path")
                if node.attr == "pardir":
                    problems.append(f"{where} uses os.pardir")
                if node.attr in ("parent", "parents") and self.names_in(node.value) & {"__file__", "HERE"} \
                        and not in_here_definition(node):
                    problems.append(f"{where} climbs from __file__ or HERE")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and not isinstance(parents.get(node), ast.Expr):  # docstrings are prose
                if ".." in node.value.split("/"):
                    problems.append(f"{where} has '..' in a string")
                if node.value == "bin" or node.value.startswith("bin/") or ENGINE_FILE_RE.search(node.value):
                    fn = enclosing_function(node)
                    if fn is None or fn.name not in ENGINE_PATH_FUNCTIONS:
                        problems.append(f"{where} names bin outside engine_module")
            elif isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name == "dirname" and self.names_in(node) & {"__file__", "HERE"}:
                    problems.append(f"{where} climbs from __file__ or HERE")
                if name == "spec_from_file_location":
                    arg = node.args[1] if len(node.args) > 1 else None
                    fn = enclosing_function(node)
                    here_form = (isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Div)
                                 and isinstance(arg.left, ast.Name) and arg.left.id == "HERE")
                    param_form = (isinstance(arg, ast.Name) and fn is not None and fn.name == "_exec_module"
                                  and arg.id in {a.arg for a in fn.args.args})
                    if not (here_form or param_form):
                        problems.append(f"{where} loads a module from {ast.unparse(arg) if arg else '?'}")
                if name == "_exec_module":
                    fn = enclosing_function(node)
                    if fn is None or fn.name not in ("server_module", "engine_module"):
                        problems.append(f"{where} calls _exec_module outside server_module/engine_module")
        return problems

    def test_server_files_never_name_the_repository_bin(self):
        problems = [p for path in self.files() for p in self.check(path)]
        self.assertEqual(problems, [])

    def test_the_check_catches_a_repository_bin_load(self):
        bad = ('from pathlib import Path\nimport importlib.util\nHERE = Path(__file__).resolve().parent\n'
               'ROOT = HERE.parent\n'
               'spec = importlib.util.spec_from_file_location("x", ROOT / "bin" / "sdd-tasks.py")\n')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.py"
            path.write_text(bad, encoding="utf-8")
            problems = self.check(path)
        self.assertTrue(any("climbs" in p for p in problems), problems)
        self.assertTrue(any("names bin" in p for p in problems), problems)
        self.assertTrue(any("loads a module" in p for p in problems), problems)

    def test_engine_module_reads_only_the_given_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "engine"
            (folder / "bin").mkdir(parents=True)
            (folder / "bin" / "probe.py").write_text("WHERE = __file__\n", encoding="utf-8")
            module = app.engine_module(folder, "probe.py", "probe_test")
            self.assertEqual(Path(module.WHERE), folder / "bin" / "probe.py")


if __name__ == "__main__":
    unittest.main()
