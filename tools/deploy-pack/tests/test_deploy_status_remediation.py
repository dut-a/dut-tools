from __future__ import annotations
import json, os, subprocess, tempfile, unittest, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deploy_pack.keyring import export_offline_trust_copies, load_recovery_trust, save_recovery_trust

def git(root,*args):
    return subprocess.check_output(["git",*args],cwd=root,text=True).strip()

class DeployStatusRemediationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        git(self.root,"init"); git(self.root,"config","user.email","x@example.com"); git(self.root,"config","user.name","X")
        (self.root/"app.php").write_text("v1\n"); git(self.root,"add","."); git(self.root,"commit","-m","v1")
        sha=git(self.root,"rev-parse","HEAD")
        p=self.run_cli("mark",sha,"--unsafe-no-evidence"); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        now=datetime.now(timezone.utc).isoformat()
        save_recovery_trust(self.root,{"schemaVersion":1,"activeSigner":"a","signers":{"a":{"signerId":"a","status":"active","trustedAt":now,"activatedAt":now}}})
        export_offline_trust_copies(self.root,self.root/"custody.json",copies=3,quorum=2)

    def tearDown(self): self.tmp.cleanup()
    def env(self):
        e=os.environ.copy(); e["PYTHONPATH"]=str(ROOT/"src"); return e
    def run_cli(self,*args):
        return subprocess.run([sys.executable,"-m","deploy_pack.cli",*args],cwd=self.root,env=self.env(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)

    def test_active_signer_change_recommends_exact_refresh_command(self):
        trust=load_recovery_trust(self.root)
        now=datetime.now(timezone.utc).isoformat()
        trust["signers"]["a"]["status"]="retired"; trust["signers"]["a"]["retiredAt"]=now
        trust["signers"]["b"]={"signerId":"b","status":"active","trustedAt":now,"activatedAt":now,"predecessorSignerId":"a"}
        trust["activeSigner"]="b"; save_recovery_trust(self.root,trust)
        p=self.run_cli("deploy","status","--json"); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        data=json.loads(p.stdout); rem=data["offlineCustody"]["remediation"]
        self.assertEqual(rem["priority"],"recommended")
        self.assertEqual(rem["copyCount"],3); self.assertEqual(rem["quorum"],2)
        self.assertEqual(rem["command"],"deploy-pack recovery trust export-copies recovery-trust-refresh.json --copies 3 --quorum 2")
        self.assertIn("active-signer-changed",rem["causeTypes"])

    def test_overdue_stale_marks_remediation_required(self):
        # Immediate failure policy avoids clock-fixture gymnastics.
        (self.root/".deploy-pack.toml").write_text('[deploy-pack]\noffline_custody_stale_grace_days = 0\n')
        trust=load_recovery_trust(self.root)
        now=datetime.now(timezone.utc).isoformat()
        trust["signers"]["backup"]={"signerId":"backup","status":"trusted","trustedAt":now}
        save_recovery_trust(self.root,trust)
        p=self.run_cli("deploy","status","--json")
        self.assertNotEqual(p.returncode,0)
        data=json.loads(p.stdout)
        self.assertEqual(data["offlineCustody"]["remediation"]["priority"],"required")

    def test_current_checkpoint_has_no_remediation(self):
        p=self.run_cli("deploy","status","--json"); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        data=json.loads(p.stdout); self.assertIsNone(data["offlineCustody"]["remediation"])

if __name__=="__main__": unittest.main()
