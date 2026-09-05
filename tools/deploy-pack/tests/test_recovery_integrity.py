from __future__ import annotations
import json, subprocess, tempfile, unittest, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from deploy_pack.core import append_deployment_history, write_baseline
from deploy_pack.keyring import export_bundle, verify_bundle, import_bundle

def git(root,*args): return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
class T(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); git(self.root,'init'); git(self.root,'config','user.email','x@example.com'); git(self.root,'config','user.name','X'); (self.root/'a.php').write_text('v1\n'); git(self.root,'add','.'); git(self.root,'commit','-m','v1'); self.sha=git(self.root,'rev-parse','HEAD'); write_baseline(self.root,self.sha); append_deployment_history(self.root,previous_baseline=None,new_baseline_ref='v1',new_baseline_commit=self.sha,evidence_path=None,evidence=None,archive=None,unsafe=True)
 def tearDown(self): self.tmp.cleanup()
 def test_signed_roundtrip(self):
  b,p=export_bundle(self.root,self.root/'r.json'); payload=verify_bundle(b,p); self.assertEqual(payload['baseline'],self.sha); self.assertEqual(len(payload['historyChain']),1)
 def test_tamper_rejected(self):
  b,p=export_bundle(self.root,self.root/'r.json'); env=json.loads(b.read_text()); env['payload']['history'][0]['newBaselineCommit']='0'*40; b.write_text(json.dumps(env));
  with self.assertRaises(Exception): verify_bundle(b,p)
 def test_verified_import(self):
  b,p=export_bundle(self.root,self.root/'r.json')
  with tempfile.TemporaryDirectory() as td:
   dst=Path(td); git(dst,'init'); result=import_bundle(dst,b,public_key_file=p); self.assertEqual(result['history'],1)
if __name__=='__main__': unittest.main()
