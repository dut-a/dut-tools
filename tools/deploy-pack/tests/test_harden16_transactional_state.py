from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deploy_pack.lifecycle import issue, VERIFIER_STATE_FILE, REPLAY_STATE_FILE
from deploy_pack.state import (
    MARK_JOURNAL_FILE,
    atomic_write_text,
    begin_mark_transaction,
    recover_mark_transaction,
    repository_lock,
)


class Harden16TransactionalStateTest(unittest.TestCase):
    def test_interrupted_mark_restores_before_images_on_next_mutator(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            baseline = root / ".deploy-pack-baseline"
            history = root / ".deploy-pack-history.jsonl"
            replay = root / REPLAY_STATE_FILE
            baseline.write_text("old\n")
            history.write_text('{"old":true}\n')
            replay.write_text('{"schemaVersion":1,"consumed":{}}\n')
            with repository_lock(root):
                begin_mark_transaction(root, [baseline, history, replay], {"ref": "new"})
                atomic_write_text(baseline, "new\n")
                atomic_write_text(history, '{"new":true}\n')
                # Simulated process death: do not commit/recover here.
            self.assertTrue((root / MARK_JOURNAL_FILE).exists())
            issue(root, 30)  # next stateful operation is a recovery boundary
            self.assertEqual("old\n", baseline.read_text())
            self.assertEqual('{"old":true}\n', history.read_text())
            self.assertEqual('{"schemaVersion":1,"consumed":{}}\n', replay.read_text())
            self.assertFalse((root / MARK_JOURNAL_FILE).exists())

    def test_explicit_recovery_restores_absent_file_as_absent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / ".deploy-pack-replay.json"
            with repository_lock(root):
                begin_mark_transaction(root, [target], {"test": True})
                atomic_write_text(target, "created-after-prepare\n")
                self.assertTrue(recover_mark_transaction(root))
            self.assertFalse(target.exists())

    def test_concurrent_verifier_issuance_does_not_lose_updates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = Path(__file__).resolve().parents[1] / "src"
            code = (
                "from pathlib import Path; "
                "from deploy_pack.lifecycle import issue; "
                f"issue(Path({str(root)!r}),30)"
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
            procs = [subprocess.Popen([sys.executable, "-c", code], env=env) for _ in range(6)]
            for proc in procs:
                self.assertEqual(0, proc.wait(timeout=15))
            state = json.loads((root / VERIFIER_STATE_FILE).read_text())
            self.assertEqual(6, len(state["verifiers"]))

    def test_repository_lock_times_out_for_competing_process(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = Path(__file__).resolve().parents[1] / "src"
            code = (
                "from pathlib import Path; "
                "from deploy_pack.state import repository_lock; "
                f"\nwith repository_lock(Path({str(root)!r}), timeout_seconds=0.15): pass"
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
            with repository_lock(root):
                proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=5)
            self.assertNotEqual(0, proc.returncode)
            self.assertIn("deploy-pack repository is locked", proc.stderr)

    def test_atomic_state_files_are_owner_only_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".deploy-pack-state.json"
            atomic_write_text(path, "{}\n")
            self.assertEqual(0o600, path.stat().st_mode & 0o777)


    def test_deploy_status_fails_closed_when_mark_journal_exists(self):
        from deploy_pack.core import deployment_status
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / MARK_JOURNAL_FILE).write_text(json.dumps({"schemaVersion":1,"files":{}}))
            status = deployment_status(root)
            self.assertEqual("FAIL", status["health"])
            self.assertTrue(status["transaction"]["recoveryPending"])

if __name__ == "__main__":
    unittest.main()
