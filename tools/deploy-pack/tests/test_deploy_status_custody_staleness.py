from __future__ import annotations
import json, os, subprocess, tempfile, unittest, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from deploy_pack.keyring import export_offline_trust_copies, load_recovery_trust, save_recovery_trust

def git(root,*args): return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
class T(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        git(self.root,'init'); git(self.root,'config','user.email','x@example.com'); git(self.root,'config','user.name','X')
        (self.root/'app.php').write_text('v1\n'); git(self.root,'add','.'); git(self.root,'commit','-m','v1')
        sha=git(self.root,'rev-parse','HEAD'); q=self.cli('mark',sha,'--unsafe-no-evidence'); self.assertEqual(q.returncode,0,q.stdout+q.stderr)
        save_recovery_trust(self.root,{'schemaVersion':1,'activeSigner':'primary','signers':{'primary':{'signerId':'primary','status':'active','predecessorSignerId':None}}})
    def tearDown(self): self.tmp.cleanup()
    def env(self):
        e=os.environ.copy(); e['PYTHONPATH']=str(ROOT/'src'); return e
    def cli(self,*args): return subprocess.run([sys.executable,'-m','deploy_pack.cli',*args],cwd=self.root,env=self.env(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    def test_unconfigured(self):
        q=self.cli('deploy','status','--json'); self.assertEqual(q.returncode,0,q.stdout+q.stderr); d=json.loads(q.stdout); self.assertEqual(d['offlineCustody']['freshness'],'UNCONFIGURED'); self.assertFalse(d['offlineCustody']['stale'])
    def test_current(self):
        export_offline_trust_copies(self.root,self.root/'c.json',copies=3,quorum=2)
        q=self.cli('deploy','status','--json'); self.assertEqual(q.returncode,0,q.stdout+q.stderr); d=json.loads(q.stdout); o=d['offlineCustody']; self.assertEqual(o['freshness'],'CURRENT'); self.assertFalse(o['stale']); self.assertEqual(o['recoveryTrustSha256'],o['currentRecoveryTrustSha256'])
    def test_stale_warning_quiet_still_zero(self):
        export_offline_trust_copies(self.root,self.root/'c.json',copies=3,quorum=2)
        t=load_recovery_trust(self.root); t['signers']['backup']={'signerId':'backup','status':'trusted','predecessorSignerId':None}; save_recovery_trust(self.root,t)
        q=self.cli('deploy','status','--json'); self.assertEqual(q.returncode,0,q.stdout+q.stderr); d=json.loads(q.stdout); self.assertTrue(d['offlineCustody']['stale']); self.assertEqual(d['offlineCustody']['freshness'],'STALE_GRACE'); self.assertTrue(d['warnings'])
        z=self.cli('deploy','status','--quiet'); self.assertEqual(z.returncode,0); self.assertEqual(z.stdout,''); self.assertEqual(z.stderr,'')
    def test_recheckpoint_current(self):
        export_offline_trust_copies(self.root,self.root/'c1.json',copies=3,quorum=2)
        t=load_recovery_trust(self.root); t['signers']['backup']={'signerId':'backup','status':'trusted','predecessorSignerId':None}; save_recovery_trust(self.root,t)
        export_offline_trust_copies(self.root,self.root/'c2.json',copies=3,quorum=2)
        q=self.cli('deploy','status','--json'); d=json.loads(q.stdout); self.assertEqual(d['offlineCustody']['freshness'],'CURRENT')
if __name__=='__main__': unittest.main()
