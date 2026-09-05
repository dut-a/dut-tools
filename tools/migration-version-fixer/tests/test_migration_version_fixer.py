from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
MODULE_PATH = HERE.parents[1] / "src" / "migration_version_fixer.py"
spec = importlib.util.spec_from_file_location("migration_version_fixer", MODULE_PATH)
mvf = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules[spec.name] = mvf
spec.loader.exec_module(mvf)

class StrategyTest(unittest.TestCase):
    def m(self, raw, name, protected=False):
        p = Path("/tmp") / f"V{raw}__{name}.sql"
        return mvf.Migration("flyway", p, p.name, ".", raw, name, protected)

    def settings(self, strategy="auto"):
        return mvf.Settings(Path("/tmp"), flyway_strategy=strategy, git_protect=False)

    def test_compound_underscore_continues_sequence(self):
        group = [
            self.m("1_1", "a", True),
            self.m("1_2", "b", True),
            self.m("1_2", "c", False),
        ]
        changes, code = mvf.plan_flyway_domain("x", group, self.settings("compound"))
        self.assertEqual(mvf.CLEAN, code)
        self.assertEqual(1, len(changes))
        self.assertEqual("V1_3__c.sql", changes[0].destination.name)

    def test_compound_dot_continues_sequence(self):
        group = [
            self.m("1.1", "a", True),
            self.m("1.2", "b", True),
            self.m("1.2", "c", False),
        ]
        changes, code = mvf.plan_flyway_domain("x", group, self.settings("compound"))
        self.assertEqual(mvf.CLEAN, code)
        self.assertEqual("V1.3__c.sql", changes[0].destination.name)

    def test_protected_duplicate_conflict(self):
        group = [
            self.m("2", "a", True),
            self.m("2", "b", True),
        ]
        changes, code = mvf.plan_flyway_domain("x", group, self.settings("sequential"))
        self.assertEqual(mvf.PROTECTED_CONFLICT, code)

    def test_sequential_keeps_one_and_renames_duplicate(self):
        group = [
            self.m("1", "a", True),
            self.m("2", "b", True),
            self.m("2", "c", False),
        ]
        changes, code = mvf.plan_flyway_domain("x", group, self.settings("sequential"))
        self.assertEqual(mvf.CLEAN, code)
        self.assertEqual("V3__c.sql", changes[0].destination.name)

class LaravelTest(unittest.TestCase):
    def test_laravel_duplicate_increments_timestamp(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = root / "database/migrations"
            d.mkdir(parents=True)
            a = d / "2026_08_19_120500_create_a.php"
            b = d / "2026_08_19_120500_create_b.php"
            a.write_text("<?php", encoding="utf-8")
            b.write_text("<?php", encoding="utf-8")
            group = [
                mvf.Migration("laravel", a, a.relative_to(root).as_posix(), "database/migrations", "2026_08_19_120500", "create_a", True),
                mvf.Migration("laravel", b, b.relative_to(root).as_posix(), "database/migrations", "2026_08_19_120500", "create_b", False),
            ]
            changes, code = mvf.plan_laravel_domain("x", group)
            self.assertEqual(mvf.CLEAN, code)
            self.assertEqual("2026_08_19_120501_create_b.php", changes[0].destination.name)

class SpringGroupingTest(unittest.TestCase):
    def test_properties_flyway_locations(self):
        text = "spring.flyway.locations=classpath:db/migration,classpath:db/shared\n"
        self.assertEqual(
            ["classpath:db/migration", "classpath:db/shared"],
            mvf.extract_flyway_locations_from_text(text)
        )

    def test_auto_scope_groups_configured_location(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for module in ("a", "b"):
                p = root / module / "src/main/resources/db/shared"
                p.mkdir(parents=True)
            m1p = root / "a/src/main/resources/db/shared/V1__a.sql"
            m2p = root / "b/src/main/resources/db/shared/V1__b.sql"
            m1p.write_text("-- a", encoding="utf-8")
            m2p.write_text("-- b", encoding="utf-8")
            s = mvf.Settings(root, scope="auto", flyway_locations=["classpath:db/shared"], git_protect=False)
            ms = [
                mvf.Migration("flyway", m1p, m1p.relative_to(root).as_posix(), m1p.parent.relative_to(root).as_posix(), "1", "a"),
                mvf.Migration("flyway", m2p, m2p.relative_to(root).as_posix(), m2p.parent.relative_to(root).as_posix(), "1", "b"),
            ]
            grouped = mvf.domains(s, ms)
            self.assertEqual(1, len(grouped))

class CliContractTest(unittest.TestCase):
    def run_cli(self, *args, env=None):
        return subprocess.run(
            ["python3", str(MODULE_PATH), *args],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
        )

    def test_version(self):
        cp = self.run_cli("--version")
        self.assertEqual(0, cp.returncode)
        self.assertIn("migration_version_fixer 1.0.0", cp.stdout)

    def test_help_has_compound_and_check(self):
        cp = self.run_cli("--help")
        self.assertEqual(0, cp.returncode)
        self.assertIn("compound", cp.stdout)
        self.assertIn("--check", cp.stdout)
        self.assertIn("--print-exit-codes", cp.stdout)

    def test_exit_codes(self):
        cp = self.run_cli("--print-exit-codes")
        self.assertEqual(0, cp.returncode)
        for code, name in mvf.EXIT_CODES.items():
            self.assertIn(f"{code} {name}", cp.stdout)

    def test_check_returns_changes_required(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = root / "src/main/resources/db/migration"
            d.mkdir(parents=True)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")
            (d / "V1__a.sql").write_text("-- a", encoding="utf-8")
            (d / "V1__b.sql").write_text("-- b", encoding="utf-8")
            cp = self.run_cli(str(root), "--check", "--no-git-protect", "--scope", "directory")
            self.assertEqual(mvf.CHANGES_REQUIRED, cp.returncode)

    def test_check_github_annotation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = root / "src/main/resources/db/migration"
            d.mkdir(parents=True)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")
            (d / "V1__a.sql").write_text("-- a", encoding="utf-8")
            (d / "V1__b.sql").write_text("-- b", encoding="utf-8")
            env = os.environ.copy()
            env["GITHUB_ACTIONS"] = "true"
            cp = self.run_cli(str(root), "--check", "--no-git-protect", "--scope", "directory", env=env)
            self.assertEqual(mvf.CHANGES_REQUIRED, cp.returncode)
            self.assertIn("::error", cp.stdout)

    def test_apply_renames(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = root / "src/main/resources/db/migration"
            d.mkdir(parents=True)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")
            (d / "V1__a.sql").write_text("-- a", encoding="utf-8")
            (d / "V1__b.sql").write_text("-- b", encoding="utf-8")
            cp = self.run_cli(str(root), "--apply", "--no-git-protect", "--scope", "directory", "--flyway-strategy", "sequential")
            self.assertEqual(0, cp.returncode, cp.stderr)
            names = sorted(p.name for p in d.glob("*.sql"))
            self.assertEqual(["V1__a.sql", "V2__b.sql"], names)

if __name__ == "__main__":
    unittest.main()
