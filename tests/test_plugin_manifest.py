""".claude-plugin/plugin.json: the plugin's version is the engine's version (research R3)."""
import json
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE.parent / ".claude-plugin" / "plugin.json"


class PluginManifestTest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_parses_as_an_object(self):
        self.assertIsInstance(self.manifest, dict)

    def test_version_is_semver(self):
        self.assertRegex(self.manifest.get("version", ""), re.compile(r"^\d+\.\d+\.\d+$"))

    def test_name(self):
        self.assertEqual(self.manifest.get("name"), "speckit-agents")


if __name__ == "__main__":
    unittest.main()
