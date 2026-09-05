import subprocess, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
class CiContractTest(unittest.TestCase):
    def test_release_tag_contract(self):
        cases={'git-context/v1.5.0':0,'context-zip/v2.0.0':0,'migration-version-fixer/v1.0.0':0,'git-context/v9.9.9':1,'v1.0.0':2,'unknown/v1.0.0':2}
        for tag,expected in cases.items():
            with self.subTest(tag=tag):
                cp=subprocess.run(['python3','scripts/validate-release-tag',tag],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                self.assertEqual(expected,cp.returncode,cp.stdout+cp.stderr)
    def test_workflow_has_selective_and_full_paths(self):
        text=(ROOT/'.github/workflows/ci.yml').read_text()
        for token in ('changed-tools','full-release-gate','release-tag','make release-gate','validate-release-tag'): self.assertIn(token,text)
if __name__=='__main__': unittest.main()
