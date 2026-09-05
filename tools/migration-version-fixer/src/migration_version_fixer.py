#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

TOOL_NAME = "migration_version_fixer"
VERSION = "1.0.0"

CLEAN = 0
INTERNAL_ERROR = 1
CONFLICT = 2
PROTECTED_CONFLICT = 3
CHANGES_REQUIRED = 4
CONFIG_ERROR = 5
GIT_ERROR = 6

EXIT_CODES = {
    CLEAN: "CLEAN",
    INTERNAL_ERROR: "INTERNAL_ERROR",
    CONFLICT: "CONFLICT",
    PROTECTED_CONFLICT: "PROTECTED_CONFLICT",
    CHANGES_REQUIRED: "CHANGES_REQUIRED",
    CONFIG_ERROR: "CONFIG_ERROR",
    GIT_ERROR: "GIT_ERROR",
}

DEFAULT_CONFIG = ".migration-version-fixer.toml"

FLYWAY_RE = re.compile(
    r"^(?P<prefix>V)(?P<version>[0-9]+(?:[._][0-9]+)*)(?P<sep>__)(?P<desc>.+?)(?P<ext>\.sql)$",
    re.IGNORECASE,
)
LARAVEL_RE = re.compile(
    r"^(?P<date>\d{4}_\d{2}_\d{2})_(?P<time>\d{6})_(?P<desc>.+?)(?P<ext>\.php)$"
)

DEFAULT_EXCLUDES = [
    ".git/**", "**/.git/**",
    "**/target/**", "**/build/**", "**/out/**",
    "**/node_modules/**", "**/vendor/**", "**/.gradle/**",
]

class ToolError(RuntimeError):
    exit_code = INTERNAL_ERROR

class ConfigError(ToolError):
    exit_code = CONFIG_ERROR

class GitError(ToolError):
    exit_code = GIT_ERROR

@dataclasses.dataclass(frozen=True)
class Migration:
    kind: str
    path: Path
    relative: str
    directory: str
    version_raw: str
    description: str
    protected: bool = False

@dataclasses.dataclass(frozen=True)
class Rename:
    source: Path
    destination: Path
    migration: Migration
    domain: str
    reason: str

@dataclasses.dataclass
class Settings:
    root: Path
    kind: str = "auto"
    scope: str = "auto"
    flyway_strategy: str = "auto"
    git_protect: bool = True
    git_base: str | None = None
    exclude_patterns: list[str] = dataclasses.field(default_factory=list)
    flyway_locations: list[str] = dataclasses.field(default_factory=list)

def run(cmd: list[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd, cwd=str(cwd), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check
        )
    except FileNotFoundError as e:
        raise GitError(f"required command not found: {cmd[0]}") from e
    except subprocess.CalledProcessError as e:
        raise GitError(e.stderr.strip() or e.stdout.strip() or f"{cmd[0]} failed") from e

def git(args: list[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd, check)

def git_root(path: Path) -> Path | None:
    cp = git(["rev-parse", "--show-toplevel"], path)
    if cp.returncode != 0:
        return None
    value = cp.stdout.strip()
    return Path(value).resolve() if value else None

def load_config(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError) as e:
        raise ConfigError(f"cannot read config {path}: {e}") from e
    if raw.get("version", 1) != 1:
        raise ConfigError("config version must be 1")
    return raw

def cfg_value(config: dict[str, Any], key: str, default: Any) -> Any:
    return config.get(key, default)

def normalize_patterns(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ConfigError("exclude.patterns must be an array of strings")
    return list(values)

def build_settings(args: argparse.Namespace) -> Settings:
    initial_root = Path(args.root or ".").expanduser().resolve()
    if not initial_root.exists():
        raise ConfigError(f"root does not exist: {initial_root}")
    root = git_root(initial_root) or initial_root
    config_path = Path(args.config).expanduser().resolve() if args.config else root / DEFAULT_CONFIG
    config = load_config(config_path if config_path.exists() else None)

    kind = args.kind if args.kind is not None else cfg_value(config, "kind", "auto")
    scope = args.scope if args.scope is not None else cfg_value(config, "scope", "auto")
    strategy = args.flyway_strategy if args.flyway_strategy is not None else cfg_value(config, "flyway_strategy", "auto")
    git_protect = args.git_protect if args.git_protect is not None else bool(cfg_value(config, "git_protect", True))
    git_base = args.git_base if args.git_base is not None else cfg_value(config, "git_base", None)

    if kind not in {"auto", "flyway", "laravel"}:
        raise ConfigError(f"invalid kind {kind!r}")
    if scope not in {"auto", "global", "directory"}:
        raise ConfigError(f"invalid scope {scope!r}")
    if strategy not in {"auto", "sequential", "timestamp", "compound"}:
        raise ConfigError(f"invalid flyway strategy {strategy!r}")

    flyway_cfg = config.get("flyway", {})
    if not isinstance(flyway_cfg, dict):
        raise ConfigError("[flyway] must be a table")
    locations = flyway_cfg.get("locations", [])
    if not isinstance(locations, list) or not all(isinstance(v, str) for v in locations):
        raise ConfigError("flyway.locations must be an array of strings")

    exclude_cfg = config.get("exclude", {})
    if not isinstance(exclude_cfg, dict):
        raise ConfigError("[exclude] must be a table")
    excludes = normalize_patterns(exclude_cfg.get("patterns", []))

    return Settings(
        root=root,
        kind=kind,
        scope=scope,
        flyway_strategy=strategy,
        git_protect=git_protect,
        git_base=git_base,
        exclude_patterns=excludes,
        flyway_locations=list(locations),
    )

def matches_any(rel: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(rel, p) for p in patterns)

def discover_files(settings: Settings) -> list[Path]:
    candidates: list[Path] = []
    excludes = DEFAULT_EXCLUDES + settings.exclude_patterns
    for dirpath, dirnames, filenames in os.walk(settings.root):
        base = Path(dirpath)
        rel_dir = base.relative_to(settings.root).as_posix() if base != settings.root else ""
        pruned = []
        for d in dirnames:
            rel = f"{rel_dir}/{d}".lstrip("/")
            if matches_any(rel + "/**", excludes) or d in {".git", "target", "build", "node_modules", "vendor", ".gradle"}:
                continue
            pruned.append(d)
        dirnames[:] = pruned
        for name in filenames:
            rel = (base / name).relative_to(settings.root).as_posix()
            if matches_any(rel, excludes):
                continue
            if FLYWAY_RE.match(name) or LARAVEL_RE.match(name):
                candidates.append((base / name).resolve())
    return sorted(candidates)

def protected_paths(settings: Settings) -> set[str]:
    if not settings.git_protect:
        return set()
    root = git_root(settings.root)
    if root is None:
        if settings.git_base:
            raise GitError("--git-base requires a Git repository")
        return set()

    base = settings.git_base
    if not base:
        # Prefer merge-base with common upstreams, then HEAD.
        for candidate in ("origin/main", "origin/master"):
            cp = git(["rev-parse", "--verify", "--quiet", candidate], root)
            if cp.returncode == 0:
                mb = git(["merge-base", "HEAD", candidate], root)
                if mb.returncode == 0 and mb.stdout.strip():
                    base = mb.stdout.strip()
                    break
        if not base:
            # HEAD protects committed history and still allows new untracked migrations.
            base = "HEAD"

    cp = git(["ls-tree", "-r", "--name-only", base], root)
    if cp.returncode != 0:
        raise GitError(f"cannot inspect git base {base!r}: {cp.stderr.strip()}")
    return {line.strip() for line in cp.stdout.splitlines() if line.strip()}

def parse_migrations(settings: Settings) -> list[Migration]:
    protected = protected_paths(settings)
    result = []
    for p in discover_files(settings):
        rel = p.relative_to(settings.root).as_posix()
        name = p.name
        fm = FLYWAY_RE.match(name)
        lm = LARAVEL_RE.match(name)
        if fm and settings.kind in {"auto", "flyway"}:
            result.append(Migration(
                kind="flyway", path=p, relative=rel,
                directory=p.parent.relative_to(settings.root).as_posix(),
                version_raw=fm.group("version"),
                description=fm.group("desc"),
                protected=rel in protected,
            ))
        elif lm and settings.kind in {"auto", "laravel"}:
            result.append(Migration(
                kind="laravel", path=p, relative=rel,
                directory=p.parent.relative_to(settings.root).as_posix(),
                version_raw=f"{lm.group('date')}_{lm.group('time')}",
                description=lm.group("desc"),
                protected=rel in protected,
            ))
    return result

def spring_properties_files(root: Path) -> list[Path]:
    patterns = [
        "**/application.properties",
        "**/application.yml",
        "**/application.yaml",
        "**/application-*.properties",
        "**/application-*.yml",
        "**/application-*.yaml",
    ]
    out = []
    for p in root.rglob("*"):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(rel, pat) for pat in patterns):
                out.append(p)
    return out

def extract_flyway_locations_from_text(text: str) -> list[str]:
    vals: list[str] = []
    # properties
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("spring.flyway.locations"):
            if "=" in stripped:
                rhs = stripped.split("=", 1)[1]
            elif ":" in stripped:
                rhs = stripped.split(":", 1)[1]
            else:
                continue
            vals.extend(x.strip() for x in rhs.strip(" []").split(",") if x.strip())
    # simple YAML shape:
    # spring:
    #   flyway:
    #     locations: classpath:a,classpath:b
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\s*locations\s*:", line):
            prefix = "\n".join(lines[max(0, i-6):i]).lower()
            if "flyway" in prefix and "spring" in prefix:
                rhs = line.split(":", 1)[1].strip().strip("[]")
                vals.extend(x.strip().strip("'\"") for x in rhs.split(",") if x.strip())
    return vals

def configured_flyway_locations(settings: Settings) -> list[str]:
    vals = list(settings.flyway_locations)
    for p in spring_properties_files(settings.root):
        try:
            vals.extend(extract_flyway_locations_from_text(p.read_text(encoding="utf-8")))
        except (UnicodeDecodeError, OSError):
            pass
    # deterministic unique order
    seen = set()
    out = []
    for v in vals:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out

def classpath_location_to_suffix(location: str) -> str | None:
    v = location.strip()
    if v.startswith("classpath*:"):
        v = v[len("classpath*:"):]
    elif v.startswith("classpath:"):
        v = v[len("classpath:"):]
    elif v.startswith("filesystem:"):
        return None
    else:
        return None
    return v.lstrip("/").rstrip("/")

def parse_maven_modules(root: Path) -> dict[Path, set[Path]]:
    poms = list(root.rglob("pom.xml"))
    artifact_to_module: dict[str, Path] = {}
    deps_by_module: dict[Path, set[str]] = {}
    module_paths: set[Path] = set()

    art_re = re.compile(r"<artifactId>\s*([^<]+)\s*</artifactId>")
    dep_re = re.compile(r"<dependency>.*?<artifactId>\s*([^<]+)\s*</artifactId>.*?</dependency>", re.S)

    for pom in poms:
        try:
            text = pom.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        module = pom.parent.resolve()
        module_paths.add(module)
        arts = art_re.findall(text)
        if arts:
            artifact_to_module.setdefault(arts[0].strip(), module)
        deps_by_module[module] = {x.strip() for x in dep_re.findall(text)}

    graph: dict[Path, set[Path]] = {m: set() for m in module_paths}
    for module, deps in deps_by_module.items():
        for artifact in deps:
            target = artifact_to_module.get(artifact)
            if target and target != module:
                graph[module].add(target)
    return graph

def parse_gradle_modules(root: Path) -> dict[Path, set[Path]]:
    settings_files = [p for n in ("settings.gradle", "settings.gradle.kts") for p in root.rglob(n)]
    graph: dict[Path, set[Path]] = {}
    project_map: dict[str, Path] = {}

    for sf in settings_files:
        project_root = sf.parent.resolve()
        try:
            text = sf.read_text(encoding="utf-8")
        except OSError:
            continue
        names = set(re.findall(r"""['"](:[^'"]+)['"]""", text))
        project_map[":"] = project_root
        for name in names:
            rel = name.lstrip(":").replace(":", "/")
            project_map[name] = (project_root / rel).resolve()

    for module in set(project_map.values()):
        graph.setdefault(module, set())
        for filename in ("build.gradle", "build.gradle.kts"):
            bp = module / filename
            if not bp.exists():
                continue
            try:
                text = bp.read_text(encoding="utf-8")
            except OSError:
                continue
            for ref in re.findall(r"""project\s*\(\s*['"](:[^'"]+)['"]\s*\)""", text):
                target = project_map.get(ref)
                if target and target != module:
                    graph[module].add(target)
    return graph

def transitive_closure(graph: dict[Path, set[Path]], start: Path) -> set[Path]:
    seen = set()
    stack = [start]
    while stack:
        cur = stack.pop()
        for nxt in graph.get(cur, set()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen

def containing_module(path: Path, modules: Iterable[Path]) -> Path | None:
    options = []
    for m in modules:
        try:
            path.relative_to(m)
            options.append(m)
        except ValueError:
            pass
    return max(options, key=lambda p: len(p.parts)) if options else None

def infer_auto_domains(settings: Settings, migrations: list[Migration]) -> dict[str, str]:
    """
    Return migration.relative -> domain id.

    Conservative policy:
      * explicit spring.flyway.locations sharing the same runtime suffix group together;
      * migration directories in a module plus transitive Maven/Gradle runtime project
        dependencies group into the consuming module's execution domain;
      * otherwise physical directories remain independent.
    """
    mapping = {m.relative: f"dir:{m.directory}" for m in migrations if m.kind == "flyway"}
    flyways = [m for m in migrations if m.kind == "flyway"]
    if not flyways:
        return mapping

    # Explicit Spring Flyway locations: same configured location suffix is one domain.
    suffixes = [s for loc in configured_flyway_locations(settings) if (s := classpath_location_to_suffix(loc))]
    for suffix in suffixes:
        matched = [m for m in flyways if f"/{suffix}/" in f"/{m.relative}" or m.directory.endswith(suffix)]
        if matched:
            domain = f"spring-location:{suffix}"
            for m in matched:
                mapping[m.relative] = domain

    # Maven/Gradle module dependency grouping. A consuming runtime module and migration
    # directories supplied by its transitive project deps belong to one execution domain.
    graphs = [parse_maven_modules(settings.root), parse_gradle_modules(settings.root)]
    for graph in graphs:
        if not graph:
            continue
        modules = set(graph)
        migrations_by_module: dict[Path, list[Migration]] = defaultdict(list)
        for m in flyways:
            mod = containing_module(m.path, modules)
            if mod:
                migrations_by_module[mod].append(m)
        for consumer in sorted(modules, key=lambda p: str(p)):
            runtime_modules = {consumer} | transitive_closure(graph, consumer)
            related = [m for mod in runtime_modules for m in migrations_by_module.get(mod, [])]
            if len({m.directory for m in related}) > 1:
                domain = f"runtime:{consumer.relative_to(settings.root).as_posix() or '.'}"
                for m in related:
                    mapping[m.relative] = domain

    return mapping

def domains(settings: Settings, migrations: list[Migration]) -> dict[str, list[Migration]]:
    grouped: dict[str, list[Migration]] = defaultdict(list)
    if settings.scope == "global":
        for m in migrations:
            grouped[f"{m.kind}:global"].append(m)
    elif settings.scope == "directory":
        for m in migrations:
            grouped[f"{m.kind}:dir:{m.directory}"].append(m)
    else:
        auto_map = infer_auto_domains(settings, migrations)
        for m in migrations:
            if m.kind == "flyway":
                grouped[f"flyway:{auto_map[m.relative]}"].append(m)
            else:
                grouped[f"laravel:dir:{m.directory}"].append(m)
    return dict(grouped)

def flyway_key(raw: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.split(r"[._]", raw))

def strategy_for(group: list[Migration], explicit: str) -> str:
    if explicit != "auto":
        return explicit
    versions = [m.version_raw for m in group]
    if any("_" in v or "." in v for v in versions):
        return "compound"
    if versions and all(len(v) >= 12 for v in versions):
        return "timestamp"
    return "sequential"

def compound_delimiter(group: list[Migration]) -> str:
    protected = [m for m in group if m.protected and ("_" in m.version_raw or "." in m.version_raw)]
    candidates = protected or [m for m in group if "_" in m.version_raw or "." in m.version_raw]
    if not candidates:
        return "_"
    return "_" if "_" in candidates[0].version_raw else "."

def next_compound_version(existing: list[str], delimiter: str) -> str:
    comps = [v for v in existing if delimiter in v]
    if not comps:
        return f"1{delimiter}1"
    parsed = [tuple(int(x) for x in v.split(delimiter)) for v in comps]
    width = max(len(p) for p in parsed)
    normalized = [p + (0,) * (width - len(p)) for p in parsed]
    top = max(normalized)
    vals = list(top)
    vals[-1] += 1
    return delimiter.join(str(x) for x in vals)

def timestamp_seed(group: list[Migration]) -> int:
    nums = [int(re.sub(r"\D", "", m.version_raw)) for m in group if re.sub(r"\D", "", m.version_raw).isdigit()]
    nums = [n for n in nums if n >= 10**11]
    if nums:
        return max(nums) + 1
    return int(dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S"))

def flyway_name(version: str, migration: Migration) -> str:
    return f"V{version}__{migration.description}.sql"

def laravel_timestamp(raw: str) -> dt.datetime:
    return dt.datetime.strptime(raw, "%Y_%m_%d_%H%M%S")

def laravel_name(value: dt.datetime, migration: Migration) -> str:
    return value.strftime("%Y_%m_%d_%H%M%S") + f"_{migration.description}.php"

def plan_flyway_domain(domain: str, group: list[Migration], settings: Settings) -> tuple[list[Rename], int]:
    by_version: dict[str, list[Migration]] = defaultdict(list)
    for m in group:
        by_version[m.version_raw].append(m)

    duplicates = {v: ms for v, ms in by_version.items() if len(ms) > 1}
    if not duplicates:
        return [], CLEAN

    strategy = strategy_for(group, settings.flyway_strategy)
    renames: list[Rename] = []

    protected_versions = {m.version_raw for m in group if m.protected}
    used = set(by_version)

    seq_next = max((flyway_key(v)[0] for v in used if re.fullmatch(r"\d+", v)), default=0) + 1
    timestamp_next = timestamp_seed(group)
    delimiter = compound_delimiter(group)

    def allocate() -> str:
        nonlocal seq_next, timestamp_next
        if strategy == "sequential":
            while str(seq_next) in used:
                seq_next += 1
            v = str(seq_next)
            seq_next += 1
            used.add(v)
            return v
        if strategy == "timestamp":
            while str(timestamp_next) in used:
                timestamp_next += 1
            v = str(timestamp_next)
            timestamp_next += 1
            used.add(v)
            return v
        if strategy == "compound":
            v = next_compound_version(list(used), delimiter)
            while v in used:
                parts = v.split(delimiter)
                parts[-1] = str(int(parts[-1]) + 1)
                v = delimiter.join(parts)
            used.add(v)
            return v
        raise ConfigError(f"unsupported Flyway strategy {strategy}")

    for version, items in sorted(duplicates.items(), key=lambda kv: flyway_key(kv[0])):
        ordered = sorted(items, key=lambda m: (not m.protected, m.relative))
        protected = [m for m in items if m.protected]
        # More than one protected migration already owns the same version:
        # changing history would be required.
        if len(protected) > 1:
            return [], PROTECTED_CONFLICT

        keeper = protected[0] if protected else ordered[0]
        for m in ordered:
            if m == keeper:
                continue
            if m.protected:
                return [], PROTECTED_CONFLICT
            new_version = allocate()
            dst = m.path.with_name(flyway_name(new_version, m))
            renames.append(Rename(m.path, dst, m, domain, f"duplicate Flyway version {version}"))

    # Protect destination collisions independent of migration parsing.
    dests = [r.destination for r in renames]
    if len(set(dests)) != len(dests):
        return [], CONFLICT
    for r in renames:
        if r.destination.exists() and r.destination != r.source:
            return [], CONFLICT
    return renames, CLEAN

def plan_laravel_domain(domain: str, group: list[Migration]) -> tuple[list[Rename], int]:
    by_version: dict[str, list[Migration]] = defaultdict(list)
    for m in group:
        by_version[m.version_raw].append(m)

    renames: list[Rename] = []
    used = set(by_version)

    for version, items in sorted(by_version.items()):
        if len(items) <= 1:
            continue
        protected = [m for m in items if m.protected]
        if len(protected) > 1:
            return [], PROTECTED_CONFLICT

        keeper = protected[0] if protected else sorted(items, key=lambda m: m.relative)[0]
        cursor = laravel_timestamp(version)

        for m in sorted(items, key=lambda m: (not m.protected, m.relative)):
            if m == keeper:
                continue
            if m.protected:
                return [], PROTECTED_CONFLICT
            candidate = cursor
            while True:
                candidate += dt.timedelta(seconds=1)
                raw = candidate.strftime("%Y_%m_%d_%H%M%S")
                if raw not in used:
                    break
            used.add(raw)
            cursor = candidate
            dst = m.path.with_name(laravel_name(candidate, m))
            if dst.exists() and dst != m.path:
                return [], CONFLICT
            renames.append(Rename(m.path, dst, m, domain, f"duplicate Laravel timestamp {version}"))
    return renames, CLEAN

def make_plan(settings: Settings, migrations: list[Migration]) -> tuple[list[Rename], int]:
    all_renames: list[Rename] = []
    for domain, group in sorted(domains(settings, migrations).items()):
        if not group:
            continue
        if group[0].kind == "flyway":
            changes, code = plan_flyway_domain(domain, group, settings)
        else:
            changes, code = plan_laravel_domain(domain, group)
        if code != CLEAN:
            return [], code
        all_renames.extend(changes)
    # source/destination uniqueness
    if len({r.source for r in all_renames}) != len(all_renames):
        return [], CONFLICT
    if len({r.destination for r in all_renames}) != len(all_renames):
        return [], CONFLICT
    return sorted(all_renames, key=lambda r: str(r.source)), CLEAN

def github_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A").replace(":", "%3A").replace(",", "%2C")

def emit_annotation(level: str, message: str, path: str | None = None) -> None:
    props = f" file={github_escape(path)}" if path else ""
    print(f"::{level}{props}::{github_escape(message)}")

def output_mode(args: argparse.Namespace) -> str:
    if args.ci_output == "auto":
        return "github" if os.environ.get("GITHUB_ACTIONS", "").lower() == "true" else "text"
    return args.ci_output

def render_plan(renames: list[Rename], args: argparse.Namespace, check_mode: bool) -> None:
    if args.format == "json":
        print(json.dumps([
            {
                "source": str(r.source),
                "destination": str(r.destination),
                "domain": r.domain,
                "reason": r.reason,
                "protected": r.migration.protected,
                "kind": r.migration.kind,
            } for r in renames
        ], indent=2))
        return

    mode = output_mode(args)
    if not renames:
        if mode == "github" and check_mode:
            emit_annotation("notice", "Migration versions are clean.")
        else:
            print("Migration versions are clean.")
        return

    for r in renames:
        msg = f"{r.source.name} -> {r.destination.name} ({r.domain}; {r.reason})"
        if mode == "github" and check_mode:
            emit_annotation("error", msg, str(r.source))
        else:
            print(f"RENAME {r.source} -> {r.destination}")
            print(f"       domain={r.domain} reason={r.reason}")

def apply_renames(renames: list[Rename]) -> None:
    # Two-phase temporary rename avoids swaps/collisions.
    staged: list[tuple[Path, Path, Path]] = []
    for idx, r in enumerate(renames):
        tmp = r.source.with_name(f".{r.source.name}.migration-version-fixer-{idx}.tmp")
        if tmp.exists():
            raise ToolError(f"temporary path already exists: {tmp}")
        r.source.rename(tmp)
        staged.append((tmp, r.destination, r.source))
    try:
        for tmp, dest, _source in staged:
            tmp.rename(dest)
    except Exception:
        # Best-effort rollback for anything still staged.
        for tmp, dest, source in reversed(staged):
            try:
                if tmp.exists():
                    tmp.rename(source)
                elif dest.exists() and not source.exists():
                    dest.rename(source)
            except OSError:
                pass
        raise

def print_exit_codes() -> None:
    for code, name in EXIT_CODES.items():
        print(f"{code} {name}")

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="migration-version-fixer",
        description="Normalize Flyway and Laravel migration versions across a repository.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        SAFETY
          Dry-run is the default. No filesystem changes occur unless --apply is supplied.
          --check is CI-only/non-mutating and is mutually exclusive with --apply.

        FLYWAY STRATEGIES
          auto        Infer sequential, timestamp, or compound from the execution domain.
          sequential  Continue V1, V2, V3...
          timestamp   Continue numeric timestamp-style VYYYYMMDDhhmmss versions.
          compound    Continue V1_1/V1_2 or V1.1/V1.2 while preserving delimiter style.

        EXECUTION SCOPE
          auto        Infer Spring/Flyway execution domains from locations and module deps.
          global      Treat every Flyway migration as one namespace.
          directory   Treat each physical migration directory independently.

        EXAMPLES
          # Inspect only; make no changes.
          migration-version-fixer .

          # Apply safe renames.
          migration-version-fixer . --apply

          # CI check; exits 4 if safe changes are required.
          migration-version-fixer . --check

          # GitHub Actions annotations.
          migration-version-fixer . --check --ci-output github

          # Preserve compound Flyway versioning explicitly.
          migration-version-fixer . --flyway-strategy compound

          # Force repository-wide uniqueness.
          migration-version-fixer . --scope global

          # Use branch history as protected baseline.
          migration-version-fixer . --git-base origin/main --check

          # Laravel only.
          migration-version-fixer . --kind laravel

          # Machine-readable plan.
          migration-version-fixer . --format json

          # Print stable exit-code contract.
          migration-version-fixer --print-exit-codes
        """)
    )
    p.add_argument("root", nargs="?", default=".", help="Repository/project root (default: current directory).")
    p.add_argument("--version", action="version", version=f"{TOOL_NAME} {VERSION}")
    p.add_argument("--config", help=f"Config path (default: <root>/{DEFAULT_CONFIG}).")
    p.add_argument("--kind", choices=["auto", "flyway", "laravel"], default=None)
    p.add_argument("--scope", choices=["auto", "global", "directory"], default=None)
    p.add_argument("--flyway-strategy", choices=["auto", "sequential", "timestamp", "compound"], default=None)
    p.add_argument("--git-base", help="Git revision used as protected-history baseline.")
    gp = p.add_mutually_exclusive_group()
    gp.add_argument("--git-protect", dest="git_protect", action="store_true", default=None)
    gp.add_argument("--no-git-protect", dest="git_protect", action="store_false")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply planned renames.")
    mode.add_argument("--check", action="store_true", help="CI mode: never mutate; exit 4 if changes are required.")
    p.add_argument("--ci-output", choices=["auto", "text", "github"], default="auto")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--print-exit-codes", action="store_true")
    return p

def main() -> int:
    args = parser().parse_args()
    if args.print_exit_codes:
        print_exit_codes()
        return CLEAN

    try:
        settings = build_settings(args)
        migrations = parse_migrations(settings)
        renames, status = make_plan(settings, migrations)

        if status == PROTECTED_CONFLICT:
            msg = "Migration conflict involves more than one protected/history migration."
            if output_mode(args) == "github" and args.check:
                emit_annotation("error", msg)
            else:
                print(f"ERROR: {msg}", file=sys.stderr)
            return PROTECTED_CONFLICT
        if status == CONFLICT:
            msg = "Migration conflict cannot be normalized safely."
            if output_mode(args) == "github" and args.check:
                emit_annotation("error", msg)
            else:
                print(f"ERROR: {msg}", file=sys.stderr)
            return CONFLICT

        render_plan(renames, args, args.check)

        if args.check:
            return CHANGES_REQUIRED if renames else CLEAN

        if args.apply and renames:
            apply_renames(renames)
            print(f"Applied {len(renames)} rename(s).")
        elif not args.apply and renames and args.format == "text":
            print(f"\nDry run: {len(renames)} rename(s) required. Re-run with --apply to mutate.")
        return CLEAN

    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return CONFIG_ERROR
    except GitError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return GIT_ERROR
    except ToolError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return getattr(e, "exit_code", INTERNAL_ERROR)
    except Exception as e:
        print(f"ERROR: internal failure: {e}", file=sys.stderr)
        return INTERNAL_ERROR

if __name__ == "__main__":
    raise SystemExit(main())
