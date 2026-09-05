from __future__ import annotations
import json, subprocess, tempfile, unittest, zipfile, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deploy_pack.core import (
    append_deployment_history,
    build_rollback_plan,
    write_baseline,
    write_rollback_package,
)

def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

class RollbackPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init")
        git(self.root, "config", "user.email", "x@example.com")
        git(self.root, "config", "user.name", "X")
        (self.root/"app").mkdir()

        (self.root/"app/a.php").write_text("v1-a\n")
        (self.root/"app/old.php").write_text("v1-old\n")
        git(self.root, "add", "."); git(self.root, "commit", "-m", "v1")
        self.v1 = git(self.root, "rev-parse", "HEAD")
        write_baseline(self.root, self.v1)
        append_deployment_history(
            self.root, previous_baseline=None, new_baseline_ref="v1",
            new_baseline_commit=self.v1, evidence_path=None, evidence=None,
            archive=None, unsafe=True
        )

        (self.root/"app/a.php").write_text("v2-a\n")
        (self.root/"app/new.php").write_text("v2-new\n")
        git(self.root, "rm", "app/old.php")
        git(self.root, "add", "."); git(self.root, "commit", "-m", "v2")
        self.v2 = git(self.root, "rev-parse", "HEAD")
        write_baseline(self.root, self.v2)
        append_deployment_history(
            self.root, previous_baseline=self.v1, new_baseline_ref="v2",
            new_baseline_commit=self.v2, evidence_path=None, evidence=None,
            archive=None, unsafe=True
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_restores_historical_bytes_and_deletes_new_paths(self):
        plan = build_rollback_plan(self.root, 1)
        self.assertIn("app/a.php", [c.path for c in plan.deployable])
        self.assertIn("app/old.php", [c.path for c in plan.deployable])
        self.assertIn("app/new.php", [c.path for c in plan.deletions])

    def test_package_uses_target_commit_not_dirty_tree(self):
        plan = build_rollback_plan(self.root, 1)
        (self.root/"app/a.php").write_text("DIRTY\n")
        out = self.root/"rollback.deploy.zip"
        archive, deletions, checksum, plan_file = write_rollback_package(plan, out)
        with zipfile.ZipFile(archive) as zf:
            self.assertEqual(zf.read("app/a.php"), b"v1-a\n")
            manifest = json.loads(zf.read(".deploy-pack-manifest.json"))
            self.assertEqual(manifest["rollback"]["targetRecord"], 1)
            self.assertEqual(manifest["headCommit"], self.v1)
        self.assertEqual(deletions.read_text().strip(), "app/new.php")
        self.assertTrue(checksum.exists())
        self.assertTrue(plan_file.exists())

    def test_cannot_plan_latest(self):
        with self.assertRaises(Exception):
            build_rollback_plan(self.root, 2)

if __name__ == "__main__":
    unittest.main()
