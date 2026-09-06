from __future__ import annotations
import os, subprocess, sys, unittest
from pathlib import Path
TOOL_ROOT=Path(__file__).resolve().parents[1]; SRC_ROOT=TOOL_ROOT/"src"
class CliHelpUx01Tests(unittest.TestCase):
    def run_help(self,*args):
        env=os.environ.copy(); old=env.get("PYTHONPATH"); env["PYTHONPATH"]=str(SRC_ROOT)+(os.pathsep+old if old else "")
        return subprocess.run([sys.executable,"-m","deploy_pack.cli",*args,"--help"],cwd=TOOL_ROOT,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    def test_top_level_help_is_expanded(self):
        r=self.run_help(); self.assertEqual(r.returncode,0,r.stdout)
        for x in ["Git-aware change-set packaging","Git-independent packaging","COMMAND GROUPS","Packaging","Verification","History and rollback","Trust and recovery","deploy-pack artifact --source dist","deploy-pack <command> --help"]: self.assertIn(x,r.stdout)
    def test_artifact_help_documents_fidelity_and_safety(self):
        r=self.run_help("artifact"); self.assertEqual(r.returncode,0,r.stdout)
        for x in ["source directory is authoritative","directly at archive root","--require","TAR.GZ","Output inside --source is rejected","does not consult Git state","build/shared-hosting"]: self.assertIn(x,r.stdout)
if __name__=="__main__": unittest.main()
