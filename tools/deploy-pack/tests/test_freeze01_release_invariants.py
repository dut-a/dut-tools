import ast
import json
import pathlib
import tomllib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class Freeze01ReleaseInvariantTests(unittest.TestCase):
    def test_no_duplicate_top_level_function_definitions(self):
        tree = ast.parse((ROOT / 'src/deploy_pack/core.py').read_text(encoding='utf-8'))
        seen = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                self.assertNotIn(node.name, seen, f'duplicate top-level function {node.name}')
                seen[node.name] = node.lineno

    def test_browser_verifier_does_not_use_query_string_for_token(self):
        source = (ROOT / 'src/deploy_pack/core.py').read_text(encoding='utf-8')
        self.assertNotIn("$_GET['token']", source)
        self.assertIn("$_POST['token']", source)
        self.assertIn("HTTP_AUTHORIZATION", source)
        self.assertIn('method="post"', source)

    def test_release_dependencies_are_bounded(self):
        data = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        self.assertEqual(data['build-system']['requires'], ['setuptools>=82,<83'])
        self.assertEqual(data['project']['dependencies'], ['cryptography>=46,<47'])

    def test_freeze_record_remains_pinned_to_1_9_1(self):
        manifest = json.loads(
            (ROOT / 'docs/manifests/DEPLOY-PACK-FREEZE-01-MANIFEST.json').read_text(encoding='utf-8')
        )
        self.assertEqual(manifest['releaseVersion'], '1.9.1')
        self.assertEqual(manifest['compatibilityBaseline'], '1.9.1')
        self.assertEqual(manifest['status'], 'FROZEN')

        data = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        current = tuple(int(part) for part in data['project']['version'].split('.'))
        frozen = (1, 9, 1)
        self.assertGreaterEqual(current, frozen)

if __name__ == '__main__':
    unittest.main()
