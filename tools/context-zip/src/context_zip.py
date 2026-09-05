#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import hashlib
import json
import os
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path
from typing import Any, Iterable

VERSION = "2.0.1"
DEFAULT_CONFIG_FILE = ".context-zip.json"

COMMON_EXCLUDES = [
    ".git/**", ".idea/**", ".vscode/**",
    "node_modules/**", "vendor/**",
    "target/**", "build/**", "dist/**", "out/**",
    ".gradle/**", ".mvn/wrapper/maven-wrapper.jar",
    ".DS_Store", "*.log", "*.tmp", "*.swp", "*.swo",
    ".env", ".env.*", "**/.env", "**/.env.*",
    "**/__pycache__/**", "**/*.pyc",
]

SPRING_DEFAULT_INCLUDES = [
    "pom.xml", "mvnw", "mvnw.cmd", ".mvn/**",
    "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
    "gradlew", "gradlew.bat", "gradle/**",
    "src/main/**", "src/test/**",
    "openapi/**", "api/**", "docs/**",
    "Dockerfile", "docker-compose*.yml", "docker-compose*.yaml",
    ".github/**", "Makefile", "README*", "CHANGELOG*",
]

PHP_DEFAULT_INCLUDES = [
    "composer.json", "composer.lock", "artisan",
    "app/**", "bootstrap/**", "config/**", "database/**",
    "routes/**", "resources/**", "tests/**",
    "public/**/*.php", "public/**/*.js", "public/**/*.css",
    "openapi/**", "api/**", "docs/**",
    "phpunit.xml", "phpunit.xml.dist",
    "Dockerfile", "docker-compose*.yml", "docker-compose*.yaml",
    ".github/**", "Makefile", "README*", "CHANGELOG*",
]

BINARY_EXTENSIONS = {
    ".7z", ".a", ".avi", ".bin", ".bmp", ".class", ".db", ".dll", ".dmg",
    ".doc", ".docx", ".exe", ".gif", ".gz", ".ico", ".jar", ".jpeg", ".jpg",
    ".mov", ".mp3", ".mp4", ".o", ".pdf", ".png", ".ppt", ".pptx", ".pyc",
    ".so", ".sqlite", ".tar", ".tgz", ".ttf", ".woff", ".woff2", ".xls",
    ".xlsx", ".zip",
}

class ContextZipError(RuntimeError):
    pass

@dataclasses.dataclass
class Settings:
    stack: str = "auto"
    whole_project: bool = False
    include_untracked: bool = False
    include_binaries: bool = False
    max_file_mb: float = 4.0
    include: list[str] = dataclasses.field(default_factory=list)
    exclude: list[str] = dataclasses.field(default_factory=list)
    output: str | None = None

def git(args: list[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(["git", *args], 127, "", "git not found")

def git_root(path: Path) -> Path | None:
    cp = git(["rev-parse", "--show-toplevel"], path)
    return Path(cp.stdout.strip()).resolve() if cp.returncode == 0 and cp.stdout.strip() else None

def detect_stack(root: Path) -> str:
    spring_markers = [
        root / "pom.xml", root / "build.gradle", root / "build.gradle.kts",
        root / "src" / "main" / "java", root / "src" / "main" / "kotlin",
    ]
    php_markers = [
        root / "composer.json", root / "artisan", root / "app",
        root / "routes", root / "public" / "index.php",
    ]
    spring_score = sum(1 for p in spring_markers if p.exists())
    php_score = sum(1 for p in php_markers if p.exists())
    if spring_score == php_score == 0:
        raise ContextZipError("could not auto-detect Spring or PHP project; use --stack")
    if spring_score == php_score:
        raise ContextZipError("project looks both Spring and PHP; use --stack explicitly")
    return "spring" if spring_score > php_score else "php"

def load_config(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ContextZipError(f"invalid JSON config {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ContextZipError("config root must be a JSON object")
    allowed = {
        "version", "stack", "whole_project", "include_untracked",
        "include_binaries", "max_file_mb", "include", "exclude", "output"
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ContextZipError(f"unknown config keys: {', '.join(unknown)}")
    if raw.get("version", 1) != 1:
        raise ContextZipError("config version must be 1")
    for key in ("include", "exclude"):
        if key in raw and (not isinstance(raw[key], list) or not all(isinstance(x, str) for x in raw[key])):
            raise ContextZipError(f"{key} must be an array of strings")
    return raw

def config_patterns(config: dict[str, Any], key: str) -> list[str]:
    value = config.get(key, [])
    return list(value) if isinstance(value, list) else []

def resolve_config_path(root: Path, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser().resolve()
    default = root / DEFAULT_CONFIG_FILE
    return default if default.exists() else None

def merge_settings(config: dict[str, Any], args: argparse.Namespace) -> Settings:
    def val(name: str, default: Any):
        cli = getattr(args, name, None)
        return cli if cli is not None else config.get(name, default)

    include = config_patterns(config, "include") + list(args.include or [])
    exclude = config_patterns(config, "exclude") + list(args.exclude or [])
    return Settings(
        stack=val("stack", "auto"),
        whole_project=bool(val("whole_project", False)),
        include_untracked=bool(val("include_untracked", False)),
        include_binaries=bool(val("include_binaries", False)),
        max_file_mb=float(val("max_file_mb", 4.0)),
        include=include,
        exclude=exclude,
        output=val("output", None),
    )

def write_default_config(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise ContextZipError(f"{path} already exists; use --force to replace")
    data = {
        "version": 1,
        "stack": "auto",
        "whole_project": False,
        "include_untracked": False,
        "include_binaries": False,
        "max_file_mb": 4,
        "include": [],
        "exclude": COMMON_EXCLUDES,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

def canonical(path: Path) -> Path:
    """Resolve aliases/symlinks before path-identity comparisons."""
    return path.expanduser().resolve()

def rel(p: Path, root: Path) -> str:
    return canonical(p).relative_to(canonical(root)).as_posix()

def matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch("/" + path, pat) for pat in patterns)

def tracked_files(root: Path, include_untracked: bool) -> list[Path] | None:
    cp = git(["ls-files", "-z"], root)
    if cp.returncode != 0:
        return None
    names = [n for n in cp.stdout.split("\0") if n]
    if include_untracked:
        cp2 = git(["ls-files", "--others", "--exclude-standard", "-z"], root)
        if cp2.returncode == 0:
            names.extend(n for n in cp2.stdout.split("\0") if n)
    return sorted({(root / n).resolve() for n in names if (root / n).is_file()})

def walk_files(root: Path) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        # prune obvious huge/generated dirs early
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", "vendor", "target", "build", "dist", ".gradle"}]
        base = Path(dirpath)
        for name in filenames:
            out.append((base / name).resolve())
    return sorted(out)

def is_probably_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    try:
        sample = path.read_bytes()[:8192]
    except OSError:
        return True
    return b"\x00" in sample

def default_includes(stack: str) -> list[str]:
    return SPRING_DEFAULT_INCLUDES if stack == "spring" else PHP_DEFAULT_INCLUDES

def select_files(root: Path, settings: Settings, stack: str) -> tuple[list[Path], list[tuple[str, str]]]:
    candidates = tracked_files(root, settings.include_untracked)
    if candidates is None:
        candidates = walk_files(root)

    exclusions = COMMON_EXCLUDES + settings.exclude
    includes = settings.include
    defaults = default_includes(stack)
    selected: list[Path] = []
    excluded: list[tuple[str, str]] = []
    max_bytes = int(settings.max_file_mb * 1024 * 1024)

    for p in candidates:
        try:
            rp = rel(p, root)
        except ValueError:
            continue
        if matches(rp, exclusions):
            excluded.append((rp, "excluded-pattern"))
            continue

        included_by_user = bool(includes and matches(rp, includes))
        if not settings.whole_project and not included_by_user and not matches(rp, defaults):
            excluded.append((rp, "outside-default-context"))
            continue
        if includes and not settings.whole_project and not included_by_user and not matches(rp, defaults):
            excluded.append((rp, "not-included"))
            continue

        try:
            size = p.stat().st_size
        except OSError:
            excluded.append((rp, "unreadable"))
            continue
        if size > max_bytes:
            excluded.append((rp, f"too-large>{settings.max_file_mb:g}MiB"))
            continue
        if not settings.include_binaries and is_probably_binary(p):
            excluded.append((rp, "binary"))
            continue
        selected.append(p)

    return sorted(selected, key=lambda p: rel(p, root)), sorted(excluded)

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def default_output(root: Path, stack: str) -> Path:
    return root.parent / f"{root.name}-{stack}-context.zip"

def manifest(root: Path, stack: str, settings: Settings, files: list[Path]) -> dict[str, Any]:
    return {
        "tool": "context-zip",
        "version": VERSION,
        "stack": stack,
        "source_root": str(root),
        "whole_project": settings.whole_project,
        "include_untracked": settings.include_untracked,
        "include_binaries": settings.include_binaries,
        "max_file_mb": settings.max_file_mb,
        "files": [
            {"path": rel(p, root), "size": p.stat().st_size, "sha256": sha256(p)}
            for p in files
        ],
    }

def create_archive(root: Path, output: Path, stack: str, settings: Settings,
                   files: list[Path], excluded: list[tuple[str, str]], force: bool) -> None:
    if output.exists() and not force:
        raise ContextZipError(f"output exists: {output}; use --force to replace")
    output.parent.mkdir(parents=True, exist_ok=True)
    m = manifest(root, stack, settings, files)
    excluded_tsv = "path\treason\n" + "".join(f"{p}\t{r}\n" for p, r in excluded)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, arcname=rel(p, root))
        z.writestr("CONTEXT-ZIP-MANIFEST.json", json.dumps(m, indent=2) + "\n")
        z.writestr("EXCLUDED-FILES.tsv", excluded_tsv)

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="context-zip",
        description="Create a compact Spring/PHP project context archive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          context-zip --dry-run
          context-zip --stack spring --output /tmp/app-context.zip
          context-zip --stack php --whole-project --include-untracked
          context-zip --init-config
          context-zip --config .context-zip.json --print-config
          context-zip --exclude '**/*.log' --max-file-mb 10
        """)
    )
    p.add_argument("stack_command", nargs="?", choices=["spring", "php"],
                   help="Compatibility shorthand for --stack spring|php.")
    p.add_argument("--version", action="version", version=f"context-zip {VERSION}")
    p.add_argument("--stack", choices=["auto", "spring", "php"])
    p.add_argument("--config")
    p.add_argument("--init-config", action="store_true")
    p.add_argument("--print-config", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--whole-project", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--include-untracked", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--include-binaries", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--max-file-mb", type=float)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--include", action="append", default=[])
    p.add_argument("--exclude", action="append", default=[])
    p.add_argument("--output")
    p.add_argument("--root", default=".")
    return p

def main() -> int:
    try:
        args = parser().parse_args()
        root = Path(args.root).expanduser().resolve()
        if not root.is_dir():
            raise ContextZipError(f"root is not a directory: {root}")
        root = git_root(root) or root

        explicit_cfg = Path(args.config).expanduser().resolve() if args.config else None
        config_path = resolve_config_path(root, args.config)

        if args.init_config:
            target = explicit_cfg or (root / DEFAULT_CONFIG_FILE)
            write_default_config(target, args.force)
            print(f"Wrote {target}")
            return 0

        config = load_config(config_path)
        if args.stack_command and args.stack and args.stack_command != args.stack:
            raise ContextZipError("positional stack and --stack disagree")
        if args.stack_command:
            args.stack = args.stack_command

        settings = merge_settings(config, args)
        stack = settings.stack
        if stack == "auto":
            stack = detect_stack(root)
        if stack not in {"spring", "php"}:
            raise ContextZipError(f"unsupported stack: {stack}")

        if args.print_config:
            effective = dataclasses.asdict(settings)
            effective["stack"] = stack
            effective["config_file"] = str(config_path) if config_path else None
            print(json.dumps(effective, indent=2))
            return 0

        files, excluded = select_files(root, settings, stack)
        output = Path(settings.output).expanduser().resolve() if settings.output else default_output(root, stack)

        print(f"context-zip {VERSION}")
        print(f"root:     {root}")
        print(f"stack:    {stack}")
        print(f"selected: {len(files)}")
        print(f"excluded: {len(excluded)}")
        print(f"output:   {output}")

        if args.dry_run:
            for p in files:
                print(f"  + {rel(p, root)}")
            return 0

        create_archive(root, output, stack, settings, files, excluded, args.force)
        print(f"Created {output}")
        return 0
    except ContextZipError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
