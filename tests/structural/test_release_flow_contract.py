import tomllib, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
class C(unittest.TestCase):
 def test_active(self):
  with (ROOT/'tools.toml').open('rb') as fh: tools=tomllib.load(fh)['tools']
  self.assertEqual('active',tools['release-flow']['status'])
 def test_boundary(self): self.assertIn('does not create or push Git tags',(ROOT/'tools/release-flow/README.md').read_text())
if __name__=='__main__': unittest.main()
