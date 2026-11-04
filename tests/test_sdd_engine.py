"""008 data-model §2: bin/sdd-engine.py, the versioned engine copy (`install`, `path`), its pruning,
its lock and its command line."""
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
BIN = HERE.parent / "bin"
SCRIPT = BIN / "sdd-engine.py"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, BIN / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engine = load("sdd_engine", "sdd-engine.py")


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.base = base
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(base / "state"),
                                                "HOME": str(base / "home"),
                                                "XDG_DATA_HOME": str(base / "data")})
        self.env.start()
        os.environ.pop("SDD_SUPERVISED", None)
        self.root = base / "data" / "speckit-agents" / "engine"
        self.plugin = self.make_plugin("plugin", "0.8.0")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def make_plugin(self, name, version):
        """A fake plugin root: bin/ with an engine file, the dashboard and a __pycache__, and templates/."""
        root = self.base / name
        (root / "bin" / "__pycache__").mkdir(parents=True)
        (root / "bin" / "sdd-tasks.py").write_text("# tasks\n", encoding="utf-8")
        (root / "bin" / "sdd-dashboard.py").write_text("# dashboard\n", encoding="utf-8")
        (root / "bin" / "__pycache__" / "x.pyc").write_bytes(b"\0")
        (root / "templates").mkdir()
        (root / "templates" / "sdd-config.toml").write_text("[run]\n", encoding="utf-8")
        self.set_version(root, version)
        return root

    def set_version(self, root, version):
        (root / ".claude-plugin").mkdir(exist_ok=True)
        manifest = {"name": "speckit-agents"}
        if version is not None:
            manifest["version"] = version
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")

    def install(self, plugin=None):
        return engine.install(plugin or self.plugin)


class InstallTest(EngineCase):
    def test_root_follows_xdg_data_home_and_falls_back_to_home(self):
        self.assertEqual(engine.engine_root(), self.root)
        with mock.patch.dict(os.environ):
            del os.environ["XDG_DATA_HOME"]
            self.assertEqual(engine.engine_root(),
                             self.base / "home" / ".local" / "share" / "speckit-agents" / "engine")

    def test_first_install_copies_bin_and_templates_and_points_current(self):
        code, out = self.install()
        self.assertEqual(code, 0)
        folder = self.root / "0.8.0"
        self.assertEqual(out, {"contract": "1.0", "installed": True, "path": str(folder), "plugin_version": "0.8.0"})
        self.assertTrue((folder / "bin" / "sdd-tasks.py").is_file())
        self.assertFalse((folder / "bin" / "sdd-dashboard.py").exists())
        self.assertFalse((folder / "bin" / "__pycache__").exists())
        self.assertTrue((folder / "templates" / "sdd-config.toml").is_file())
        stamp = json.loads((folder / "VERSION.json").read_text(encoding="utf-8"))
        self.assertEqual(stamp["plugin_version"], "0.8.0")
        self.assertEqual(stamp["contract"], "1.0")
        self.assertEqual(stamp["source"], str(self.plugin.resolve()))
        self.assertIsInstance(stamp["copied_at"], int)
        current = self.root / "current"
        self.assertTrue(current.is_symlink())
        self.assertEqual(current.resolve(), folder)
        self.assertEqual([p.name for p in self.root.iterdir() if ".tmp-" in p.name or ".old-" in p.name], [])

    def test_same_version_again_is_not_installed(self):
        self.install()
        code, out = self.install()
        self.assertEqual(code, 0)
        self.assertEqual(out, {"contract": "1.0", "installed": False, "path": str(self.root / "0.8.0")})

    def test_same_version_from_another_source_replaces_the_copy(self):
        self.install()
        other = self.make_plugin("other", "0.8.0")
        (other / "bin" / "sdd-new.py").write_text("# new\n", encoding="utf-8")
        code, out = self.install(other)
        self.assertEqual(code, 0)
        self.assertTrue(out["installed"])
        folder = self.root / "0.8.0"
        self.assertTrue((folder / "bin" / "sdd-new.py").is_file())
        self.assertEqual(engine.read_version(folder)["source"], str(other.resolve()))

    def test_new_version_moves_current(self):
        self.install()
        self.set_version(self.plugin, "0.9.0")
        code, out = self.install()
        self.assertEqual(code, 0)
        self.assertTrue(out["installed"])
        self.assertEqual(out["plugin_version"], "0.9.0")
        self.assertEqual((self.root / "current").resolve(), self.root / "0.9.0")
        self.assertTrue((self.root / "0.8.0").is_dir())

    def test_sixth_version_prunes_the_oldest(self):
        for n in range(6):
            self.set_version(self.plugin, f"0.{n}.0")
            with mock.patch.object(engine.time, "time", return_value=1793620000 + n):
                self.assertTrue(self.install()[1]["installed"])
        names = sorted(p.name for p in self.root.iterdir() if p.is_dir() and not p.is_symlink())
        self.assertEqual(names, ["0.1.0", "0.2.0", "0.3.0", "0.4.0", "0.5.0"])
        self.assertEqual((self.root / "current").resolve(), self.root / "0.5.0")

    def test_pruning_never_deletes_current(self):
        # current is re-pointed at an old version whose copied_at is older than five newer ones.
        for n in range(5):
            self.set_version(self.plugin, f"0.{n}.0")
            with mock.patch.object(engine.time, "time", return_value=1793620000 + n):
                self.install()
        self.set_version(self.plugin, "0.0.1")
        with mock.patch.object(engine.time, "time", return_value=1793610000):  # oldest copied_at
            code, out = self.install()
        self.assertEqual(code, 0)
        self.assertEqual((self.root / "current").resolve(), self.root / "0.0.1")
        self.assertTrue((self.root / "0.0.1").is_dir())
        names = sorted(p.name for p in self.root.iterdir() if p.is_dir() and not p.is_symlink())
        self.assertEqual(names, ["0.0.0", "0.0.1", "0.1.0", "0.2.0", "0.3.0", "0.4.0"])

    def test_missing_version_uses_a_hash_of_bin(self):
        self.set_version(self.plugin, None)
        digest = hashlib.sha256()
        for name in ("sdd-dashboard.py", "sdd-tasks.py"):  # sorted bin/ files, __pycache__ left out
            digest.update((self.plugin / "bin" / name).read_bytes())
        expected = "0.0.0+" + digest.hexdigest()[:12]
        self.assertEqual(engine.plugin_version(self.plugin), expected)
        code, out = self.install()
        self.assertEqual(code, 0)
        self.assertEqual(out["plugin_version"], expected)
        self.assertTrue((self.root / expected / "VERSION.json").is_file())

    def test_missing_manifest_uses_a_hash_of_bin(self):
        (self.plugin / ".claude-plugin" / "plugin.json").unlink()
        self.assertTrue(engine.plugin_version(self.plugin).startswith("0.0.0+"))

    def test_supervised_run_is_skipped_and_writes_nothing(self):
        with mock.patch.dict(os.environ, {"SDD_SUPERVISED": "1"}):
            code, out = self.install()
        self.assertEqual(code, 0)
        self.assertEqual(out, {"contract": "1.0", "installed": False, "skipped": "supervised run"})
        self.assertFalse((self.base / "data").exists())

    def test_plugin_without_bin_is_a_copy_error(self):
        code, out = engine.install(self.base / "nowhere")
        self.assertEqual(code, 1)
        self.assertEqual(out["contract"], "1.0")
        self.assertIn("error", out)
        self.assertFalse((self.root / "current").exists())

    def test_copy_error_exits_1_and_leaves_no_temporary_folder(self):
        with mock.patch.object(engine.shutil, "copytree", side_effect=OSError("disk full")):
            code, out = self.install()
        self.assertEqual(code, 1)
        self.assertIn("disk full", out["error"])
        self.assertEqual([p.name for p in self.root.iterdir() if p.name != ".lock"], [])

    def test_two_threads_installing_at_once_end_with_one_valid_copy(self):
        other = self.make_plugin("other", "0.9.0")
        results = []
        barrier = threading.Barrier(2)

        def run(plugin):
            barrier.wait()
            results.append(engine.install(plugin))

        threads = [threading.Thread(target=run, args=(p,)) for p in (self.plugin, other)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual([code for code, _ in results], [0, 0])
        current = self.root / "current"
        info = engine.read_version(current)
        self.assertIn(info["plugin_version"], ("0.8.0", "0.9.0"))
        self.assertTrue((current / "bin" / "sdd-tasks.py").is_file())
        self.assertTrue((current / "templates" / "sdd-config.toml").is_file())
        leftovers = [p.name for p in self.root.iterdir() if ".tmp-" in p.name or ".old-" in p.name]
        self.assertEqual(leftovers, [])
        for name in ("0.8.0", "0.9.0"):
            self.assertEqual(engine.read_version(self.root / name)["plugin_version"], name)

    def test_two_threads_installing_the_same_version_install_once(self):
        results = []
        barrier = threading.Barrier(2)

        def run():
            barrier.wait()
            results.append(engine.install(self.plugin))

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(out["installed"] for _, out in results), [False, True])
        self.assertEqual(engine.read_version(self.root / "current")["plugin_version"], "0.8.0")


class PathTest(EngineCase):
    def test_path_without_a_copy_exits_2(self):
        self.assertEqual(engine.path_info(), (2, {"contract": "1.0", "error": "no engine copy"}))

    def test_path_names_the_resolved_current(self):
        self.install()
        self.assertEqual(engine.path_info(),
                         (0, {"contract": "1.0", "path": str(self.root / "0.8.0"), "plugin_version": "0.8.0"}))


class CommandLineTest(EngineCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                              env={**os.environ})

    def test_install_then_path(self):
        done = self.run_cli("install", "--from", str(self.plugin))
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertEqual(out["contract"], "1.0")
        self.assertTrue(out["installed"])
        shown = self.run_cli("path")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout)["path"], str(self.root / "0.8.0"))

    def test_path_without_copy_exits_2(self):
        shown = self.run_cli("path")
        self.assertEqual(shown.returncode, 2)
        self.assertEqual(json.loads(shown.stdout), {"contract": "1.0", "error": "no engine copy"})

    def test_main_prints_one_json_object(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = engine.main(["install", "--from", str(self.plugin)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buffer.getvalue())["plugin_version"], "0.8.0")

    def test_repository_installs_without_the_dashboard(self):
        code, out = engine.install(HERE.parent)
        self.assertEqual(code, 0, out)
        folder = Path(out["path"])
        self.assertTrue((folder / "bin" / "sdd-tasks.py").is_file())
        self.assertTrue((folder / "bin" / "sdd-engine.py").is_file())
        self.assertFalse((folder / "bin" / "sdd-dashboard.py").exists())
        self.assertFalse((folder / "bin" / "__pycache__").exists())
        self.assertTrue((folder / "templates" / "sdd-config.toml").is_file())


if __name__ == "__main__":
    unittest.main()
