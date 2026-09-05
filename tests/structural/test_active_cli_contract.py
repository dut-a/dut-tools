from __future__ import annotations
import subprocess
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

class ActiveCliContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (ROOT / "tools.toml").open("rb") as fh:
            cls.tools = tomllib.load(fh)["tools"]

    def test_active_commands_support_help_and_version(self):
        for name, meta in self.tools.items():
            if meta["status"] != "active":
                continue
            entry = ROOT / meta["entrypoint"]
            for flag in ("--help", "--version"):
                with self.subTest(tool=name, flag=flag):
                    cp = subprocess.run([str(entry), flag], cwd=ROOT, text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    self.assertEqual(0, cp.returncode, cp.stderr)
                    self.assertTrue(cp.stdout.strip())

if __name__ == "__main__":
    unittest.main()
