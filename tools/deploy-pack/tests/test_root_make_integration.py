from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

class RootMakeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "mk").mkdir()
        (self.repo / "scripts").mkdir()
        shutil.copy(ROOT / "mk" / "deploy-pack.inc", self.repo / "mk" / "deploy-pack.inc")
        shutil.copy(
            ROOT / "scripts" / "check-deploy-status-target.sh",
            self.repo / "scripts" / "check-deploy-status-target.sh",
        )
        (self.repo / "scripts" / "check-deploy-status-target.sh").chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def test_checker_passes_when_root_makefile_includes_fragment(self):
        (self.repo / "Makefile").write_text(
            "-include mk/deploy-pack.inc\n",
            encoding="utf-8",
        )
        p = subprocess.run(
            [str(self.repo / "scripts" / "check-deploy-status-target.sh")],
            cwd=self.repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("DEPLOY-STATUS-CHECKER: PASS", p.stdout)

    def test_checker_fails_when_include_is_missing(self):
        (self.repo / "Makefile").write_text(
            "help:\n\t@true\n",
            encoding="utf-8",
        )
        p = subprocess.run(
            [str(self.repo / "scripts" / "check-deploy-status-target.sh")],
            cwd=self.repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("does not include mk/deploy-pack.inc", p.stdout)

if __name__ == "__main__":
    unittest.main()
