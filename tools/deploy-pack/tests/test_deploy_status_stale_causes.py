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
    load_recovery_trust,
    save_recovery_trust,
)


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


class DeployStatusStaleCausesTests(unittest.TestCase):
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
                        "trustedAt": "2026-01-01T00:00:00+00:00",
                        "activatedAt": "2026-01-01T00:00:00+00:00",
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

    def status(self):
        p = self.run_cli("deploy", "status", "--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads(p.stdout)

    def test_signer_added_is_exact_stale_cause(self):
        export_offline_trust_copies(
            self.root, self.root / "custody.json", copies=3, quorum=2
        )
        trust = load_recovery_trust(self.root)
        trust["signers"]["backup"] = {
            "signerId": "backup",
            "status": "trusted",
            "trustedAt": "2026-09-03T18:00:00+00:00",
            "activatedAt": None,
            "predecessorSignerId": None,
        }
        save_recovery_trust(self.root, trust)

        custody = self.status()["offlineCustody"]
        self.assertTrue(custody["stale"])
        self.assertEqual(custody["staleCauseEvidence"], "exact")
        self.assertTrue(
            any(
                c["type"] == "signer-added" and c["signerId"] == "backup"
                for c in custody["staleCauses"]
            )
        )

    def test_rotation_reports_active_change_and_lifecycle_changes(self):
        export_offline_trust_copies(
            self.root, self.root / "custody.json", copies=3, quorum=2
        )
        trust = load_recovery_trust(self.root)
        trust["signers"]["primary"]["status"] = "retired"
        trust["signers"]["primary"]["retiredAt"] = "2026-09-03T18:10:00+00:00"
        trust["signers"]["next"] = {
            "signerId": "next",
            "status": "active",
            "trustedAt": "2026-09-03T18:10:00+00:00",
            "activatedAt": "2026-09-03T18:10:00+00:00",
            "predecessorSignerId": "primary",
        }
        trust["activeSigner"] = "next"
        save_recovery_trust(self.root, trust)

        custody = self.status()["offlineCustody"]
        types = {c["type"] for c in custody["staleCauses"]}
        self.assertIn("active-signer-changed", types)
        self.assertIn("signer-added", types)
        self.assertIn("signer-retired", types)

    def test_legacy_checkpoint_reports_partial_cause_evidence(self):
        export_offline_trust_copies(
            self.root, self.root / "custody.json", copies=3, quorum=2
        )
        checkpoint = self.root / ".deploy-pack-offline-checkpoints.jsonl"
        rec = json.loads(checkpoint.read_text().strip())
        rec.pop("recoveryTrustSnapshot", None)
        body = {k: v for k, v in rec.items() if k != "checkpointHash"}
        import hashlib
        rec["checkpointHash"] = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        checkpoint.write_text(
            json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n"
        )
        trust = load_recovery_trust(self.root)
        trust["activeSigner"] = None
        trust["signers"]["primary"]["status"] = "retired"
        save_recovery_trust(self.root, trust)

        custody = self.status()["offlineCustody"]
        self.assertEqual(custody["staleCauseEvidence"], "partial")
        self.assertTrue(custody["staleCauses"])
        self.assertEqual(custody["staleCauses"][0]["type"], "active-signer-changed")


if __name__ == "__main__":
    unittest.main()
