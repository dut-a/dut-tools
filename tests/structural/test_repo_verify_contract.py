from __future__ import annotations
import subprocess
import tomllib
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

class RepoVerifyContractTest(unittest.TestCase):
    def test_registered_active(self):
        with (ROOT/"tools.toml").open("rb") as fh:
            tools=tomllib.load(fh)["tools"]
        meta=tools["repo-verify"]
        self.assertEqual("active",meta["status"])
        self.assertEqual("bin/repo-verify",meta["entrypoint"])

    def test_policy_boundary_documented(self):
        text=(ROOT/"tools/repo-verify/README.md").read_text(encoding="utf-8")
        self.assertIn("mechanism",text)
        self.assertIn("repository/company policy",text)
        self.assertIn("does not silently invoke a shell",text)

    def test_cli_contract(self):
        entry=ROOT/"bin/repo-verify"
        for flag in ("--help","--version"):
            cp=subprocess.run([str(entry),flag],cwd=ROOT,text=True,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.assertEqual(0,cp.returncode,cp.stderr)

if __name__=="__main__":
    unittest.main()
