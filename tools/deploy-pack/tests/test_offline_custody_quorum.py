from __future__ import annotations
import subprocess,tempfile,unittest,sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from deploy_pack.keyring import export_offline_trust_copies,verify_offline_trust_quorum,load_offline_checkpoints,save_recovery_trust

def git(root,*args): return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
class Q(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory(); self.root=Path(self.t.name)
        git(self.root,'init'); git(self.root,'config','user.email','x@example.com'); git(self.root,'config','user.name','X')
        (self.root/'app.php').write_text('v1\n'); git(self.root,'add','.'); git(self.root,'commit','-m','v1')
        save_recovery_trust(self.root,{'schemaVersion':1,'activeSigner':'primary','signers':{'primary':{'signerId':'primary','status':'active','predecessorSignerId':None}}})
    def tearDown(self): self.t.cleanup()
    def test_default_is_two_of_three(self):
        r=export_offline_trust_copies(self.root,self.root/'a.json',copies=3); self.assertEqual(r['checkpoint']['quorum'],2); self.assertEqual(load_offline_checkpoints(self.root)[0]['quorum'],2)
    def test_two_of_three_passes(self):
        r=export_offline_trust_copies(self.root,self.root/'a.json',copies=3); a=r['artifacts']; out=verify_offline_trust_quorum([a[0]['anchor'],a[2]['anchor']],[a[0]['publicKey'],a[2]['publicKey']],root=self.root); self.assertEqual(out['validAgreeingCopies'],2)
    def test_one_fails(self):
        r=export_offline_trust_copies(self.root,self.root/'a.json',copies=3); a=r['artifacts'][0]
        with self.assertRaises(Exception): verify_offline_trust_quorum([a['anchor']],[a['publicKey']],root=self.root)
    def test_corrupt_one_tolerated(self):
        r=export_offline_trust_copies(self.root,self.root/'a.json',copies=3); a=r['artifacts']; bad=a[1]['anchor']; env=json.loads(bad.read_text()); env['payload']['activeSigner']='tampered'; bad.write_text(json.dumps(env)); out=verify_offline_trust_quorum([x['anchor'] for x in a],[x['publicKey'] for x in a],root=self.root); self.assertEqual(out['validAgreeingCopies'],2); self.assertEqual(len(out['rejectedCopies']),1)
if __name__=='__main__': unittest.main()
