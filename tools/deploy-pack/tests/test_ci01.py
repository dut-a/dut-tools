from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from deploy_pack.ci import CiStep, run_ci_build, run_ci_check


def passing(name, *command):
    return CiStep(
        name=name,
        command=("deploy-pack", *command),
        status="pass",
        exit_code=0,
        stdout="",
        stderr="",
    )


class Ci01Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_check_runs_read_only_gate_set(self):
        calls = []

        def fake(root, name, args):
            calls.append((name, tuple(args)))
            return passing(name, *tuple(args))

        with patch("deploy_pack.ci._run_step", side_effect=fake):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = run_ci_check(self.root, json_output=True)

        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(
            calls,
            [
                ("gitignore", ("gitignore", "status", "--check")),
                ("inspect", ("inspect",)),
                ("history", ("history-verify",)),
                ("deployment_status", ("deploy", "status")),
            ],
        )
        self.assertFalse(payload["state_mutation"]["detected"])

    def test_check_fails_when_authoritative_state_mutates(self):
        counter = {"n": 0}

        def fake(root, name, args):
            counter["n"] += 1
            if counter["n"] == 2:
                (root / ".deploy-pack-baseline").write_text("changed\n", encoding="utf-8")
            return passing(name, *tuple(args))

        with patch("deploy_pack.ci._run_step", side_effect=fake):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = run_ci_check(self.root, json_output=True)

        self.assertEqual(rc, 1)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["state_mutation"]["detected"])
        self.assertIn(".deploy-pack-baseline", payload["state_mutation"]["diff"]["added"])

    def test_build_is_inspect_pack_verify_and_reports_hash(self):
        artifact = self.root / "dist" / "deploy.zip"

        def fake(root, name, args):
            args = tuple(args)
            if name == "pack":
                out_index = args.index("--output") + 1
                path = Path(args[out_index])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"ci-artifact")
            return passing(name, *args)

        with patch("deploy_pack.ci._run_step", side_effect=fake):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = run_ci_build(
                    self.root,
                    output="dist/deploy.zip",
                    json_output=True,
                )

        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(
            [step["name"] for step in payload["steps"]],
            ["inspect", "pack", "verify"],
        )
        self.assertEqual(payload["artifact"]["path"], str(artifact.resolve()))
        self.assertEqual(payload["artifact"]["size"], len(b"ci-artifact"))
        self.assertEqual(len(payload["artifact"]["sha256"]), 64)

    def test_build_stops_after_inspect_failure(self):
        def fake(root, name, args):
            if name == "inspect":
                return CiStep(
                    name="inspect",
                    command=("deploy-pack", "inspect"),
                    status="fail",
                    exit_code=1,
                    stdout="",
                    stderr="bad deployment surface",
                )
            self.fail(f"unexpected nested command: {name}")

        with patch("deploy_pack.ci._run_step", side_effect=fake):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = run_ci_build(
                    self.root,
                    output="deploy.zip",
                    json_output=True,
                )

        self.assertEqual(rc, 1)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["steps"][1]["status"], "skipped")
        self.assertEqual(payload["steps"][2]["status"], "skipped")

    def test_transient_lock_is_exempt_from_mutation_guard(self):
        def fake(root, name, args):
            (root / ".deploy-pack.lock").write_text("pid\n", encoding="utf-8")
            return passing(name, *tuple(args))

        with patch("deploy_pack.ci._run_step", side_effect=fake):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = run_ci_check(self.root, json_output=True)

        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["state_mutation"]["detected"])


if __name__ == "__main__":
    unittest.main()
