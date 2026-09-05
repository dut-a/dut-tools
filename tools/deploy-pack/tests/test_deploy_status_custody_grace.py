from __future__ import annotations
import json, os, subprocess, tempfile, unittest, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from deploy_pack.keyring import export_offline_trust_copies, load_recovery_trust, save_recovery_trust

def git(root,*args):
    return subprocess.check_output(['git',*args],cwd=root,text=True).strip()

class CustodyGraceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        git(self.root,'init'); git(self.root,'config','user.email','x@example.com'); git(self.root,'config','user.name','X')
        (self.root/'app.php').write_text('v1\n'); git(self.root,'add','.'); git(self.root,'commit','-m','v1')
        sha=git(self.root,'rev-parse','HEAD')
        p=self.run_cli('mark',sha,'--unsafe-no-evidence'); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        now=datetime.now(timezone.utc)
        save_recovery_trust(self.root,{'schemaVersion':1,'activeSigner':'primary','signers':{'primary':{'signerId':'primary','status':'active','trustedAt':now.isoformat(),'activatedAt':now.isoformat(),'predecessorSignerId':None}}})
        export_offline_trust_copies(self.root,self.root/'custody.json',copies=3,quorum=2)

    def tearDown(self): self.tmp.cleanup()
    def env(self):
        e=os.environ.copy(); e['PYTHONPATH']=str(ROOT/'src'); return e
    def run_cli(self,*args):
        return subprocess.run([sys.executable,'-m','deploy_pack.cli',*args],cwd=self.root,env=self.env(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    def make_stale(self, days_ago: int, checkpoint_days_ago: int = 20):
        checkpoint_path=self.root/'.deploy-pack-offline-checkpoints.jsonl'
        records=[json.loads(line) for line in checkpoint_path.read_text().splitlines() if line.strip()]
        latest=records[-1]
        latest['createdAt']=(datetime.now(timezone.utc)-timedelta(days=checkpoint_days_ago)).isoformat()
        body={k:v for k,v in latest.items() if k!='checkpointHash'}
        import hashlib
        latest['checkpointHash']=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        checkpoint_path.write_text(json.dumps(latest,sort_keys=True,separators=(',',':'))+'\n')
        trust=load_recovery_trust(self.root)
        old_primary=datetime.now(timezone.utc)-timedelta(days=checkpoint_days_ago+5)
        trust['signers']['primary']['trustedAt']=old_primary.isoformat()
        trust['signers']['primary']['activatedAt']=old_primary.isoformat()
        t=datetime.now(timezone.utc)-timedelta(days=days_ago)
        trust['signers']['backup']={'signerId':'backup','status':'trusted','trustedAt':t.isoformat(),'predecessorSignerId':None}
        save_recovery_trust(self.root,trust)

    def test_within_default_grace_warns_but_quiet_passes(self):
        self.make_stale(2, checkpoint_days_ago=5)
        p=self.run_cli('deploy','status','--json'); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        d=json.loads(p.stdout); oc=d['offlineCustody']
        self.assertEqual(oc['freshness'],'STALE_GRACE'); self.assertFalse(oc['staleOverdue']); self.assertEqual(oc['staleGraceDays'],7)
        q=self.run_cli('deploy','status','--quiet'); self.assertEqual(q.returncode,0); self.assertEqual(q.stdout,''); self.assertEqual(q.stderr,'')

    def test_past_default_grace_fails_quiet(self):
        self.make_stale(10, checkpoint_days_ago=20)
        p=self.run_cli('deploy','status','--json'); self.assertNotEqual(p.returncode,0)
        d=json.loads(p.stdout); oc=d['offlineCustody']
        self.assertEqual(oc['freshness'],'STALE_OVERDUE'); self.assertTrue(oc['staleOverdue'])
        self.assertTrue(any('exceeded configured grace period' in x for x in d['problems']))
        q=self.run_cli('deploy','status','--quiet'); self.assertNotEqual(q.returncode,0); self.assertEqual(q.stdout,''); self.assertEqual(q.stderr,'')

    def test_config_override_extends_grace(self):
        (self.root/'.deploy-pack.toml').write_text('[deploy-pack]\noffline_custody_stale_grace_days = 14\n')
        self.make_stale(10, checkpoint_days_ago=20)
        p=self.run_cli('deploy','status','--json'); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        d=json.loads(p.stdout); self.assertEqual(d['offlineCustody']['staleGraceDays'],14); self.assertEqual(d['offlineCustody']['freshness'],'STALE_GRACE')

    def test_zero_grace_fails_any_detectable_stale(self):
        (self.root/'.deploy-pack.toml').write_text('[deploy-pack]\noffline_custody_stale_grace_days = 0\n')
        self.make_stale(0, checkpoint_days_ago=1)
        p=self.run_cli('deploy','status','--json'); self.assertNotEqual(p.returncode,0)
        self.assertEqual(json.loads(p.stdout)['offlineCustody']['freshness'],'STALE_OVERDUE')

if __name__=='__main__': unittest.main()
