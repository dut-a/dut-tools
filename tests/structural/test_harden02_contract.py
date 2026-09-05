import tomllib,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
class T(unittest.TestCase):
 def test_contract(self):
  with (ROOT/"tools.toml").open("rb") as f: tools=tomllib.load(f)["tools"]
  self.assertEqual("frozen",tools["repo-patch"]["maintenance"]); self.assertTrue(all(m["maintenance"] in {"normal","frozen"} for m in tools.values())); self.assertTrue((ROOT/"shared/git_context_model/model.py").is_file()); text=(ROOT/"tools/release-flow/README.md").read_text(); self.assertIn("ALL VERSION TARGETS RESTORED",text)
