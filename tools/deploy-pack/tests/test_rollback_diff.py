from __future__ import annotations
import json, subprocess, tempfile, unittest, sys, os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from deploy_pack.core import append_deployment_history, write_baseline

def git(root,*args): return subprocess.check_output(['git',*args],cwd=root,text=True).strip()

class T(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        git(self.root,'init'); git(self.root,'config','user.email','x@example.com'); git(self.root,'config','user.name','X')
        prev=None; self.shas=[]
        for i in range(1,4):
            (self.root/'app.php').write_text(f'v{i}\n')
            if i==2: (self.root/'new.php').write_text('new\n')
            if i==3 and (self.root/'new.php').exists(): git(self.root,'rm','new.php')
            git(self.root,'add','.'); git(self.root,'commit','-m',f'v{i}')
            sha=git(self.root,'rev-parse','HEAD'); self.shas.append(sha); write_baseline(self.root,sha)
            append_deployment_history(self.root,previous_baseline=prev,new_baseline_ref=f'v{i}',new_baseline_commit=sha,evidence_path=None,evidence=None,archive=None,unsafe=True); prev=sha
    def tearDown(self): self.tmp.cleanup()
    def env(self):
        env=os.environ.copy(); env['PYTHONPATH']=str(ROOT/'src'); return env
    def test_report_only(self):
        before=sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*') if p.is_file())
        baseline=(self.root/'.deploy-pack-baseline').read_text(); hist=(self.root/'.deploy-pack-history.jsonl').read_text()
        p=subprocess.run([sys.executable,'-m','deploy_pack.cli','rollback','diff','1'],cwd=self.root,env=self.env(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stdout); self.assertIn('Report only; no rollback artifacts created.',p.stdout)
        after=sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*') if p.is_file())
        self.assertEqual(before,after); self.assertEqual(baseline,(self.root/'.deploy-pack-baseline').read_text()); self.assertEqual(hist,(self.root/'.deploy-pack-history.jsonl').read_text())
    def test_hypothetical_json(self):
        p=subprocess.run([sys.executable,'-m','deploy_pack.cli','rollback','diff','1','--from','2','--json'],cwd=self.root,env=self.env(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stdout); data=json.loads(p.stdout); self.assertEqual(data['mode'],'hypothetical'); self.assertEqual(data['sourceRecord'],2); self.assertEqual(data['targetRecord'],1)
if __name__=='__main__': unittest.main()
