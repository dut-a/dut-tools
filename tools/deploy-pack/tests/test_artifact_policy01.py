from __future__ import annotations

import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from deploy_pack.artifact import build_artifact_plan, write_artifact
from deploy_pack.core import DeployPackError


@contextmanager
def cwd(path: Path):
    before = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(before)


class ArtifactPolicy01Tests(unittest.TestCase):
    def make_repo(self) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        files = {
            ".htaccess": "deny\n",
            "index.php": "<?php\n",
            "privacy.php": "<?php\n",
            "css/app.css": "body{}\n",
            "js/app.js": "ok\n",
            "dist/index.php": "duplicate\n",
            "tools/build.php": "dev\n",
            "artifacts/report.html": "report\n",
            ".git/config": "vcs\n",
            "node_modules/x.js": "dep\n",
        }
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return root

    def write_policy(self, root: Path, body: str) -> None:
        (root / ".deploy-pack.toml").write_text(body.lstrip(), encoding="utf-8")

    def plan_members(self, root: Path) -> set[str]:
        plan = build_artifact_plan(root, root.parent / "out.zip", "zip")
        return {entry.path for entry in plan.entries}

    def test_include_is_allowlist_and_exclude_narrows(self):
        root = self.make_repo()
        self.write_policy(
            root,
            """
[artifact]
include = [".htaccess", "*.php", "css/**", "js/**"]
exclude = ["privacy.php"]
""",
        )
        with cwd(root):
            members = self.plan_members(root)
        self.assertEqual(
            members,
            {".htaccess", "index.php", "css", "css/app.css", "js", "js/app.js"},
        )

    def test_hard_hygiene_cannot_be_reincluded(self):
        root = self.make_repo()
        self.write_policy(root, '[artifact]\ninclude = ["**"]\n')
        with cwd(root):
            members = self.plan_members(root)
        self.assertFalse(any(x == ".git" or x.startswith(".git/") for x in members))
        self.assertFalse(any(x == "node_modules" or x.startswith("node_modules/") for x in members))
        self.assertNotIn(".deploy-pack.toml", members)

    def test_exclude_only_policy_preserves_ordinary_payload(self):
        root = self.make_repo()
        self.write_policy(
            root,
            '[artifact]\nexclude = ["dist/**", "tools/**", "artifacts/**"]\n',
        )
        with cwd(root):
            members = self.plan_members(root)
        self.assertIn("index.php", members)
        self.assertIn("css/app.css", members)
        self.assertFalse(any(x == "dist" or x.startswith("dist/") for x in members))
        self.assertFalse(any(x == "tools" or x.startswith("tools/") for x in members))

    def test_required_must_survive_policy(self):
        root = self.make_repo()
        self.write_policy(root, '[artifact]\ninclude = ["index.php"]\n')
        with cwd(root):
            with self.assertRaisesRegex(
                DeployPackError,
                "required artifact path is excluded by artifact policy: .htaccess",
            ):
                build_artifact_plan(
                    root,
                    root.parent / "out.zip",
                    "zip",
                    required=[".htaccess"],
                )

    def test_root_star_does_not_cross_directories(self):
        root = self.make_repo()
        self.write_policy(root, '[artifact]\ninclude = ["*.php"]\n')
        with cwd(root):
            members = self.plan_members(root)
        self.assertIn("index.php", members)
        self.assertIn("privacy.php", members)
        self.assertNotIn("dist/index.php", members)

    def test_source_policy_wins_over_cwd_policy(self):
        root = self.make_repo()
        outer = root.parent

        (outer / ".deploy-pack.toml").write_text(
            '[pack]\npolicy = "allowlist"\ninclude = ["**"]\n',
            encoding="utf-8",
        )
        (root / ".deploy-pack.toml").write_text(
            '[artifact]\nexclude = ["tools/**"]\n',
            encoding="utf-8",
        )

        output = outer / "source-policy-wins.zip"
        with cwd(outer):
            plan = build_artifact_plan(root, output, "zip")

        members = {entry.path for entry in plan.entries}

        self.assertFalse(
            any(path == "tools" or path.startswith("tools/") for path in members)
        )
        self.assertIn("index.php", members)

    def test_unknown_artifact_setting_fails_closed(self):
        root = self.make_repo()
        self.write_policy(root, '[artifact]\nincludes = ["*.php"]\n')
        with cwd(root):
            with self.assertRaisesRegex(DeployPackError, r"unknown \[artifact\] setting"):
                build_artifact_plan(root, root.parent / "out.zip", "zip")

    def test_policy_applies_identically_to_zip_and_tar_gz(self):
        root = self.make_repo()
        self.write_policy(root, '[artifact]\ninclude = [".htaccess", "*.php"]\n')
        with cwd(root):
            zplan = build_artifact_plan(root, root.parent / "out.zip", "zip")
            tplan = build_artifact_plan(root, root.parent / "out.tar.gz", "tar.gz")
            write_artifact(zplan)
            write_artifact(tplan)

        with zipfile.ZipFile(zplan.output) as archive:
            zip_members = {name.rstrip("/") for name in archive.namelist()}
        with tarfile.open(tplan.output, "r:gz") as archive:
            tar_members = {member.name.rstrip("/") for member in archive.getmembers()}
        self.assertEqual(zip_members, tar_members)
        self.assertEqual(zip_members, {".htaccess", "index.php", "privacy.php"})


if __name__ == "__main__":
    unittest.main()
