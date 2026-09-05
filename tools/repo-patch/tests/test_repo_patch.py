from __future__ import annotations
import importlib.util, subprocess, sys, tempfile, unittest
from pathlib import Path

MODULE=Path(__file__).resolve().parents[1]/"src"/"repo_patch.py"
spec=importlib.util.spec_from_file_location("repo_patch",MODULE)
rp=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=rp
assert spec.loader
spec.loader.exec_module(rp)

class RepoPatchTest(unittest.TestCase):
    def test_escape_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(rp.InvalidInput):
                rp.safe_path(Path(td).resolve(),"../x")

    def test_alias_root_is_not_false_escape(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            real=base/"real"
            real.mkdir()
            (real/"a.txt").write_text("before")
            alias=base/"alias"
            alias.symlink_to(real,target_is_directory=True)
            resolved=rp.safe_path(alias,"a.txt")
            self.assertEqual((real/"a.txt").resolve(),resolved)
            snaps=rp.take_snapshots(alias,["a.txt"])
            (real/"a.txt").write_text("after")
            rp.restore(snaps)
            self.assertEqual("before",(real/"a.txt").read_text())

    def test_alias_root_still_rejects_escape(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            real=base/"real"
            real.mkdir()
            alias=base/"alias"
            alias.symlink_to(real,target_is_directory=True)
            with self.assertRaises(rp.InvalidInput):
                rp.safe_path(alias,"../outside.txt")

    def test_patch_paths(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"p.diff"
            p.write_text("--- a/pom.xml\n+++ b/pom.xml\n",encoding="utf-8")
            self.assertEqual({"pom.xml"},rp.parse_patch_paths(p))

    def test_restore_existing_file(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            f=root/"a.txt"; f.write_text("before")
            s=rp.take_snapshots(root,["a.txt"])
            f.write_text("after")
            rp.restore(s)
            self.assertEqual("before",f.read_text())

    def test_restore_removes_new_file(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            s=rp.take_snapshots(root,["a.txt"])
            (root/"a.txt").write_text("new")
            rp.restore(s)
            self.assertFalse((root/"a.txt").exists())

    def test_nested_maven_module_selection(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            (root/"pom.xml").write_text("<project/>")
            mod=root/"m"; mod.mkdir(); (mod/"pom.xml").write_text("<project/>")
            f=mod/"src/main/java/A.java"; f.parent.mkdir(parents=True); f.write_text("class A{}")
            self.assertEqual(["m"],rp.affected_selectors(root,[f]))

    def test_cli_version_and_help(self):
        for arg in ("--version","--help"):
            cp=subprocess.run(["python3",str(MODULE),arg],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.assertEqual(0,cp.returncode)
        self.assertIn("repo-patch 1.3.1",
            subprocess.run(["python3",str(MODULE),"--version"],text=True,stdout=subprocess.PIPE).stdout)

if __name__=="__main__":
    unittest.main()


class GradleAdapterTest(unittest.TestCase):
    def test_detect_gradle(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"settings.gradle").write_text("rootProject.name='x'")
            self.assertEqual("gradle",rp.detect_adapter(root))

    def test_gradle_project_selection(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            (root/"settings.gradle").write_text("include ':app', ':lib'")
            for name in ("app","lib"):
                d=root/name
                d.mkdir()
                (d/"build.gradle").write_text("plugins {}")
            f=root/"app/src/main/java/A.java"
            f.parent.mkdir(parents=True)
            f.write_text("class A{}")
            ref,_=rp.containing_gradle_project(f,rp.gradle_projects(root),root)
            self.assertEqual(":app",ref)

    def test_gradle_wrapper_preferred(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            wrapper=root/"gradlew"
            wrapper.write_text("#!/bin/sh\n")
            wrapper.chmod(0o755)
            self.assertEqual([str(wrapper)],rp.gradle_command(root))

    def test_gradle_tasks_project_scoped(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            (root/"settings.gradle").write_text("include ':app'")
            app=root/"app"; app.mkdir()
            (app/"build.gradle").write_text("plugins {}")
            wrapper=root/"gradlew"; wrapper.write_text("#!/bin/sh\n"); wrapper.chmod(0o755)
            f=app/"src/main/java/A.java"; f.parent.mkdir(parents=True); f.write_text("class A{}")
            vals=rp.gradle_validations(root,[f],False)
            self.assertIn(":app:classes",vals[0].command)
            self.assertIn(":app:testClasses",vals[0].command)


class ComposerPhpAdapterTest(unittest.TestCase):
    def test_detect_composer_php(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"composer.json").write_text('{"name":"example/root"}')
            self.assertEqual("composer-php",rp.detect_adapter(root))

    def test_nearest_composer_package(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            (root/"composer.json").write_text('{"name":"example/root"}')
            package=root/"packages/foo"
            package.mkdir(parents=True)
            (package/"composer.json").write_text('{"name":"example/foo"}')
            source=package/"src/Foo.php"
            source.parent.mkdir(parents=True)
            source.write_text("<?php class Foo {}")
            found=rp.containing_composer_package(source,rp.composer_packages(root),root)
            self.assertEqual(package,found)

    def test_local_composer_phar_preferred(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            (root/"composer.json").write_text('{}')
            phar=root/"composer.phar"
            phar.write_bytes(b"placeholder")
            cmd=rp.composer_command(root,root)
            self.assertTrue(cmd[-1].endswith("composer.phar"))

    def test_composer_validations_include_php_lint_and_validate(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            (root/"composer.json").write_text('{"name":"example/root"}')
            phar=root/"composer.phar"
            phar.write_bytes(b"placeholder")
            phpfile=root/"src/A.php"
            phpfile.parent.mkdir(parents=True)
            phpfile.write_text("<?php echo 'ok';")
            original_php=rp.shutil.which
            try:
                rp.shutil.which=lambda name: "/usr/bin/php" if name=="php" else None
                vals=rp.composer_php_validations(root,[phpfile],False)
            finally:
                rp.shutil.which=original_php
            names=[v.name for v in vals]
            self.assertTrue(any(n.startswith("php-lint:") for n in names))
            self.assertTrue(any(n.startswith("composer-validate:") for n in names))

    def test_laravel_is_fallback_not_assumption(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve()
            (root/"composer.json").write_text('{"name":"example/root"}')
            phar=root/"composer.phar"; phar.write_bytes(b"placeholder")
            phpfile=root/"src/A.php"; phpfile.parent.mkdir(); phpfile.write_text("<?php")
            original_php=rp.shutil.which
            try:
                rp.shutil.which=lambda name: "/usr/bin/php" if name=="php" else None
                vals=rp.composer_php_validations(root,[phpfile],False)
            finally:
                rp.shutil.which=original_php
            self.assertFalse(any(v.name.startswith("laravel-test:") for v in vals))


class GenericAdapterTest(unittest.TestCase):
    def test_generic_is_auto_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual("generic",rp.detect_adapter(Path(td)))

    def test_generic_json_validation(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve(); p=root/"a.json"
            p.write_text('{"ok": true}')
            rp.validate_generic_file(root,p)
            p.write_text('{"broken":')
            with self.assertRaises(rp.InvalidInput): rp.validate_generic_file(root,p)

    def test_generic_python_validation(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve(); p=root/"a.py"
            p.write_text("x = 1\n")
            rp.validate_generic_file(root,p)
            p.write_text("def nope(:\n")
            with self.assertRaises(rp.InvalidInput): rp.validate_generic_file(root,p)

    def test_generic_conflict_marker_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve(); p=root/"a.txt"
            p.write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> branch\n")
            with self.assertRaises(rp.InvalidInput): rp.validate_generic_file(root,p)

    def test_generic_deleted_file_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve(); rp.validate_generic_file(root,root/"gone.txt")

    def test_generic_config_argv_validation(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve(); cfg={"generic":{"validations":[{"name":"tests","argv":["python3","-V"],"cwd":"."}]}}
            vals=rp.generic_config_validations(root,cfg)
            self.assertEqual("generic:tests",vals[0].name)

    def test_generic_config_rejects_shell_string(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve(); cfg={"generic":{"validations":[{"name":"bad","argv":"rm -rf /"}]}}
            with self.assertRaises(rp.InvalidInput): rp.generic_config_validations(root,cfg)
