from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from deploy_pack.core import DeployPackError, build_plan
from deploy_pack.cli import main


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


class Selection01Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "test@example.com")
        git(self.root, "config", "user.name", "Test")
        (self.root / "index.php").write_text("one\n", encoding="utf-8")
        (self.root / ".htaccess").write_text("RewriteEngine On\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "base")
        self.base = git(self.root, "rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def _policy(self, include, exclude=(), require=()):
        def arr(values):
            return "[" + ", ".join(repr(v) for v in values).replace("'", '"') + "]"
        (self.root / ".deploy-pack.toml").write_text(
            "schema = 2\n\n[pack]\npolicy = \"allowlist\"\n"
            f"include = {arr(include)}\nexclude = {arr(exclude)}\nrequire = {arr(require)}\n",
            encoding="utf-8",
        )

    def test_allowlist_excludes_unlisted_dev_files_scripts_and_hidden_paths(self):
        self._policy([".htaccess", "*.php", "assets/**"])
        (self.root / "index.php").write_text("two\n", encoding="utf-8")
        (self.root / "package.json").write_text("{}\n", encoding="utf-8")
        (self.root / "playwright.config.js").write_text("export default {}\n", encoding="utf-8")
        (self.root / ".tembeek").mkdir()
        (self.root / ".tembeek/local.yaml").write_text("local: true\n", encoding="utf-8")
        (self.root / "scripts").mkdir()
        (self.root / "scripts/check.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        plan = build_plan(self.root, self.base)
        self.assertEqual([c.path for c in plan.deployable], ["index.php"])
        reasons = dict(plan.ignored_reasons)
        self.assertEqual(reasons["package.json"], "dev-config-not-allowlisted")
        self.assertEqual(reasons["playwright.config.js"], "dev-config-not-allowlisted")
        self.assertEqual(reasons[".tembeek/local.yaml"], "hidden-path-not-allowlisted")
        self.assertEqual(reasons["scripts/check.sh"], "script-not-allowlisted")

    def test_explicit_allowlist_can_include_runtime_script_and_package_metadata(self):
        self._policy([".htaccess", "scripts/commerce-backup.php", "package.json"])
        (self.root / "scripts").mkdir()
        (self.root / "scripts/commerce-backup.php").write_text("<?php echo 'ok';\n", encoding="utf-8")
        (self.root / "package.json").write_text("{}\n", encoding="utf-8")
        plan = build_plan(self.root, self.base)
        self.assertEqual(
            [c.path for c in plan.deployable],
            ["package.json", "scripts/commerce-backup.php"],
        )

    def test_tests_are_hard_denied_even_when_allowlisted(self):
        self._policy(["**"])
        (self.root / "tests-ui").mkdir()
        (self.root / "tests-ui/public-pages.spec.js").write_text("test('x',()=>{})\n", encoding="utf-8")
        (self.root / "test_api.py").write_text("pass\n", encoding="utf-8")
        (self.root / "testimonials.php").write_text("<?php\n", encoding="utf-8")
        plan = build_plan(self.root, self.base)
        paths = [c.path for c in plan.deployable]
        self.assertIn("testimonials.php", paths)
        self.assertNotIn("test_api.py", paths)
        self.assertNotIn("tests-ui/public-pages.spec.js", paths)
        reasons = dict(plan.ignored_reasons)
        self.assertEqual(reasons["test_api.py"], "test")
        self.assertEqual(reasons["tests-ui/public-pages.spec.js"], "test")

    def test_env_files_are_hard_denied_even_when_allowlisted(self):
        self._policy(["**"])
        (self.root / ".env.local.example").write_text("SECRET=x\n", encoding="utf-8")
        plan = build_plan(self.root, self.base)
        self.assertNotIn(".env.local.example", [c.path for c in plan.deployable])
        self.assertEqual(dict(plan.ignored_reasons)[".env.local.example"], "environment-secret/config")

    def test_cli_pack_and_inspect_fail_closed_without_config(self):
        old = Path.cwd()
        os.chdir(self.root)
        try:
            self.assertEqual(main(["inspect", self.base]), 2)
        finally:
            os.chdir(old)

    def test_init_creates_schema2_allowlist_and_prefills_htaccess_only(self):
        old = Path.cwd()
        os.chdir(self.root)
        try:
            self.assertEqual(main(["init"]), 0)
        finally:
            os.chdir(old)
        text = (self.root / ".deploy-pack.toml").read_text(encoding="utf-8")
        self.assertIn("schema = 2", text)
        self.assertIn('policy = "allowlist"', text)
        self.assertIn('  ".htaccess",', text)
        self.assertNotIn('"*.php",', [line for line in text.splitlines() if not line.lstrip().startswith("#")])

    def test_cli_include_narrows_but_cannot_expand_project_allowlist(self):
        self._policy(["*.php"])
        (self.root / "a.php").write_text("<?php\n", encoding="utf-8")
        (self.root / "b.php").write_text("<?php\n", encoding="utf-8")
        (self.root / "package.json").write_text("{}\n", encoding="utf-8")
        narrowed = build_plan(self.root, self.base, cli_includes=["a.php"])
        self.assertEqual([c.path for c in narrowed.deployable], ["a.php"])
        cannot_expand = build_plan(self.root, self.base, cli_includes=["package.json"])
        self.assertNotIn("package.json", [c.path for c in cannot_expand.deployable])

    def test_require_fails_before_pack_when_required_runtime_path_missing(self):
        self._policy(["*.php"], require=["config/runtime.php"])
        with self.assertRaisesRegex(DeployPackError, "required deployment path is missing"):
            build_plan(self.root, self.base)


if __name__ == "__main__":
    unittest.main()
