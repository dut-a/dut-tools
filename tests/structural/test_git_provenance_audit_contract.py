from __future__ import annotations
import subprocess
import tomllib
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

class GitProvenanceAuditContractTest(unittest.TestCase):
    def test_registered_active(self):
        with (ROOT/"tools.toml").open("rb") as fh:
            tools=tomllib.load(fh)["tools"]
        meta=tools["git-provenance-audit"]
        self.assertEqual("active",meta["status"])
        self.assertEqual("bin/git-provenance-audit",meta["entrypoint"])

    def test_read_only_non_goal_is_documented(self):
        text=(ROOT/"tools/git-provenance-audit/README.md").read_text(encoding="utf-8")
        self.assertIn("It never rewrites history.",text)
        self.assertIn("git filter-repo",text)

    def test_cli_contract(self):
        entry=ROOT/"bin/git-provenance-audit"
        for flag in ("--help","--version"):
            cp=subprocess.run([str(entry),flag],cwd=ROOT,text=True,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.assertEqual(0,cp.returncode,cp.stderr)

if __name__=="__main__":
    unittest.main()
