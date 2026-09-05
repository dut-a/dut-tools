#!/usr/bin/env python3
from __future__ import annotations

import argparse
import configparser
import dataclasses
import json
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
import time
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.git_context_model import (
    ConfigError as SharedGitContextConfigError,
    load_git_context as shared_load_git_context,
    resolve_profile_for_path,
)
from typing import Any

VERSION = "1.5.0"
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
BASE_DIR = CONFIG_HOME / "git-context"
DEFAULT_CONTEXTS = BASE_DIR / "contexts.toml"
DEFAULT_MACHINE = BASE_DIR / "machine.toml"
GENERATED = BASE_DIR / "generated"
BACKUPS = BASE_DIR / "backups"
PIN_KEY = "git-context.profile"

STARTER_CONTEXTS = """\
version = 1

[settings]
remove_global_identity = true
use_config_only = true

[profiles.tembeek]
name = "Dut Athian"
email = "dut.dev@tembeek.com"

[profiles.clients]
name = "Dut Athian"
email = "dut.clients@tembeek.com"

[[contexts]]
name = "tembeek"
root = "~/dev/tembeek"
profile = "tembeek"

[[contexts]]
name = "clients"
root = "~/dev/clients"
profile = "clients"
"""

STARTER_MACHINE = """\
version = 1

[workspace]
roots = ["~/dev/tembeek", "~/dev/clients"]

[ssh]
include = "~/.ssh/config.d/git-context.conf"

[completion]
directory = "~/.config/git-context/completions"
"""

class GitContextError(RuntimeError):
    pass

def run(args: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args, cwd=str(cwd) if cwd else None, check=check,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except FileNotFoundError as e:
        raise GitContextError(f"required command not found: {args[0]}") from e
    except subprocess.CalledProcessError as e:
        msg = e.stderr.strip() or e.stdout.strip() or f"exit {e.returncode}"
        raise GitContextError(f"{' '.join(args)}: {msg}") from e

def git(args: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=cwd, check=check)

def expand(path: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()

@dataclasses.dataclass(frozen=True)
class Profile:
    key: str
    name: str
    email: str
    signing_key: str | None = None
    github_account: str | None = None
    gitlab_account: str | None = None

@dataclasses.dataclass(frozen=True)
class Context:
    name: str
    root: Path
    profile: str
    github_account: str | None = None
    gitlab_account: str | None = None

@dataclasses.dataclass
class Model:
    version: int
    profiles: dict[str, Profile]
    contexts: list[Context]
    settings: dict[str, Any]

def load_model(path: Path = DEFAULT_CONTEXTS) -> Model:
    try:
        shared = shared_load_git_context(path)
    except SharedGitContextConfigError as e:
        if not path.exists():
            raise GitContextError(f"configuration not found: {path}; run `git-context init`") from e
        raise GitContextError(str(e)) from e

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    raw_profiles = raw.get("profiles", {})
    profiles: dict[str, Profile] = {}
    for key, canonical in shared.profiles.items():
        data = raw_profiles.get(key, {})
        profiles[key] = Profile(
            key=key,
            name=canonical.name,
            email=canonical.email,
            signing_key=data.get("signing_key"),
            github_account=data.get("github_account"),
            gitlab_account=data.get("gitlab_account"),
        )

    raw_contexts = {str(x.get("name", "")): x for x in raw.get("contexts", []) if isinstance(x, dict)}
    contexts: list[Context] = []
    for canonical in shared.contexts:
        data = raw_contexts.get(canonical.name, {})
        contexts.append(Context(
            name=canonical.name,
            root=canonical.root,
            profile=canonical.profile,
            github_account=data.get("github_account"),
            gitlab_account=data.get("gitlab_account"),
        ))

    return Model(
        version=1,
        profiles=profiles,
        contexts=contexts,
        settings=dict(raw.get("settings", {})),
    )

def path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False

def repo_root(path: Path) -> Path | None:
    cp = git(["-C", str(path), "rev-parse", "--show-toplevel"], check=False)
    if cp.returncode != 0:
        return None
    return Path(cp.stdout.strip()).resolve()

def local_pin(path: Path) -> str | None:
    root = repo_root(path)
    if not root:
        return None
    cp = git(["-C", str(root), "config", "--local", "--get", PIN_KEY], check=False)
    return cp.stdout.strip() or None if cp.returncode == 0 else None

def resolve_context(model: Model, path: Path, honor_pin: bool = True) -> tuple[Context | None, Profile | None, str | None]:
    pin = local_pin(path) if honor_pin else None
    shared_model = shared_load_git_context(DEFAULT_CONTEXTS) if False else None
    # Build the canonical resolver view from the already-loaded model so resolution semantics
    # are shared without re-reading configuration.
    from shared.git_context_model import Profile as CanonicalProfile, Context as CanonicalContext, GitContextModel
    canonical = GitContextModel(
        profiles={k: CanonicalProfile(k, v.name, v.email) for k, v in model.profiles.items()},
        contexts=[CanonicalContext(c.name, c.root, c.profile) for c in model.contexts],
    )
    try:
        selected, source = resolve_profile_for_path(canonical, path.resolve(), pin)
    except SharedGitContextConfigError as e:
        raise GitContextError(str(e)) from e

    if selected is None:
        return None, None, None
    profile = model.profiles[selected.key]
    if source and source.startswith("pin:"):
        return None, profile, selected.key
    ctx = next((c for c in model.contexts if c.name == source), None)
    return ctx, profile, None

def init_files(args: argparse.Namespace) -> int:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    created = []
    if not DEFAULT_CONTEXTS.exists():
        DEFAULT_CONTEXTS.write_text(STARTER_CONTEXTS, encoding="utf-8")
        created.append(DEFAULT_CONTEXTS)
    if not DEFAULT_MACHINE.exists():
        DEFAULT_MACHINE.write_text(STARTER_MACHINE, encoding="utf-8")
        created.append(DEFAULT_MACHINE)
    model = load_model()
    for ctx in model.contexts:
        ctx.root.mkdir(parents=True, exist_ok=True)
    for p in created:
        print(f"CREATE {p}")
    if not created:
        print("git-context configuration already initialized.")
    return 0

def profile_gitconfig(profile: Profile) -> str:
    lines = [
        "[user]",
        f"\tname = {profile.name}",
        f"\temail = {profile.email}",
    ]
    if profile.signing_key:
        lines.append(f"\tsigningKey = {profile.signing_key}")
    return "\n".join(lines) + "\n"

def managed_gitconfig(model: Model) -> str:
    out = [
        "# Generated by git-context. DO NOT EDIT.",
        "[user]",
        "\tuseConfigOnly = true",
        "",
    ]
    for ctx in sorted(model.contexts, key=lambda c: len(c.root.parts)):
        profile_file = GENERATED / "profiles" / f"{ctx.profile}.gitconfig"
        gitdir = str(ctx.root).rstrip("/") + "/"
        out += [
            f'[includeIf "gitdir:{gitdir}"]',
            f"\tpath = {profile_file}",
            "",
        ]
    return "\n".join(out)

def backup_global_identity() -> None:
    vals = {}
    for key in ("user.name", "user.email"):
        cp = git(["config", "--global", "--get", key], check=False)
        if cp.returncode == 0 and cp.stdout.strip():
            vals[key] = cp.stdout.strip()
    if not vals:
        return
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = BACKUPS / f"global-identity-{stamp}.json"
    path.write_text(json.dumps(vals, indent=2) + "\n", encoding="utf-8")

def ensure_global_include(path: Path) -> None:
    cp = git(["config", "--global", "--get-all", "include.path"], check=False)
    includes = [Path(os.path.expanduser(x.strip())).resolve() for x in cp.stdout.splitlines() if x.strip()]
    if path.resolve() not in includes:
        git(["config", "--global", "--add", "include.path", str(path)])

def apply_config(args: argparse.Namespace) -> int:
    model = load_model()
    GENERATED.mkdir(parents=True, exist_ok=True)
    (GENERATED / "profiles").mkdir(parents=True, exist_ok=True)

    for key, profile in model.profiles.items():
        (GENERATED / "profiles" / f"{key}.gitconfig").write_text(profile_gitconfig(profile), encoding="utf-8")

    managed = GENERATED / "git-context.gitconfig"
    managed.write_text(managed_gitconfig(model), encoding="utf-8")
    (GENERATED / "allowed_signers").touch(exist_ok=True)

    if model.settings.get("remove_global_identity", True):
        backup_global_identity()
        for key in ("user.name", "user.email"):
            git(["config", "--global", "--unset-all", key], check=False)

    if model.settings.get("use_config_only", True):
        git(["config", "--global", "user.useConfigOnly", "true"])

    ensure_global_include(managed)
    print(f"Generated {managed}")
    print("Applied managed global Git include.")
    return 0

def current(args: argparse.Namespace) -> int:
    model = load_model()
    path = expand(args.path or os.getcwd())
    ctx, profile, pin = resolve_context(model, path)
    if not profile:
        print(f"UNCLASSIFIED {path}")
        return 1
    print(f"path: {path}")
    print(f"context: {ctx.name if ctx else '(manual pin)'}")
    print(f"profile: {profile.key}")
    print(f"name: {profile.name}")
    print(f"email: {profile.email}")
    if pin:
        print(f"pin: {pin}")
    return 0

def explain(args: argparse.Namespace) -> int:
    model = load_model()
    path = expand(args.path or os.getcwd())
    print(f"path: {path}")
    pin = local_pin(path)
    if pin:
        print(f"repository pin: {pin}")
    for ctx in model.contexts:
        match = path_is_under(path, ctx.root)
        print(f"{'MATCH' if match else 'skip ':5}  {ctx.name:<20} {ctx.root} -> {ctx.profile}")
    ctx, profile, pin = resolve_context(model, path)
    if profile:
        print(f"resolved: {profile.key}")
        return 0
    print("resolved: UNCLASSIFIED")
    return 1

def pin(args: argparse.Namespace) -> int:
    model = load_model()
    if args.profile not in model.profiles:
        raise GitContextError(f"unknown profile {args.profile!r}")
    root = repo_root(expand(os.getcwd()))
    if not root:
        raise GitContextError("current directory is not inside a Git repository")
    git(["-C", str(root), "config", "--local", PIN_KEY, args.profile])
    print(f"Pinned {root} -> {args.profile}")
    return 0

def unpin(args: argparse.Namespace) -> int:
    root = repo_root(expand(os.getcwd()))
    if not root:
        raise GitContextError("current directory is not inside a Git repository")
    git(["-C", str(root), "config", "--local", "--unset-all", PIN_KEY], check=False)
    print(f"Removed profile pin from {root}")
    return 0

def load_machine() -> dict[str, Any]:
    if not DEFAULT_MACHINE.exists():
        return {}
    with DEFAULT_MACHINE.open("rb") as fh:
        raw = tomllib.load(fh)
    if raw.get("version") != 1:
        raise GitContextError("machine.toml must contain `version = 1`")
    return raw

def machine_bootstrap(args: argparse.Namespace) -> int:
    if not DEFAULT_CONTEXTS.exists():
        init_files(args)
    apply_config(args)
    machine = load_machine()
    for root in machine.get("workspace", {}).get("roots", []):
        p = expand(root)
        p.mkdir(parents=True, exist_ok=True)
        print(f"DIR   {p}")

    completion = machine.get("completion", {}).get("directory")
    if completion:
        p = expand(completion)
        p.mkdir(parents=True, exist_ok=True)
        print(f"DIR   {p}")

    ssh_include = machine.get("ssh", {}).get("include")
    if ssh_include:
        p = expand(ssh_include)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch(exist_ok=True)
        print(f"FILE  {p}")

    model = load_model()
    accounts = set()
    for p in model.profiles.values():
        if p.github_account: accounts.add(("github", p.github_account))
        if p.gitlab_account: accounts.add(("gitlab", p.gitlab_account))
    for c in model.contexts:
        if c.github_account: accounts.add(("github", c.github_account))
        if c.gitlab_account: accounts.add(("gitlab", c.gitlab_account))
    for host, account in sorted(accounts):
        p = BASE_DIR / "host-cli" / host / account
        p.mkdir(parents=True, exist_ok=True)
        print(f"DIR   {p}")

    if args.install_tools:
        for tool in ("gh", "glab"):
            if shutil.which(tool):
                print(f"OK    {tool} already installed")
            else:
                print(f"WARN  {tool} missing; automatic package-manager installation is intentionally not performed")
    if args.provision_keys:
        print("WARN  key provisioning is an explicit hook only; no private keys are generated automatically")
    return 0

def host_path(args: argparse.Namespace) -> int:
    if args.host not in {"github", "gitlab"}:
        raise GitContextError("host must be github or gitlab")
    p = BASE_DIR / "host-cli" / args.host / args.account
    p.mkdir(parents=True, exist_ok=True)
    print(p)
    return 0

def enter(args: argparse.Namespace) -> int:
    model = load_model()
    path = expand(args.path or os.getcwd())
    ctx, profile, pin = resolve_context(model, path)
    if not profile:
        raise GitContextError(f"no git-context matches {path}")
    lines = [f"cd {shlex.quote(str(path))}"]
    github = (ctx.github_account if ctx else None) or profile.github_account
    gitlab = (ctx.gitlab_account if ctx else None) or profile.gitlab_account
    if github:
        gh_dir = BASE_DIR / "host-cli" / "github" / github
        gh_dir.mkdir(parents=True, exist_ok=True)
        lines.append(f"export GH_CONFIG_DIR={shlex.quote(str(gh_dir))}")
    if gitlab:
        glab_dir = BASE_DIR / "host-cli" / "gitlab" / gitlab
        glab_dir.mkdir(parents=True, exist_ok=True)
        lines.append(f"export GLAB_CONFIG_DIR={shlex.quote(str(glab_dir))}")
    lines.append(f"export GIT_CONTEXT_PROFILE={shlex.quote(profile.key)}")
    print("\n".join(lines))
    return 0

SKIP_DIRS = {".cache", "node_modules", "vendor", ".gradle", ".m2", "target", "build", "dist"}

def find_repos(root: Path) -> list[Path]:
    repos = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        p = Path(dirpath)
        if ".git" in dirnames or (p / ".git").is_file():
            repos.append(p.resolve())
            if ".git" in dirnames:
                dirnames.remove(".git")
    return sorted(set(repos))

def is_dirty(repo: Path) -> bool:
    cp = git(["-C", str(repo), "status", "--porcelain"], check=False)
    return bool(cp.stdout.strip()) if cp.returncode == 0 else False

def audit(args: argparse.Namespace) -> int:
    model = load_model()
    root = expand(args.root or Path.home())
    repos = find_repos(root)
    for repo in repos:
        ctx, profile, pin = resolve_context(model, repo)
        status = "DIRTY" if is_dirty(repo) else "clean"
        resolved = profile.key if profile else "UNCLASSIFIED"
        pin_text = pin or "-"
        print(f"{status:<5}  {resolved:<16} pin={pin_text:<12} {repo}")
    return 0

def relocate(args: argparse.Namespace) -> int:
    src = expand(args.source)
    dst = expand(args.destination)
    home = Path.home().resolve()
    if not src.exists():
        raise GitContextError(f"source does not exist: {src}")
    if not args.allow_outside_home and (not path_is_under(src, home) or not path_is_under(dst, home)):
        raise GitContextError("source/destination must remain under home; use --allow-outside-home deliberately")
    if dst.exists():
        raise GitContextError(f"destination already exists: {dst}")
    repo = repo_root(src)
    if repo != src:
        raise GitContextError("source must be a Git repository root")
    if is_dirty(src) and not args.allow_dirty:
        raise GitContextError("repository is dirty; commit/stash first or use --allow-dirty")
    print(f"PLAN  {src} -> {dst}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to move.")
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    print(f"MOVED {dst}")
    return 0

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="git-context",
        description="Directory-driven Git identity and account context manager.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          git-context init
          git-context apply
          git-context current
          git-context explain ~/dev/tembeek/project
          git-context pin tembeek
          git-context machine bootstrap
          eval "$(git-context enter ~/dev/tembeek/project)"
          git-context audit ~/dev
          git-context relocate ~/old/repo ~/dev/tembeek/repo
        """)
    )
    p.add_argument("--version", action="version", version=f"git-context {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    q = sub.add_parser("init", help="Create starter configuration.")
    q.set_defaults(func=init_files)

    q = sub.add_parser("apply", help="Generate and apply managed Git configuration.")
    q.set_defaults(func=apply_config)

    q = sub.add_parser("current", help="Show resolved context.")
    q.add_argument("path", nargs="?")
    q.set_defaults(func=current)

    q = sub.add_parser("explain", help="Explain context resolution.")
    q.add_argument("path", nargs="?")
    q.set_defaults(func=explain)

    q = sub.add_parser("pin", help="Pin current repository to a profile.")
    q.add_argument("profile")
    q.set_defaults(func=pin)

    q = sub.add_parser("unpin", help="Remove current repository profile pin.")
    q.set_defaults(func=unpin)

    machine = sub.add_parser("machine", help="Machine bootstrap operations.")
    msub = machine.add_subparsers(dest="machine_command", required=True)
    q = msub.add_parser("bootstrap")
    q.add_argument("--install-tools", action="store_true")
    q.add_argument("--provision-keys", action="store_true")
    q.set_defaults(func=machine_bootstrap)

    host = sub.add_parser("host", help="Host CLI isolation operations.")
    hsub = host.add_subparsers(dest="host_command", required=True)
    q = hsub.add_parser("path")
    q.add_argument("host", choices=["github", "gitlab"])
    q.add_argument("account")
    q.set_defaults(func=host_path)

    q = sub.add_parser("enter", help="Emit shell commands for a context-aware shell.")
    q.add_argument("path", nargs="?")
    q.set_defaults(func=enter)

    q = sub.add_parser("audit", help="Audit Git repositories under a directory.")
    q.add_argument("root", nargs="?")
    q.set_defaults(func=audit)

    q = sub.add_parser("relocate", help="Plan/apply a safe repository relocation.")
    q.add_argument("source")
    q.add_argument("destination")
    q.add_argument("--apply", action="store_true")
    q.add_argument("--allow-dirty", action="store_true")
    q.add_argument("--allow-outside-home", action="store_true")
    q.set_defaults(func=relocate)
    return p



def main() -> int:
    try:
        args = parser().parse_args()
        return int(args.func(args) or 0)
    except GitContextError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
