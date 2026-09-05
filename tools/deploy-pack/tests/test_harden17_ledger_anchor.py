from __future__ import annotations
import json, subprocess, tempfile, unittest, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from deploy_pack.core import (
    append_deployment_history, read_deployment_history, verify_deployment_history,
    deployment_history_chain_state, deployment_status, write_baseline,
    _deployment_record_hash, LEDGER_ZERO_HASH,
)
from deploy_pack.keyring import export_offline_trust_copies, save_recovery_trust


def git(root,*args): return subprocess.check_output(['git',*args],cwd=root,text=True).strip()

class Harden17LedgerAnchorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        git(self.root,'init'); git(self.root,'config','user.email','x@example.com'); git(self.root,'config','user.name','X')
        (self.root/'app.php').write_text('v1\n'); git(self.root,'add','.'); git(self.root,'commit','-m','v1'); self.sha1=git(self.root,'rev-parse','HEAD')
        save_recovery_trust(self.root,{"schemaVersion":1,"activeSigner":"primary","signers":{"primary":{"signerId":"primary","status":"active","predecessorSignerId":None}}})
    def tearDown(self): self.tmp.cleanup()
    def add_record(self, ref, sha, prev=None):
        write_baseline(self.root, ref)
        return append_deployment_history(self.root,previous_baseline=prev,new_baseline_ref=ref,new_baseline_commit=sha,evidence_path=None,evidence=None,archive=None,unsafe=True)
    def commit2(self):
        (self.root/'app.php').write_text('v2\n'); git(self.root,'add','.'); git(self.root,'commit','-m','v2'); return git(self.root,'rev-parse','HEAD')
    def test_native_chain_is_written_and_verified(self):
        r1=self.add_record(self.sha1,self.sha1)
        sha2=self.commit2(); r2=self.add_record(sha2,sha2,self.sha1)
        self.assertEqual(r1['previousRecordHash'], LEDGER_ZERO_HASH)
        self.assertEqual(r2['previousRecordHash'], r1['recordHash'])
        state=deployment_history_chain_state(read_deployment_history(self.root))
        self.assertEqual(state['mode'],'native'); self.assertFalse(state['errors'])
        ok,errors=verify_deployment_history(self.root); self.assertTrue(ok,errors)
    def test_hash_tamper_is_detected(self):
        self.add_record(self.sha1,self.sha1)
        path=self.root/'.deploy-pack-history.jsonl'; rec=json.loads(path.read_text().strip()); rec['newBaselineRef']='evil'
        path.write_text(json.dumps(rec,sort_keys=True,separators=(',',':'))+'\n')
        ok,errors=verify_deployment_history(self.root); self.assertFalse(ok); self.assertTrue(any('recordHash mismatch' in x for x in errors),errors)
    def test_legacy_ledger_is_upgraded_before_next_append(self):
        legacy={"schemaVersion":1,"recordNumber":1,"recordedAt":"2026-01-01T00:00:00+00:00","deploymentKind":"forward","previousBaseline":None,"newBaselineRef":self.sha1,"newBaselineCommit":self.sha1,"unsafeNoEvidence":True,"evidence":None,"archive":None,"rollback":None}
        (self.root/'.deploy-pack-history.jsonl').write_text(json.dumps(legacy)+'\n'); write_baseline(self.root,self.sha1)
        sha2=self.commit2(); self.add_record(sha2,sha2,self.sha1)
        records=read_deployment_history(self.root); self.assertEqual(len(records),2)
        self.assertTrue(all(r.get('recordHash') for r in records)); self.assertEqual(records[1]['previousRecordHash'],records[0]['recordHash'])
    def test_checkpoint_anchors_ledger_and_later_append_is_advanced(self):
        self.add_record(self.sha1,self.sha1)
        exported=export_offline_trust_copies(self.root,self.root/'custody.json',copies=3,quorum=2)
        anchor=exported['checkpoint']['deploymentLedger']
        self.assertEqual(anchor['recordCount'],1); self.assertEqual(anchor['headRecordHash'],read_deployment_history(self.root)[0]['recordHash'])
        envelope=json.loads(exported['artifacts'][0]['anchor'].read_text())
        self.assertEqual(envelope['payload']['checkpoint']['deploymentLedger'], anchor)
        status=deployment_status(self.root); self.assertEqual(status['history']['offlineAnchor']['status'],'CURRENT'); self.assertEqual(status['health'],'PASS')
        sha2=self.commit2(); self.add_record(sha2,sha2,self.sha1)
        status=deployment_status(self.root); self.assertEqual(status['history']['offlineAnchor']['status'],'ADVANCED'); self.assertEqual(status['health'],'PASS'); self.assertTrue(any('does not cover the latest' in w for w in status['warnings']))
    def test_wholesale_rehash_cannot_defeat_offline_anchor(self):
        self.add_record(self.sha1,self.sha1)
        export_offline_trust_copies(self.root,self.root/'custody.json',copies=3,quorum=2)
        sha2=self.commit2(); self.add_record(sha2,sha2,self.sha1)
        records=read_deployment_history(self.root)
        records[0]['newBaselineRef']='rewritten-history'
        previous=LEDGER_ZERO_HASH
        for rec in records:
            rec['previousRecordHash']=previous; rec['recordHash']=_deployment_record_hash(rec); previous=rec['recordHash']
        (self.root/'.deploy-pack-history.jsonl').write_text(''.join(json.dumps(r,sort_keys=True,separators=(',',':'))+'\n' for r in records))
        ok,errors=verify_deployment_history(self.root); self.assertTrue(ok,errors)
        status=deployment_status(self.root)
        self.assertEqual(status['history']['offlineAnchor']['status'],'MISMATCH'); self.assertEqual(status['health'],'FAIL')
        self.assertTrue(any('anchored history prefix' in p for p in status['problems']),status['problems'])

if __name__=='__main__': unittest.main()
