import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
class Coverage(unittest.TestCase):
    def test_shipped_regression(self):
        text=(ROOT/"tools/release-flow/tests/test_release_flow.py").read_text()
        self.assertTrue("test_transactional_restore" in text or "test_transactional_write_restores_all_targets" in text)
        self.assertIn("apply_changes_transactionally", text)
if __name__=="__main__":
    unittest.main()
