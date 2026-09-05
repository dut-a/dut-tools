from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deploy_pack.core import DeployPackError, build_plan, manifest_sha256, verify_archive, write_package
from deploy_pack.lifecycle import get, issue, precommit_signing_key
from deploy_pack.signed import (
    canonical_bytes,
    generate_ephemeral_keypair,
    ingest_signed_remote_evidence,
    write_public_key_file,
    write_signed_remote_verifier,
)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


class Harden15VerifierKeyPrecommitmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init")
        git(self.root, "config", "user.email", "x@example.com")
        git(self.root, "config", "user.name", "X")
        (self.root / "app.php").write_text("v1\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "base")
        base = git(self.root, "rev-parse", "HEAD")
        (self.root / "app.php").write_text("v2\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "release")
        self.archive = self.root / "release.deploy.zip"
        write_package(build_plan(self.root, base, committed_only=True), self.archive)
        self.identity = issue(self.root, 30)
        self.ext = self.root / "ext"
        self.ext.mkdir()
        with zipfile.ZipFile(self.archive) as zf:
            zf.extractall(self.ext)

    def tearDown(self):
        self.tmp.cleanup()

    def test_signed_verifier_generation_precommits_key_to_issuance_state(self):
        verifier, pub = write_signed_remote_verifier(
            self.archive, "python", self.identity, root=self.root
        )
        pub_obj = json.loads(pub.read_text())
        rec = get(self.root, self.identity["verifierId"])
        self.assertEqual(rec["expectedPublicKeySha256"], pub_obj["publicKeySha256"])
        self.assertTrue(rec["signingKeyCommittedAt"])
        self.assertIn(rec["expectedPublicKeySha256"], verifier.read_text())

    def test_round_trip_requires_and_accepts_precommitted_key(self):
        verifier, pub = write_signed_remote_verifier(
            self.archive, "python", self.identity, root=self.root
        )
        evidence = self.root / "remote.signed.json"
        run = subprocess.run(
            [sys.executable, str(verifier), str(self.ext), "--signed-evidence-out", str(evidence)],
            text=True,
            capture_output=True,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        normalized = ingest_signed_remote_evidence(
            evidence, self.archive, pub, root=self.root
        )
        value = json.loads(normalized.read_text())
        expected = get(self.root, self.identity["verifierId"])["expectedPublicKeySha256"]
        self.assertEqual(value["signedRemoteEvidence"]["publicKeySha256"], expected)

    def test_valid_replacement_key_is_rejected_before_registration(self):
        verifier, legitimate_pub = write_signed_remote_verifier(
            self.archive, "python", self.identity, root=self.root
        )
        legitimate = get(self.root, self.identity["verifierId"])
        manifest = verify_archive(self.archive).manifest
        attacker_seed, attacker_pub = generate_ephemeral_keypair()
        attacker_key = self.root / "attacker.public-key.json"
        write_public_key_file(attacker_key, attacker_pub)
        payload = {
            "schemaVersion": 1,
            "verifierIdentity": legitimate,
            "result": "PASS",
            "verificationScope": "remote",
            "verificationMethod": "ssh-cli",
            "verificationRoot": "/srv/app",
            "strictPermissions": False,
            "verifierRuntime": "python",
            "verifiedAt": legitimate["issuedAt"],
            "manifest": {
                "sha256": manifest_sha256(manifest),
                "schemaVersion": manifest["schemaVersion"],
                "baselineRef": manifest["baselineRef"],
                "baselineCommit": manifest["baselineCommit"],
                "headCommit": manifest["headCommit"],
                "fileCount": len(manifest["files"]),
                "remoteDeletionCount": len(manifest["remoteDeletions"]),
            },
        }
        message = canonical_bytes(payload)
        signature = Ed25519PrivateKey.from_private_bytes(attacker_seed).sign(message)
        envelope = {
            "schemaVersion": 1,
            "kind": "deploy-pack.remote-evidence.signed",
            "payload": payload,
            "signature": {
                "algorithm": "Ed25519",
                "payloadSha256": hashlib.sha256(message).hexdigest(),
                "signatureBase64": base64.b64encode(signature).decode(),
            },
            "signer": {
                "publicKeyBase64": base64.b64encode(attacker_pub).decode(),
                "publicKeySha256": hashlib.sha256(attacker_pub).hexdigest(),
            },
        }
        attack = self.root / "attacker.signed.json"
        attack.write_text(json.dumps(envelope))
        with self.assertRaisesRegex(DeployPackError, "does not match verifier key precommitment"):
            ingest_signed_remote_evidence(
                attack, self.archive, attacker_key, root=self.root
            )
        self.assertNotEqual(
            json.loads(legitimate_pub.read_text())["publicKeySha256"],
            hashlib.sha256(attacker_pub).hexdigest(),
        )

    def test_legacy_uncommitted_identity_fails_closed(self):
        seed, public = generate_ephemeral_keypair()
        key = self.root / "legacy.public-key.json"
        write_public_key_file(key, public)
        manifest = verify_archive(self.archive).manifest
        payload = {
            "schemaVersion": 1,
            "verifierIdentity": self.identity,
            "result": "PASS",
            "verificationScope": "remote",
            "verificationMethod": "ssh-cli",
            "verificationRoot": "/srv/app",
            "strictPermissions": False,
            "verifierRuntime": "python",
            "verifiedAt": self.identity["issuedAt"],
            "manifest": {
                "sha256": manifest_sha256(manifest),
                "schemaVersion": manifest["schemaVersion"],
                "baselineRef": manifest["baselineRef"],
                "baselineCommit": manifest["baselineCommit"],
                "headCommit": manifest["headCommit"],
                "fileCount": len(manifest["files"]),
                "remoteDeletionCount": len(manifest["remoteDeletions"]),
            },
        }
        message = canonical_bytes(payload)
        sig = Ed25519PrivateKey.from_private_bytes(seed).sign(message)
        env = {
            "schemaVersion": 1,
            "kind": "deploy-pack.remote-evidence.signed",
            "payload": payload,
            "signature": {
                "algorithm": "Ed25519",
                "payloadSha256": hashlib.sha256(message).hexdigest(),
                "signatureBase64": base64.b64encode(sig).decode(),
            },
            "signer": {
                "publicKeyBase64": base64.b64encode(public).decode(),
                "publicKeySha256": hashlib.sha256(public).hexdigest(),
            },
        }
        evidence = self.root / "legacy.signed.json"
        evidence.write_text(json.dumps(env))
        with self.assertRaisesRegex(DeployPackError, "no precommitted signing key"):
            ingest_signed_remote_evidence(evidence, self.archive, key, root=self.root)

    def test_second_signing_key_cannot_replace_existing_commitment(self):
        write_signed_remote_verifier(self.archive, "python", self.identity, root=self.root)
        with self.assertRaisesRegex(DeployPackError, "already has a different precommitted signing key"):
            write_signed_remote_verifier(self.archive, "python", self.identity, root=self.root)


if __name__ == "__main__":
    unittest.main()
