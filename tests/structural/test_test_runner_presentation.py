from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "test"


class TestRunnerPresentationTests(unittest.TestCase):
    def make_repo(self, *, failing: bool = False) -> Path:
        temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp, ignore_errors=True)
        (temp / "scripts").mkdir()
        (temp / "tools" / "demo" / "tests").mkdir(parents=True)
        shutil.copy2(RUNNER, temp / "scripts" / "test")
        (temp / "tools.toml").write_text(textwrap.dedent("""
            [tools.demo]
            command = "demo"
            path = "tools/demo"
            language = "python"
            status = "active"
            version_file = "tools/demo/VERSION"
            entrypoint = "bin/demo"
            test_command = "python3 -m unittest discover -s tests -p 'test_*.py' -v"
        """).strip() + "\n")
        body = "self.fail('boom')" if failing else "self.assertEqual(2 + 2, 4)"
        (temp / "tools" / "demo" / "tests" / "test_demo.py").write_text(textwrap.dedent(f"""
            import unittest

            class DemoTest(unittest.TestCase):
                def test_demo(self):
                    {body}
        """).strip() + "\n")
        return temp

    def run_runner(self, repo: Path, *args: str, env: dict[str, str] | None = None):
        merged = os.environ.copy()
        merged["NO_COLOR"] = "1"
        if env:
            merged.update(env)
        return subprocess.run(
            ["python3", str(repo / "scripts" / "test"), "--jobs", "1", *args],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=merged,
        )

    def test_default_success_is_compact(self):
        repo = self.make_repo()
        run = self.run_runner(repo)
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn("== demo ==", run.stdout)
        self.assertIn("✓ 1 test passed", run.stdout)
        self.assertIn("✓ All 1 tool suite passed (1 test)", run.stdout)
        self.assertNotIn("test_demo (test_demo.DemoTest.test_demo)", run.stdout)
        self.assertNotIn("+ python3 -m unittest", run.stdout)

    def test_failure_expands_captured_output(self):
        repo = self.make_repo(failing=True)
        run = self.run_runner(repo)
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn("✗ tests failed", run.stdout)
        self.assertIn("test_demo", run.stdout)
        self.assertIn("AssertionError", run.stdout)
        self.assertIn("+ python3 -m unittest", run.stdout)

    def test_verbose_shows_command_and_success_output(self):
        repo = self.make_repo()
        run = self.run_runner(repo, "--verbose")
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn("+ python3 -m unittest", run.stdout)
        self.assertIn("test_demo", run.stdout)
        self.assertIn("✓ 1 test passed", run.stdout)

    def test_quiet_is_one_line_per_suite(self):
        repo = self.make_repo()
        run = self.run_runner(repo, "--quiet")
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertEqual(run.stdout.strip(), "✓ demo")

    def test_environment_modes_are_supported(self):
        repo = self.make_repo()
        verbose = self.run_runner(repo, env={"VERBOSE": "1"})
        self.assertEqual(verbose.returncode, 0, verbose.stdout)
        self.assertIn("+ python3 -m unittest", verbose.stdout)

        quiet = self.run_runner(repo, env={"QUIET": "1"})
        self.assertEqual(quiet.returncode, 0, quiet.stdout)
        self.assertEqual(quiet.stdout.strip(), "✓ demo")

    def test_verbose_and_quiet_conflict_fails_closed(self):
        repo = self.make_repo()
        run = self.run_runner(repo, env={"VERBOSE": "1", "QUIET": "1"})
        self.assertEqual(run.returncode, 2, run.stdout)
        self.assertIn("mutually exclusive", run.stdout)


if __name__ == "__main__":
    unittest.main()
