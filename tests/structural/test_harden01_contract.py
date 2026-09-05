from __future__ import annotations
import stat
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

class Harden01ContractTest(unittest.TestCase):
    def test_release_gate_runs_all_implementation_tests(self):
        text=(ROOT/"scripts/release-gate").read_text(encoding="utf-8")
        self.assertIn('section "Implementation tests"',text)
        self.assertIn('"$ROOT/scripts/test"',text)

    def test_test_runner_has_no_shell_true(self):
        text=(ROOT/"scripts/test").read_text(encoding="utf-8")
        self.assertNotIn("shell=True",text)

    def test_distribution_commands_exist_and_executable(self):
        for rel in ("scripts/package-distribution","scripts/distribution-smoke"):
            p=ROOT/rel
            self.assertTrue(p.is_file())
            self.assertTrue(bool(p.stat().st_mode & stat.S_IXUSR))

    def test_release_uses_full_gate_and_restores_version(self):
        text=(ROOT/"scripts/release").read_text(encoding="utf-8")
        self.assertIn("./scripts/release-gate",text)
        self.assertIn("VERSION restored",text)

if __name__=="__main__":
    unittest.main()
