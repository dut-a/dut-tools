from __future__ import annotations
import json, subprocess, tempfile, unittest, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

from deploy_pack.keyring import (
    export_offline_trust_anchor,
    trust_recovery_signer,
    revoke_recovery_signer,
    verify_offline_trust_anchor,
)
from deploy_pack.signed import generate_ephemeral_keypair, write_public_key_file


def git(root,*args):
    return subprocess.check_output(['git',*args],cwd=root,text=True).strip()


class OfflineTrustAnchorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        git(self.root,'init'); git(self.root,'config','user.email','x@example.com'); git(self.root,'config','user.name','X')
        (self.root/'app.php').write_text('v1\n'); git(self.root,'add','.'); git(self.root,'commit','-m','v1')
        _p1,raw1=generate_ephemeral_keypair(); self.pub1=self.root/'p1.json'; write_public_key_file(self.pub1,raw1)
        first=trust_recovery_signer(self.root,self.pub1,activate=True,reason='initial')
        _p2,raw2=generate_ephemeral_keypair(); self.pub2=self.root/'p2.json'; write_public_key_file(self.pub2,raw2)
        second=trust_recovery_signer(self.root,self.pub2,activate=True,predecessor=first['signerId'],reason='rotate')
        revoke_recovery_signer(self.root,first['signerId'],reason='old compromised')
        self.second=second

    def tearDown(self): self.tmp.cleanup()

    def test_export_verify_round_trip(self):
        anchor,pub,fpfile=export_offline_trust_anchor(self.root,self.root/'anchor.json')
        payload=verify_offline_trust_anchor(anchor,pub)
        self.assertEqual(payload['activeSigner'],self.second['signerId'])
        self.assertEqual(payload['signerCounts']['active'],1)
        self.assertEqual(payload['signerCounts']['revoked'],1)
        self.assertTrue(fpfile.exists())

    def test_tampering_is_rejected(self):
        anchor,pub,_=export_offline_trust_anchor(self.root,self.root/'anchor.json')
        value=json.loads(anchor.read_text())
        value['payload']['activeSigner']='tampered'
        anchor.write_text(json.dumps(value))
        with self.assertRaises(Exception): verify_offline_trust_anchor(anchor,pub)

    def test_expected_fingerprint_is_enforced(self):
        anchor,pub,_=export_offline_trust_anchor(self.root,self.root/'anchor.json')
        with self.assertRaises(Exception):
            verify_offline_trust_anchor(anchor,pub,expected_fingerprint='0'*64)

if __name__=='__main__': unittest.main()
