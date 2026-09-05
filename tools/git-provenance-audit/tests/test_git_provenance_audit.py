from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "git_provenance_audit.py"
spec = importlib.util.spec_from_file_location("git_provenance_audit", MODULE)
gpa = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gpa
assert spec.loader
spec.loader.exec_module(gpa)

def git(repo: Path, *args: str):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

def init_repo(root: Path, name: str, email: str):
    root.mkdir(parents=True)
    git(root, "init", "-q")
    git(root, "config", "user.name", name)
    git(root, "config", "user.email", email)
    (root / "a.txt").write_text("a\n")
    git(root, "add", "a.txt")
    git(root, "commit", "-qm", "initial")

def context_config(path: Path, workspace: Path):
    path.write_text(
        f'''version = 1

[profiles.expected]
name = "Expected User"
email = "expected@example.com"

[profiles.other]
name = "Other User"
email = "other@example.com"

[[contexts]]
name = "workspace"
root = "{workspace.as_posix()}"
profile = "expected"
''',
        encoding="utf-8",
    )

class ResolutionTest(unittest.TestCase):
    def test_most_specific_context_wins(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            profiles={
                "a":gpa.Profile("a","A","a@example.com"),
                "b":gpa.Profile("b","B","b@example.com"),
            }
            model=gpa.GitContextModel(profiles,[
                gpa.Context("nested",base/"work"/"nested","b"),
                gpa.Context("broad",base/"work","a"),
            ])
            repo=base/"work"/"nested"/"repo"
            repo.mkdir(parents=True)
            git(repo,"init","-q")
            profile,context=gpa.resolve_profile(model,repo)
            self.assertEqual("b",profile.key)
            self.assertEqual("nested",context)

    def test_local_pin_wins(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            repo=base/"repo"
            repo.mkdir()
            git(repo,"init","-q")
            git(repo,"config","git-context.profile","other")
            model=gpa.GitContextModel(
                {
                    "expected":gpa.Profile("expected","Expected","e@example.com"),
                    "other":gpa.Profile("other","Other","o@example.com"),
                },
                [gpa.Context("base",base,"expected")],
            )
            profile,context=gpa.resolve_profile(model,repo)
            self.assertEqual("other",profile.key)
            self.assertEqual("pin:other",context)

class DiscoveryTest(unittest.TestCase):
    def test_depth_limits_repository_discovery(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            shallow=root/"a"
            deep=root/"x"/"y"
            shallow.mkdir()
            deep.mkdir(parents=True)
            git(shallow,"init","-q")
            git(deep,"init","-q")
            self.assertEqual([shallow.resolve()],gpa.discover_repositories(root,1))
            self.assertEqual({shallow.resolve(),deep.resolve()},set(gpa.discover_repositories(root,2)))

class AuditTest(unittest.TestCase):
    def test_matching_history_is_clean(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            repo=base/"workspace"/"repo"
            init_repo(repo,"Expected User","expected@example.com")
            cfg=base/"contexts.toml"
            context_config(cfg,base/"workspace")
            result=gpa.audit_repo(repo,gpa.load_git_context(cfg),
                                  gpa.AuditConfig(False,gpa.AllowRules(),[]),[],[],0)
            self.assertEqual([],result.findings)

    def test_author_and_committer_mismatch_reported(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            repo=base/"workspace"/"repo"
            init_repo(repo,"Wrong User","wrong@example.com")
            cfg=base/"contexts.toml"
            context_config(cfg,base/"workspace")
            result=gpa.audit_repo(repo,gpa.load_git_context(cfg),
                                  gpa.AuditConfig(False,gpa.AllowRules(),[]),[],[],0)
            self.assertEqual({"author","committer"},{f.role for f in result.findings})

    def test_allowlisted_email_is_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            repo=base/"workspace"/"repo"
            init_repo(repo,"Legacy User","legacy@example.com")
            cfg=base/"contexts.toml"
            context_config(cfg,base/"workspace")
            allow=gpa.AllowRules(emails={"legacy@example.com"})
            result=gpa.audit_repo(repo,gpa.load_git_context(cfg),
                                  gpa.AuditConfig(False,allow,[]),[],[],0)
            self.assertEqual([],result.findings)

    def test_unclassified_non_strict_is_warning(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            repo=base/"elsewhere"/"repo"
            init_repo(repo,"Someone","s@example.com")
            model=gpa.GitContextModel(
                {"expected":gpa.Profile("expected","Expected","e@example.com")},
                [gpa.Context("other",base/"workspace","expected")],
            )
            result=gpa.audit_repo(repo,model,gpa.AuditConfig(False,gpa.AllowRules(),[]),[],[],0)
            self.assertEqual([],result.findings)
            self.assertTrue(result.warnings)

    def test_unclassified_strict_is_violation(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            repo=base/"elsewhere"/"repo"
            init_repo(repo,"Someone","s@example.com")
            model=gpa.GitContextModel(
                {"expected":gpa.Profile("expected","Expected","e@example.com")},
                [gpa.Context("other",base/"workspace","expected")],
            )
            result=gpa.audit_repo(repo,model,gpa.AuditConfig(True,gpa.AllowRules(),[]),[],[],0)
            self.assertTrue(result.findings)

class CliTest(unittest.TestCase):
    def run_cli(self,*args):
        return subprocess.run(
            ["python3",str(MODULE),*args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_help_and_version(self):
        cp=self.run_cli("--version")
        self.assertEqual(0,cp.returncode)
        self.assertIn("git-provenance-audit 1.0.0",cp.stdout)
        cp=self.run_cli("--help")
        self.assertEqual(0,cp.returncode)
        for token in ("--depth","--commits","--branch","--author","--strict","--format"):
            self.assertIn(token,cp.stdout)

    def test_json_output_and_violation_exit(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            workspace=base/"workspace"
            repo=workspace/"repo"
            init_repo(repo,"Wrong","wrong@example.com")
            cfg=base/"contexts.toml"
            context_config(cfg,workspace)
            cp=self.run_cli(str(workspace),"--git-context-config",str(cfg),"--format","json")
            self.assertEqual(1,cp.returncode,cp.stderr)
            data=json.loads(cp.stdout)
            self.assertEqual(2,data["summary"]["violations"])

    def test_read_only_does_not_change_head_or_status(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            workspace=base/"workspace"
            repo=workspace/"repo"
            init_repo(repo,"Expected User","expected@example.com")
            cfg=base/"contexts.toml"
            context_config(cfg,workspace)
            before_head=git(repo,"rev-parse","HEAD").stdout.strip()
            before_status=git(repo,"status","--porcelain=v1").stdout
            cp=self.run_cli(str(workspace),"--git-context-config",str(cfg))
            self.assertEqual(0,cp.returncode,cp.stderr)
            self.assertEqual(before_head,git(repo,"rev-parse","HEAD").stdout.strip())
            self.assertEqual(before_status,git(repo,"status","--porcelain=v1").stdout)

if __name__=="__main__":
    unittest.main()
