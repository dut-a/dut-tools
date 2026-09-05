from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "repo_verify.py"
spec = importlib.util.spec_from_file_location("repo_verify", MODULE)
rv = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rv
assert spec.loader
spec.loader.exec_module(rv)

class ConfigTest(unittest.TestCase):
    def test_shell_string_rejected_for_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = root / ".repo-verify.toml"
            cfg.write_text("""
version = 1
[[check]]
name = "bad"
type = "command"
argv = "echo hi"
""")
            with self.assertRaises(rv.ConfigError):
                rv.load_config(cfg)

    def test_invalid_regex_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            cfg=root/".repo-verify.toml"
            cfg.write_text("""
version = 1
[[check]]
name = "regex"
type = "matches"
path = "VERSION"
regex = "["
""")
            with self.assertRaises(rv.ConfigError):
                rv.load_config(cfg)

class BuiltinChecksTest(unittest.TestCase):
    def spec(self, kind, **kwargs):
        return rv.CheckSpec(
            index=0,
            name="x",
            type=kind,
            severity="error",
            **kwargs
        )

    def test_file_directory_absent(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            (root/"README.md").write_text("hello")
            (root/"docs").mkdir()
            self.assertEqual("pass",rv.run_check(root,self.spec("file_exists",path="README.md")).status)
            self.assertEqual("pass",rv.run_check(root,self.spec("directory_exists",path="docs")).status)
            self.assertEqual("pass",rv.run_check(root,self.spec("path_absent",path=".env")).status)

    def test_contains_matches(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            (root/"VERSION").write_text("1.2.3\n")
            self.assertEqual("pass",rv.run_check(root,self.spec("contains",path="VERSION",text="1.2")).status)
            self.assertEqual("pass",rv.run_check(root,self.spec("matches",path="VERSION",regex=r"^\d+\.\d+\.\d+$")).status)

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            with self.assertRaises(rv.ConfigError):
                rv.safe_path(root,"../outside")

class CommandCheckTest(unittest.TestCase):
    def test_command_success_and_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            ok=rv.CheckSpec(0,"ok","command","error",argv=["python3","-c","print('ok')"])
            bad=rv.CheckSpec(1,"bad","command","error",argv=["python3","-c","import sys;sys.exit(7)"])
            rok=rv.run_check(root,ok)
            rbad=rv.run_check(root,bad)
            self.assertEqual("pass",rok.status)
            self.assertEqual(0,rok.exit_code)
            self.assertEqual("fail",rbad.status)
            self.assertEqual(7,rbad.exit_code)

class CliTest(unittest.TestCase):
    def run_cli(self,*args):
        return subprocess.run(
            ["python3",str(MODULE),*args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_help_version(self):
        cp=self.run_cli("--version")
        self.assertEqual(0,cp.returncode)
        self.assertIn("repo-verify 1.0.0",cp.stdout)
        cp=self.run_cli("--help")
        self.assertEqual(0,cp.returncode)
        for flag in ("--config","--fail-fast","--format"):
            self.assertIn(flag,cp.stdout)

    def test_warning_does_not_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/".repo-verify.toml").write_text("""
version = 1
[[check]]
name = "optional"
type = "file_exists"
path = "missing.txt"
severity = "warning"
""")
            cp=self.run_cli(str(root))
            self.assertEqual(0,cp.returncode,cp.stderr)
            self.assertIn("WARN",cp.stdout)

    def test_error_fails_and_json_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/".repo-verify.toml").write_text("""
version = 1
[[check]]
name = "required"
type = "file_exists"
path = "missing.txt"
""")
            cp=self.run_cli(str(root),"--format","json")
            self.assertEqual(1,cp.returncode,cp.stderr)
            data=json.loads(cp.stdout)
            self.assertEqual(1,data["summary"]["errors"])

if __name__=="__main__":
    unittest.main()
