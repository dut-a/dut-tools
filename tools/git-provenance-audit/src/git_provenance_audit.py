#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, TextIO

_REPO_ROOT=Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path: sys.path.insert(0,str(_REPO_ROOT))
from shared.git_context_model import Profile,Context,GitContextModel,ConfigError as SharedGitContextConfigError,load_git_context as shared_load_git_context,resolve_profile_for_path

VERSION = "1.0.0"

CLEAN = 0
VIOLATIONS_FOUND = 1
INVALID_INPUT_OR_CONFIG = 2
GIT_ERROR = 3

DEFAULT_GIT_CONTEXT_CONFIG = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "git-context" / "contexts.toml"

DEFAULT_AUDIT_CONFIG = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "git-provenance-audit" / "config.toml"

SKIP_DIRS = {
    ".cache", ".gradle", ".idea", ".m2", ".repo-patch",
    "build", "dist", "node_modules", "out", "target", "vendor",
}

class AuditError(RuntimeError):
    exit_code = INVALID_INPUT_OR_CONFIG

class ConfigError(AuditError):
    pass

class GitCommandError(AuditError):
    exit_code = GIT_ERROR

@dataclasses.dataclass
class AllowRules:
    names: set[str] = dataclasses.field(default_factory=set)
    emails: set[str] = dataclasses.field(default_factory=set)
    identities: set[tuple[str, str]] = dataclasses.field(default_factory=set)

@dataclasses.dataclass
class RepoRule:
    path: Path
    allowed_names: set[str] = dataclasses.field(default_factory=set)
    allowed_emails: set[str] = dataclasses.field(default_factory=set)
    allowed_identities: set[tuple[str, str]] = dataclasses.field(default_factory=set)
    allowed_profiles: set[str] = dataclasses.field(default_factory=set)

@dataclasses.dataclass
class AuditConfig:
    strict: bool
    allow: AllowRules
    repositories: list[RepoRule]

@dataclasses.dataclass(frozen=True)
class Commit:
    sha: str
    author_name: str
    author_email: str
    committer_name: str
    committer_email: str
    subject: str

@dataclasses.dataclass(frozen=True)
class Finding:
    severity: str
    repo: str
    commit: str
    role: str
    actual_name: str
    actual_email: str
    expected_profile: str
    expected_name: str
    expected_email: str
    reason: str

@dataclasses.dataclass
class RepoResult:
    repo: str
    context: str | None
    profile: str | None
    expected_name: str | None
    expected_email: str | None
    commits_scanned: int
    findings: list[Finding]
    warnings: list[str]

def expand(path: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()

def run_git(repo: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        cp = subprocess.run(
            ["git", "-C", str(repo), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise GitCommandError("git executable not found") from e
    if check and cp.returncode != 0:
        raise GitCommandError(
            f"{repo}: git {' '.join(args)}: "
            + (cp.stderr.strip() or cp.stdout.strip() or f"exit {cp.returncode}")
        )
    return cp

def safe_str_set(value: Any, field: str) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ConfigError(f"{field} must be an array of strings")
    return {x.strip() for x in value if x.strip()}

def load_git_context(path: Path) -> GitContextModel:
    try: return shared_load_git_context(path)
    except SharedGitContextConfigError as e: raise ConfigError(str(e)) from e

def load_audit_config(path: Path | None, cli_strict: bool) -> AuditConfig:
    if path is None or not path.exists():
        return AuditConfig(strict=cli_strict, allow=AllowRules(), repositories=[])

    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read audit config {path}: {e}") from e

    if raw.get("version", 1) != 1:
        raise ConfigError("audit config must contain version = 1")

    allow_raw = raw.get("allow", {})
    if not isinstance(allow_raw, dict):
        raise ConfigError("[allow] must be a table")

    allow = AllowRules(
        names=safe_str_set(allow_raw.get("names"), "allow.names"),
        emails=safe_str_set(allow_raw.get("emails"), "allow.emails"),
    )

    identities_raw = allow_raw.get("identity", [])
    if identities_raw is None:
        identities_raw = []
    if not isinstance(identities_raw, list):
        raise ConfigError("allow.identity must be an array of tables")
    for idx, item in enumerate(identities_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"allow.identity[{idx}] must be a table")
        name = str(item.get("name", "")).strip()
        email = str(item.get("email", "")).strip()
        if not name or not email:
            raise ConfigError(f"allow.identity[{idx}] requires name and email")
        allow.identities.add((name, email))

    repo_rules: list[RepoRule] = []
    repos_raw = raw.get("repository", [])
    if repos_raw is None:
        repos_raw = []
    if not isinstance(repos_raw, list):
        raise ConfigError("repository must be an array of tables")

    for idx, item in enumerate(repos_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"repository[{idx}] must be a table")
        path_value = item.get("path")
        if not path_value:
            raise ConfigError(f"repository[{idx}] requires path")
        rule = RepoRule(
            path=expand(str(path_value)),
            allowed_names=safe_str_set(item.get("allowed_names"), f"repository[{idx}].allowed_names"),
            allowed_emails=safe_str_set(item.get("allowed_emails"), f"repository[{idx}].allowed_emails"),
            allowed_profiles=safe_str_set(item.get("allowed_profiles"), f"repository[{idx}].allowed_profiles"),
        )
        identities = item.get("allowed_identity", [])
        if identities is None:
            identities = []
        if not isinstance(identities, list):
            raise ConfigError(f"repository[{idx}].allowed_identity must be an array of tables")
        for j, ident in enumerate(identities):
            if not isinstance(ident, dict):
                raise ConfigError(f"repository[{idx}].allowed_identity[{j}] must be a table")
            n = str(ident.get("name", "")).strip()
            e = str(ident.get("email", "")).strip()
            if not n or not e:
                raise ConfigError(f"repository[{idx}].allowed_identity[{j}] requires name and email")
            rule.allowed_identities.add((n, e))
        repo_rules.append(rule)

    repo_rules.sort(key=lambda r: len(r.path.parts), reverse=True)
    return AuditConfig(
        strict=bool(raw.get("strict", False)) or cli_strict,
        allow=allow,
        repositories=repo_rules,
    )

def path_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False

def local_profile_pin(repo: Path) -> str | None:
    cp=run_git(repo,["config","--local","--get","git-context.profile"],check=False)
    return cp.stdout.strip() if cp.returncode==0 and cp.stdout.strip() else None

def resolve_profile(model: GitContextModel, repo: Path):
    try: return resolve_profile_for_path(model,repo,local_profile_pin(repo))
    except SharedGitContextConfigError as e: raise ConfigError(f"{repo}: {e}") from e

def matching_repo_rule(config: AuditConfig, repo: Path) -> RepoRule | None:
    for rule in config.repositories:
        if repo.resolve() == rule.path.resolve() or path_under(repo, rule.path):
            return rule
    return None

def discover_repositories(root: Path, depth: int) -> list[Path]:
    if depth < 0:
        raise ConfigError("--depth must be >= 0")
    root = root.resolve()
    repositories: list[Path] = []

    def visit(directory: Path, current_depth: int) -> None:
        marker = directory / ".git"
        if marker.is_dir() or marker.is_file():
            repositories.append(directory)
            return
        if current_depth >= depth:
            return
        try:
            children = sorted(
                (p for p in directory.iterdir() if p.is_dir() and p.name not in SKIP_DIRS),
                key=lambda p: p.name,
            )
        except OSError:
            return
        for child in children:
            visit(child, current_depth + 1)

    visit(root, 0)
    return sorted(set(repositories), key=str)

LOG_FORMAT = "%H%x00%an%x00%ae%x00%cn%x00%ce%x00%s"

def parse_log(raw: str) -> list[Commit]:
    commits = []
    for record in raw.splitlines():
        if not record:
            continue
        parts = record.split("\x00")
        if len(parts) != 6:
            raise GitCommandError("unexpected git log output shape")
        commits.append(Commit(
            parts[0], parts[1], parts[2],
            parts[3], parts[4], parts[5],
        ))
    return commits

def branches_for(repo: Path, requested: list[str]) -> list[str]:
    if requested:
        for branch in requested:
            cp = run_git(repo, ["rev-parse", "--verify", "--quiet", branch], check=False)
            if cp.returncode != 0:
                raise GitCommandError(f"{repo}: branch/ref not found: {branch}")
        return list(requested)
    cp = run_git(repo, ["rev-parse", "--verify", "--quiet", "HEAD"], check=False)
    return ["HEAD"] if cp.returncode == 0 else []

def commits_for(repo: Path, branches: list[str], authors: list[str], limit: int) -> list[Commit]:
    if limit < 0:
        raise ConfigError("--commits must be >= 0")
    seen: dict[str, Commit] = {}
    author_filters: list[str | None] = authors or [None]
    for branch in branches:
        for author in author_filters:
            args = ["log", branch, f"--format={LOG_FORMAT}"]
            if limit:
                args += ["-n", str(limit)]
            if author:
                args += [f"--author={author}"]
            cp = run_git(repo, args)
            for commit in parse_log(cp.stdout):
                seen.setdefault(commit.sha, commit)
    return sorted(seen.values(), key=lambda c: c.sha)

def identity_allowed(name: str, email: str, expected: Profile | None,
                     model: GitContextModel, config: AuditConfig,
                     repo_rule: RepoRule | None) -> bool:
    if expected and name == expected.name and email == expected.email:
        return True
    if name in config.allow.names or email in config.allow.emails:
        return True
    if (name, email) in config.allow.identities:
        return True
    if repo_rule:
        if name in repo_rule.allowed_names or email in repo_rule.allowed_emails:
            return True
        if (name, email) in repo_rule.allowed_identities:
            return True
        for key in repo_rule.allowed_profiles:
            profile = model.profiles.get(key)
            if profile is None:
                raise ConfigError(f"{repo_rule.path}: unknown allowed profile {key!r}")
            if name == profile.name and email == profile.email:
                return True
    return False

def audit_repo(repo: Path, model: GitContextModel, config: AuditConfig,
               branches: list[str], authors: list[str], commit_limit: int) -> RepoResult:
    expected, context = resolve_profile(model, repo)
    rule = matching_repo_rule(config, repo)
    warnings: list[str] = []
    findings: list[Finding] = []
    selected = branches_for(repo, branches)
    commits = commits_for(repo, selected, authors, commit_limit) if selected else []

    if expected is None:
        msg = "repository has no resolvable git-context profile"
        if config.strict:
            findings.append(Finding(
                "violation", str(repo), "", "repository", "", "", "", "", "", msg
            ))
        else:
            warnings.append(msg)

    for commit in commits:
        for role, name, email in (
            ("author", commit.author_name, commit.author_email),
            ("committer", commit.committer_name, commit.committer_email),
        ):
            if identity_allowed(name, email, expected, model, config, rule):
                continue
            if expected is None and not config.strict:
                warnings.append(f"{commit.sha[:12]} {role}: unclassified identity {name} <{email}>")
                continue
            findings.append(Finding(
                "violation", str(repo), commit.sha, role,
                name, email,
                expected.key if expected else "",
                expected.name if expected else "",
                expected.email if expected else "",
                "identity does not match expected profile or allowlist"
                if expected else
                "identity is not allowlisted in strict unclassified repository",
            ))

    return RepoResult(
        str(repo), context,
        expected.key if expected else None,
        expected.name if expected else None,
        expected.email if expected else None,
        len(commits),
        sorted(findings, key=lambda f: (f.commit, f.role, f.actual_email, f.actual_name)),
        sorted(set(warnings)),
    )

def render_text(results: list[RepoResult], out: TextIO) -> None:
    for result in results:
        expected = (
            f"{result.profile}: {result.expected_name} <{result.expected_email}>"
            if result.profile else "UNCLASSIFIED"
        )
        print(f"REPO {result.repo}", file=out)
        print(f"  context: {result.context or '-'}", file=out)
        print(f"  expected: {expected}", file=out)
        print(f"  commits: {result.commits_scanned}", file=out)
        for warning in result.warnings:
            print(f"  WARN {warning}", file=out)
        for finding in result.findings:
            commit = finding.commit[:12] if finding.commit else "-"
            print(
                f"  VIOLATION {commit} {finding.role}: "
                f"{finding.actual_name} <{finding.actual_email}> ({finding.reason})",
                file=out,
            )
    print(
        f"SUMMARY repos={len(results)} "
        f"commits={sum(r.commits_scanned for r in results)} "
        f"violations={sum(len(r.findings) for r in results)} "
        f"warnings={sum(len(r.warnings) for r in results)}",
        file=out,
    )

def render_json(results: list[RepoResult], out: TextIO) -> None:
    payload = {
        "tool": "git-provenance-audit",
        "version": VERSION,
        "summary": {
            "repositories": len(results),
            "commits_scanned": sum(r.commits_scanned for r in results),
            "violations": sum(len(r.findings) for r in results),
            "warnings": sum(len(r.warnings) for r in results),
        },
        "repositories": [
            {
                "repo": r.repo,
                "context": r.context,
                "profile": r.profile,
                "expected": (
                    {"name": r.expected_name, "email": r.expected_email}
                    if r.profile else None
                ),
                "commits_scanned": r.commits_scanned,
                "warnings": r.warnings,
                "findings": [dataclasses.asdict(f) for f in r.findings],
            }
            for r in results
        ],
    }
    json.dump(payload, out, indent=2)
    out.write("\n")

CSV_FIELDS = [
    "severity", "repo", "commit", "role",
    "actual_name", "actual_email",
    "expected_profile", "expected_name", "expected_email",
    "reason",
]

def render_csv(results: list[RepoResult], out: TextIO) -> None:
    writer = csv.DictWriter(out, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for result in results:
        for finding in result.findings:
            writer.writerow(dataclasses.asdict(finding))
        for warning in result.warnings:
            writer.writerow({
                "severity": "warning",
                "repo": result.repo,
                "commit": "",
                "role": "repository",
                "actual_name": "",
                "actual_email": "",
                "expected_profile": result.profile or "",
                "expected_name": result.expected_name or "",
                "expected_email": result.expected_email or "",
                "reason": warning,
            })

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="git-provenance-audit",
        description="Read-only Git author/committer provenance audit against git-context identity rules.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  git-provenance-audit ~/dev
  git-provenance-audit ~/dev --depth 3 --commits 100
  git-provenance-audit ~/dev --branch main --strict
  git-provenance-audit ~/dev --author '@tembeek.com$' --format json
""",
    )
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--version", action="version", version=f"git-provenance-audit {VERSION}")
    p.add_argument("--git-context-config", default=str(DEFAULT_GIT_CONTEXT_CONFIG))
    p.add_argument("--config")
    p.add_argument("--depth", type=int, default=4)
    p.add_argument("--commits", type=int, default=0)
    p.add_argument("--branch", action="append", default=[])
    p.add_argument("--author", action="append", default=[])
    p.add_argument("--strict", action="store_true")
    p.add_argument("--format", choices=["text", "json", "csv"], default="text")
    return p

def main() -> int:
    args = parser().parse_args()
    try:
        root = expand(args.root)
        if not root.is_dir():
            raise ConfigError(f"root is not a directory: {root}")

        model = load_git_context(expand(args.git_context_config))

        if args.config:
            audit_path = expand(args.config)
            if not audit_path.is_file():
                raise ConfigError(f"audit config not found: {audit_path}")
        else:
            audit_path = DEFAULT_AUDIT_CONFIG if DEFAULT_AUDIT_CONFIG.exists() else None

        config = load_audit_config(audit_path, args.strict)
        repos = discover_repositories(root, args.depth)

        if not repos:
            cp = run_git(root, ["rev-parse", "--is-inside-work-tree"], check=False)
            if cp.returncode == 0 and cp.stdout.strip() == "true":
                top = Path(run_git(root, ["rev-parse", "--show-toplevel"]).stdout.strip()).resolve()
                repos = [top]

        results = [
            audit_repo(repo, model, config, args.branch, args.author, args.commits)
            for repo in repos
        ]

        if args.format == "json":
            render_json(results, sys.stdout)
        elif args.format == "csv":
            render_csv(results, sys.stdout)
        else:
            render_text(results, sys.stdout)

        return VIOLATIONS_FOUND if any(r.findings for r in results) else CLEAN

    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return INVALID_INPUT_OR_CONFIG
    except GitCommandError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return GIT_ERROR
    except AuditError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return e.exit_code

if __name__ == "__main__":
    raise SystemExit(main())
