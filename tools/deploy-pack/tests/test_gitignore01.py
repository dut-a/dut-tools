from __future__ import annotations

import argparse
import subprocess
import tempfile
import unittest
from pathlib import Path

from deploy_pack.gitignore_managed import (
    BEGIN_MARKER,
    DURABLE_TRACKED_PATHS,
    END_MARKER,
    GitignoreManagedError,
    install_managed_gitignore,
    remove_managed_gitignore,
    run_gitignore_command,
)


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class Gitignore01Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        p = git(self.root, "init", "-q", "-b", "primary")
        self.assertEqual(p.returncode, 0, p.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_preserves_existing_bytes_and_is_idempotent(self):
        original = b"node_modules/\n.env\n"
        (self.root / ".gitignore").write_bytes(original)
        self.assertEqual(install_managed_gitignore(self.root), "installed")
        first = (self.root / ".gitignore").read_bytes()
        self.assertTrue(first.startswith(original))
        self.assertIn(BEGIN_MARKER.encode(), first)
        self.assertTrue(first.endswith((END_MARKER + "\n").encode()))
        self.assertEqual(install_managed_gitignore(self.root), "current")
        self.assertEqual((self.root / ".gitignore").read_bytes(), first)

    def test_stale_block_moves_to_eof_without_changing_user_bytes(self):
        before = b"alpha/\n"
        stale = ((BEGIN_MARKER + "\n") + "old-generated-rule\n" + (END_MARKER + "\n")).encode()
        after = b"omega/\n"
        (self.root / ".gitignore").write_bytes(before + stale + after)
        self.assertEqual(install_managed_gitignore(self.root), "updated")
        data = (self.root / ".gitignore").read_bytes()
        self.assertTrue(data.startswith(before + after))
        self.assertTrue(data.endswith((END_MARKER + "\n").encode()))

    def test_mutable_security_state_is_ignored(self):
        install_managed_gitignore(self.root)

        for path in (
            ".deploy-pack-keyring.json",
            ".deploy-pack-replay.json",
            ".deploy-pack-verifiers.json",
        ):
            p = git(self.root, "check-ignore", "-q", "--", path)
            self.assertEqual(
                p.returncode,
                0,
                f"{path} is unexpectedly visible to Git",
            )

    def test_durable_state_is_unignored_after_legacy_broad_rule(self):
        (self.root / ".gitignore").write_text(".deploy-pack-*\n", encoding="utf-8")
        install_managed_gitignore(self.root)
        for path in DURABLE_TRACKED_PATHS:
            p = git(self.root, "check-ignore", "-q", "--", path)
            self.assertEqual(p.returncode, 1, f"{path} unexpectedly ignored")

    def test_status_check_distinguishes_missing_and_current(self):
        args = argparse.Namespace(gitignore_command="status", check=True)
        self.assertEqual(run_gitignore_command(self.root, args), 1)
        install_managed_gitignore(self.root)
        self.assertEqual(run_gitignore_command(self.root, args), 0)

    def test_remove_only_removes_managed_block(self):
        original = b"vendor/\n"
        (self.root / ".gitignore").write_bytes(original)
        install_managed_gitignore(self.root)
        self.assertEqual(remove_managed_gitignore(self.root), "removed")
        data = (self.root / ".gitignore").read_bytes()
        self.assertIn(original, data)
        self.assertNotIn(BEGIN_MARKER.encode(), data)
        self.assertNotIn(END_MARKER.encode(), data)

    def test_malformed_markers_fail_closed(self):
        (self.root / ".gitignore").write_text(BEGIN_MARKER + "\n", encoding="utf-8")
        with self.assertRaises(GitignoreManagedError):
            install_managed_gitignore(self.root)


if __name__ == "__main__":
    unittest.main()
