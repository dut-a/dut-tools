from __future__ import annotations
import subprocess
import tomllib
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

class ModuleNameNormalizerContractTest(unittest.TestCase):
    def test_registered_active(self):
        with (ROOT/"tools.toml").open("rb") as fh:
            tools=tomllib.load(fh)["tools"]
        meta=tools["module-name-normalizer"]
        self.assertEqual("active",meta["status"])
        self.assertEqual("bin/module-name-normalizer",meta["entrypoint"])

    def test_coordinate_safety_documented(self):
        text=(ROOT/"tools/module-name-normalizer/README.md").read_text(encoding="utf-8")
        self.assertIn("project `<artifactId>`",text)
        self.assertIn("dependency coordinates",text)

    def test_cli_contract(self):
        entry=ROOT/"bin/module-name-normalizer"
        for flag in ("--help","--version"):
            cp=subprocess.run([str(entry),flag],cwd=ROOT,text=True,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.assertEqual(0,cp.returncode,cp.stderr)

if __name__=="__main__":
    unittest.main()
