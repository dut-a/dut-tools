from __future__ import annotations
import json, subprocess, tempfile, unittest, zipfile, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from deploy_pack.core import (
    DeployPackError, build_plan, ingest_remote_evidence,
    load_verification_evidence, validate_mark_evidence,
    verify_archive, write_package, write_remote_verifier,
)

def git(root,*args):
    return subprocess.check_output(['git',*args],cwd=root,text=True).strip()

class RemoteEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        git(self.root,'init'); git(self.root,'config','user.email','x@example.com'); git(self.root,'config','user.name','X')
        (self.root/'app.php').write_text('v1\n'); git(self.root,'add','.'); git(self.root,'commit','-m','base'); self.base=git(self.root,'rev-parse','HEAD')
        (self.root/'app.php').write_text('v2\n'); git(self.root,'add','.'); git(self.root,'commit','-m','release'); self.head=git(self.root,'rev-parse','HEAD')
        plan=build_plan(self.root,self.base,committed_only=True); self.archive=self.root/'release.deploy.zip'; write_package(plan,self.archive)
        self.extract=self.root/'extract'; self.extract.mkdir()
        with zipfile.ZipFile(self.archive) as z: z.extractall(self.extract)
    def tearDown(self): self.tmp.cleanup()

    def test_python_remote_evidence_round_trip_authorizes_mark(self):
        verifier=write_remote_verifier(self.archive,'python')
        remote=self.root/'remote.json'
        p=subprocess.run([sys.executable,str(verifier),str(self.extract),'--evidence-out',str(remote)],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        self.assertEqual(p.returncode,0,p.stdout); self.assertTrue(remote.exists())
        raw=json.loads(remote.read_text()); self.assertEqual(raw['verificationScope'],'remote'); self.assertEqual(raw['verificationMethod'],'ssh-cli')
        normalized=ingest_remote_evidence(remote,self.archive)
        evidence=load_verification_evidence(normalized)
        self.assertEqual(evidence['verificationScope'],'remote'); self.assertEqual(evidence['manifest']['headCommit'],self.head)
        resolved,_=validate_mark_evidence(self.root,'HEAD',normalized,archive=self.archive)
        self.assertEqual(resolved,self.head)

    def test_wrong_manifest_hash_is_rejected(self):
        manifest=verify_archive(self.archive).manifest
        remote=self.root/'remote.json'
        remote.write_text(json.dumps({'schemaVersion':1,'result':'PASS','verificationScope':'remote','verificationMethod':'browser','verificationRoot':'/x','strictPermissions':False,'verifierRuntime':'php-browser','verifiedAt':'2026-08-28T00:00:00Z','manifest':{'sha256':'0'*64,'schemaVersion':manifest['schemaVersion'],'baselineRef':manifest['baselineRef'],'baselineCommit':manifest['baselineCommit'],'headCommit':manifest['headCommit'],'fileCount':len(manifest['files']),'remoteDeletionCount':len(manifest['remoteDeletions'])}}))
        with self.assertRaises(DeployPackError): ingest_remote_evidence(remote,self.archive)

    def test_browser_verifier_returns_json_evidence_shape(self):
        verifier=write_remote_verifier(self.archive,'php',browser=True,token='secret')
        text=verifier.read_text()
        self.assertIn('application/json',text); self.assertIn("'verificationMethod'=>'browser'",text); self.assertIn("'verificationScope'=>'remote'",text)

if __name__=='__main__': unittest.main()
