from __future__ import annotations
import json, subprocess, tempfile, unittest, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from deploy_pack.lifecycle import issue,get,revoke,validate,assert_usable,consume,_load,_write,VERIFIER_STATE_FILE
from deploy_pack.core import DeployPackError
class T(unittest.TestCase):
 def setUp(self): self.t=tempfile.TemporaryDirectory(); self.r=Path(self.t.name)
 def tearDown(self): self.t.cleanup()
 def test_issue_validate_revoke(self):
  i=issue(self.r,30); validate(self.r,i); revoke(self.r,i['verifierId'])
  with self.assertRaises(DeployPackError): validate(self.r,i)
 def test_replay_consumption(self):
  i=issue(self.r,30); ev={'signedRemoteEvidence':{'sourceSha256':'a','payloadSha256':'b'},'remoteEvidence':{'verifierIdentity':i,'verifiedAt':i['issuedAt']}}
  k=assert_usable(self.r,ev); consume(self.r,k,evidence_path=self.r/'e.json',marked_ref='x',marked_commit='c')
  with self.assertRaises(DeployPackError): assert_usable(self.r,ev)
 def test_expired_identity_rejected(self):
  i=issue(self.r,30); path=self.r/VERIFIER_STATE_FILE; state=_load(path,{})
  state['verifiers'][i['verifierId']]['expiresAt']='2000-01-01T00:00:00+00:00'; _write(path,state)
  i['expiresAt']='2000-01-01T00:00:00+00:00'
  with self.assertRaises(DeployPackError): validate(self.r,i)
if __name__=='__main__': unittest.main()
