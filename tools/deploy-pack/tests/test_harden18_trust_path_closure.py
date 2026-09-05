from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deploy_pack.core import (
    Change,
    DeployPackError,
    MANIFEST_NAME,
    MANIFEST_SCHEMA_VERSION,
    PackPlan,
    build_manifest,
    deployment_status,
    verify_archive,
    verify_extracted_tree,
)
from deploy_pack.keyring import save_recovery_trust


def file_entry(path: str, data: bytes = b"ok\n") -> dict:
    return {
        "path": path,
        "status": "M",
        "source": "committed",
        "type": "file",
        "mode": "0o644",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


def manifest(*entries: dict, deletions=None) -> dict:
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "createdAt": "2026-09-05T00:00:00+00:00",
        "baselineRef": "base",
        "baselineCommit": "a" * 40,
        "headCommit": "b" * 40,
        "files": list(entries),
        "remoteDeletions": list(deletions or []),
    }


class Harden18TrustPathClosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_exactly_one_active_recovery_signer_is_required(self):
        bad = {
            "schemaVersion": 1,
            "activeSigner": "one",
            "signers": {
                "one": {"signerId": "one", "status": "active", "predecessorSignerId": None},
                "two": {"signerId": "two", "status": "active", "predecessorSignerId": None},
            },
        }
        with self.assertRaisesRegex(DeployPackError, "exactly one active signer"):
            save_recovery_trust(self.root, bad)

    def test_status_fails_for_multiple_active_recovery_signers(self):
        state = {
            "schemaVersion": 1,
            "activeSigner": "one",
            "signers": {
                "one": {"signerId": "one", "status": "active"},
                "two": {"signerId": "two", "status": "active"},
            },
        }
        (self.root / ".deploy-pack-recovery-trust.json").write_text(json.dumps(state))
        status = deployment_status(self.root)
        self.assertEqual(status["health"], "FAIL")
        self.assertTrue(any("exactly one active signer" in p for p in status["problems"]), status["problems"])

    def test_manifest_parent_traversal_is_rejected(self):
        issues = verify_extracted_tree(manifest(file_entry("../outside.txt")), self.root)
        self.assertEqual(issues[0].kind, "MANIFEST_UNSAFE_PATH")
        self.assertIn("canonical", issues[0].detail)

    def test_windows_drive_and_backslash_paths_are_rejected(self):
        for rel in ("C:/outside.txt", r"safe\\..\\outside.txt"):
            with self.subTest(rel=rel):
                issues = verify_extracted_tree(manifest(file_entry(rel)), self.root)
                self.assertEqual(issues[0].kind, "MANIFEST_UNSAFE_PATH")

    def test_safe_lexical_path_cannot_traverse_symlink_parent(self):
        outside = self.root.parent / (self.root.name + "-outside")
        outside.mkdir(exist_ok=True)
        try:
            (outside / "config.php").write_bytes(b"ok\n")
            (self.root / "public").symlink_to(outside, target_is_directory=True)
            issues = verify_extracted_tree(manifest(file_entry("public/config.php")), self.root)
            self.assertTrue(any(i.kind == "REMOTE_PATH_ESCAPE" for i in issues), issues)
        finally:
            if (self.root / "public").is_symlink():
                (self.root / "public").unlink()
            (outside / "config.php").unlink(missing_ok=True)
            outside.rmdir()

    def test_root_escaping_symlink_target_is_rejected_at_manifest_build(self):
        (self.root / "link").symlink_to("../outside-secret")
        plan = PackPlan(
            self.root, "base", "a" * 40, "b" * 40,
            (Change("A", "link"),), (), (), (),
        )
        with self.assertRaisesRegex(DeployPackError, "escapes deployment root"):
            build_manifest(plan)

    def test_safe_internal_symlink_remains_supported(self):
        (self.root / "target.txt").write_text("ok\n")
        (self.root / "link").symlink_to("target.txt")
        plan = PackPlan(
            self.root, "base", "a" * 40, "b" * 40,
            (Change("A", "target.txt"), Change("A", "link")), (), (), (),
        )
        value = build_manifest(plan)
        link = next(x for x in value["files"] if x["path"] == "link")
        self.assertEqual(link["type"], "symlink")
        self.assertEqual(link["symlinkTarget"], "target.txt")

    def test_archive_rejects_undeclared_unsafe_zip_member(self):
        archive = self.root / "bad.zip"
        value = manifest(file_entry("app.txt"))
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("app.txt", b"ok\n")
            zf.writestr("../escape.txt", b"owned")
            zf.writestr(MANIFEST_NAME, json.dumps(value))
        result = verify_archive(archive)
        self.assertFalse(result.ok)
        self.assertTrue(any(i.kind == "ARCHIVE_UNSAFE_PATH" for i in result.issues), result.issues)

    def test_remote_deletion_through_symlink_parent_is_rejected(self):
        outside = self.root.parent / (self.root.name + "-delete-outside")
        outside.mkdir(exist_ok=True)
        try:
            (self.root / "uploads").symlink_to(outside, target_is_directory=True)
            issues = verify_extracted_tree(manifest(deletions=["uploads/secret.txt"]), self.root)
            self.assertTrue(any(i.kind == "REMOTE_PATH_ESCAPE" for i in issues), issues)
        finally:
            if (self.root / "uploads").is_symlink():
                (self.root / "uploads").unlink()
            outside.rmdir()


if __name__ == "__main__":
    unittest.main()
