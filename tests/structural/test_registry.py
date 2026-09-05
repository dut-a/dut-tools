from __future__ import annotations

import re
import stat
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)

class RegistryStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (ROOT / "tools.toml").open("rb") as fh:
            cls.data = tomllib.load(fh)
        cls.tools = cls.data["tools"]

    def test_registry_has_tools(self):
        self.assertGreaterEqual(len(self.tools), 1)

    def test_command_names_are_unique(self):
        commands = [meta["command"] for meta in self.tools.values()]
        self.assertEqual(len(commands), len(set(commands)))

    def test_registered_paths_exist(self):
        for name, meta in self.tools.items():
            with self.subTest(tool=name):
                self.assertTrue((ROOT / meta["path"]).is_dir())

    def test_versions_are_semver(self):
        for name, meta in self.tools.items():
            with self.subTest(tool=name):
                value = (ROOT / meta["version_file"]).read_text(encoding="utf-8").strip()
                self.assertRegex(value, SEMVER)

    def test_required_tool_docs_exist(self):
        for name, meta in self.tools.items():
            base = ROOT / meta["path"]
            with self.subTest(tool=name):
                self.assertTrue((base / "README.md").is_file())
                self.assertTrue((base / "CHANGELOG.md").is_file())

    def test_active_tools_have_executable_entrypoints(self):
        for name, meta in self.tools.items():
            if meta["status"] != "active":
                continue
            with self.subTest(tool=name):
                self.assertIn("entrypoint", meta)
                path = ROOT / meta["entrypoint"]
                self.assertTrue(path.is_file())
                self.assertTrue(path.stat().st_mode & stat.S_IXUSR)

if __name__ == "__main__":
    unittest.main()
