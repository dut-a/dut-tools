import subprocess, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
class HelpContract(unittest.TestCase):
    def test_operator_targets_advertised(self):
        cp=subprocess.run(["make","help"],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.assertEqual(0,cp.returncode,cp.stderr)
        for target in ("release-gate","changed-tools","validate-release-tag","package-distribution","distribution-smoke"):
            self.assertIn(target,cp.stdout)
if __name__=="__main__":
    unittest.main()
