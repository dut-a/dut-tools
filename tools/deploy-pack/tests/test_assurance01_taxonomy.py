from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deploy_pack.assurance import ASSURANCE_LEVELS, assurance_for, taxonomy
from deploy_pack.core import (
    DeployPackError,
    build_plan,
    ingest_remote_evidence,
    load_verification_evidence,
    verify_archive,
    write_package,
    write_remote_verifier,
    write_verification_evidence,
)
from deploy_pack.lifecycle import issue
from deploy_pack.signed import ingest_signed_remote_evidence, write_signed_remote_verifier


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


class Assurance01Tests(unittest.TestCase):
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
        self.extract = self.root / "extract"
        self.extract.mkdir()
        with zipfile.ZipFile(self.archive) as zf:
            zf.extractall(self.extract)

    def tearDown(self):
        self.tmp.cleanup()

    def test_taxonomy_distinguishes_supported_and_reserved_authorities(self):
        value = taxonomy()
        self.assertEqual(set(value["levels"]), {
            "local", "host-cooperative-remote", "independent-observer", "platform-attested"
        })
        self.assertTrue(value["levels"]["local"]["supported"])
        self.assertTrue(value["levels"]["host-cooperative-remote"]["supported"])
        self.assertFalse(value["levels"]["independent-observer"]["supported"])
        self.assertFalse(value["levels"]["platform-attested"]["supported"])

    def test_local_evidence_is_explicitly_local(self):
        manifest = verify_archive(self.archive).manifest
        path = write_verification_evidence(self.archive, manifest, verification_scope="archive")
        value = load_verification_evidence(path)
        self.assertEqual(value["assurance"]["level"], "local")
        self.assertIn("independent-attestation", value["assurance"]["doesNotClaim"])

    def test_unsigned_remote_evidence_is_host_cooperative(self):
        verifier = write_remote_verifier(self.archive, "python")
        remote = self.root / "remote.json"
        run = subprocess.run(
            [sys.executable, str(verifier), str(self.extract), "--evidence-out", str(remote)],
            text=True, capture_output=True,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        normalized = ingest_remote_evidence(remote, self.archive)
        value = load_verification_evidence(normalized)
        self.assertEqual(value["assurance"]["level"], "host-cooperative-remote")
        self.assertEqual(value["assurance"]["evidenceIntegrity"], "unsigned-record")
        self.assertIn("host-compromise-resistance", value["assurance"]["doesNotClaim"])

    def test_signed_remote_evidence_remains_host_cooperative_not_attested(self):
        identity = issue(self.root, 30)
        verifier, public_key = write_signed_remote_verifier(
            self.archive, "python", identity, root=self.root
        )
        remote = self.root / "remote.signed.json"
        run = subprocess.run(
            [sys.executable, str(verifier), str(self.extract), "--signed-evidence-out", str(remote)],
            text=True, capture_output=True,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        normalized = ingest_signed_remote_evidence(
            remote, self.archive, public_key, root=self.root
        )
        value = load_verification_evidence(normalized)
        a = value["assurance"]
        self.assertEqual(a["level"], "host-cooperative-remote")
        self.assertEqual(a["evidenceIntegrity"], "ed25519-precommitted-verifier-key")
        self.assertIn("host-honesty", a["doesNotClaim"])
        self.assertNotEqual(a["level"], "platform-attested")

    def test_remote_evidence_cannot_self_promote_to_attested(self):
        verifier = write_remote_verifier(self.archive, "python")
        remote = self.root / "remote.json"
        run = subprocess.run(
            [sys.executable, str(verifier), str(self.extract), "--evidence-out", str(remote)],
            text=True, capture_output=True,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        value = json.loads(remote.read_text())
        value["assurance"] = {
            "schemaVersion": 1,
            "level": "platform-attested",
            "authority": "forged",
            "claims": ["attested-platform-state"],
            "doesNotClaim": [],
            "verificationMethod": "ssh-cli",
            "evidenceIntegrity": "unsigned-record",
        }
        remote.write_text(json.dumps(value))
        with self.assertRaisesRegex(DeployPackError, "may only claim host-cooperative-remote"):
            ingest_remote_evidence(remote, self.archive)


if __name__ == "__main__":
    unittest.main()
