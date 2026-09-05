from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class Harden04Contract(unittest.TestCase):
    def test_context_zip_canonicalizes_relative_comparison(self):
        text = (ROOT / "tools/context-zip/src/context_zip.py").read_text()
        self.assertIn("canonical(p).relative_to(canonical(root))", text)

    def test_repo_patch_canonicalizes_both_sides(self):
        text = (ROOT / "tools/repo-patch/src/repo_patch.py").read_text()
        self.assertIn("root_canonical=canonical(root)", text)
        self.assertIn("p.relative_to(root_canonical)", text)

    def test_repo_patch_remains_frozen(self):
        import tomllib
        with (ROOT / "tools.toml").open("rb") as fh:
            tools = tomllib.load(fh)["tools"]
        self.assertEqual("frozen", tools["repo-patch"]["maintenance"])


if __name__ == "__main__":
    unittest.main()
