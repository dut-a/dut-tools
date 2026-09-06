from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

TOOL_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from deploy_pack.artifact import build_artifact_plan, write_artifact
from deploy_pack.core import DeployPackError


class Artifact01Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "artifact"
        (self.source / "assets").mkdir(parents=True)
        (self.source / "_assets").mkdir()
        (self.source / "empty").mkdir()
        (self.source / "index.html").write_text("index\n", encoding="utf-8")
        (self.source / ".htaccess").write_text("rewrite\n", encoding="utf-8")
        (self.source / "assets" / "app.css").write_text("css\n", encoding="utf-8")
        (self.source / "_assets" / "app.js").write_text("js\n", encoding="utf-8")
        (self.source / ".DS_Store").write_bytes(b"junk")

    def tearDown(self):
        self.tmp.cleanup()

    def _plan(self, fmt: str, name: str, required=()):
        return build_artifact_plan(self.source, self.root / name, fmt, required=list(required))

    def test_zip_flattens_source_root_preserves_dotfiles_nested_and_empty_dirs(self):
        out = write_artifact(self._plan("zip", "deploy.zip"))
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        self.assertEqual(names, sorted(names, key=lambda n: n.rstrip("/")))
        self.assertIn("index.html", names)
        self.assertIn(".htaccess", names)
        self.assertIn("assets/app.css", names)
        self.assertIn("_assets/app.js", names)
        self.assertIn("empty/", names)
        self.assertNotIn("artifact/index.html", names)
        self.assertNotIn(".DS_Store", names)

    def test_tar_gz_flattens_source_root_preserves_dotfiles_nested_and_empty_dirs(self):
        out = write_artifact(self._plan("tar.gz", "deploy.tar.gz"))
        with tarfile.open(out, "r:gz") as tf:
            names = tf.getnames()
        self.assertIn("index.html", names)
        self.assertIn(".htaccess", names)
        self.assertIn("assets/app.css", names)
        self.assertIn("_assets/app.js", names)
        self.assertIn("empty", names)
        self.assertNotIn("artifact/index.html", names)
        self.assertNotIn(".DS_Store", names)

    def test_reproducible_archives(self):
        one = write_artifact(self._plan("zip", "one.zip")).read_bytes()
        two = write_artifact(self._plan("zip", "two.zip")).read_bytes()
        self.assertEqual(one, two)
        one = write_artifact(self._plan("tar.gz", "one.tar.gz")).read_bytes()
        two = write_artifact(self._plan("tar.gz", "two.tar.gz")).read_bytes()
        self.assertEqual(one, two)

    def test_git_is_not_consulted(self):
        with mock.patch("deploy_pack.core._git", side_effect=AssertionError("git consulted")):
            out = write_artifact(self._plan("zip", "nogit.zip"))
        self.assertTrue(out.is_file())

    def test_missing_source_and_file_source_fail_cleanly(self):
        with self.assertRaisesRegex(DeployPackError, "does not exist"):
            build_artifact_plan(self.root / "missing", self.root / "x.zip", "zip")
        file_source = self.root / "file.txt"
        file_source.write_text("x")
        with self.assertRaisesRegex(DeployPackError, "not a directory"):
            build_artifact_plan(file_source, self.root / "x.zip", "zip")

    def test_output_inside_source_is_rejected_before_write(self):
        with self.assertRaisesRegex(DeployPackError, "must not be inside"):
            build_artifact_plan(self.source, self.source / "deploy.zip", "zip")

    def test_require_is_repeatable_and_fails_before_archive_write(self):
        plan = self._plan("zip", "required.zip", required=["index.html", ".htaccess"])
        self.assertEqual(plan.required, ("index.html", ".htaccess"))
        missing = self.root / "missing-required.zip"
        with self.assertRaisesRegex(DeployPackError, "required artifact path is absent"):
            build_artifact_plan(self.source, missing, "zip", required=["nope.html"])
        self.assertFalse(missing.exists())

    def test_require_rejects_noncanonical_paths(self):
        for value in ("../index.html", "/index.html", "a/../b", r"a\\b"):
            with self.subTest(value=value):
                with self.assertRaises(DeployPackError):
                    self._plan("zip", "x.zip", required=[value])

    def test_noncanonical_platform_filename_is_rejected(self):
        if os.name == "nt":
            self.skipTest("backslash is a separator on Windows and cannot be created as a filename")
        (self.source / "bad\\name.txt").write_text("x")
        with self.assertRaisesRegex(DeployPackError, "backslash"):
            self._plan("zip", "x.zip")

    @unittest.skipIf(os.name == "nt", "symlink setup requires POSIX test semantics")
    def test_symlink_escape_is_rejected_and_internal_symlink_is_preserved(self):
        outside = self.root / "outside.txt"
        outside.write_text("secret")
        escape = self.source / "escape"
        escape.symlink_to("../outside.txt")
        with self.assertRaises(DeployPackError):
            self._plan("zip", "escape.zip")
        escape.unlink()
        internal = self.source / "current.css"
        internal.symlink_to("assets/app.css")
        out = write_artifact(self._plan("zip", "internal.zip"))
        with zipfile.ZipFile(out) as zf:
            info = zf.getinfo("current.css")
            self.assertEqual(zf.read(info), b"assets/app.css")
            self.assertEqual((info.external_attr >> 16) & 0o170000, 0o120000)

    def test_cli_works_outside_git_and_json_contract_is_stable(self):
        output = self.root / "cli.zip"
        env = dict(os.environ)
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(TOOL_ROOT / "src") + (
            os.pathsep + existing_pythonpath if existing_pythonpath else ""
        )
        proc = subprocess.run(
            [
                sys.executable, "-m", "deploy_pack.cli", "artifact",
                "--source", str(self.source), "--format", "zip",
                "--output", str(output), "--require", "index.html", "--json",
            ],
            cwd=self.root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        value = json.loads(proc.stdout)
        self.assertEqual(value["mode"], "artifact")
        self.assertEqual(value["format"], "zip")
        self.assertEqual(value["output"], str(output.resolve()))
        self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
