from __future__ import annotations
import subprocess,tempfile,unittest,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from deploy_pack.keyring import export_offline_trust_copies,load_offline_checkpoints,verify_offline_checkpoint_history,verify_offline_trust_copy_set,save_recovery_trust

def git(root,*args): return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
class T(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); git(self.root,'init'); git(self.root,'config','user.email','x@example.com'); git(self.root,'config','user.name','X'); (self.root/'app.php').write_text('v1\n'); git(self.root,'add','.'); git(self.root,'commit','-m','v1')
        save_recovery_trust(self.root,{"schemaVersion":1,"activeSigner":"primary","signers":{"primary":{"signerId":"primary","status":"active","predecessorSignerId":None}}})
    def tearDown(self): self.tmp.cleanup()
    def test_copies_and_chain(self):
        first=export_offline_trust_copies(self.root,self.root/'a.json',copies=3); self.assertEqual(len({x['publicKeySha256'] for x in first['checkpoint']['copies']}),3)
        verified=verify_offline_trust_copy_set([x['anchor'] for x in first['artifacts']],[x['publicKey'] for x in first['artifacts']],root=self.root); self.assertEqual(verified['independentSigners'],3)
        second=export_offline_trust_copies(self.root,self.root/'b.json',copies=2); self.assertEqual(second['checkpoint']['previousCheckpointHash'],first['checkpoint']['checkpointHash']); self.assertEqual(second['checkpoint']['sequence'],2)
        ok,errors=verify_offline_checkpoint_history(self.root); self.assertTrue(ok,errors); self.assertEqual(len(load_offline_checkpoints(self.root)),2)
if __name__=='__main__': unittest.main()
