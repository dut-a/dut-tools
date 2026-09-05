from __future__ import annotations
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
from shared.git_context_model import load_git_context, resolve_profile_for_path

GPA=ROOT/"tools/git-provenance-audit/src/git_provenance_audit.py"
spec=importlib.util.spec_from_file_location("gpa",GPA)
gpa=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=gpa
assert spec.loader
spec.loader.exec_module(gpa)

class Parity(unittest.TestCase):
    def test_context_and_pin_parity(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            cfg=base/"contexts.toml"
            cfg.write_text(f"""
version=1
[profiles.personal]
name="Personal"
email="personal@example.com"
[profiles.company]
name="Company"
email="company@example.com"
[[contexts]]
name="broad"
root="{(base/'work').as_posix()}"
profile="personal"
[[contexts]]
name="company"
root="{(base/'work'/'company').as_posix()}"
profile="company"
""")
            model=load_git_context(cfg)
            repo=base/"work"/"company"/"repo"
            repo.mkdir(parents=True)
            subprocess.run(["git","init","-q"],cwd=repo,check=True)

            p1,c1=resolve_profile_for_path(model,repo)
            p2,c2=gpa.resolve_profile(model,repo)
            self.assertEqual((p1.key,c1),(p2.key,c2))

            subprocess.run(["git","config","git-context.profile","personal"],cwd=repo,check=True)
            p1,c1=resolve_profile_for_path(model,repo,"personal")
            p2,c2=gpa.resolve_profile(model,repo)
            self.assertEqual((p1.key,c1),(p2.key,c2))

if __name__=="__main__":
    unittest.main()
