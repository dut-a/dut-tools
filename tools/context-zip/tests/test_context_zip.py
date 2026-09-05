from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve()
MODULE_PATH = HERE.parents[1] / "src" / "context_zip.py"
spec = importlib.util.spec_from_file_location("context_zip", MODULE_PATH)
cz = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules[spec.name] = cz
spec.loader.exec_module(cz)

class ContextZipUnitTest(unittest.TestCase):
    def test_detect_spring(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")
            (root / "src/main/java").mkdir(parents=True)
            self.assertEqual("spring", cz.detect_stack(root))

    def test_detect_php(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "composer.json").write_text("{}", encoding="utf-8")
            (root / "app").mkdir()
            self.assertEqual("php", cz.detect_stack(root))

    def test_config_patterns(self):
        cfg = {"include": ["docs/**"], "exclude": ["tmp/**"]}
        self.assertEqual(["docs/**"], cz.config_patterns(cfg, "include"))
        self.assertEqual(["tmp/**"], cz.config_patterns(cfg, "exclude"))

    def test_archive_has_manifest_and_exclusions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")
            (root / "src/main/java").mkdir(parents=True)
            source = root / "src/main/java/App.java"
            source.write_text("class App {}", encoding="utf-8")
            settings = cz.Settings(stack="spring")
            files, excluded = cz.select_files(root, settings, "spring")
            out = root / "out.zip"
            cz.create_archive(root, out, "spring", settings, files, excluded, False)
            with zipfile.ZipFile(out) as z:
                names = set(z.namelist())
                self.assertIn("pom.xml", names)
                self.assertIn("src/main/java/App.java", names)
                self.assertIn("CONTEXT-ZIP-MANIFEST.json", names)
                self.assertIn("EXCLUDED-FILES.tsv", names)
                manifest = json.loads(z.read("CONTEXT-ZIP-MANIFEST.json"))
                self.assertEqual("spring", manifest["stack"])
                self.assertEqual("2.0.1", manifest["version"])

    def test_binary_excluded_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")
            (root / "src/main/resources").mkdir(parents=True)
            binary = root / "src/main/resources/logo.png"
            binary.write_bytes(b"\x89PNG\x00binary")
            files, excluded = cz.select_files(root, cz.Settings(stack="spring"), "spring")
            self.assertNotIn(binary.resolve(), files)
            self.assertTrue(any(p.endswith("logo.png") and reason == "binary" for p, reason in excluded))

    def test_alias_root_uses_canonical_relative_paths(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            real = base / "real"
            real.mkdir()
            (real / "pom.xml").write_text("<project/>", encoding="utf-8")
            resources = real / "src/main/resources"
            resources.mkdir(parents=True)
            (resources / "logo.png").write_bytes(b"\x89PNG\x00binary")
            alias = base / "alias"
            alias.symlink_to(real, target_is_directory=True)

            files, excluded = cz.select_files(alias, cz.Settings(stack="spring"), "spring")
            self.assertIn((real / "pom.xml").resolve(), files)
            self.assertTrue(any(path == "src/main/resources/logo.png" and reason == "binary"
                                for path, reason in excluded))

class ContextZipCliSmokeTest(unittest.TestCase):
    def test_version(self):
        cp = subprocess.run(["python3", str(MODULE_PATH), "--version"],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(0, cp.returncode)
        self.assertIn("context-zip 2.0.1", cp.stdout)

    def test_help_mentions_parity_flags(self):
        cp = subprocess.run(["python3", str(MODULE_PATH), "--help"],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(0, cp.returncode)
        for flag in ("--config", "--init-config", "--print-config", "--whole-project",
                     "--include-untracked", "--include-binaries", "--max-file-mb", "--dry-run"):
            self.assertIn(flag, cp.stdout)

if __name__ == "__main__":
    unittest.main()
