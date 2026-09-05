from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deploy_pack.core import build_plan, write_package, write_remote_verifier
from deploy_pack.lifecycle import issue as issue_verifier
from deploy_pack.signed import write_signed_remote_verifier


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


class Harden13ProtectedArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init")
        git(self.root, "config", "user.email", "x@example.com")
        git(self.root, "config", "user.name", "X")
        (self.root / "app.php").write_text("v1\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "base")
        self.base = git(self.root, "rev-parse", "HEAD")
        (self.root / "app.php").write_text("v2\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _paths(self, plan):
        return {c.path for c in plan.deployable}, {c.path for c in plan.ignored}

    def _archive(self) -> Path:
        plan = build_plan(self.root, self.base)
        out = self.root / "release.deploy.zip"
        write_package(plan, out)
        return out

    def test_protected_state_cannot_be_reincluded_by_project_policy(self):
        state = self.root / ".deploy-pack-replay.json"
        state.write_text('{"consumed": []}\n', encoding="utf-8")
        (self.root / ".deploy-pack.toml").write_text(
            '[deploy-pack]\ninclude = [".deploy-pack-replay.json"]\n',
            encoding="utf-8",
        )
        deployable, ignored = self._paths(build_plan(self.root, self.base))
        self.assertNotIn(state.name, deployable)
        self.assertIn(state.name, ignored)

    def test_protected_state_cannot_be_reincluded_by_cli_policy(self):
        state = self.root / ".deploy-pack-keyring.json"
        state.write_text('{"keys": []}\n', encoding="utf-8")
        deployable, ignored = self._paths(
            build_plan(self.root, self.base, cli_includes=[state.name])
        )
        self.assertNotIn(state.name, deployable)
        self.assertIn(state.name, ignored)

    def test_custom_named_signed_php_verifier_is_blocked_by_content(self):
        archive = self._archive()
        identity = issue_verifier(self.root, ttl_minutes=30)
        custom = self.root / "innocent-looking.php"
        verifier, _ = write_signed_remote_verifier(
            archive, "php", identity, output=custom, root=self.root
        )
        deployable, ignored = self._paths(
            build_plan(self.root, self.base, cli_includes=[custom.name])
        )
        self.assertNotIn(verifier.name, deployable)
        self.assertIn(verifier.name, ignored)

    def test_custom_named_browser_verifier_is_blocked_by_content(self):
        archive = self._archive()
        custom = self.root / "health.php"
        verifier = write_remote_verifier(
            archive,
            "php",
            output=custom,
            browser=True,
            token="one-time-secret-token",
        )
        deployable, ignored = self._paths(
            build_plan(self.root, self.base, cli_includes=[custom.name])
        )
        self.assertNotIn(verifier.name, deployable)
        self.assertIn(verifier.name, ignored)

    def test_signed_php_verifier_is_mode_0600(self):
        archive = self._archive()
        identity = issue_verifier(self.root, ttl_minutes=30)
        verifier, _ = write_signed_remote_verifier(archive, "php", identity, root=self.root)
        self.assertEqual(stat.S_IMODE(verifier.stat().st_mode), 0o600)

    def test_signed_python_verifier_is_mode_0700(self):
        archive = self._archive()
        identity = issue_verifier(self.root, ttl_minutes=30)
        verifier, _ = write_signed_remote_verifier(archive, "python", identity, root=self.root)
        self.assertEqual(stat.S_IMODE(verifier.stat().st_mode), 0o700)

    def test_browser_verifier_is_mode_0600(self):
        archive = self._archive()
        verifier = write_remote_verifier(
            archive, "php", browser=True, token="one-time-secret-token"
        )
        self.assertEqual(stat.S_IMODE(verifier.stat().st_mode), 0o600)

    def test_deletion_of_protected_artifact_is_not_emitted_remote(self):
        # Simulate an old committed deploy-pack control file that is removed.
        state = self.root / ".deploy-pack-replay.json"
        state.write_text('{"consumed": []}\n', encoding="utf-8")
        git(self.root, "add", state.name)
        git(self.root, "commit", "-m", "accidentally tracked control state")
        baseline = git(self.root, "rev-parse", "HEAD")
        state.unlink()
        plan = build_plan(self.root, baseline)
        self.assertNotIn(state.name, {d.path for d in plan.deletions})
        self.assertIn(state.name, {c.path for c in plan.ignored})


if __name__ == "__main__":
    unittest.main()
