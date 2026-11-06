"""The standalone server's HTTP side (008 data-model §6, §10; contracts/server-api.md).

Python standard library only. The server reaches the engine (`bin/` plus `templates/`) only through
the engine folder it was given (`--engine`, default `<engine root>/current`), never through the
repository's own `bin/` (research R1, R3). Engine modules are loaded with
`importlib.util.spec_from_file_location` from `<engine folder>/bin/<file>` in `engine_module()`, and
server modules from this folder in `server_module()`; nothing else loads code.

What lives here:
- `EngineHolder`: the loaded engine modules. The server starts without an engine folder; every
  RELOAD_SECONDS it resolves the folder again and loads the modules, under fresh names, when they
  first appear or when `VERSION.json`'s `plugin_version` changes (§6, "Engine reload").
- `child_env()`: the environment of every process the server starts, without `CLAUDECODE`,
  `CLAUDE_PID` or any `CLAUDE_CODE_*` variable (§6, FR-011).
- `Handler`: the request checks of §10 (Host 421; POST Origin 403 and Content-Type 415; the guarded
  GETs), the `X-SDD-Contract` header on every response (§1), the 503 for every route except
  `GET /api/server` and `GET /api/projects` while there is no engine (§6), and a small route table
  (`route()`) that the server modules in SERVER_MODULES fill (`load_server_modules()`; T014 adds
  `projects.py`, the project list).
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import re
import sys
import threading
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The contract version of the formats the server shares with the engine (008 data-model §1). It
# moves to server/versions.py with T020; a test keeps it equal to the engine's.
CONTRACT = "1.0"
DEFAULT_PORT = 8765
RELOAD_SECONDS = 10  # how often the engine folder is resolved again (§6)
NO_ENGINE = "no engine copy: start a Claude session with the plugin installed"

# Hosts a request may name (003 §4.7). Any other Host header, e.g. one a DNS-rebinding page sends,
# gets 421.
ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]")
# GETs that also check Origin and Sec-Fetch-Site (§10): they reveal folders and the plugin setup.
GUARDED_GETS = ("/api/fs/list", "/api/plugin")
SAFE_FETCH_SITES = ("same-origin", "none")
# Routes that answer while there is no engine folder (§6).
NO_ENGINE_ROUTES = (("GET", "/api/server"), ("GET", "/api/projects"))

# Environment variables a Claude session sets; none of them reaches a process the server starts.
SESSION_VARS = ("CLAUDECODE", "CLAUDE_PID")
SESSION_PREFIX = "CLAUDE_CODE_"


# --- Paths --------------------------------------------------------------------------------------


def engine_root() -> Path:
    """`$XDG_DATA_HOME/speckit-agents/engine`, or `~/.local/share/speckit-agents/engine` (§2)."""
    base = os.environ.get("XDG_DATA_HOME")
    data = Path(base) if base else Path.home() / ".local" / "share"
    return data / "speckit-agents" / "engine"


def default_engine() -> Path:
    """`<engine root>/current`, the `--engine` default (§6)."""
    return engine_root() / "current"


def server_state_dir() -> Path:
    """`$XDG_STATE_HOME/speckit-agents/server`, or `~/.local/state/speckit-agents/server` (§6)."""
    base = os.environ.get("XDG_STATE_HOME")
    state = Path(base) if base else Path.home() / ".local" / "state"
    return state / "speckit-agents" / "server"


# --- The child environment (§6, FR-011) ---------------------------------------------------------


def is_session_var(name: str) -> bool:
    """True for `CLAUDECODE`, `CLAUDE_PID` and every `CLAUDE_CODE_*` variable."""
    return name in SESSION_VARS or name.startswith(SESSION_PREFIX)


def child_env(base=None) -> dict:
    """A copy of `base` (default `os.environ`) without the Claude session's variables. Every process
    the server starts gets this environment."""
    source = os.environ if base is None else base
    return {key: value for key, value in source.items() if not is_session_var(key)}


def scrub_environ():
    """Remove the session variables from this process's own environment, so that processes the
    engine modules start from inside the server (they inherit `os.environ`) are clean too."""
    for key in [key for key in os.environ if is_session_var(key)]:
        del os.environ[key]


# --- Loading modules ----------------------------------------------------------------------------


def _exec_module(name: str, path: Path):
    """Load the Python file `path` as a module called `name` (not registered in sys.modules)."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def server_module(name: str):
    """A module of the server itself, `server/<name>.py`."""
    return _exec_module(f"sdd_server_{name}", HERE / f"{name}.py")


def engine_module(folder: Path, file: str, name: str):
    """The engine file `<engine folder>/bin/<file>`, loaded as `name`. The only way the server loads
    engine code (research R1)."""
    return _exec_module(name, Path(folder) / "bin" / file)


def read_version(folder: Path) -> dict | None:
    """`<folder>/VERSION.json` (§2), or None when missing or malformed."""
    try:
        data = json.loads((Path(folder) / "VERSION.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_engine_folder(folder: Path) -> bool:
    """An engine folder holds `bin/` and `templates/` (§2)."""
    folder = Path(folder)
    return (folder / "bin").is_dir() and (folder / "templates").is_dir()


class Engine:
    """One loaded engine: its folder (resolved), its `VERSION.json` and its modules."""

    # (attribute, file under bin/) loaded for every engine. sdd-views brings sdd-tasks, sdd-decide,
    # sdd-launch and sdd-job with it, as the dashboard uses them.
    FILES = (("views", "sdd-views.py"), ("control", "sdd-control.py"), ("manual", "sdd-manual.py"),
             ("unblock", "sdd-unblock.py"), ("edit", "sdd-edit.py"))

    def __init__(self, folder: Path, info: dict | None, generation: int):
        self.folder = Path(folder).resolve()
        self.info = info
        self.generation = generation
        for attr, file in self.FILES:
            stem = file[:-3].replace("-", "_")
            setattr(self, attr, engine_module(self.folder, file, f"{stem}__engine{generation}"))
        self.tasks = self.views.sdd_tasks
        self.decide = self.views.sdd_decide
        self.launch = self.views.sdd_launch
        self.job = self.views.sdd_job

    @property
    def plugin_version(self) -> str:
        """`VERSION.json` `plugin_version`, or `"repo"` for a folder without one (the repository)."""
        version = (self.info or {}).get("plugin_version")
        return version if isinstance(version, str) and version else "repo"

    @property
    def contract(self) -> str:
        """`VERSION.json` `contract`, or the engine's own `CONTRACT` when it has no `VERSION.json`."""
        contract = (self.info or {}).get("contract")
        return contract if isinstance(contract, str) else self.tasks.CONTRACT

    def describe(self) -> dict:
        """`{"plugin_version", "contract", "path"}`, as `GET /api/server` reports it (§11)."""
        return {"plugin_version": self.plugin_version, "contract": self.contract, "path": str(self.folder)}


class EngineHolder:
    """The current engine, or None, behind a lock. `refresh()` loads it when the folder appears and
    loads it again when the folder or its `plugin_version` changes; requests take `get()` once and
    use that engine to the end, so a reload never changes modules under a running request."""

    def __init__(self, folder: Path | None = None):
        self.folder = Path(folder) if folder is not None else default_engine()
        self._lock = threading.Lock()
        self._engine: Engine | None = None
        self._key = None
        self._generation = 0
        self.error: str | None = None  # why the last load failed, if it did

    def get(self) -> Engine | None:
        with self._lock:
            return self._engine

    def _resolve(self):
        """(resolved folder, VERSION.json) when the folder is an engine folder, else None."""
        try:
            if not is_engine_folder(self.folder):
                return None
            return self.folder.resolve(), read_version(self.folder)
        except OSError:
            return None

    def refresh(self) -> bool:
        """Resolve the folder again and (re)load the modules when needed. True when it loaded."""
        found = self._resolve()
        if found is None:
            with self._lock:
                self._engine, self._key = None, None
            return False
        folder, info = found
        key = (str(folder), (info or {}).get("plugin_version"))
        with self._lock:
            if key == self._key and self._engine is not None:
                return False
            self._generation += 1
            generation = self._generation
        try:
            engine = Engine(folder, info, generation)
        except Exception as exc:  # noqa: BLE001 — a broken copy must not stop the server
            with self._lock:
                self._engine, self._key = None, None
                self.error = f"cannot load the engine at {folder}: {exc}"
            print(self.error, file=sys.stderr, flush=True)
            return False
        with self._lock:
            self._engine, self._key, self.error = engine, key, None
        return True

    def watch(self, stop: threading.Event, every: float = RELOAD_SECONDS):
        """Call `refresh()` every `every` seconds until `stop` is set (a daemon thread's body)."""
        while not stop.wait(every):
            self.refresh()


# --- Routes -------------------------------------------------------------------------------------

# (method, compiled pattern, function(handler, match)). Later modules add their routes with
# `route()`; the first full match wins.
ROUTES: list = []


def route(method: str, pattern: str):
    """Register `fn(handler, match)` for `method` and a path matching `pattern` in full."""
    compiled = re.compile(pattern)

    def register(fn):
        ROUTES.append((method, compiled, fn))
        return fn
    return register


# Server modules whose `ROUTES` (method, pattern, fn) join the table, loaded once from this folder.
SERVER_MODULES = ("projects",)
LOADED: dict = {}
_load_lock = threading.Lock()


def load_server_modules() -> dict:
    """Load each of SERVER_MODULES once with `server_module()` and add its routes; returns
    `{name: module}`."""
    with _load_lock:
        for name in SERVER_MODULES:
            if name not in LOADED:
                module = server_module(name)
                for method, pattern, fn in module.ROUTES:
                    route(method, pattern)(fn)
                LOADED[name] = module
        return LOADED


def find_route(method: str, path: str):
    for m, pattern, fn in ROUTES:
        if m == method:
            match = pattern.fullmatch(path)
            if match:
                return fn, match
    return None, None


def host_without_port(host):
    """The Host header without its port: `[::1]:8765` → `[::1]`, `localhost:8765` → `localhost`."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[:end + 1] if end != -1 else host
    return host.split(":", 1)[0]


class Handler(http.server.BaseHTTPRequestHandler):
    """Every request: the §10 checks first, then the engine gate, then the route table."""

    engines: EngineHolder | None = None
    projects = None  # the projects.Store on `<server state dir>/projects.json`, set by make_server()
    port = DEFAULT_PORT  # the server's own port, for the allowed Origins
    protocol_version = "HTTP/1.0"

    def log_message(self, format, *args):  # noqa: ANN001, ARG002
        pass

    def end_headers(self):
        self.send_header("X-SDD-Contract", CONTRACT)
        super().end_headers()

    # The checks (§10, as in 003 §4.7 and §4.9).

    @classmethod
    def allowed_origins(cls):
        return {f"http://{name}:{cls.port}" for name in ALLOWED_HOSTS}

    def _host_allowed(self) -> bool:
        if host_without_port(self.headers.get("Host")) in ALLOWED_HOSTS:
            return True
        self.send_json({"error": "misdirected request"}, 421)
        return False

    def _origin_header(self) -> str:
        return (self.headers.get("Origin") or "").strip()

    def _post_allowed(self) -> bool:
        if self._origin_header() not in self.allowed_origins():
            self.send_json({"error": "origin not allowed"}, 403)
            return False
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self.send_json({"error": "Content-Type must be application/json"}, 415)
            return False
        return True

    def _guarded_get_allowed(self) -> bool:
        """A present Origin must be allowed (a missing one is fine), and a present Sec-Fetch-Site
        must be `same-origin` or `none`."""
        origin = self._origin_header()
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if (origin and origin not in self.allowed_origins()) or (site and site not in SAFE_FETCH_SITES):
            self.send_json({"error": "origin not allowed"}, 403)
            return False
        return True

    # Answers.

    def send_json(self, obj, status=200, headers=None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        """The request body as JSON; an empty body is `{}`. Raises ValueError when it does not parse."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        return json.loads(body) if body.strip() else {}

    @property
    def engine(self) -> Engine | None:
        holder = type(self).engines
        return holder.get() if holder is not None else None

    def _dispatch(self, method: str):
        if not self._host_allowed():
            return
        path, _, query = self.path.partition("?")
        self.query = urllib.parse.parse_qs(query)
        if method == "POST" and not self._post_allowed():
            return
        if method == "GET" and path in GUARDED_GETS and not self._guarded_get_allowed():
            return
        if (method, path) not in NO_ENGINE_ROUTES and self.engine is None:
            self.send_json({"error": NO_ENGINE}, 503)
            return
        fn, match = find_route(method, path)
        if fn is None:
            self.send_json({"error": "not found"}, 404)
            return
        fn(self, match)

    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")


@route("GET", r"/api/server")
def get_server(handler: Handler, match):  # noqa: ARG001
    """`GET /api/server` (§11): the server's contract and the engine it uses, or `engine: null`.
    T020 (server/versions.py) adds the installed plugin version and the warnings."""
    engine = handler.engine
    warnings = [] if engine is not None else [{"what": NO_ENGINE, "level": "major"}]
    handler.send_json({"contract": CONTRACT, "engine": engine.describe() if engine else None,
                       "warnings": warnings})


def make_server(port: int, engines: EngineHolder) -> http.server.ThreadingHTTPServer:
    """A `ThreadingHTTPServer` on 127.0.0.1:`port` (0 = any free port). Localhost only, no auth:
    do not add a host option. Raises OSError when the port is busy. The project list lives in
    `<server state dir>/projects.json` (§6)."""
    modules = load_server_modules()
    store = modules["projects"].Store(server_state_dir() / "projects.json", CONTRACT)
    handler = type("BoundHandler", (Handler,), {"engines": engines, "projects": store})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    handler.port = server.server_address[1]
    return server
