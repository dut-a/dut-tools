from __future__ import annotations
import json, subprocess, tempfile, unittest, zipfile, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from deploy_pack.core import build_plan, write_package
from deploy_pack.signed import write_signed_remote_verifier, ingest_signed_remote_evidence
from deploy_pack.lifecycle import issue

def git(r,*a): return subprocess.check_output(['git',*a],cwd=r,text=True).strip()

class SignedRemoteEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        git(self.root,'init'); git(self.root,'config','user.email','x@example.com'); git(self.root,'config','user.name','X')
        (self.root/'app.php').write_text('v1\n'); git(self.root,'add','.'); git(self.root,'commit','-m','base'); self.base=git(self.root,'rev-parse','HEAD')
        (self.root/'app.php').write_text('v2\n'); git(self.root,'add','.'); git(self.root,'commit','-m','release')
        plan=build_plan(self.root,self.base,committed_only=True); self.archive=self.root/'release.deploy.zip'; write_package(plan,self.archive)
        self.identity=issue(self.root,30)
        self.ext=self.root/'ext'; self.ext.mkdir()
        with zipfile.ZipFile(self.archive) as z:z.extractall(self.ext)
    def tearDown(self): self.tmp.cleanup()
    def test_python_signed_round_trip(self):
        verifier,pub=write_signed_remote_verifier(self.archive,'python',self.identity,root=self.root); evidence=self.root/'python.signed.json'
        p=subprocess.run([sys.executable,str(verifier),str(self.ext),'--signed-evidence-out',str(evidence)],text=True,capture_output=True)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        normalized=ingest_signed_remote_evidence(evidence,self.archive,pub,root=self.root)
        self.assertIn('signedRemoteEvidence',json.loads(normalized.read_text()))
    def test_php_sodium_signed_round_trip(self):
        verifier,pub=write_signed_remote_verifier(self.archive,'php',self.identity,root=self.root); evidence=self.root/'php.signed.json'
        p=subprocess.run(['php',str(verifier),str(self.ext),'--signed-evidence-out',str(evidence)],text=True,capture_output=True)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        normalized=ingest_signed_remote_evidence(evidence,self.archive,pub,root=self.root)
        self.assertIn('signedRemoteEvidence',json.loads(normalized.read_text()))
    def test_tampering_is_rejected(self):
        verifier,pub=write_signed_remote_verifier(self.archive,'php',self.identity,root=self.root); evidence=self.root/'tamper.signed.json'
        p=subprocess.run(['php',str(verifier),str(self.ext),'--signed-evidence-out',str(evidence)],text=True,capture_output=True)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        value=json.loads(evidence.read_text()); value['payload']['verificationRoot']='/evil'; evidence.write_text(json.dumps(value))
        with self.assertRaises(Exception): ingest_signed_remote_evidence(evidence,self.archive,pub,root=self.root)
if __name__=='__main__': unittest.main()
