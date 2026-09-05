from __future__ import annotations
import subprocess, tempfile, unittest, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deploy_pack.core import append_deployment_history, write_baseline
from deploy_pack.keyring import (
    export_bundle,
    trust_recovery_signer,
    revoke_recovery_signer,
    verify_bundle_trusted,
)
from deploy_pack.signed import generate_ephemeral_keypair, write_public_key_file

def git(root,*args):
    return subprocess.check_output(["git",*args],cwd=root,text=True).strip()

class RecoveryTrustTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        git(self.root,"init"); git(self.root,"config","user.email","x@example.com"); git(self.root,"config","user.name","X")
        (self.root/"app.php").write_text("v1\n"); git(self.root,"add","."); git(self.root,"commit","-m","v1")
        sha=git(self.root,"rev-parse","HEAD"); write_baseline(self.root,sha)
        append_deployment_history(self.root,previous_baseline=None,new_baseline_ref="v1",new_baseline_commit=sha,evidence_path=None,evidence=None,archive=None,unsafe=True)
        self.bundle,self.pub=export_bundle(self.root,self.root/"recovery.json")

    def tearDown(self): self.tmp.cleanup()

    def test_untrusted_rejected(self):
        with self.assertRaises(Exception):
            verify_bundle_trusted(self.root,self.bundle,self.pub)

    def test_trusted_accepted(self):
        rec=trust_recovery_signer(self.root,self.pub,activate=True)
        payload=verify_bundle_trusted(self.root,self.bundle,self.pub)
        self.assertTrue(payload["history"]); self.assertEqual(rec["status"],"active")

    def test_revoked_rejected(self):
        rec=trust_recovery_signer(self.root,self.pub,activate=True)
        revoke_recovery_signer(self.root,rec["signerId"],reason="compromise")
        with self.assertRaises(Exception):
            verify_bundle_trusted(self.root,self.bundle,self.pub)

    def test_successor_rotation(self):
        first=trust_recovery_signer(self.root,self.pub,activate=True)
        _priv,raw=generate_ephemeral_keypair()
        pub2=self.root/"second.public-key.json"; write_public_key_file(pub2,raw)
        second=trust_recovery_signer(self.root,pub2,activate=True,predecessor=first["signerId"],reason="rotation")
        self.assertEqual(second["predecessorSignerId"],first["signerId"])

if __name__=="__main__": unittest.main()
