import tomllib, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
class Contract(unittest.TestCase):
    def test_active(self):
        with (ROOT/"tools.toml").open("rb") as fh:
            tools=tomllib.load(fh)["tools"]
        self.assertEqual("active",tools["build-diagnostics"]["status"])
    def test_policy_boundary(self):
        text=(ROOT/"tools/build-diagnostics/README.md").read_text()
        self.assertIn("repository-specific",text.lower())
        self.assertIn("No implicit shell".lower(),text.lower())
if __name__=="__main__":
    unittest.main()
