from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve()
MODULE_PATH = HERE.parents[1] / "src" / "git_context.py"
spec = importlib.util.spec_from_file_location("git_context", MODULE_PATH)
gc = importlib.util.module_from_spec(spec)
assert spec.loader
import sys
sys.modules[spec.name] = gc
spec.loader.exec_module(gc)

class GitContextUnitTest(unittest.TestCase):
    def test_narrower_context_wins(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            profiles = {
                "a": gc.Profile("a", "A", "a@example.com"),
                "b": gc.Profile("b", "B", "b@example.com"),
            }
            model = gc.Model(
                1, profiles,
                [
                    gc.Context("nested", base / "work" / "special", "b"),
                    gc.Context("broad", base / "work", "a"),
                ],
                {}
            )
            ctx, profile, pin = gc.resolve_context(model, base / "work" / "special" / "repo", honor_pin=False)
            self.assertEqual("nested", ctx.name)
            self.assertEqual("b", profile.key)

    def test_unclassified_has_no_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            model = gc.Model(
                1,
                {"a": gc.Profile("a", "A", "a@example.com")},
                [gc.Context("a", base / "known", "a")],
                {}
            )
            ctx, profile, pin = gc.resolve_context(model, base / "unknown", honor_pin=False)
            self.assertIsNone(ctx)
            self.assertIsNone(profile)

    def test_generated_config_sets_use_config_only(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            model = gc.Model(
                1,
                {"a": gc.Profile("a", "A", "a@example.com")},
                [gc.Context("a", base / "known", "a")],
                {}
            )
            text = gc.managed_gitconfig(model)
            self.assertIn("useConfigOnly = true", text)
            self.assertIn('includeIf "gitdir:', text)

    def test_host_path_shape(self):
        expected = gc.BASE_DIR / "host-cli" / "github" / "tembeek"
        self.assertIn("host-cli", str(expected))
        self.assertTrue(str(expected).endswith("github/tembeek"))

class GitContextCliSmokeTest(unittest.TestCase):
    def test_version(self):
        cp = subprocess.run(
            ["python3", str(MODULE_PATH), "--version"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.assertEqual(0, cp.returncode)
        self.assertIn("git-context 1.5.0", cp.stdout)

    def test_help(self):
        cp = subprocess.run(
            ["python3", str(MODULE_PATH), "--help"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.assertEqual(0, cp.returncode)
        self.assertIn("relocate", cp.stdout)
        self.assertIn("machine", cp.stdout)

if __name__ == "__main__":
    unittest.main()
