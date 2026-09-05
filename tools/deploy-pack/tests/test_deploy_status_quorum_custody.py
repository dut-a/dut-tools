from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deploy_pack.keyring import (
    export_offline_trust_copies,
    save_recovery_trust,
)

def git(root, *args):
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True
    ).strip()

class DeployStatusQuorumCustodyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init")
        git(self.root, "config", "user.email", "x@example.com")
        git(self.root, "config", "user.name", "X")
        (self.root / "app.php").write_text("v1\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "v1")
        sha = git(self.root, "rev-parse", "HEAD")
        p = self.run_cli("mark", sha, "--unsafe-no-evidence")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        save_recovery_trust(
            self.root,
            {
                "schemaVersion": 1,
                "activeSigner": "primary",
                "signers": {
                    "primary": {
                        "signerId": "primary",
                        "status": "active",
                        "predecessorSignerId": None,
                    }
                },
            },
        )

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
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_no_checkpoint_is_healthy_but_unconfigured(self):
        p = self.run_cli("deploy", "status", "--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        data = json.loads(p.stdout)
        self.assertFalse(data["offlineCustody"]["configured"])
        self.assertTrue(data["offlineCustody"]["healthy"])
        self.assertIsNone(data["offlineCustody"]["quorumMet"])

    def test_three_copy_two_quorum_is_reported_healthy(self):
        export_offline_trust_copies(
            self.root,
            self.root / "custody.json",
            copies=3,
            quorum=2,
        )
        p = self.run_cli("deploy", "status", "--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        data = json.loads(p.stdout)
        custody = data["offlineCustody"]
        self.assertTrue(custody["configured"])
        self.assertTrue(custody["healthy"])
        self.assertTrue(custody["checkpointChainHealthy"])
        self.assertEqual(custody["declaredCopyCount"], 3)
        self.assertEqual(custody["declaredQuorum"], 2)
        self.assertEqual(custody["uniqueCopyIds"], 3)
        self.assertEqual(custody["independentSigners"], 3)
        self.assertTrue(custody["quorumMet"])

    def test_quiet_fails_if_latest_checkpoint_loses_independent_signer_quorum(self):
        export_offline_trust_copies(
            self.root,
            self.root / "custody.json",
            copies=3,
            quorum=2,
        )
        checkpoint_path = self.root / ".deploy-pack-offline-checkpoints.jsonl"
        records = [
            json.loads(line)
            for line in checkpoint_path.read_text().splitlines()
            if line.strip()
        ]
        latest = records[-1]

        # Collapse all signer fingerprints onto one identity, then recompute
        # the checkpoint hash so this isolates quorum semantics from chain hash.
        same_fp = latest["copies"][0]["publicKeySha256"]
        for copy in latest["copies"]:
            copy["publicKeySha256"] = same_fp

        body = {k: v for k, v in latest.items() if k != "checkpointHash"}
        import hashlib
        latest["checkpointHash"] = hashlib.sha256(
            json.dumps(
                body,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        checkpoint_path.write_text(
            json.dumps(
                latest,
                sort_keys=True,
                separators=(",", ":"),
            ) + "\n"
        )

        p = self.run_cli("deploy", "status", "--quiet")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "")
        self.assertEqual(p.stderr, "")

if __name__ == "__main__":
    unittest.main()
