from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import zipfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deploy_pack.core import (
    DeployPackError,
    append_deployment_history,
    build_plan,
    ingest_remote_evidence,
    load_verification_evidence,
    manifest_sha256,
    read_deployment_history,
    validate_mark_evidence,
    verify_archive,
    verify_deployment_history,
    write_baseline,
    write_package,
)

def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

class DeploymentLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init")
        git(self.root, "config", "user.email", "x@example.com")
        git(self.root, "config", "user.name", "X")
        (self.root/"app.php").write_text("v1\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "base")
        self.base = git(self.root, "rev-parse", "HEAD")
        (self.root/"app.php").write_text("v2\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "release")
        self.head = git(self.root, "rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def package(self):
        plan = build_plan(self.root, self.base, committed_only=True)
        out = self.root/"release.deploy.zip"
        write_package(plan, out)
        return out

    def normalized_remote_evidence(self, out):
        manifest = verify_archive(out).manifest
        remote = {
            "schemaVersion": 1,
            "result": "PASS",
            "verificationScope": "remote",
            "verificationMethod": "browser",
            "verificationRoot": "/home/account/public_html",
            "strictPermissions": False,
            "verifierRuntime": "php-browser",
            "verifiedAt": "2026-08-28T18:30:00+00:00",
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
        rp = self.root/"remote.json"
        rp.write_text(json.dumps(remote))
        return ingest_remote_evidence(rp, out)

    def test_safe_mark_record_shape_and_history_verify(self):
        out = self.package()
        evidence_path = self.normalized_remote_evidence(out)
        resolved, evidence = validate_mark_evidence(
            self.root, "HEAD", evidence_path, archive=out
        )

        write_baseline(self.root, "HEAD")
        append_deployment_history(
            self.root,
            previous_baseline=None,
            new_baseline_ref="HEAD",
            new_baseline_commit=resolved,
            evidence_path=evidence_path,
            evidence=evidence,
            archive=out,
        )

        records = read_deployment_history(self.root)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertFalse(rec["unsafeNoEvidence"])
        self.assertEqual(rec["newBaselineCommit"], self.head)
        self.assertEqual(rec["evidence"]["headCommit"], self.head)
        self.assertEqual(rec["archive"]["name"], out.name)
        ok, errors = verify_deployment_history(self.root)
        self.assertTrue(ok, errors)

    def test_unsafe_bootstrap_is_explicit(self):
        write_baseline(self.root, self.base)
        append_deployment_history(
            self.root,
            previous_baseline=None,
            new_baseline_ref=self.base,
            new_baseline_commit=self.base,
            evidence_path=None,
            evidence=None,
            archive=None,
            unsafe=True,
        )
        rec = read_deployment_history(self.root)[0]
        self.assertTrue(rec["unsafeNoEvidence"])
        ok, errors = verify_deployment_history(self.root)
        self.assertTrue(ok, errors)

    def test_history_detects_broken_continuity(self):
        write_baseline(self.root, "HEAD")
        append_deployment_history(
            self.root,
            previous_baseline=None,
            new_baseline_ref=self.base,
            new_baseline_commit=self.base,
            evidence_path=None,
            evidence=None,
            archive=None,
            unsafe=True,
        )
        append_deployment_history(
            self.root,
            previous_baseline="not-the-base",
            new_baseline_ref="HEAD",
            new_baseline_commit=self.head,
            evidence_path=None,
            evidence=None,
            archive=None,
            unsafe=True,
        )
        ok, errors = verify_deployment_history(self.root)
        self.assertFalse(ok)
        self.assertTrue(any("previousBaseline" in e for e in errors))

    def test_history_detects_baseline_drift(self):
        append_deployment_history(
            self.root,
            previous_baseline=None,
            new_baseline_ref=self.base,
            new_baseline_commit=self.base,
            evidence_path=None,
            evidence=None,
            archive=None,
            unsafe=True,
        )
        write_baseline(self.root, "HEAD")
        ok, errors = verify_deployment_history(self.root)
        self.assertFalse(ok)
        self.assertTrue(any("current baseline" in e for e in errors))

if __name__ == "__main__":
    unittest.main()
