import tomllib, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]

class RepoPatchContractTest(unittest.TestCase):
    def test_active_and_rollback_locked(self):
        with (ROOT/"tools.toml").open("rb") as fh:
            tools=tomllib.load(fh)["tools"]
        self.assertEqual("active",tools["repo-patch"]["status"])
        text=(ROOT/"tools/repo-patch/README.md").read_text()
        self.assertIn("Rollback is **mandatory**, not optional.",text)
        self.assertIn("gradle",text.lower())
        self.assertIn("composer-php",text.lower())
        self.assertIn("generic",text.lower())

if __name__=="__main__":
    unittest.main()
