from __future__ import annotations
import importlib.util, json, subprocess, sys, tempfile, unittest
from pathlib import Path

MODULE=Path(__file__).resolve().parents[1]/"src"/"build_diagnostics.py"
spec=importlib.util.spec_from_file_location("build_diagnostics",MODULE)
bd=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=bd
assert spec.loader
spec.loader.exec_module(bd)

class ClassificationTest(unittest.TestCase):
    def test_builtin(self):
        ds=bd.classify(["[WARNING] deprecated API","[ERROR] compilation failure","INFO done"],[])
        self.assertEqual(["warning","error","note"],[d.severity for d in ds])
    def test_custom_precedes_builtin(self):
        import re
        custom=[bd.Classifier("deprecation","warning",re.compile("deprecated"))]
        ds=bd.classify(["WARNING deprecated thing"],custom)
        self.assertEqual("deprecation",ds[0].category)

class PolicyTest(unittest.TestCase):
    def test_warning_budget(self):
        import re
        ds=[bd.Diagnostic("warning","deprecation","x",1),bd.Diagnostic("warning","deprecation","y",2)]
        p=bd.Policy(max_warnings=1,category_budget={"deprecation":1})
        v=bd.policy_violations(ds,p,["x","y"])
        self.assertEqual(2,len(v))

class ConfigTest(unittest.TestCase):
    def test_invalid_regex(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"c.toml"
            p.write_text("""version=1
[[classifier]]
name="x"
severity="warning"
regex="["
""")
            with self.assertRaises(bd.ConfigError): bd.load_config(p)

class CliTest(unittest.TestCase):
    def run_cli(self,*args):
        return subprocess.run(["python3",str(MODULE),*args],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    def test_help_version(self):
        self.assertEqual(0,self.run_cli("--help").returncode)
        cp=self.run_cli("--version")
        self.assertIn("build-diagnostics 1.0.0",cp.stdout)
    def test_success_json(self):
        cp=self.run_cli("--format","json","--","python3","-c","print('[WARNING] hi')")
        self.assertEqual(0,cp.returncode,cp.stderr)
        data=json.loads(cp.stdout)
        self.assertEqual(1,data["summary"]["diagnostics"]["warnings"])
    def test_check_budget_fails_successful_build(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/".build-diagnostics.toml").write_text("""version=1
[budget]
max_warnings=0
""")
            cp=self.run_cli("--cwd",str(root),"--check","--","python3","-c","print('WARNING hi')")
            self.assertEqual(1,cp.returncode)
    def test_build_failure_fails(self):
        cp=self.run_cli("--","python3","-c","import sys;sys.exit(4)")
        self.assertEqual(1,cp.returncode)

if __name__=="__main__":
    unittest.main()
