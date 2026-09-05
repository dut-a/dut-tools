from __future__ import annotations
import json, os, subprocess, tempfile, unittest, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from deploy_pack.keyring import register,load,revoke,export_bundle,import_bundle
from deploy_pack.lifecycle import issue,get,precommit_signing_key
from deploy_pack.signed import generate_ephemeral_keypair,write_public_key_file

def git(root,*args): return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
class T(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); git(self.root,'init'); git(self.root,'config','user.email','x@example.com'); git(self.root,'config','user.name','X'); (self.root/'a').write_text('x'); git(self.root,'add','.'); git(self.root,'commit','-m','x')
 def tearDown(self): self.tmp.cleanup()
 def keyfile(self,verifier_id,name='k.json'):
  _,pub=generate_ephemeral_keypair(); p=self.root/name; write_public_key_file(p,pub); precommit_signing_key(self.root,verifier_id,__import__('hashlib').sha256(pub).hexdigest()); return p
 def test_rotation_is_new_ephemeral_signer_history(self):
  v1=issue(self.root); r1=register(self.root,v1['verifierId'],self.keyfile(v1['verifierId'],'k1.json')); v2=issue(self.root); r2=register(self.root,v2['verifierId'],self.keyfile(v2['verifierId'],'k2.json')); self.assertNotEqual(r1['publicKeySha256'],r2['publicKeySha256']); self.assertEqual(len(load(self.root)['signers']),2)
 def test_compromise_revokes_bound_verifier(self):
  v=issue(self.root); rec=register(self.root,v['verifierId'],self.keyfile(v['verifierId'])); revoke(self.root,rec['publicKeySha256'],reason='test'); self.assertEqual(get(self.root,v['verifierId'])['status'],'revoked')
 def test_recovery_has_public_trust_no_private_key(self):
  v=issue(self.root); register(self.root,v['verifierId'],self.keyfile(v['verifierId'])); b=self.root/'recovery.json'; b,pub=export_bundle(self.root,b); text=b.read_text(); self.assertNotIn('privateKey',text); self.assertNotIn('PRIVATE KEY',text)
  with tempfile.TemporaryDirectory() as td:
   rr=Path(td); git(rr,'init'); result=import_bundle(rr,b,public_key_file=pub); self.assertGreaterEqual(result['signers'],1); self.assertEqual(len(load(rr)['signers']),1)
if __name__=='__main__': unittest.main()
