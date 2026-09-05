from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


class DeployStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init")
        git(self.root, "config", "user.email", "x@example.com")
        git(self.root, "config", "user.name", "X")
        (self.root / "app.php").write_text("v1\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "v1")
        self.sha = git(self.root, "rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def env(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        return env

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "deploy_pack.cli", *args],
            cwd=self.root,
            env=self.env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def test_json_and_read_only(self):
        p = self.run_cli("mark", self.sha, "--unsafe-no-evidence")
        self.assertEqual(p.returncode, 0, p.stdout)

        (self.root / "x.deploy.zip").write_bytes(b"x")
        (self.root / "x.deploy.zip.sha256").write_text("x")
        before = (self.root / ".deploy-pack-history.jsonl").read_text()

        p = self.run_cli("deploy", "status", "--json")
        self.assertEqual(p.returncode, 0, p.stdout)
        data = json.loads(p.stdout)
        self.assertEqual(data["health"], "PASS")
        self.assertEqual(data["history"]["latestTrustMode"], "unsafe-no-evidence")
        self.assertEqual(len(data["pending"]["packages"]), 1)
        self.assertEqual(
            (self.root / ".deploy-pack-history.jsonl").read_text(), before
        )


if __name__ == "__main__":
    unittest.main()
