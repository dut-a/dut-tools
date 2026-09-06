from __future__ import annotations
import os, shutil, subprocess, tempfile, textwrap, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; RUNNER = ROOT / "scripts" / "test"
class TestRunnerPresentationTests(unittest.TestCase):
    def make_repo(self, failing=False):
        temp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, temp, ignore_errors=True); (temp / "scripts").mkdir(); (temp / "tools/demo/tests").mkdir(parents=True); shutil.copy2(RUNNER, temp / "scripts/test")
        (temp / "tools.toml").write_text('[tools.demo]\ncommand="demo"\npath="tools/demo"\nlanguage="python"\nstatus="active"\nversion_file="tools/demo/VERSION"\nentrypoint="bin/demo"\ntest_command=["python3","-m","unittest","discover","-s","tests","-p","test_*.py","-v"]\n')
        first = "self.fail('boom')" if failing else "self.assertEqual(4, 2 + 2)"
        (temp / "tools/demo/tests/test_demo.py").write_text(textwrap.dedent(f'''import unittest
class DemoTest(unittest.TestCase):
    def test_alpha(self): {first}
    def test_beta(self): self.assertTrue(True)
'''))
        return temp
    def run_runner(self, repo, *args, env=None):
        merged = os.environ.copy(); merged["NO_COLOR"]="1"; merged.update(env or {})
        return subprocess.run(["python3", str(repo / "scripts/test"), "--jobs", "1", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=merged)
    def test_default_lists_each_test(self):
        r=self.run_runner(self.make_repo()); self.assertEqual(r.returncode,0,r.stdout); self.assertIn("✓ test_alpha",r.stdout); self.assertIn("✓ test_beta",r.stdout); self.assertIn("[DemoTest]",r.stdout); self.assertIn("✓ 2 tests passed",r.stdout); self.assertNotIn("test_alpha (test_demo.DemoTest.test_alpha) ... ok",r.stdout)
    def test_failure_expands_raw_output(self):
        r=self.run_runner(self.make_repo(True)); self.assertEqual(r.returncode,1,r.stdout); self.assertIn("AssertionError",r.stdout); self.assertIn("+ python3 -m unittest",r.stdout)
    def test_verbose_is_raw(self):
        r=self.run_runner(self.make_repo(),"--verbose"); self.assertEqual(r.returncode,0,r.stdout); self.assertIn("test_alpha (test_demo.DemoTest.test_alpha) ... ok",r.stdout)
    def test_quiet_is_suite_only(self):
        r=self.run_runner(self.make_repo(),"--quiet"); self.assertEqual(r.stdout.strip(),"✓ demo")
    def test_environment_modes(self):
        self.assertIn("+ python3 -m unittest", self.run_runner(self.make_repo(),env={"VERBOSE":"1"}).stdout); self.assertEqual(self.run_runner(self.make_repo(),env={"QUIET":"1"}).stdout.strip(),"✓ demo")
    def test_mode_conflict_fails(self):
        r=self.run_runner(self.make_repo(),env={"VERBOSE":"1","QUIET":"1"}); self.assertEqual(r.returncode,2); self.assertIn("mutually exclusive",r.stdout)
if __name__ == "__main__": unittest.main()
