from __future__ import annotations
import subprocess, tempfile, unittest, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from deploy_pack.core import append_deployment_history, build_rollback_plan, write_baseline

def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

class HypotheticalRollbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init")
        git(self.root, "config", "user.email", "x@example.com")
        git(self.root, "config", "user.name", "X")
        self.commits = []
        previous = None
        for i in range(1, 5):
            (self.root / "app.php").write_text(f"v{i}\n")
            git(self.root, "add", ".")
            git(self.root, "commit", "-m", f"v{i}")
            sha = git(self.root, "rev-parse", "HEAD")
            self.commits.append(sha)
            write_baseline(self.root, sha)
            append_deployment_history(
                self.root,
                previous_baseline=previous,
                new_baseline_ref=f"v{i}",
                new_baseline_commit=sha,
                evidence_path=None,
                evidence=None,
                archive=None,
                unsafe=True,
            )
            previous = sha
    def tearDown(self):
        self.tmp.cleanup()
    def test_hypothetical_from_record(self):
        plan = build_rollback_plan(self.root, 1, source_record_number=3)
        self.assertEqual(plan.source_record, 3)
        self.assertEqual(plan.source_commit, self.commits[2])
        self.assertEqual(plan.target_record, 1)
    def test_target_must_be_earlier(self):
        with self.assertRaises(Exception):
            build_rollback_plan(self.root, 3, source_record_number=2)
    def test_default_remains_current_production(self):
        plan = build_rollback_plan(self.root, 1)
        self.assertEqual(plan.source_record, 4)
        self.assertEqual(plan.source_commit, self.commits[3])

if __name__ == "__main__":
    unittest.main()
