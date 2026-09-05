from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deploy_pack.core import (
    DeployPackError,
    append_deployment_history,
    deployment_history_record,
    format_deployment_history_record,
    read_deployment_history,
    verify_deployment_history,
    write_baseline,
)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


class LedgerRollbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.email", "test@example.com")
        git(self.root, "config", "user.name", "Test")

        self.commits = []
        for value in ["v1\n", "v2\n", "v3\n"]:
            (self.root / "app.php").write_text(value)
            git(self.root, "add", "app.php")
            git(self.root, "commit", "-m", value.strip())
            self.commits.append(git(self.root, "rev-parse", "HEAD"))

        # Record three production deployments. Unsafe records keep this test
        # focused on ledger semantics rather than evidence construction.
        previous = None
        for number, commit in enumerate(self.commits, start=1):
            write_baseline(self.root, commit)
            append_deployment_history(
                self.root,
                previous_baseline=previous,
                new_baseline_ref=commit,
                new_baseline_commit=commit,
                evidence_path=None,
                evidence=None,
                archive=None,
                unsafe=True,
            )
            previous = commit

    def tearDown(self):
        self.tmp.cleanup()

    def test_rollback_ancestry_points_to_historical_record(self):
        target = self.commits[0]
        write_baseline(self.root, target)
        record = append_deployment_history(
            self.root,
            previous_baseline=self.commits[2],
            new_baseline_ref="rollback-v1",
            new_baseline_commit=target,
            evidence_path=None,
            evidence=None,
            archive=None,
            unsafe=True,
            rollback_target_record=1,
        )

        self.assertEqual(record["recordNumber"], 4)
        self.assertEqual(record["deploymentKind"], "rollback")
        self.assertEqual(record["rollback"]["fromRecord"], 3)
        self.assertEqual(record["rollback"]["fromCommit"], self.commits[2])
        self.assertEqual(record["rollback"]["targetRecord"], 1)
        self.assertEqual(record["rollback"]["targetCommit"], target)

        ok, errors = verify_deployment_history(self.root)
        self.assertTrue(ok, errors)

    def test_rollback_target_must_match_commit_being_marked(self):
        with self.assertRaises(DeployPackError):
            append_deployment_history(
                self.root,
                previous_baseline=self.commits[2],
                new_baseline_ref=self.commits[1],
                new_baseline_commit=self.commits[1],
                evidence_path=None,
                evidence=None,
                archive=None,
                unsafe=True,
                rollback_target_record=1,
            )

    def test_history_record_selector_supports_number_and_latest(self):
        number, record = deployment_history_record(self.root, "2")
        self.assertEqual(number, 2)
        self.assertEqual(record["newBaselineCommit"], self.commits[1])

        number, record = deployment_history_record(self.root, "latest")
        self.assertEqual(number, 3)
        self.assertEqual(record["newBaselineCommit"], self.commits[2])

    def test_history_record_rendering_is_forensic(self):
        number, record = deployment_history_record(self.root, 1)
        text = format_deployment_history_record(number, record)
        self.assertIn("Record              : 1", text)
        self.assertIn("New baseline commit", text)
        self.assertIn(self.commits[0], text)

    def test_history_verify_detects_tampered_rollback_target(self):
        target = self.commits[0]
        write_baseline(self.root, target)
        append_deployment_history(
            self.root,
            previous_baseline=self.commits[2],
            new_baseline_ref=target,
            new_baseline_commit=target,
            evidence_path=None,
            evidence=None,
            archive=None,
            unsafe=True,
            rollback_target_record=1,
        )
        ledger = self.root / ".deploy-pack-history.jsonl"
        records = [json.loads(line) for line in ledger.read_text().splitlines()]
        records[-1]["rollback"]["targetCommit"] = self.commits[1]
        ledger.write_text("\n".join(json.dumps(r, separators=(",", ":"), sort_keys=True) for r in records) + "\n")

        ok, errors = verify_deployment_history(self.root)
        self.assertFalse(ok)
        self.assertTrue(any("rollback targetCommit" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
