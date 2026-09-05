from __future__ import annotations

import subprocess
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

class ReleaseBaselineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (ROOT / "tools.toml").open("rb") as fh:
            cls.tools = tomllib.load(fh)["tools"]

    def test_all_initial_tools_active(self):
        baseline = {"context-zip", "git-context", "migration-version-fixer"}
        self.assertTrue(baseline.issubset(set(self.tools)))
        self.assertTrue(all(meta["status"] == "active" for meta in self.tools.values()))

    def test_migration_fixer_exit_contract(self):
        entry = ROOT / self.tools["migration-version-fixer"]["entrypoint"]
        cp = subprocess.run([str(entry), "--print-exit-codes"],
                            cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(0, cp.returncode, cp.stderr)
        expected = {
            0: "CLEAN",
            1: "INTERNAL_ERROR",
            2: "CONFLICT",
            3: "PROTECTED_CONFLICT",
            4: "CHANGES_REQUIRED",
            5: "CONFIG_ERROR",
            6: "GIT_ERROR",
        }
        for code, name in expected.items():
            self.assertIn(f"{code} {name}", cp.stdout)

if __name__ == "__main__":
    unittest.main()
