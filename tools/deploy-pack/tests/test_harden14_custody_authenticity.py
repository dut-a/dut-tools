from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deploy_pack.core import DeployPackError
from deploy_pack.keyring import (
    _write_offline_anchor_copy,
    export_offline_trust_copies,
    save_recovery_trust,
    verify_offline_trust_quorum,
)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


class Harden14CustodyAuthenticityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init")
        git(self.root, "config", "user.email", "x@example.com")
        git(self.root, "config", "user.name", "X")
        (self.root / "app.php").write_text("v1\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "v1")
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

    def test_quorum_uses_pre_enrolled_local_signers(self):
        result = export_offline_trust_copies(self.root, self.root / "custody.json", copies=3)
        a = result["artifacts"]
        verified = verify_offline_trust_quorum(
            [a[0]["anchor"], a[2]["anchor"]],
            [a[0]["publicKey"], a[2]["publicKey"]],
            root=self.root,
        )
        self.assertEqual(verified["authenticityRoot"], "local-checkpoint-history")
        self.assertTrue(verified["custodySignersPreEnrolled"])
        self.assertEqual(verified["checkpointHash"], result["checkpoint"]["checkpointHash"])

    def test_replacement_keypairs_cannot_mint_forged_quorum(self):
        result = export_offline_trust_copies(self.root, self.root / "custody.json", copies=3)
        legitimate = result["artifacts"]
        forged = []
        for index in (0, 1):
            envelope = json.loads(legitimate[index]["anchor"].read_text(encoding="utf-8"))
            payload = envelope["payload"]
            out = self.root / f"forged-{index}.json"
            anchor, public_key, _fingerprint, _metadata = _write_offline_anchor_copy(payload, out)
            forged.append((anchor, public_key))
        with self.assertRaisesRegex(DeployPackError, "not enrolled"):
            verify_offline_trust_quorum(
                [x[0] for x in forged],
                [x[1] for x in forged],
                root=self.root,
            )

    def test_external_checkpoint_requires_pinned_hash(self):
        result = export_offline_trust_copies(self.root, self.root / "custody.json", copies=3)
        a = result["artifacts"]
        manifest = result["manifest"]
        with self.assertRaisesRegex(DeployPackError, "expected-checkpoint-hash"):
            verify_offline_trust_quorum(
                [a[0]["anchor"], a[1]["anchor"]],
                [a[0]["publicKey"], a[1]["publicKey"]],
                trusted_checkpoint=manifest,
            )
        verified = verify_offline_trust_quorum(
            [a[0]["anchor"], a[1]["anchor"]],
            [a[0]["publicKey"], a[1]["publicKey"]],
            trusted_checkpoint=manifest,
            expected_checkpoint_hash=result["checkpoint"]["checkpointHash"],
        )
        self.assertEqual(verified["authenticityRoot"], "externally-pinned-checkpoint")

    def test_wrong_external_checkpoint_hash_fails(self):
        result = export_offline_trust_copies(self.root, self.root / "custody.json", copies=3)
        a = result["artifacts"]
        with self.assertRaisesRegex(DeployPackError, "expected checkpoint hash"):
            verify_offline_trust_quorum(
                [a[0]["anchor"], a[1]["anchor"]],
                [a[0]["publicKey"], a[1]["publicKey"]],
                trusted_checkpoint=result["manifest"],
                expected_checkpoint_hash="0" * 64,
            )

    def test_no_authenticity_root_fails_closed(self):
        result = export_offline_trust_copies(self.root, self.root / "custody.json", copies=3)
        a = result["artifacts"]
        with self.assertRaisesRegex(DeployPackError, "authenticity root is required"):
            verify_offline_trust_quorum(
                [a[0]["anchor"], a[1]["anchor"]],
                [a[0]["publicKey"], a[1]["publicKey"]],
            )


if __name__ == "__main__":
    unittest.main()
