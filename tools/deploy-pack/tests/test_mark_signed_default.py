from __future__ import annotations
import json, subprocess, tempfile, unittest, sys, os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from deploy_pack.core import build_plan, ingest_remote_evidence, manifest_sha256, verify_archive, write_package
from deploy_pack.signed import generate_ephemeral_keypair, write_public_key_file, ingest_signed_remote_evidence, canonical_bytes
from deploy_pack.lifecycle import issue, precommit_signing_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import base64, hashlib

def git(root,*args): return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
class T(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory(); self.r=Path(self.t.name)
  git(self.r,'init'); git(self.r,'config','user.email','x@example.com'); git(self.r,'config','user.name','X')
  (self.r/'app.php').write_text('v1\n'); git(self.r,'add','.'); git(self.r,'commit','-m','base'); self.base=git(self.r,'rev-parse','HEAD')
  (self.r/'app.php').write_text('v2\n'); git(self.r,'add','.'); git(self.r,'commit','-m','release')
  plan=build_plan(self.r,self.base,committed_only=True); self.a=self.r/'r.deploy.zip'; write_package(plan,self.a); m=verify_archive(self.a).manifest
  self.identity=issue(self.r,30)
  self.payload={'schemaVersion':1,'verifierIdentity':self.identity,'result':'PASS','verificationScope':'remote','verificationMethod':'ssh-cli','verificationRoot':'/srv/app','strictPermissions':False,'verifierRuntime':'python','verifiedAt':self.identity['issuedAt'],'manifest':{'sha256':manifest_sha256(m),'schemaVersion':m['schemaVersion'],'baselineRef':m['baselineRef'],'baselineCommit':m['baselineCommit'],'headCommit':m['headCommit'],'fileCount':len(m['files']),'remoteDeletionCount':len(m['remoteDeletions'])}}
 def tearDown(self): self.t.cleanup()
 def env(self):
  e=os.environ.copy(); e['PYTHONPATH']=str(ROOT/'src'); return e
 def bootstrap(self):
  p=subprocess.run([sys.executable,'-m','deploy_pack.cli','mark',self.base,'--unsafe-no-evidence'],cwd=self.r,env=self.env(),text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT); self.assertEqual(p.returncode,0,p.stdout)
 def test_unsigned_default_reject_and_override(self):
  self.bootstrap(); raw=self.r/'remote.json'; raw.write_text(json.dumps(self.payload)); n=ingest_remote_evidence(raw,self.a)
  p=subprocess.run([sys.executable,'-m','deploy_pack.cli','mark','HEAD','--evidence',str(n),'--archive',str(self.a)],cwd=self.r,env=self.env(),text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT); self.assertNotEqual(p.returncode,0); self.assertIn('signed remote evidence is required',p.stdout)
  p=subprocess.run([sys.executable,'-m','deploy_pack.cli','mark','HEAD','--evidence',str(n),'--archive',str(self.a),'--allow-unsigned-evidence'],cwd=self.r,env=self.env(),text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT); self.assertEqual(p.returncode,0,p.stdout); self.assertIn('legacy-unsigned-compatibility',p.stdout)
  hist=[json.loads(x) for x in (self.r/'.deploy-pack-history.jsonl').read_text().splitlines() if x.strip()]; self.assertEqual(hist[-1]['evidence']['trustMode'],'legacy-unsigned-compatibility')
 def test_signed_default_accept(self):
  self.bootstrap(); seed,pub=generate_ephemeral_keypair(); key=self.r/'pub.json'; write_public_key_file(key,pub)
  self.identity=precommit_signing_key(self.r,self.identity['verifierId'],hashlib.sha256(pub).hexdigest()); self.payload['verifierIdentity']=self.identity
  msg=canonical_bytes(self.payload); sig=Ed25519PrivateKey.from_private_bytes(seed).sign(msg)
  env={'schemaVersion':1,'kind':'deploy-pack.remote-evidence.signed','payload':self.payload,'signature':{'algorithm':'Ed25519','payloadSha256':hashlib.sha256(msg).hexdigest(),'signatureBase64':base64.b64encode(sig).decode()},'signer':{'publicKeyBase64':base64.b64encode(pub).decode(),'publicKeySha256':hashlib.sha256(pub).hexdigest()}}
  sp=self.r/'signed.json'; sp.write_text(json.dumps(env)); n=ingest_signed_remote_evidence(sp,self.a,key,root=self.r)
  p=subprocess.run([sys.executable,'-m','deploy_pack.cli','mark','HEAD','--evidence',str(n),'--archive',str(self.a)],cwd=self.r,env=self.env(),text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT); self.assertEqual(p.returncode,0,p.stdout); self.assertIn('signed-remote',p.stdout)
  hist=[json.loads(x) for x in (self.r/'.deploy-pack-history.jsonl').read_text().splitlines() if x.strip()]; self.assertEqual(hist[-1]['evidence']['trustMode'],'signed-remote')
if __name__=='__main__': unittest.main()
