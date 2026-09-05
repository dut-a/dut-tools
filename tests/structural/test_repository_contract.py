from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

class RepositoryContractTest(unittest.TestCase):
    def test_root_contract_files_exist(self):
        required = [
            "README.md",
            "LICENSE",
            "Makefile",
            "tools.toml",
            "VERSIONING.md",
            "CONTRIBUTING.md",
        ]
        for rel in required:
            with self.subTest(path=rel):
                self.assertTrue((ROOT / rel).is_file())

    def test_operator_scripts_exist(self):
        required = [
            "scripts/bootstrap",
            "scripts/install",
            "scripts/uninstall",
            "scripts/doctor",
            "scripts/test",
            "scripts/check",
            "scripts/lint",
            "scripts/release",
            "scripts/registry.py",
        ]
        for rel in required:
            with self.subTest(path=rel):
                self.assertTrue((ROOT / rel).is_file())

    def test_origin_is_not_registered_as_a_tool(self):
        import tomllib
        with (ROOT / "tools.toml").open("rb") as fh:
            tools = tomllib.load(fh)["tools"]
        self.assertNotIn("origin", tools)

if __name__ == "__main__":
    unittest.main()
