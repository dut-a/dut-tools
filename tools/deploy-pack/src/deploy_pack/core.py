from __future__ import annotations

import fnmatch
import hashlib
import json
import ntpath
import posixpath
import stat
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable

from .state import atomic_write_text, MARK_JOURNAL_FILE
from .assurance import assurance_for, infer_assurance, validate_assurance

# HARDEN-13: security-sensitive deploy-pack artifacts are denied before any
# project or CLI include policy is evaluated.
PROTECTED_ARTIFACT_PATTERNS: tuple[str, ...] = (
    ".deploy-pack-*", ".deploy-pack.toml",
    "*.deploy.zip", "*.deploy.zip.*",
    "*.verify-signed.php", "*.verify-signed.py",
    "*.verify-browser.php",
    "*.verify-evidence.json", "*.remote-verify-evidence.json",
)

DEFAULT_IGNORE_PATTERNS: tuple[str, ...] = (
    "README", "README.*", "**/README", "**/README.*",
    "CHANGELOG", "CHANGELOG.*", "CONTRIBUTING", "CONTRIBUTING.*",
    "LICENSE", "LICENSE.*", "docs/**",
    ".gitignore", ".gitattributes", ".editorconfig",
    ".github/**", ".gitlab-ci.yml", ".circleci/**", "Jenkinsfile", "azure-pipelines.yml",
    "tests/**", "test/**", "phpunit.xml", "phpunit.xml.dist",
    "phpstan.neon", "phpstan.neon.dist", "psalm.xml",
    "infection.json", "infection.json.dist",
    "Makefile", "makefile", "GNUmakefile",
    "scripts/dev/**", "scripts/local/**", "scripts/test/**",
    "tools/dev/**", "tools/local/**", "tools/test/**",
    ".idea/**", ".vscode/**", ".DS_Store", "**/.DS_Store",
    ".deploy-pack-baseline", ".deploy-pack-history.jsonl", ".deploy-pack.toml", ".deploy-pack-keyring.json", ".deploy-pack-verifiers.json", ".deploy-pack-replay.json", ".deploy-pack-recovery-trust.json", ".deploy-pack-offline-checkpoints.jsonl",
    "*.deploy.zip", "*.deploy.zip.sha256", "*.deploy.zip.deletions.txt",
    "*.deploy.zip.verify.py", "*.deploy.zip.verify.php",
)

BASELINE_FILE = ".deploy-pack-baseline"
CONFIG_FILE = ".deploy-pack.toml"
MANIFEST_NAME = ".deploy-pack-manifest.json"
MANIFEST_SCHEMA_VERSION = 2


class DeployPackError(RuntimeError):
    pass


@dataclass(frozen=True)
class Change:
    status: str
    path: str
    old_path: str | None = None
    source: str = "committed"


@dataclass(frozen=True)
class PackPlan:
    root: Path
    baseline_ref: str
    baseline_commit: str
    head_commit: str
    deployable: tuple[Change, ...]
    ignored: tuple[Change, ...]
    deletions: tuple[Change, ...]
    skipped: tuple[tuple[Change, str], ...]
    ignored_reasons: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ProjectPolicy:
    mode: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    require: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class VerificationIssue:
    kind: str
    path: str
    detail: str


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    archive: Path
    manifest: dict
    issues: tuple[VerificationIssue, ...]


def _git(root: Path, *args: str) -> bytes:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
        )
    except FileNotFoundError as exc:
        raise DeployPackError("git is not installed or not on PATH") from exc
    if proc.returncode != 0:
        msg = proc.stdout.decode("utf-8", errors="replace").strip()
        raise DeployPackError(msg or f"git {' '.join(args)} failed")
    return proc.stdout


def repo_root(start: Path | None = None) -> Path:
    start = (start or Path.cwd()).resolve()
    return Path(_git(start, "rev-parse", "--show-toplevel").decode().strip()).resolve()


def resolve_ref(root: Path, ref: str) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False
    )
    if proc.returncode != 0:
        raise DeployPackError(f"baseline ref does not resolve to a commit: {ref}")
    return proc.stdout.decode().strip()


def current_head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").decode().strip()


def _parse_name_status(raw: bytes, source: str) -> list[Change]:
    parts = raw.split(b"\0")
    result = []
    i = 0
    while i < len(parts):
        if not parts[i]:
            i += 1
            continue
        status = parts[i].decode("utf-8", errors="surrogateescape")
        i += 1
        if status.startswith(("R", "C")):
            old_path = parts[i].decode("utf-8", errors="surrogateescape")
            new_path = parts[i + 1].decode("utf-8", errors="surrogateescape")
            i += 2
            result.append(Change(status, new_path, old_path, source))
        else:
            path = parts[i].decode("utf-8", errors="surrogateescape")
            i += 1
            result.append(Change(status, path, None, source))
    return result


def committed_changes(root: Path, baseline_commit: str) -> list[Change]:
    return _parse_name_status(
        _git(root, "diff", "--name-status", "-z", "--find-renames", "--find-copies",
             baseline_commit, "HEAD"),
        "committed",
    )


def working_tree_changes(root: Path) -> list[Change]:
    result = []
    result.extend(_parse_name_status(
        _git(root, "diff", "--name-status", "-z", "--find-renames", "--find-copies"),
        "unstaged",
    ))
    result.extend(_parse_name_status(
        _git(root, "diff", "--cached", "--name-status", "-z", "--find-renames",
             "--find-copies", "HEAD"),
        "staged",
    ))
    for raw_path in _git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if raw_path:
            result.append(Change(
                "??", raw_path.decode("utf-8", errors="surrogateescape"), None, "untracked"
            ))
    return result


def merge_changes(*groups: Iterable[Change]) -> list[Change]:
    merged = {}
    for group in groups:
        for change in group:
            prior = merged.get(change.path)
            merged[change.path] = Change(
                change.status,
                change.path,
                change.old_path or (prior.old_path if prior else None),
                change.source,
            )
    return sorted(merged.values(), key=lambda c: c.path)


def matches_pattern(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return fnmatch.fnmatchcase(path, pattern)


def protected_artifact_reason(root: Path, path: str) -> str | None:
    """Explain why *path* may never enter a normal deployment pack."""
    if any(matches_pattern(path, pattern) for pattern in PROTECTED_ARTIFACT_PATTERNS):
        return "protected deploy-pack control/generated artifact"

    candidate = root / path
    if not candidate.exists() or candidate.is_symlink() or not candidate.is_file():
        return None
    try:
        with candidate.open("rb") as fh:
            raw = fh.read(512 * 1024)
        text = raw.decode("utf-8", errors="ignore")
    except OSError:
        return None

    signed_python = (
        "SEED=base64.b64decode(" in text
        and "deploy-pack.remote-evidence.signed" in text
        and "Ed25519PrivateKey.from_private_bytes" in text
    )
    signed_php = (
        "$secret=base64_decode(" in text
        and "deploy-pack.remote-evidence.signed" in text
        and "sodium_crypto_sign_detached" in text
    )
    browser_php = (
        "$expectedToken=" in text
        and "hash_equals($expectedToken,$providedToken)" in text
        and "verificationMethod'=>'browser'" in text
        and "php-browser" in text
    )
    if signed_python or signed_php:
        return "secret-bearing deploy-pack signed verifier"
    if browser_php:
        return "token-bearing deploy-pack browser verifier"

    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            value = json.loads(text)
        except Exception:
            value = None
        if isinstance(value, dict):
            kind = str(value.get("kind", ""))
            if kind.startswith("deploy-pack."):
                return "deploy-pack signed/control artifact"
            if (
                value.get("algorithm") == "Ed25519"
                and "publicKeyBase64" in value
                and "publicKeySha256" in value
                and set(value).issubset({
                    "schemaVersion", "algorithm", "publicKeyBase64",
                    "publicKeySha256", "kind"
                })
            ):
                return "deploy-pack-style public-key artifact"
            if (
                value.get("verificationScope") in {"local-archive", "local-extracted", "remote"}
                and "manifest" in value
                and "verifiedAt" in value
            ):
                return "deploy-pack verification evidence"
            if (
                "checkpointId" in value
                and "checkpointHash" in value
                and value.get("custodyMode") == "threshold"
                and isinstance(value.get("copies"), list)
            ):
                return "deploy-pack offline custody checkpoint artifact"

    if all(marker in text for marker in (
        "artifactSha256=", "publicKeySha256=", "payloadSha256="
    )):
        return "deploy-pack custody fingerprint artifact"
    return None


def _string_array(section: dict, key: str, *, config: str = CONFIG_FILE) -> tuple[str, ...]:
    value = section.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise DeployPackError(f"{config}: {key} must be an array of non-empty strings")
    return tuple(value)


def load_project_policy(root: Path, *, required: bool = True) -> ProjectPolicy | None:
    """Load the Git-aware deployment selection policy.

    Schema 2 / [pack] is allowlist-first. The historical [deploy-pack]
    ignore/include form remains readable as legacy-denylist compatibility for
    repositories that already opted into deploy-pack before SELECTION-01.
    """
    path = root / CONFIG_FILE
    if not path.exists():
        if required:
            raise DeployPackError(
                "no deployment selection policy is configured. Git-aware `pack` and `inspect` "
                "fail closed until `.deploy-pack.toml` defines an explicit allowlist. "
                "Run `deploy-pack init`, review the generated file, then run `deploy-pack inspect`."
            )
        return None

    import tomllib
    value = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise DeployPackError(f"{CONFIG_FILE}: top level must be a TOML table")

    if "pack" in value:
        section = value["pack"]
        if not isinstance(section, dict):
            raise DeployPackError(f"{CONFIG_FILE}: [pack] must be a table")
        mode = section.get("policy", "allowlist")
        if mode != "allowlist":
            raise DeployPackError(f"{CONFIG_FILE}: [pack].policy must be 'allowlist'")
        include = _string_array(section, "include")
        exclude = _string_array(section, "exclude")
        require = _string_array(section, "require")
        if not include:
            raise DeployPackError(
                f"{CONFIG_FILE}: [pack].include must contain at least one deployable path pattern"
            )
        for pattern in (*include, *exclude, *require):
            if "\\" in pattern:
                raise DeployPackError(f"{CONFIG_FILE}: patterns must use POSIX '/' separators: {pattern!r}")
        return ProjectPolicy("allowlist", include, exclude, require, "pack")

    # Backward-compatible reader for pre-SELECTION-01 repositories. It is not
    # generated by `deploy-pack init` and should be migrated to [pack].
    section = value.get("deploy-pack", value)
    if not isinstance(section, dict):
        raise DeployPackError(f"{CONFIG_FILE}: legacy deploy-pack section must be a table")
    ignore = _string_array(section, "ignore")
    include = _string_array(section, "include")
    return ProjectPolicy("legacy-denylist", include, ignore, (), "legacy")


def _is_test_path(path: str) -> bool:
    parts = path.split("/")
    lower_parts = [p.lower() for p in parts]
    test_dirs = {"test", "tests", "test-ui", "tests-ui", "__tests__", "__snapshots__"}
    if any(part in test_dirs or part.endswith("-snapshots") for part in lower_parts[:-1]):
        return True
    name = lower_parts[-1]
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
        or name.endswith(".snap")
    )


def _hard_selection_reason(path: str) -> str | None:
    """Non-overridable repository paths that never belong in Git packs."""
    if _is_test_path(path):
        return "test"
    parts = path.split("/")
    if any(part == ".git" for part in parts):
        return "vcs-internal"
    name = parts[-1]
    if name == ".env" or name.startswith(".env."):
        return "environment-secret/config"
    return None


def _default_exclusion_reason(path: str) -> str:
    parts = path.split("/")
    name = parts[-1]
    lower = name.lower()
    if path.startswith("scripts/") or "/scripts/" in f"/{path}":
        return "script-not-allowlisted"
    if any(part.startswith(".") for part in parts) and path != ".htaccess":
        return "hidden-path-not-allowlisted"
    dev_names = {
        "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
        "playwright.config.js", "playwright.config.ts", "vitest.config.js", "vitest.config.ts",
        "vite.config.js", "vite.config.ts", "tsconfig.json", "jsconfig.json",
        "eslint.config.js", "eslint.config.mjs", "prettier.config.js", "prettier.config.mjs",
        "composer.lock", "phpunit.xml", "phpunit.xml.dist",
    }
    if lower in dev_names or lower.startswith("playwright.config."):
        return "dev-config-not-allowlisted"
    return "not-allowlisted"


def read_baseline(root: Path, filename: str = BASELINE_FILE) -> str | None:
    path = root / filename
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def write_baseline(root: Path, ref: str, filename: str = BASELINE_FILE) -> str:
    resolved = resolve_ref(root, ref)
    atomic_write_text(root / filename, ref + "\n")
    return resolved


def build_plan(
    root: Path,
    baseline_ref: str,
    *,
    committed_only: bool = False,
    cli_ignores: Iterable[str] = (),
    cli_includes: Iterable[str] = (),
) -> PackPlan:
    baseline_commit = resolve_ref(root, baseline_ref)
    head = current_head(root)
    committed = committed_changes(root, baseline_commit)
    working = [] if committed_only else working_tree_changes(root)
    changes = merge_changes(committed, working)

    policy = load_project_policy(root, required=False)
    if policy is None:
        policy = ProjectPolicy("legacy-denylist", (), (), (), "implicit-legacy-internal")
    if policy.mode == "allowlist":
        for required_path in policy.require:
            _validate_relative_path(required_path, field="[pack].require path")
            candidate = root / required_path
            if not candidate.exists() and not candidate.is_symlink():
                raise DeployPackError(
                    f"required deployment path is missing: {required_path}"
                )
    cli_ignores = tuple(cli_ignores)
    cli_includes = tuple(cli_includes)

    def protected(path: str) -> bool:
        return protected_artifact_reason(root, path) is not None

    def decision(path: str) -> tuple[bool, str | None]:
        if protected(path):
            return False, "protected"
        hard = _hard_selection_reason(path)
        if hard:
            return False, hard

        if policy.mode == "legacy-denylist":
            ignores = DEFAULT_IGNORE_PATTERNS + policy.exclude + cli_ignores
            includes = policy.include + cli_includes
            if any(matches_pattern(path, p) for p in includes):
                return True, None
            if any(matches_pattern(path, p) for p in ignores):
                return False, "legacy-ignore"
            return True, None

        # Allowlist mode: project include is the deployment ceiling. CLI
        # --include can only narrow that ceiling, never expand it.
        if not any(matches_pattern(path, p) for p in policy.include):
            return False, _default_exclusion_reason(path)
        if cli_includes and not any(matches_pattern(path, p) for p in cli_includes):
            return False, "cli-include-filter"
        if any(matches_pattern(path, p) for p in policy.exclude):
            return False, "project-exclude"
        if any(matches_pattern(path, p) for p in cli_ignores):
            return False, "cli-ignore"
        return True, None

    deployable, ignored_changes, deletions, skipped = [], [], [], []
    ignored_reasons: list[tuple[str, str]] = []

    for change in changes:
        allowed, reason = decision(change.path)
        if "D" in change.status:
            # Remote deletion is safe only for paths that belong to the declared
            # deployment surface. Never emit deletion instructions for excluded
            # repository material.
            if allowed:
                deletions.append(change)
            else:
                ignored_changes.append(change)
                ignored_reasons.append((change.path, reason or "excluded"))
            continue
        if not allowed:
            ignored_changes.append(change)
            ignored_reasons.append((change.path, reason or "excluded"))
            if change.status.startswith("R") and change.old_path:
                old_allowed, _ = decision(change.old_path)
                if old_allowed:
                    deletions.append(Change("D", change.old_path, None, change.source))
            continue
        local = root / change.path
        if not local.exists() and not local.is_symlink():
            deletions.append(Change("D", change.path, None, change.source))
            continue
        if local.is_dir():
            skipped.append((change, "directory/submodule cannot be packed as a regular file"))
            continue
        deployable.append(change)
        if change.status.startswith("R") and change.old_path:
            deletions.append(Change("D", change.old_path, None, change.source))

    unique_deletes = {}
    for d in deletions:
        unique_deletes.setdefault(d.path, d)

    return PackPlan(
        root, baseline_ref, baseline_commit, head,
        tuple(deployable), tuple(ignored_changes),
        tuple(unique_deletes.values()), tuple(skipped),
        tuple(ignored_reasons),
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()



def _path_type(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_file():
        return "file"
    if path.is_dir():
        return "directory"
    return "other"


def _symlink_target(path: Path) -> str:
    return str(path.readlink())


def build_manifest(plan: PackPlan) -> dict:
    files = []
    for change in plan.deployable:
        _validate_relative_path(change.path, field="deployable path")
        local = plan.root / change.path
        path_type = _path_type(local)
        entry = {
            "path": change.path,
            "status": change.status,
            "source": change.source,
            "type": path_type,
            "mode": oct(stat.S_IMODE(local.lstat().st_mode)),
        }

        if path_type == "symlink":
            target = _validate_symlink_target(change.path, _symlink_target(local))
            entry.update({
                "symlinkTarget": target,
                "sha256": hashlib.sha256(target.encode("utf-8")).hexdigest(),
                "size": len(target.encode("utf-8")),
            })
        elif path_type == "file":
            entry.update({
                "sha256": sha256_file(local),
                "size": local.stat().st_size,
            })
        else:
            raise DeployPackError(
                f"unsupported deployable path type for {change.path}: {path_type}"
            )
        files.append(entry)

    manifest = {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "baselineRef": plan.baseline_ref,
        "baselineCommit": plan.baseline_commit,
        "headCommit": plan.head_commit,
        "files": files,
        "remoteDeletions": [c.path for c in plan.deletions],
    }
    _validate_manifest_paths(manifest)
    return manifest


def write_package(plan: PackPlan, output: Path) -> tuple[Path | None, Path | None, Path | None]:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    archive = deletion_file = checksum_file = None

    if plan.deployable:
        manifest = build_manifest(plan)
        by_path = {entry["path"]: entry for entry in manifest["files"]}

        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for change in plan.deployable:
                local = plan.root / change.path
                entry = by_path[change.path]

                if entry["type"] == "symlink":
                    zi = zipfile.ZipInfo(change.path)
                    zi.create_system = 3
                    # Unix symlink file type + recorded permission bits.
                    mode = int(entry["mode"], 8)
                    zi.external_attr = ((stat.S_IFLNK | mode) & 0xFFFF) << 16
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    zf.writestr(zi, entry["symlinkTarget"].encode("utf-8"))
                else:
                    zf.write(local, arcname=change.path)

            zf.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, indent=2, sort_keys=True) + "\n"
            )

        archive = output
        checksum_file = output.with_suffix(output.suffix + ".sha256")
        checksum_file.write_text(
            f"{sha256_file(output)}  {output.name}\n", encoding="utf-8"
        )

    if plan.deletions:
        deletion_file = output.with_suffix(output.suffix + ".deletions.txt")
        deletion_file.write_text(
            "\n".join(c.path for c in plan.deletions) + "\n", encoding="utf-8"
        )

    return archive, deletion_file, checksum_file

def load_manifest_from_archive(archive: Path) -> dict:
    try:
        with zipfile.ZipFile(archive) as zf:
            if MANIFEST_NAME not in zf.namelist():
                raise DeployPackError(f"archive does not contain {MANIFEST_NAME}")
            manifest = json.loads(zf.read(MANIFEST_NAME))
            _validate_manifest_paths(manifest)
            return manifest
    except zipfile.BadZipFile as exc:
        raise DeployPackError(f"invalid ZIP archive: {archive}") from exc



def _validate_relative_path(value: object, *, field: str = "path") -> str:
    """Return a canonical deploy path or fail closed.

    Deployment manifests and ZIP entries use POSIX-style repository-relative
    names on every platform. Backslashes are rejected rather than interpreted
    differently by Windows/POSIX runtimes.
    """
    if not isinstance(value, str) or not value:
        raise DeployPackError(f"{field} must be a non-empty string")
    if "\x00" in value:
        raise DeployPackError(f"{field} contains a NUL byte")
    if "\\" in value:
        raise DeployPackError(f"{field} contains a backslash and is not canonical POSIX-relative")
    drive, _tail = ntpath.splitdrive(value)
    if drive:
        raise DeployPackError(f"{field} must not contain a drive prefix: {value!r}")
    if value.startswith("/"):
        raise DeployPackError(f"{field} must be relative: {value!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise DeployPackError(f"{field} is not a canonical repository-relative path: {value!r}")
    normalized = posixpath.normpath(value)
    if normalized != value or normalized == ".." or normalized.startswith("../"):
        raise DeployPackError(f"{field} escapes or is not canonical: {value!r}")
    return value


def _validate_symlink_target(link_path: str, target: object) -> str:
    if not isinstance(target, str) or not target:
        raise DeployPackError(f"symlink target for {link_path!r} must be a non-empty string")
    if "\x00" in target:
        raise DeployPackError(f"symlink target for {link_path!r} contains a NUL byte")
    drive, _tail = ntpath.splitdrive(target)
    if drive or target.startswith("/") or target.startswith("\\"):
        raise DeployPackError(f"symlink target for {link_path!r} must be relative: {target!r}")
    # Symlink resolution is relative to the link's parent. Reject any target
    # whose normalized destination leaves the deployment root.
    parent = posixpath.dirname(link_path)
    resolved = posixpath.normpath(posixpath.join(parent, target.replace("\\", "/")))
    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
        raise DeployPackError(
            f"symlink target for {link_path!r} escapes deployment root: {target!r}"
        )
    return target


def _validate_manifest_paths(manifest: dict) -> None:
    if not isinstance(manifest, dict):
        raise DeployPackError("manifest must be a JSON object")
    files = manifest.get("files", [])
    deletions = manifest.get("remoteDeletions", [])
    if not isinstance(files, list) or not isinstance(deletions, list):
        raise DeployPackError("manifest files and remoteDeletions must be arrays")

    seen: set[str] = set()
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise DeployPackError(f"manifest files[{index}] must be an object")
        rel = _validate_relative_path(entry.get("path"), field=f"files[{index}].path")
        if rel in seen:
            raise DeployPackError(f"manifest contains duplicate file path: {rel}")
        seen.add(rel)
        entry_type = entry.get("type", "file")
        if entry_type not in {"file", "symlink"}:
            raise DeployPackError(f"manifest path {rel!r} has unsupported type {entry_type!r}")
        if entry_type == "symlink":
            _validate_symlink_target(rel, entry.get("symlinkTarget"))

    deletion_seen: set[str] = set()
    for index, value in enumerate(deletions):
        rel = _validate_relative_path(value, field=f"remoteDeletions[{index}]")
        if rel in deletion_seen:
            raise DeployPackError(f"manifest contains duplicate remote deletion: {rel}")
        if rel in seen:
            raise DeployPackError(f"manifest path is both deployed and deleted: {rel}")
        deletion_seen.add(rel)


def _safe_tree_path(root: Path, rel: str) -> Path:
    """Join a validated relative path without traversing symlink parents."""
    rel = _validate_relative_path(rel)
    root = root.resolve()
    current = root
    parts = rel.split("/")
    for part in parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise DeployPackError(
                f"manifest path {rel!r} traverses symlink parent {current.relative_to(root)!s}"
            )
    return root.joinpath(*parts)


def _zip_entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    if info.create_system != 3:
        return False
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def verify_archive(archive: Path, checksum_file: Path | None = None) -> VerificationResult:
    archive = archive.resolve()
    issues = []

    if checksum_file is None:
        candidate = archive.with_suffix(archive.suffix + ".sha256")
        checksum_file = candidate if candidate.exists() else None
    if checksum_file is not None:
        expected = checksum_file.read_text(encoding="utf-8").strip().split()[0]
        actual = sha256_file(archive)
        if expected != actual:
            issues.append(VerificationIssue(
                "ARCHIVE_CHECKSUM_MISMATCH", archive.name,
                f"expected {expected}, got {actual}"
            ))

    manifest = load_manifest_from_archive(archive)
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        issues.append(VerificationIssue(
            "MANIFEST_SCHEMA_UNSUPPORTED", MANIFEST_NAME,
            f"expected {MANIFEST_SCHEMA_VERSION}, got {manifest.get('schemaVersion')}"
        ))

    expected_entries = {f["path"]: f for f in manifest.get("files", [])}
    with zipfile.ZipFile(archive) as zf:
        infos = {i.filename: i for i in zf.infolist()}
        names = set(infos)
        for name in names:
            if name == MANIFEST_NAME:
                continue
            try:
                _validate_relative_path(name, field="ZIP member")
            except DeployPackError as exc:
                issues.append(VerificationIssue("ARCHIVE_UNSAFE_PATH", name, str(exc)))

        for path, entry in expected_entries.items():
            if path not in names:
                issues.append(VerificationIssue(
                    "ARCHIVE_FILE_MISSING", path, "listed in manifest but absent from ZIP"
                ))
                continue

            info = infos[path]
            data = zf.read(path)
            expected_type = entry.get("type", "file")
            actual_is_symlink = _zip_entry_is_symlink(info)

            if expected_type == "symlink":
                if not actual_is_symlink:
                    issues.append(VerificationIssue(
                        "ARCHIVE_TYPE_MISMATCH", path,
                        "manifest expects symlink but ZIP entry is not a Unix symlink"
                    ))
                target = data.decode("utf-8", errors="strict")
                if target != entry.get("symlinkTarget"):
                    issues.append(VerificationIssue(
                        "ARCHIVE_SYMLINK_TARGET_MISMATCH", path,
                        f"expected {entry.get('symlinkTarget')!r}, got {target!r}"
                    ))
                digest = hashlib.sha256(data).hexdigest()
            else:
                if actual_is_symlink:
                    issues.append(VerificationIssue(
                        "ARCHIVE_TYPE_MISMATCH", path,
                        "manifest expects regular file but ZIP entry is a symlink"
                    ))
                digest = hashlib.sha256(data).hexdigest()

            if digest != entry["sha256"]:
                issues.append(VerificationIssue(
                    "ARCHIVE_FILE_HASH_MISMATCH", path,
                    f"expected {entry['sha256']}, got {digest}"
                ))
            if len(data) != entry["size"]:
                issues.append(VerificationIssue(
                    "ARCHIVE_FILE_SIZE_MISMATCH", path,
                    f"expected {entry['size']}, got {len(data)}"
                ))

            if info.create_system == 3:
                actual_mode = oct((info.external_attr >> 16) & 0o7777)
                expected_mode = entry.get("mode")
                if expected_mode and actual_mode != expected_mode:
                    issues.append(VerificationIssue(
                        "ARCHIVE_MODE_MISMATCH", path,
                        f"expected {expected_mode}, got {actual_mode}"
                    ))

        allowed = set(expected_entries) | {MANIFEST_NAME}
        for extra in sorted(names - allowed):
            issues.append(VerificationIssue(
                "ARCHIVE_UNDECLARED_FILE", extra,
                "present in ZIP but absent from manifest"
            ))

    return VerificationResult(not issues, archive, manifest, tuple(issues))


def _mode_matches(expected: str | None, actual_mode: int, strict_permissions: bool) -> bool:
    if not expected:
        return True
    expected_mode = int(expected, 8) & 0o7777
    actual_mode = actual_mode & 0o7777
    if strict_permissions:
        return actual_mode == expected_mode

    # Shared hosting commonly normalizes owner/group write bits.
    # Preserve the important executable/readability class while tolerating
    # benign writable-bit normalization.
    expected_exec = expected_mode & 0o111
    actual_exec = actual_mode & 0o111
    return expected_exec == actual_exec


def verify_extracted_tree(
    manifest: dict,
    root: Path,
    *,
    strict_permissions: bool = False,
) -> tuple[VerificationIssue, ...]:
    root = root.resolve()
    issues = []

    try:
        _validate_manifest_paths(manifest)
    except DeployPackError as exc:
        return (VerificationIssue("MANIFEST_UNSAFE_PATH", MANIFEST_NAME, str(exc)),)

    for entry in manifest.get("files", []):
        rel = entry["path"]
        try:
            path = _safe_tree_path(root, rel)
        except DeployPackError as exc:
            issues.append(VerificationIssue("REMOTE_PATH_ESCAPE", rel, str(exc)))
            continue
        expected_type = entry.get("type", "file")

        if not path.exists() and not path.is_symlink():
            issues.append(VerificationIssue(
                "REMOTE_FILE_MISSING", rel, "expected deployed path missing"
            ))
            continue

        actual_type = _path_type(path)
        if actual_type != expected_type:
            issues.append(VerificationIssue(
                "REMOTE_TYPE_MISMATCH", rel,
                f"expected {expected_type}, got {actual_type}"
            ))
            continue

        actual_mode = stat.S_IMODE(path.lstat().st_mode)
        if not _mode_matches(entry.get("mode"), actual_mode, strict_permissions):
            issues.append(VerificationIssue(
                "REMOTE_MODE_MISMATCH", rel,
                f"expected {entry.get('mode')}, got {oct(actual_mode)}"
            ))

        if expected_type == "symlink":
            target = _symlink_target(path)
            if target != entry.get("symlinkTarget"):
                issues.append(VerificationIssue(
                    "REMOTE_SYMLINK_TARGET_MISMATCH", rel,
                    f"expected {entry.get('symlinkTarget')!r}, got {target!r}"
                ))
                continue
            digest = hashlib.sha256(target.encode("utf-8")).hexdigest()
            if digest != entry["sha256"]:
                issues.append(VerificationIssue(
                    "REMOTE_FILE_HASH_MISMATCH", rel,
                    f"expected {entry['sha256']}, got {digest}"
                ))
        elif expected_type == "file":
            if path.stat().st_size != entry["size"]:
                issues.append(VerificationIssue(
                    "REMOTE_FILE_SIZE_MISMATCH", rel,
                    f"expected {entry['size']}, got {path.stat().st_size}"
                ))
                continue
            actual = sha256_file(path)
            if actual != entry["sha256"]:
                issues.append(VerificationIssue(
                    "REMOTE_FILE_HASH_MISMATCH", rel,
                    f"expected {entry['sha256']}, got {actual}"
                ))

    for rel in manifest.get("remoteDeletions", []):
        try:
            path = _safe_tree_path(root, rel)
        except DeployPackError as exc:
            issues.append(VerificationIssue("REMOTE_PATH_ESCAPE", rel, str(exc)))
            continue
        if path.exists() or path.is_symlink():
            issues.append(VerificationIssue(
                "REMOTE_DELETION_NOT_APPLIED", rel, "path should have been deleted"
            ))

    return tuple(issues)








def write_remote_verifier(
    archive: Path,
    language: str,
    output: Path | None = None,
    *,
    browser: bool = False,
    token: str | None = None,
    relative_root: str = ".",
) -> Path:
    manifest = load_manifest_from_archive(archive)

    if browser:
        if language != "php":
            raise DeployPackError("browser verifier is supported only for PHP")
        if not token:
            raise DeployPackError("browser verifier requires a token")
        content = generate_php_browser_verifier(
            manifest,
            token=token,
            relative_root=relative_root,
        )
        suffix = ".verify-browser.php"
    elif language == "php":
        content, suffix = generate_php_remote_verifier(manifest), ".verify.php"
    elif language == "python":
        content, suffix = generate_python_remote_verifier(manifest), ".verify.py"
    else:
        raise DeployPackError(f"unsupported verifier language: {language}")

    if output is None:
        output = archive.with_name(archive.name + suffix)

    output.write_text(content, encoding="utf-8")
    if browser:
        # Browser verifier source embeds the one-time bearer token.
        output.chmod(0o600)
    elif language == "python":
        output.chmod(0o700)
    return output


EVIDENCE_SCHEMA_VERSION = 1
EVIDENCE_SUFFIX = ".verify-evidence.json"


def manifest_sha256(manifest: dict) -> str:
    canonical = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_verification_evidence(
    archive: Path,
    manifest: dict,
    *,
    verification_scope: str,
    verification_root: str | None = None,
    strict_permissions: bool = False,
    verifier: str = "deploy-pack",
) -> dict:
    archive = archive.resolve()
    return {
        "schemaVersion": EVIDENCE_SCHEMA_VERSION,
        "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "verifier": verifier,
        "result": "PASS",
        "verificationScope": verification_scope,
        "verificationRoot": verification_root,
        "strictPermissions": strict_permissions,
        "assurance": assurance_for(verification_scope=verification_scope),
        "archive": {"name": archive.name, "sha256": sha256_file(archive)},
        "manifest": {
            "sha256": manifest_sha256(manifest),
            "schemaVersion": manifest.get("schemaVersion"),
            "baselineRef": manifest.get("baselineRef"),
            "baselineCommit": manifest.get("baselineCommit"),
            "headCommit": manifest.get("headCommit"),
            "fileCount": len(manifest.get("files", [])),
            "remoteDeletionCount": len(manifest.get("remoteDeletions", [])),
        },
    }


def write_verification_evidence(
    archive: Path,
    manifest: dict,
    *,
    verification_scope: str,
    verification_root: str | None = None,
    strict_permissions: bool = False,
    output: Path | None = None,
    verifier: str = "deploy-pack",
) -> Path:
    archive = archive.resolve()
    if output is None:
        output = archive.with_name(archive.name + EVIDENCE_SUFFIX)
    evidence = build_verification_evidence(
        archive,
        manifest,
        verification_scope=verification_scope,
        verification_root=verification_root,
        strict_permissions=strict_permissions,
        verifier=verifier,
    )
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def load_verification_evidence(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DeployPackError(f"verification evidence does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DeployPackError(f"invalid verification evidence JSON: {exc}") from exc
    if value.get("schemaVersion") != EVIDENCE_SCHEMA_VERSION:
        raise DeployPackError(f"unsupported verification evidence schema: {value.get('schemaVersion')}")
    if value.get("result") != "PASS":
        raise DeployPackError("verification evidence is not a PASS record")
    try:
        value["assurance"] = infer_assurance(value)
    except ValueError as exc:
        raise DeployPackError(str(exc)) from exc
    return value


def verify_evidence_against_archive(evidence: dict, archive: Path) -> None:
    archive = archive.resolve()
    if sha256_file(archive) != evidence.get("archive", {}).get("sha256"):
        raise DeployPackError("verification evidence does not match archive SHA-256")
    manifest = load_manifest_from_archive(archive)
    if manifest_sha256(manifest) != evidence.get("manifest", {}).get("sha256"):
        raise DeployPackError("verification evidence does not match archive manifest")


def validate_mark_evidence(
    root: Path,
    ref: str,
    evidence_path: Path,
    *,
    archive: Path | None = None,
) -> tuple[str, dict]:
    resolved_ref = resolve_ref(root, ref)
    evidence = load_verification_evidence(evidence_path)
    if archive is not None:
        verify_evidence_against_archive(evidence, archive)
    verified_head = evidence.get("manifest", {}).get("headCommit")
    if not verified_head:
        raise DeployPackError("verification evidence does not contain headCommit")
    if resolved_ref != verified_head:
        raise DeployPackError(
            "ref being marked does not match the verified deployment head: "
            f"{resolved_ref} != {verified_head}"
        )
    scope = evidence.get("verificationScope")
    if scope not in {"extracted-tree", "remote"}:
        raise DeployPackError(
            "mark requires deployment-level evidence; local archive-only "
            f"verification scope {scope!r} is insufficient"
        )
    return resolved_ref, evidence


REMOTE_EVIDENCE_SCHEMA_VERSION = 1


def load_remote_evidence(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DeployPackError(f"remote evidence does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DeployPackError(f"invalid remote evidence JSON: {exc}") from exc
    if value.get("schemaVersion") != REMOTE_EVIDENCE_SCHEMA_VERSION:
        raise DeployPackError(f"unsupported remote evidence schema: {value.get('schemaVersion')}")
    if value.get("result") != "PASS": raise DeployPackError("remote evidence is not a PASS record")
    if value.get("verificationScope") != "remote": raise DeployPackError("evidence is not remote verification evidence")
    if value.get("verificationMethod") not in {"ssh-cli", "browser"}: raise DeployPackError("remote evidence has unsupported verification method")
    try:
        value["assurance"] = infer_assurance(value)
    except ValueError as exc:
        raise DeployPackError(str(exc)) from exc
    return value


def ingest_remote_evidence(remote_evidence_path: Path, archive: Path, *, output: Path | None = None) -> Path:
    remote = load_remote_evidence(remote_evidence_path)
    archive = archive.resolve()
    manifest = load_manifest_from_archive(archive)
    expected_manifest_sha = manifest_sha256(manifest)
    if remote.get("manifest", {}).get("sha256") != expected_manifest_sha:
        raise DeployPackError("remote evidence does not match the archive manifest")
    if remote.get("manifest", {}).get("headCommit") != manifest.get("headCommit"):
        raise DeployPackError("remote evidence headCommit does not match archive manifest")
    if remote.get("manifest", {}).get("baselineCommit") != manifest.get("baselineCommit"):
        raise DeployPackError("remote evidence baselineCommit does not match archive manifest")
    normalized = {
        "schemaVersion": EVIDENCE_SCHEMA_VERSION, "verifiedAt": remote.get("verifiedAt"),
        "verifier": f"remote:{remote.get('verifierRuntime')}", "result": "PASS",
        "verificationScope": "remote", "verificationMethod": remote.get("verificationMethod"),
        "verificationRoot": remote.get("verificationRoot"), "strictPermissions": bool(remote.get("strictPermissions")),
        "assurance": assurance_for(verification_scope="remote", signed=False, method=remote.get("verificationMethod")),
        "archive": {"name": archive.name, "sha256": sha256_file(archive)},
        "manifest": {"sha256": expected_manifest_sha, "schemaVersion": manifest.get("schemaVersion"),
            "baselineRef": manifest.get("baselineRef"), "baselineCommit": manifest.get("baselineCommit"),
            "headCommit": manifest.get("headCommit"), "fileCount": len(manifest.get("files", [])),
            "remoteDeletionCount": len(manifest.get("remoteDeletions", []))},
        "remoteEvidence": remote,
    }
    if output is None: output = archive.with_name(archive.name + ".remote-verify-evidence.json")
    output.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def generate_python_remote_verifier(manifest: dict) -> str:
    _validate_manifest_paths(manifest)
    payload = json.dumps(manifest, separators=(",", ":"), sort_keys=True)
    msha = manifest_sha256(manifest)
    template = r'''#!/usr/bin/env python3
import hashlib, json, stat, sys
from datetime import datetime, timezone
from pathlib import Path
MANIFEST=json.loads(__PAYLOAD__); MANIFEST_SHA256="__MSHA__"
def sha256_file(path):
    h=hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024*1024), b""): h.update(block)
    return h.hexdigest()
def path_type(path):
    if path.is_symlink(): return "symlink"
    if path.is_file(): return "file"
    if path.is_dir(): return "directory"
    return "other"
def safe_path(root, rel):
    if not isinstance(rel,str) or not rel or "\x00" in rel or "\\" in rel or rel.startswith("/"):
        return None
    parts=rel.split("/")
    if any(part in ("",".","..") for part in parts): return None
    if len(rel)>=2 and rel[1]==":" and rel[0].isalpha(): return None
    cur=root
    for part in parts[:-1]:
        cur=cur/part
        if cur.is_symlink(): return None
    return root.joinpath(*parts)
def main():
    root=Path(sys.argv[1] if len(sys.argv)>1 else ".").resolve(); strict="--strict-permissions" in sys.argv[2:]; evidence_out=None
    for i,v in enumerate(sys.argv[2:],start=2):
        if v=="--evidence-out" and i+1<len(sys.argv): evidence_out=Path(sys.argv[i+1]); break
    failures=[]
    for entry in MANIFEST.get("files",[]):
        rel=entry["path"]; path=safe_path(root,rel)
        if path is None: failures.append(("PATH_ESCAPE",rel)); continue
        if not path.exists() and not path.is_symlink(): failures.append(("MISSING",rel)); continue
        actual=path_type(path); expected=entry.get("type","file")
        if actual!=expected: failures.append(("TYPE",rel)); continue
        em=int(entry.get("mode","0o0"),8)&0o7777; am=stat.S_IMODE(path.lstat().st_mode)&0o7777
        if not (am==em if strict else ((am&0o111)==(em&0o111))): failures.append(("MODE",rel))
        if expected=="symlink":
            target=str(path.readlink())
            if target!=entry.get("symlinkTarget"): failures.append(("LINK_TARGET",rel)); continue
            if hashlib.sha256(target.encode()).hexdigest()!=entry["sha256"]: failures.append(("HASH",rel))
        else:
            if path.stat().st_size!=entry["size"]: failures.append(("SIZE",rel)); continue
            if sha256_file(path)!=entry["sha256"]: failures.append(("HASH",rel))
    for rel in MANIFEST.get("remoteDeletions",[]):
        path=safe_path(root,rel)
        if path is None: failures.append(("PATH_ESCAPE",rel)); continue
        if path.exists() or path.is_symlink(): failures.append(("DELETE_PENDING",rel))
    if failures:
        print("DEPLOY-PACK VERIFY: FAIL")
        for kind,rel in failures: print(f"  {kind:<14} {rel}")
        return 1
    print("DEPLOY-PACK VERIFY: PASS")
    if evidence_out:
        evidence={"schemaVersion":1,"result":"PASS","verificationScope":"remote","verificationMethod":"ssh-cli","verificationRoot":str(root),"strictPermissions":strict,"verifierRuntime":"python","assurance":{"schemaVersion":1,"level":"host-cooperative-remote","authority":"target-host-self-verification","claims":["remote-deployed-tree-state","manifest-binding"],"doesNotClaim":["host-compromise-resistance","independent-attestation","hardware-rooted-attestation"],"verificationMethod":"ssh-cli","evidenceIntegrity":"unsigned-record"},"verifiedAt":datetime.now(timezone.utc).isoformat(),"manifest":{"sha256":MANIFEST_SHA256,"schemaVersion":MANIFEST.get("schemaVersion"),"baselineRef":MANIFEST.get("baselineRef"),"baselineCommit":MANIFEST.get("baselineCommit"),"headCommit":MANIFEST.get("headCommit"),"fileCount":len(MANIFEST.get("files",[])),"remoteDeletionCount":len(MANIFEST.get("remoteDeletions",[]))}}
        evidence_out.write_text(json.dumps(evidence,indent=2,sort_keys=True)+"\n"); print(f"  evidence: {evidence_out}")
    return 0
if __name__=="__main__": raise SystemExit(main())
'''
    return template.replace("__PAYLOAD__", repr(payload)).replace("__MSHA__", msha)


def generate_php_remote_verifier(manifest: dict) -> str:
    _validate_manifest_paths(manifest)
    payload=json.dumps(manifest,separators=(",",":"),sort_keys=True); escaped=payload.replace("\\","\\\\").replace("'","\\'"); msha=manifest_sha256(manifest)
    template=r'''<?php
declare(strict_types=1);
$manifest=json_decode('__PAYLOAD__',true,512,JSON_THROW_ON_ERROR); $manifestSha256='__MSHA__'; $root=$argv[1]??'.'; $strictPermissions=in_array('--strict-permissions',$argv,true); $evidenceOut=null;
for($i=2;$i<count($argv);$i++){if($argv[$i]==='--evidence-out'&&isset($argv[$i+1])){$evidenceOut=$argv[$i+1];break;}}
$root=realpath($root)?:$root; $failures=[];
function dpPathType(string $p):string{if(is_link($p))return'symlink';if(is_file($p))return'file';if(is_dir($p))return'directory';return'other';}
function dpSafePath(string $root,$rel){if(!is_string($rel)||$rel===''||strpos($rel,"\0")!==false||strpos($rel,'\\')!==false||str_starts_with($rel,'/')||preg_match('/^[A-Za-z]:/',$rel))return null;$parts=explode('/',$rel);$cur=rtrim($root,DIRECTORY_SEPARATOR);for($i=0;$i<count($parts);$i++){if($parts[$i]===''||$parts[$i]==='.'||$parts[$i]==='..')return null;if($i<count($parts)-1){$cur.=DIRECTORY_SEPARATOR.$parts[$i];if(is_link($cur))return null;}}return rtrim($root,DIRECTORY_SEPARATOR).DIRECTORY_SEPARATOR.implode(DIRECTORY_SEPARATOR,$parts);}
foreach($manifest['files']??[] as $e){$rel=$e['path'];$path=dpSafePath($root,$rel);if($path===null){$failures[]=['PATH_ESCAPE',$rel];continue;}if(!file_exists($path)&&!is_link($path)){$failures[]=['MISSING',$rel];continue;}$a=dpPathType($path);$x=$e['type']??'file';if($a!==$x){$failures[]=['TYPE',$rel];continue;}$em=intval(substr($e['mode']??'0o0',2),8)&07777;$am=fileperms($path)&07777;$ok=$strictPermissions?($am===$em):(($am&0111)===($em&0111));if(!$ok)$failures[]=['MODE',$rel];if($x==='symlink'){$t=readlink($path);if($t!==($e['symlinkTarget']??null)){$failures[]=['LINK_TARGET',$rel];continue;}if(hash('sha256',$t)!==$e['sha256'])$failures[]=['HASH',$rel];continue;}if(filesize($path)!==$e['size']){$failures[]=['SIZE',$rel];continue;}if(hash_file('sha256',$path)!==$e['sha256'])$failures[]=['HASH',$rel];}
foreach($manifest['remoteDeletions']??[] as $rel){$path=dpSafePath($root,$rel);if($path===null){$failures[]=['PATH_ESCAPE',$rel];continue;}if(file_exists($path)||is_link($path))$failures[]=['DELETE_PENDING',$rel];}
if($failures){fwrite(STDOUT,"DEPLOY-PACK VERIFY: FAIL\n");foreach($failures as[$k,$r])fwrite(STDOUT,sprintf("  %-14s %s\n",$k,$r));exit(1);}fwrite(STDOUT,"DEPLOY-PACK VERIFY: PASS\n");
if($evidenceOut!==null){$ev=['schemaVersion'=>1,'result'=>'PASS','verificationScope'=>'remote','verificationMethod'=>'ssh-cli','verificationRoot'=>$root,'strictPermissions'=>$strictPermissions,'verifierRuntime'=>'php','assurance'=>['schemaVersion'=>1,'level'=>'host-cooperative-remote','authority'=>'target-host-self-verification','claims'=>['remote-deployed-tree-state','manifest-binding'],'doesNotClaim'=>['host-compromise-resistance','independent-attestation','hardware-rooted-attestation'],'verificationMethod'=>'ssh-cli','evidenceIntegrity'=>'unsigned-record'],'verifiedAt'=>gmdate('c'),'manifest'=>['sha256'=>$manifestSha256,'schemaVersion'=>$manifest['schemaVersion']??null,'baselineRef'=>$manifest['baselineRef']??null,'baselineCommit'=>$manifest['baselineCommit']??null,'headCommit'=>$manifest['headCommit']??null,'fileCount'=>count($manifest['files']??[]),'remoteDeletionCount'=>count($manifest['remoteDeletions']??[])]];file_put_contents($evidenceOut,json_encode($ev,JSON_PRETTY_PRINT|JSON_UNESCAPED_SLASHES)."\n");fwrite(STDOUT,"  evidence: ".$evidenceOut."\n");}exit(0);
'''
    return template.replace("__PAYLOAD__",escaped).replace("__MSHA__",msha)


def generate_php_browser_verifier(manifest: dict, *, token: str, relative_root: str = ".") -> str:
    _validate_manifest_paths(manifest)
    payload=json.dumps(manifest,separators=(",",":"),sort_keys=True); escaped=payload.replace("\\","\\\\").replace("'","\\'"); token_escaped=token.replace("\\","\\\\").replace("'","\\'"); root_escaped=relative_root.replace("\\","\\\\").replace("'","\\'"); msha=manifest_sha256(manifest)
    template=r'''<?php
declare(strict_types=1);header('Content-Type: application/json; charset=UTF-8');header('Cache-Control: no-store, max-age=0');header('X-Robots-Tag: noindex, nofollow, noarchive',true);header('X-Content-Type-Options: nosniff');$expectedToken='__TOKEN__';$auth=$_SERVER['HTTP_AUTHORIZATION']??'';$providedToken=$_POST['token']??'';if(is_string($auth)&&str_starts_with($auth,'Bearer ')){$providedToken=substr($auth,7);}if($_SERVER['REQUEST_METHOD']==='GET'){header('Content-Type: text/html; charset=UTF-8');echo '<!doctype html><meta name="robots" content="noindex,nofollow"><title>deploy-pack verifier</title><form method="post"><label>One-time token <input type="password" name="token" autocomplete="off" required></label><button type="submit">Verify deployment</button></form>';exit;}if(!is_string($providedToken)||!hash_equals($expectedToken,$providedToken)){http_response_code(404);echo json_encode(['error'=>'Not Found']);exit;}$manifest=json_decode('__PAYLOAD__',true,512,JSON_THROW_ON_ERROR);$manifestSha256='__MSHA__';$root=__DIR__.DIRECTORY_SEPARATOR.'__ROOT__';$resolved=realpath($root);if($resolved!==false)$root=$resolved;$strictPermissions=(($_POST['strict_permissions']??'')==='1'||($_SERVER['HTTP_X_DEPLOY_PACK_STRICT_PERMISSIONS']??'')==='1');$failures=[];function dpPathType(string $p):string{if(is_link($p))return'symlink';if(is_file($p))return'file';if(is_dir($p))return'directory';return'other';}foreach($manifest['files']??[] as $e){$rel=$e['path'];$path=dpSafePath($root,$rel);if($path===null){$failures[]=['PATH_ESCAPE',$rel];continue;}if(!file_exists($path)&&!is_link($path)){$failures[]=['MISSING',$rel];continue;}$a=dpPathType($path);$x=$e['type']??'file';if($a!==$x){$failures[]=['TYPE',$rel];continue;}$em=intval(substr($e['mode']??'0o0',2),8)&07777;$am=fileperms($path)&07777;$ok=$strictPermissions?($am===$em):(($am&0111)===($em&0111));if(!$ok)$failures[]=['MODE',$rel];if($x==='symlink'){$t=readlink($path);if($t!==($e['symlinkTarget']??null)){$failures[]=['LINK_TARGET',$rel];continue;}if(hash('sha256',$t)!==$e['sha256'])$failures[]=['HASH',$rel];continue;}if(filesize($path)!==$e['size']){$failures[]=['SIZE',$rel];continue;}if(hash_file('sha256',$path)!==$e['sha256'])$failures[]=['HASH',$rel];}foreach($manifest['remoteDeletions']??[] as $rel){$path=dpSafePath($root,$rel);if($path===null){$failures[]=['PATH_ESCAPE',$rel];continue;}if(file_exists($path)||is_link($path))$failures[]=['DELETE_PENDING',$rel];}if($failures){http_response_code(409);echo json_encode(['result'=>'FAIL','failures'=>$failures],JSON_PRETTY_PRINT|JSON_UNESCAPED_SLASHES);exit;}$ev=['schemaVersion'=>1,'result'=>'PASS','verificationScope'=>'remote','verificationMethod'=>'browser','verificationRoot'=>$root,'strictPermissions'=>$strictPermissions,'verifierRuntime'=>'php-browser','assurance'=>['schemaVersion'=>1,'level'=>'host-cooperative-remote','authority'=>'target-host-self-verification','claims'=>['remote-deployed-tree-state','manifest-binding'],'doesNotClaim'=>['host-compromise-resistance','independent-attestation','hardware-rooted-attestation'],'verificationMethod'=>'browser','evidenceIntegrity'=>'unsigned-record'],'verifiedAt'=>gmdate('c'),'manifest'=>['sha256'=>$manifestSha256,'schemaVersion'=>$manifest['schemaVersion']??null,'baselineRef'=>$manifest['baselineRef']??null,'baselineCommit'=>$manifest['baselineCommit']??null,'headCommit'=>$manifest['headCommit']??null,'fileCount'=>count($manifest['files']??[]),'remoteDeletionCount'=>count($manifest['remoteDeletions']??[])]];http_response_code(200);echo json_encode($ev,JSON_PRETTY_PRINT|JSON_UNESCAPED_SLASHES);exit;
'''
    return template.replace("__PAYLOAD__",escaped).replace("__TOKEN__",token_escaped).replace("__ROOT__",root_escaped).replace("__MSHA__",msha)


LEDGER_SCHEMA_VERSION = 1
LEDGER_FILE = ".deploy-pack-history.jsonl"
LEDGER_ZERO_HASH = "0" * 64


def _deployment_record_hash(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "recordHash"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def deployment_history_chain_state(records: list[dict]) -> dict:
    if not records:
        return {"mode": "native", "headHash": None, "recordCount": 0, "errors": []}
    hashed = [
        isinstance(r.get("previousRecordHash"), str) and isinstance(r.get("recordHash"), str)
        for r in records
    ]
    if not any(hashed):
        return {"mode": "legacy-unhashed", "headHash": None, "recordCount": len(records), "errors": []}
    if not all(hashed):
        return {"mode": "mixed-invalid", "headHash": None, "recordCount": len(records),
                "errors": ["deployment history mixes native hash-chained and legacy unhashed records"]}
    errors=[]; previous=LEDGER_ZERO_HASH
    for index, record in enumerate(records, start=1):
        if record.get("previousRecordHash") != previous:
            errors.append(f"record {index}: previousRecordHash mismatch")
        actual=_deployment_record_hash(record)
        if record.get("recordHash") != actual:
            errors.append(f"record {index}: recordHash mismatch")
        previous=record.get("recordHash") or actual
    return {"mode": "native" if not errors else "native-invalid",
            "headHash": previous if records else None, "recordCount": len(records), "errors": errors}


def _upgrade_legacy_deployment_history(records: list[dict]) -> list[dict]:
    state=deployment_history_chain_state(records)
    if state["mode"] == "native":
        return records
    if state["mode"] != "legacy-unhashed":
        raise DeployPackError("deployment history cannot be upgraded: " + "; ".join(state.get("errors") or [state["mode"]]))
    upgraded=[]; previous=LEDGER_ZERO_HASH
    for record in records:
        value=dict(record)
        value["previousRecordHash"]=previous
        value["recordHash"]=_deployment_record_hash(value)
        upgraded.append(value)
        previous=value["recordHash"]
    return upgraded


def deployment_history_anchor(root: Path) -> dict:
    records=read_deployment_history(root)
    state=deployment_history_chain_state(records)
    if state["mode"] == "legacy-unhashed":
        return {"chainMode": state["mode"], "recordCount": len(records), "headRecordHash": None,
                "headCommit": records[-1].get("newBaselineCommit") if records else None}
    if state["errors"]:
        raise DeployPackError("deployment history hash chain is invalid: " + "; ".join(state["errors"]))
    return {"chainMode": state["mode"], "recordCount": len(records), "headRecordHash": state["headHash"],
            "headCommit": records[-1].get("newBaselineCommit") if records else None}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_deployment_history(
    root: Path,
    filename: str = LEDGER_FILE,
) -> list[dict]:
    path = root / filename
    if not path.exists():
        return []

    records: list[dict] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DeployPackError(
                f"invalid deployment history at {path}:{line_no}: {exc}"
            ) from exc
        if value.get("schemaVersion") != LEDGER_SCHEMA_VERSION:
            raise DeployPackError(
                f"unsupported deployment history schema at {path}:{line_no}: "
                f"{value.get('schemaVersion')}"
            )
        records.append(value)
    return records


def append_deployment_history(
    root: Path,
    *,
    previous_baseline: str | None,
    new_baseline_ref: str,
    new_baseline_commit: str,
    evidence_path: Path | None,
    evidence: dict | None,
    archive: Path | None,
    unsafe: bool = False,
    rollback_target_record: int | None = None,
    filename: str = LEDGER_FILE,
) -> dict:
    path = root / filename

    # Validate and, if necessary, upgrade an entirely legacy ledger before extending it.
    existing_records = read_deployment_history(root, filename) if path.exists() else []
    existing_records = _upgrade_legacy_deployment_history(existing_records)
    chain_state = deployment_history_chain_state(existing_records)
    if chain_state.get("errors"):
        raise DeployPackError("deployment history hash chain is invalid: " + "; ".join(chain_state["errors"]))
    record_number = len(existing_records) + 1

    record = {
        "schemaVersion": LEDGER_SCHEMA_VERSION,
        "recordNumber": record_number,
        "recordedAt": _utc_now_iso(),
        "deploymentKind": "rollback" if rollback_target_record is not None else "forward",
        "previousBaseline": previous_baseline,
        "newBaselineRef": new_baseline_ref,
        "newBaselineCommit": new_baseline_commit,
        "unsafeNoEvidence": bool(unsafe),
        "evidence": None,
        "archive": None,
        "rollback": None,
        "previousRecordHash": existing_records[-1]["recordHash"] if existing_records else LEDGER_ZERO_HASH,
    }

    if rollback_target_record is not None:
        if rollback_target_record < 1 or rollback_target_record > len(existing_records):
            raise DeployPackError(
                f"rollback target record {rollback_target_record} does not exist"
            )
        target = existing_records[rollback_target_record - 1]
        target_commit = target.get("newBaselineCommit")
        if target_commit != new_baseline_commit:
            raise DeployPackError(
                "rollback target record does not deploy the commit being marked: "
                f"record {rollback_target_record} has {target_commit}, "
                f"mark resolves to {new_baseline_commit}"
            )
        from_record = len(existing_records) if existing_records else None
        record["rollback"] = {
            "fromRecord": from_record,
            "fromCommit": previous_baseline,
            "targetRecord": rollback_target_record,
            "targetCommit": target_commit,
        }

    if evidence_path is not None and evidence is not None:
        record["evidence"] = {
            "path": str(evidence_path),
            "sha256": sha256_file(evidence_path),
            "verificationScope": evidence.get("verificationScope"),
            "verificationMethod": evidence.get("verificationMethod"),
            "verificationRoot": evidence.get("verificationRoot"),
            "verifiedAt": evidence.get("verifiedAt"),
            "verifier": evidence.get("verifier"),
            "manifestSha256": evidence.get("manifest", {}).get("sha256"),
            "headCommit": evidence.get("manifest", {}).get("headCommit"),
            "trustMode": evidence.get("trustMode"),
            "assurance": infer_assurance(evidence),
        }

    if archive is not None:
        archive = archive.resolve()
        record["archive"] = {
            "path": str(archive),
            "name": archive.name,
            "sha256": sha256_file(archive),
        }

    record["recordHash"] = _deployment_record_hash(record)

    # Rewrite atomically so a crash cannot leave a partial JSONL record.
    records = existing_records + [record]
    text = "".join(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n" for item in records)
    atomic_write_text(path, text)
    return record


def deployment_history_latest(
    root: Path,
    filename: str = LEDGER_FILE,
) -> dict | None:
    records = read_deployment_history(root, filename)
    return records[-1] if records else None


def deployment_history_record(
    root: Path,
    selector: str | int,
    filename: str = LEDGER_FILE,
) -> tuple[int, dict]:
    records = read_deployment_history(root, filename)
    if not records:
        raise DeployPackError("no deployment history recorded")

    if isinstance(selector, int):
        number = selector
    else:
        text = str(selector).strip()
        if text.lower() == "latest":
            number = len(records)
        else:
            try:
                number = int(text)
            except ValueError as exc:
                raise DeployPackError(
                    f"history record must be a 1-based number or 'latest': {selector}"
                ) from exc

    if number < 1 or number > len(records):
        raise DeployPackError(
            f"deployment history record {number} does not exist; "
            f"valid range is 1..{len(records)}"
        )
    return number, records[number - 1]


def format_deployment_history_record(number: int, record: dict) -> str:
    evidence = record.get("evidence") or {}
    archive = record.get("archive") or {}
    rollback = record.get("rollback") or {}
    lines = [
        f"Record              : {number}",
        f"Recorded at         : {record.get('recordedAt')}",
        f"Deployment kind     : {record.get('deploymentKind') or ('rollback' if record.get('rollback') else 'forward')}",
        f"Previous baseline   : {record.get('previousBaseline')}",
        f"New baseline ref    : {record.get('newBaselineRef')}",
        f"New baseline commit : {record.get('newBaselineCommit')}",
        f"Unsafe no evidence  : {bool(record.get('unsafeNoEvidence'))}",
        f"Previous record hash: {record.get('previousRecordHash') or '-'}",
        f"Record hash         : {record.get('recordHash') or '-'}",
    ]
    if rollback:
        lines.extend([
            f"Rollback from record: {rollback.get('fromRecord')}",
            f"Rollback from commit: {rollback.get('fromCommit')}",
            f"Rollback target     : {rollback.get('targetRecord')}",
            f"Rollback target SHA : {rollback.get('targetCommit')}",
        ])
    if evidence:
        lines.extend([
            f"Evidence path       : {evidence.get('path')}",
            f"Evidence SHA-256    : {evidence.get('sha256')}",
            f"Verification scope  : {evidence.get('verificationScope')}",
            f"Verification method : {evidence.get('verificationMethod')}",
            f"Assurance level     : {(evidence.get('assurance') or {}).get('level')}",
            f"Verification root   : {evidence.get('verificationRoot')}",
            f"Verified at         : {evidence.get('verifiedAt')}",
            f"Verified head       : {evidence.get('headCommit')}",
        ])
    if archive:
        lines.extend([
            f"Archive path        : {archive.get('path')}",
            f"Archive SHA-256     : {archive.get('sha256')}",
        ])
    return "\n".join(lines)


def verify_deployment_history(
    root: Path,
    filename: str = LEDGER_FILE,
) -> tuple[bool, list[str]]:
    records = read_deployment_history(root, filename)
    errors: list[str] = []
    chain_state = deployment_history_chain_state(records)
    errors.extend(chain_state.get("errors") or [])

    previous_commit: str | None = None
    for index, record in enumerate(records):
        number = index + 1
        label = f"record {number}"

        stored_number = record.get("recordNumber")
        if stored_number is not None and stored_number != number:
            errors.append(
                f"{label}: stored recordNumber {stored_number!r} does not match position {number}"
            )

        if index > 0:
            expected_previous = previous_commit
            actual_previous = record.get("previousBaseline")
            if actual_previous != expected_previous:
                errors.append(
                    f"{label}: previousBaseline {actual_previous!r} does not "
                    f"match preceding newBaselineCommit {expected_previous!r}"
                )

        new_commit = record.get("newBaselineCommit")
        if not new_commit:
            errors.append(f"{label}: missing newBaselineCommit")
        previous_commit = new_commit

        rollback = record.get("rollback")
        kind = record.get("deploymentKind")
        if rollback is not None or kind == "rollback":
            if not isinstance(rollback, dict):
                errors.append(f"{label}: rollback deployment is missing rollback ancestry")
            else:
                target_number = rollback.get("targetRecord")
                if not isinstance(target_number, int) or not (1 <= target_number < number):
                    errors.append(f"{label}: rollback targetRecord must reference an earlier record")
                else:
                    target = records[target_number - 1]
                    if rollback.get("targetCommit") != target.get("newBaselineCommit"):
                        errors.append(f"{label}: rollback targetCommit does not match target record")
                    if new_commit != target.get("newBaselineCommit"):
                        errors.append(f"{label}: rollback deployed commit does not match target record")
                expected_from_record = number - 1 if number > 1 else None
                if rollback.get("fromRecord") != expected_from_record:
                    errors.append(f"{label}: rollback fromRecord must reference immediately preceding production record")
                if rollback.get("fromCommit") != record.get("previousBaseline"):
                    errors.append(f"{label}: rollback fromCommit does not match previousBaseline")

        if record.get("unsafeNoEvidence"):
            continue

        evidence = record.get("evidence")
        if not isinstance(evidence, dict):
            errors.append(f"{label}: evidence is required for a safe deployment record")
            continue

        if evidence.get("headCommit") != new_commit:
            errors.append(
                f"{label}: evidence headCommit does not match newBaselineCommit"
            )

    baseline = read_baseline(root)
    if records and baseline is not None:
        last_commit = records[-1].get("newBaselineCommit")
        try:
            resolved_baseline = resolve_ref(root, baseline)
        except DeployPackError as exc:
            errors.append(f"current baseline cannot be resolved: {exc}")
        else:
            if resolved_baseline != last_commit:
                errors.append(
                    "current baseline does not match the latest deployment history record"
                )

    return not errors, errors


@dataclass(frozen=True)
class RollbackPlan:
    root: Path
    source_record: int
    source_commit: str
    target_record: int
    target_ref: str
    target_commit: str
    deployable: tuple[Change, ...]
    deletions: tuple[Change, ...]
    ignored: tuple[Change, ...]
    skipped: tuple[tuple[Change, str], ...]


def _history_record_by_number(root: Path, record_number: int) -> dict:
    records = read_deployment_history(root)
    if record_number < 1 or record_number > len(records):
        raise DeployPackError(f"deployment history record does not exist: {record_number}")
    return records[record_number - 1]


def _git_show_bytes(root: Path, commit: str, path: str) -> bytes:
    proc = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise DeployPackError(
            f"cannot read {path!r} from commit {commit}: "
            f"{proc.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return proc.stdout


def _git_ls_tree_mode(root: Path, commit: str, path: str) -> tuple[str, str]:
    proc = subprocess.run(
        ["git", "ls-tree", commit, "--", path],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise DeployPackError(f"cannot inspect {path!r} in commit {commit}")
    left, _ = proc.stdout.rstrip("\n").split("\t", 1)
    mode, obj_type, _obj = left.split()
    return mode, obj_type


def build_rollback_plan(
    root: Path,
    target_record_number: int,
    *,
    source_record_number: int | None = None,
    cli_ignores: Iterable[str] = (),
    cli_includes: Iterable[str] = (),
) -> RollbackPlan:
    records = read_deployment_history(root)
    if not records:
        raise DeployPackError("deployment history is empty")

    target = _history_record_by_number(root, target_record_number)
    latest_number = len(records)

    if source_record_number is None:
        source_record_number = latest_number
        source = records[-1]
        current_baseline_ref = read_baseline(root)
        if not current_baseline_ref:
            raise DeployPackError("no current deployment baseline is recorded")
        source_commit = resolve_ref(root, current_baseline_ref)
        latest_commit = source.get("newBaselineCommit")
        if source_commit != latest_commit:
            raise DeployPackError(
                "current deployment baseline does not match latest history record; "
                "run `deploy-pack history-verify` before planning rollback"
            )
    else:
        source = _history_record_by_number(root, source_record_number)
        source_commit = source.get("newBaselineCommit")
        if not source_commit:
            raise DeployPackError(
                f"history record {source_record_number} has no deployed commit"
            )

    if target_record_number >= source_record_number:
        raise DeployPackError(
            "rollback target must be earlier than the source deployment record"
        )

    target_commit = target.get("newBaselineCommit")
    target_ref = target.get("newBaselineRef") or target_commit
    if not target_commit:
        raise DeployPackError(
            f"history record {target_record_number} has no deployed commit"
        )

    raw = _git(
        root, "diff", "--name-status", "-z", "--find-renames", "--find-copies",
        source_commit, target_commit,
    )
    changes = _parse_name_status(raw, "rollback")

    policy = load_project_policy(root, required=False)
    if policy is None:
        policy = ProjectPolicy("legacy-denylist", (), (), (), "implicit-legacy-internal")
    cli_ignores = tuple(cli_ignores)
    cli_includes = tuple(cli_includes)

    def allowed(path: str) -> bool:
        if protected_artifact_reason(root, path) is not None or _hard_selection_reason(path):
            return False
        if policy.mode == "legacy-denylist":
            includes = policy.include + cli_includes
            ignores = DEFAULT_IGNORE_PATTERNS + policy.exclude + cli_ignores
            if any(matches_pattern(path, p) for p in includes):
                return True
            return not any(matches_pattern(path, p) for p in ignores)
        if not any(matches_pattern(path, p) for p in policy.include):
            return False
        if cli_includes and not any(matches_pattern(path, p) for p in cli_includes):
            return False
        if any(matches_pattern(path, p) for p in policy.exclude):
            return False
        if any(matches_pattern(path, p) for p in cli_ignores):
            return False
        return True

    deployable, deletions, ignored_changes, skipped = [], [], [], []
    for change in changes:
        if "D" in change.status:
            if not allowed(change.path):
                ignored_changes.append(change)
            else:
                deletions.append(Change("D", change.path, None, "rollback"))
            continue
        if not allowed(change.path):
            ignored_changes.append(change)
            if change.status.startswith("R") and change.old_path and allowed(change.old_path):
                deletions.append(Change("D", change.old_path, None, "rollback"))
            continue
        try:
            _mode, obj_type = _git_ls_tree_mode(root, target_commit, change.path)
        except DeployPackError as exc:
            skipped.append((change, str(exc)))
            continue
        if obj_type != "blob":
            skipped.append((change, f"unsupported Git object type: {obj_type}"))
            continue
        deployable.append(change)
        if change.status.startswith("R") and change.old_path:
            deletions.append(Change("D", change.old_path, None, "rollback"))

    unique_deletes = {}
    for d in deletions:
        unique_deletes.setdefault(d.path, d)

    return RollbackPlan(
        root=root,
        source_record=source_record_number,
        source_commit=source_commit,
        target_record=target_record_number,
        target_ref=target_ref,
        target_commit=target_commit,
        deployable=tuple(deployable),
        deletions=tuple(unique_deletes.values()),
        ignored=tuple(ignored_changes),
        skipped=tuple(skipped),
    )

def build_rollback_manifest(plan: RollbackPlan) -> dict:
    files = []
    for change in plan.deployable:
        mode, _ = _git_ls_tree_mode(plan.root, plan.target_commit, change.path)
        data = _git_show_bytes(plan.root, plan.target_commit, change.path)
        if mode == "120000":
            target = data.decode("utf-8")
            files.append({
                "path": change.path,
                "status": change.status,
                "source": "rollback",
                "type": "symlink",
                "mode": "0o777",
                "symlinkTarget": target,
                "sha256": hashlib.sha256(target.encode("utf-8")).hexdigest(),
                "size": len(target.encode("utf-8")),
            })
        else:
            permissions = "0o755" if mode == "100755" else "0o644"
            files.append({
                "path": change.path,
                "status": change.status,
                "source": "rollback",
                "type": "file",
                "mode": permissions,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            })

    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "baselineRef": f"history-record-{plan.source_record}",
        "baselineCommit": plan.source_commit,
        "headCommit": plan.target_commit,
        "rollback": {
            "fromRecord": plan.source_record,
            "fromCommit": plan.source_commit,
            "targetRecord": plan.target_record,
            "targetCommit": plan.target_commit,
            "targetRef": plan.target_ref,
        },
        "files": files,
        "remoteDeletions": [c.path for c in plan.deletions],
    }


def write_rollback_package(
    plan: RollbackPlan,
    output: Path,
) -> tuple[Path | None, Path | None, Path | None, Path]:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = build_rollback_manifest(plan)

    plan_file = output.with_suffix(output.suffix + ".rollback-plan.json")
    plan_file.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    archive = deletion_file = checksum_file = None

    if plan.deployable:
        with zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as zf:
            by_path = {f["path"]: f for f in manifest["files"]}
            for change in plan.deployable:
                entry = by_path[change.path]
                data = _git_show_bytes(plan.root, plan.target_commit, change.path)

                zi = zipfile.ZipInfo(change.path)
                zi.create_system = 3
                zi.compress_type = zipfile.ZIP_DEFLATED

                if entry["type"] == "symlink":
                    zi.external_attr = ((stat.S_IFLNK | 0o777) & 0xFFFF) << 16
                    zf.writestr(zi, entry["symlinkTarget"].encode("utf-8"))
                else:
                    mode = int(entry["mode"], 8)
                    zi.external_attr = ((stat.S_IFREG | mode) & 0xFFFF) << 16
                    zf.writestr(zi, data)

            zf.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            )

        archive = output
        checksum_file = output.with_suffix(output.suffix + ".sha256")
        checksum_file.write_text(
            f"{sha256_file(output)}  {output.name}\n", encoding="utf-8"
        )

    if plan.deletions:
        deletion_file = output.with_suffix(output.suffix + ".deletions.txt")
        deletion_file.write_text(
            "\n".join(c.path for c in plan.deletions) + "\n",
            encoding="utf-8",
        )

    return archive, deletion_file, checksum_file, plan_file


def evidence_is_signed_remote(evidence: dict) -> bool:
    signed = evidence.get("signedRemoteEvidence")
    return (
        evidence.get("verificationScope") == "remote"
        and isinstance(signed, dict)
        and signed.get("algorithm") == "Ed25519"
        and bool(signed.get("publicKeySha256"))
        and bool(signed.get("payloadSha256"))
    )


DEFAULT_OFFLINE_CUSTODY_STALE_GRACE_DAYS = 7


def load_status_policy(root: Path) -> dict:
    policy = {
        "offlineCustodyStaleGraceDays": DEFAULT_OFFLINE_CUSTODY_STALE_GRACE_DAYS,
    }
    path = root / CONFIG_FILE
    if not path.exists():
        return policy
    import tomllib
    value = tomllib.loads(path.read_text(encoding="utf-8"))
    section = value.get("deploy-pack", value)
    raw = section.get(
        "offline_custody_stale_grace_days",
        DEFAULT_OFFLINE_CUSTODY_STALE_GRACE_DAYS,
    )
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise DeployPackError(
            f"{CONFIG_FILE}: offline_custody_stale_grace_days "
            "must be a non-negative integer"
        )
    policy["offlineCustodyStaleGraceDays"] = raw
    return policy


def _latest_recovery_trust_change_at(trust: dict) -> datetime | None:
    candidates = []
    fields = ("trustedAt", "activatedAt", "retiredAt", "revokedAt")
    for signer in (trust.get("signers") or {}).values():
        for field in fields:
            raw = signer.get(field)
            if not raw:
                continue
            try:
                value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                candidates.append(value.astimezone(timezone.utc))
            except Exception:
                continue
    return max(candidates) if candidates else None




def _recovery_trust_stale_causes(checkpoint: dict | None, current: dict) -> tuple[list[dict], str]:
    if not checkpoint:
        return [], "none"

    snapshot = checkpoint.get("recoveryTrustSnapshot")
    if not isinstance(snapshot, dict):
        causes = []
        prior_active = checkpoint.get("activeRecoverySigner")
        current_active = current.get("activeSigner")
        if prior_active != current_active:
            causes.append({
                "type": "active-signer-changed",
                "signerId": current_active,
                "previousSignerId": prior_active,
                "summary": (
                    f"active recovery signer changed from {prior_active or '-'} "
                    f"to {current_active or '-'}"
                ),
                "changedAt": None,
            })
        if not causes:
            causes.append({
                "type": "legacy-checkpoint-snapshot-unavailable",
                "signerId": None,
                "previousSignerId": None,
                "summary": (
                    "recovery trust changed, but this legacy checkpoint did not "
                    "retain the captured trust snapshot needed for an exact diff"
                ),
                "changedAt": None,
            })
        return causes, "partial"

    causes: list[dict] = []
    before_signers = snapshot.get("signers") or {}
    after_signers = current.get("signers") or {}
    before_active = snapshot.get("activeSigner")
    after_active = current.get("activeSigner")

    if before_active != after_active:
        rec = after_signers.get(after_active) or {} if after_active else {}
        changed_at = rec.get("activatedAt") or rec.get("trustedAt")
        causes.append({
            "type": "active-signer-changed",
            "signerId": after_active,
            "previousSignerId": before_active,
            "summary": (
                f"active recovery signer changed from {before_active or '-'} "
                f"to {after_active or '-'}"
            ),
            "changedAt": changed_at,
        })

    before_ids = set(before_signers)
    after_ids = set(after_signers)
    for signer_id in sorted(after_ids - before_ids):
        rec = after_signers[signer_id]
        causes.append({
            "type": "signer-added",
            "signerId": signer_id,
            "previousSignerId": None,
            "status": rec.get("status"),
            "summary": f"recovery signer {signer_id} was added with status {rec.get('status')!r}",
            "changedAt": rec.get("trustedAt") or rec.get("activatedAt"),
        })
    for signer_id in sorted(before_ids - after_ids):
        causes.append({
            "type": "signer-removed",
            "signerId": signer_id,
            "previousSignerId": signer_id,
            "summary": f"recovery signer {signer_id} was removed",
            "changedAt": None,
        })

    lifecycle_fields = {
        "trustedAt", "activatedAt", "retiredAt", "revokedAt",
        "revocationReason", "predecessorSignerId", "trustReason",
        "publicKeySha256", "algorithm",
    }
    for signer_id in sorted(before_ids & after_ids):
        before = before_signers[signer_id]
        after = after_signers[signer_id]
        old_status = before.get("status")
        new_status = after.get("status")
        if old_status != new_status:
            if new_status == "revoked": cause_type = "signer-revoked"
            elif new_status == "retired": cause_type = "signer-retired"
            elif new_status == "active": cause_type = "signer-activated"
            else: cause_type = "signer-status-changed"
            changed_at = (
                after.get("revokedAt") if new_status == "revoked" else
                after.get("retiredAt") if new_status == "retired" else
                after.get("activatedAt") if new_status == "active" else
                after.get("trustedAt")
            )
            causes.append({
                "type": cause_type,
                "signerId": signer_id,
                "previousSignerId": signer_id,
                "summary": (
                    f"recovery signer {signer_id} status changed from "
                    f"{old_status!r} to {new_status!r}"
                ),
                "changedAt": changed_at,
            })

        changed_fields = sorted(
            field for field in lifecycle_fields
            if before.get(field) != after.get(field)
        )
        # Avoid duplicating the lifecycle timestamp that merely evidences an
        # already-reported status transition. Still report other metadata drift.
        transition_fields = {
            "revokedAt" if new_status == "revoked" and old_status != new_status else None,
            "retiredAt" if new_status == "retired" and old_status != new_status else None,
            "activatedAt" if new_status == "active" and old_status != new_status else None,
        }
        transition_fields.discard(None)
        metadata_fields = [f for f in changed_fields if f not in transition_fields]
        if metadata_fields:
            causes.append({
                "type": "signer-metadata-changed",
                "signerId": signer_id,
                "previousSignerId": signer_id,
                "summary": (
                    f"recovery signer {signer_id} metadata changed: "
                    + ", ".join(metadata_fields)
                ),
                "changedAt": (
                    after.get("revokedAt") or after.get("retiredAt") or
                    after.get("activatedAt") or after.get("trustedAt")
                ),
                "fields": metadata_fields,
            })

    if snapshot.get("schemaVersion") != current.get("schemaVersion"):
        causes.append({
            "type": "trust-schema-changed",
            "signerId": None,
            "previousSignerId": None,
            "summary": (
                f"recovery trust schema changed from {snapshot.get('schemaVersion')!r} "
                f"to {current.get('schemaVersion')!r}"
            ),
            "changedAt": None,
        })

    if not causes:
        causes.append({
            "type": "trust-state-changed",
            "signerId": None,
            "previousSignerId": None,
            "summary": "recovery trust snapshot changed in an unclassified field",
            "changedAt": None,
        })
    return causes, "exact"


def _offline_custody_remediation(
    *,
    stale: bool,
    stale_causes: list[dict],
    stale_cause_evidence: str,
    copy_count: int | None,
    quorum: int | None,
    overdue: bool,
) -> dict | None:
    if not stale:
        return None

    safe_copy_count = copy_count if isinstance(copy_count, int) and copy_count >= 2 else 3
    safe_quorum = quorum if isinstance(quorum, int) and 1 <= quorum <= safe_copy_count else ((safe_copy_count // 2) + 1)
    command = (
        "deploy-pack recovery trust export-copies recovery-trust-refresh.json "
        f"--copies {safe_copy_count} --quorum {safe_quorum}"
    )

    cause_types = [cause.get("type") for cause in stale_causes if cause.get("type")]
    priority = "required" if overdue else "recommended"

    # Conservative automation boundary: refreshing custody is automatic-safe
    # only when exact evidence proves that the sole trust-state change is the
    # additive registration of one or more non-active trusted signers. Any
    # mutation of the active anchor, lifecycle/key metadata, removal/revocation,
    # schema, unknown drift, or partial legacy evidence requires operator review.
    automatic_safe = (
        stale_cause_evidence == "exact"
        and bool(stale_causes)
        and all(
            cause.get("type") == "signer-added"
            and cause.get("status") == "trusted"
            for cause in stale_causes
        )
    )
    remediation_class = (
        "automatic-safe" if automatic_safe else "operator-review-required"
    )
    operator_review_reasons = []
    if not automatic_safe:
        if stale_cause_evidence != "exact":
            operator_review_reasons.append(
                "historical cause evidence is incomplete, so automatic remediation cannot safely validate the trust change"
            )
        review_reason_map = {
            "active-signer-changed": "the active recovery-trust anchor changed",
            "signer-revoked": "a recovery signer was revoked",
            "signer-retired": "a recovery signer was retired",
            "signer-activated": "a recovery signer was activated",
            "signer-removed": "a recovery signer was removed",
            "signer-status-changed": "a signer lifecycle status changed",
            "signer-metadata-changed": "security-relevant signer metadata changed",
            "trust-schema-changed": "the recovery-trust schema changed",
            "trust-state-changed": "the trust state changed in an unclassified field",
            "legacy-checkpoint-snapshot-unavailable": "the legacy checkpoint cannot prove the exact trust-state transition",
        }
        for cause_type in cause_types:
            reason = review_reason_map.get(cause_type)
            if reason and reason not in operator_review_reasons:
                operator_review_reasons.append(reason)

    reason_map = {
        "signer-revoked": "a recovery signer was revoked after the latest custody checkpoint",
        "signer-retired": "a recovery signer was retired after the latest custody checkpoint",
        "signer-activated": "a recovery signer was activated after the latest custody checkpoint",
        "active-signer-changed": "the active recovery signer changed after the latest custody checkpoint",
        "signer-added": "a recovery signer was added after the latest custody checkpoint",
        "signer-removed": "a recovery signer was removed after the latest custody checkpoint",
        "signer-status-changed": "a recovery signer lifecycle status changed after the latest custody checkpoint",
        "signer-metadata-changed": "recovery signer metadata changed after the latest custody checkpoint",
        "trust-schema-changed": "the recovery trust schema changed after the latest custody checkpoint",
        "legacy-checkpoint-snapshot-unavailable": "the checkpoint is stale and its legacy format cannot identify the exact trust-state change",
        "trust-state-changed": "the recovery trust state changed after the latest custody checkpoint",
    }
    reasons = []
    for cause_type in cause_types:
        reason = reason_map.get(cause_type)
        if reason and reason not in reasons:
            reasons.append(reason)
    if not reasons:
        reasons.append("the latest offline custody checkpoint no longer matches current recovery trust")

    return {
        "action": "refresh-offline-custody-checkpoint",
        "priority": priority,
        "classification": remediation_class,
        "automaticSafe": automatic_safe,
        "operatorReviewRequired": not automatic_safe,
        "operatorReviewReasons": operator_review_reasons,
        "command": command,
        "copyCount": safe_copy_count,
        "quorum": safe_quorum,
        "causeEvidence": stale_cause_evidence,
        "causeTypes": cause_types,
        "reasons": reasons,
        "expectedResult": "a new offline custody checkpoint matching current recovery trust",
    }

def deployment_status(root: Path) -> dict:
    baseline_ref = read_baseline(root)
    baseline_commit = None
    baseline_error = None
    if baseline_ref:
        try:
            baseline_commit = resolve_ref(root, baseline_ref)
        except DeployPackError as exc:
            baseline_error = str(exc)

    records = read_deployment_history(root)
    latest = records[-1] if records else None
    history_ok, history_errors = verify_deployment_history(root)
    history_chain_state = deployment_history_chain_state(records)

    def _status_json(path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DeployPackError(f"invalid status state file {path}: {exc}") from exc

    verifier_state = _status_json(
        root / ".deploy-pack-verifiers.json",
        {"schemaVersion": 1, "verifiers": {}},
    )
    now = datetime.now(timezone.utc)
    status_policy = load_status_policy(root)
    active = expired = revoked = 0
    active_ids, expired_ids, revoked_ids = [], [], []
    for verifier_id, record in verifier_state.get("verifiers", {}).items():
        if record.get("status") == "revoked":
            revoked += 1; revoked_ids.append(verifier_id); continue
        try:
            raw_expires = record.get("expiresAt")
            expires = datetime.fromisoformat(str(raw_expires).replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            expires = expires.astimezone(timezone.utc)
        except Exception:
            expired += 1; expired_ids.append(verifier_id); continue
        if now > expires:
            expired += 1; expired_ids.append(verifier_id)
        else:
            active += 1; active_ids.append(verifier_id)

    replay_state = _status_json(
        root / ".deploy-pack-replay.json",
        {"schemaVersion": 1, "consumed": {}},
    )

    recovery_trust_state = _status_json(
        root / ".deploy-pack-recovery-trust.json",
        {"schemaVersion": 1, "activeSigner": None, "signers": {}},
    )
    recovery_signers = recovery_trust_state.get("signers", {})
    recovery_counts = {"active": 0, "trusted": 0, "retired": 0, "revoked": 0, "invalid": 0}
    for _signer_id, signer in recovery_signers.items():
        signer_status = signer.get("status")
        if signer_status in {"active", "trusted", "retired", "revoked"}:
            recovery_counts[signer_status] += 1
        else:
            recovery_counts["invalid"] += 1
    recovery_active_signer = recovery_trust_state.get("activeSigner")
    recovery_trust_error = None
    if recovery_active_signer:
        active_record = recovery_signers.get(recovery_active_signer)
        if active_record is None:
            recovery_trust_error = f"active recovery signer {recovery_active_signer!r} is missing"
        elif active_record.get("status") != "active":
            recovery_trust_error = (f"active recovery signer {recovery_active_signer!r} "
                                    f"has status {active_record.get('status')!r}")
    elif recovery_counts["active"] > 0:
        recovery_trust_error = "recovery trust has active signer records but activeSigner is unset"
    if recovery_trust_error is None and recovery_counts["active"] > 1:
        recovery_trust_error = (
            "recovery trust must contain exactly one active signer record matching activeSigner"
        )

    # Offline custody checkpoint/quorum health.
    checkpoint_path = root / ".deploy-pack-offline-checkpoints.jsonl"
    checkpoint_records = []
    checkpoint_errors = []
    previous_checkpoint_hash = "0" * 64
    previous_sequence = 0

    if checkpoint_path.exists():
        for line_no, line in enumerate(
            checkpoint_path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                checkpoint = json.loads(line)
            except json.JSONDecodeError as exc:
                checkpoint_errors.append(
                    f"line {line_no}: invalid JSON: {exc}"
                )
                continue

            sequence = checkpoint.get("sequence")
            if sequence != previous_sequence + 1:
                checkpoint_errors.append(
                    f"checkpoint sequence discontinuity: "
                    f"{sequence!r} after {previous_sequence}"
                )

            if checkpoint.get("previousCheckpointHash") != previous_checkpoint_hash:
                checkpoint_errors.append(
                    f"checkpoint {sequence}: previousCheckpointHash mismatch"
                )

            checkpoint_body = {
                k: v
                for k, v in checkpoint.items()
                if k != "checkpointHash"
            }
            actual_checkpoint_hash = hashlib.sha256(
                json.dumps(
                    checkpoint_body,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if checkpoint.get("checkpointHash") != actual_checkpoint_hash:
                checkpoint_errors.append(
                    f"checkpoint {sequence}: checkpointHash mismatch"
                )

            checkpoint_records.append(checkpoint)
            previous_checkpoint_hash = (
                checkpoint.get("checkpointHash") or actual_checkpoint_hash
            )
            if isinstance(sequence, int):
                previous_sequence = sequence

    latest_checkpoint = checkpoint_records[-1] if checkpoint_records else None
    custody_configured = latest_checkpoint is not None
    custody_copy_count = latest_checkpoint.get("copyCount") if latest_checkpoint else None
    custody_quorum = latest_checkpoint.get("quorum") if latest_checkpoint else None
    custody_sequence = latest_checkpoint.get("sequence") if latest_checkpoint else None
    custody_checkpoint_id = (
        latest_checkpoint.get("checkpointId")
        if latest_checkpoint
        else None
    )
    custody_trust_sha = (
        latest_checkpoint.get("recoveryTrustSha256")
        if latest_checkpoint
        else None
    )
    current_recovery_trust_sha = hashlib.sha256(
        json.dumps(
            recovery_trust_state,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    custody_stale = (
        custody_configured
        and custody_trust_sha != current_recovery_trust_sha
    )
    custody_stale_causes, custody_stale_cause_evidence = (
        _recovery_trust_stale_causes(latest_checkpoint, recovery_trust_state)
        if custody_stale
        else ([], "none")
    )
    custody_grace_days = status_policy["offlineCustodyStaleGraceDays"]
    custody_stale_since = None
    custody_stale_age_days = None
    custody_grace_due_at = None
    custody_grace_remaining_days = None
    custody_stale_overdue = False

    if custody_stale:
        checkpoint_created_at = None
        raw_checkpoint_created = latest_checkpoint.get("createdAt") if latest_checkpoint else None
        if raw_checkpoint_created:
            try:
                checkpoint_created_at = datetime.fromisoformat(
                    str(raw_checkpoint_created).replace("Z", "+00:00")
                )
                if checkpoint_created_at.tzinfo is None:
                    checkpoint_created_at = checkpoint_created_at.replace(
                        tzinfo=timezone.utc
                    )
                checkpoint_created_at = checkpoint_created_at.astimezone(timezone.utc)
            except Exception:
                checkpoint_created_at = None

        latest_trust_change_at = _latest_recovery_trust_change_at(
            recovery_trust_state
        )
        if latest_trust_change_at and checkpoint_created_at:
            if latest_trust_change_at > checkpoint_created_at:
                custody_stale_since = latest_trust_change_at
        elif latest_trust_change_at and checkpoint_created_at is None:
            custody_stale_since = latest_trust_change_at

        if custody_stale_since is not None:
            stale_seconds = max(0.0, (now - custody_stale_since).total_seconds())
            custody_stale_age_days = stale_seconds / 86400.0
            custody_grace_due_at = custody_stale_since + timedelta(
                days=custody_grace_days
            )
            custody_stale_overdue = now > custody_grace_due_at
            custody_grace_remaining_days = max(
                0.0,
                (custody_grace_due_at - now).total_seconds() / 86400.0,
            )
        elif custody_grace_days == 0:
            custody_stale_overdue = True

    custody_freshness = (
        "STALE_OVERDUE" if custody_stale and custody_stale_overdue
        else (
            "STALE_GRACE" if custody_stale
            else ("CURRENT" if custody_configured else "UNCONFIGURED")
        )
    )

    custody_remediation = _offline_custody_remediation(
        stale=custody_stale,
        stale_causes=custody_stale_causes,
        stale_cause_evidence=custody_stale_cause_evidence,
        copy_count=custody_copy_count,
        quorum=custody_quorum,
        overdue=custody_stale_overdue,
    )

    custody_unique_copy_ids = 0
    custody_unique_signers = 0
    custody_recorded_copies = 0
    custody_quorum_met = None
    custody_error = None

    if latest_checkpoint:
        copies = latest_checkpoint.get("copies") or []
        custody_recorded_copies = len(copies)
        copy_ids = [
            copy.get("copyId")
            for copy in copies
            if copy.get("copyId")
        ]
        signer_fingerprints = [
            copy.get("publicKeySha256")
            for copy in copies
            if copy.get("publicKeySha256")
        ]
        custody_unique_copy_ids = len(set(copy_ids))
        custody_unique_signers = len(set(signer_fingerprints))

        if not isinstance(custody_copy_count, int) or custody_copy_count < 2:
            custody_error = "latest checkpoint has invalid copyCount"
        elif not isinstance(custody_quorum, int) or custody_quorum < 1:
            custody_error = "latest checkpoint has invalid quorum"
        elif custody_quorum > custody_copy_count:
            custody_error = (
                "latest checkpoint quorum exceeds copy count"
            )
        elif custody_recorded_copies != custody_copy_count:
            custody_error = (
                f"latest checkpoint records {custody_recorded_copies} copies "
                f"but declares {custody_copy_count}"
            )
        elif custody_unique_copy_ids < custody_quorum:
            custody_error = (
                f"latest checkpoint has only {custody_unique_copy_ids} "
                f"unique custody copy IDs; quorum requires {custody_quorum}"
            )
        elif custody_unique_signers < custody_quorum:
            custody_error = (
                f"latest checkpoint has only {custody_unique_signers} "
                f"independent signer fingerprints; quorum requires "
                f"{custody_quorum}"
            )
        else:
            custody_quorum_met = True

    custody_chain_healthy = not checkpoint_errors
    custody_healthy = (
        not custody_configured
        or (custody_chain_healthy and custody_error is None)
    )

    patterns = {
        "packages": "*.deploy.zip",
        "checksums": "*.deploy.zip.sha256",
        "deletionLists": "*.deploy.zip.deletions.txt",
        "rollbackPlans": "*.deploy.zip.rollback-plan.json",
        "verificationEvidence": "*.verify-evidence.json",
        "remoteEvidence": "*.remote-verify-evidence.json",
        "signedRemoteEvidence": "*.signed.json",
        "remoteVerifiers": "*.verify.php",
        "browserVerifiers": "*.verify-browser.php",
        "pythonVerifiers": "*.verify.py",
    }
    artifacts = {
        key: sorted(str(p.relative_to(root)) for p in root.glob(pattern) if p.is_file())
        for key, pattern in patterns.items()
    }
    pending_packages = []
    for rel in artifacts["packages"]:
        archive = root / rel
        pending_packages.append({
            "path": rel,
            "hasChecksum": archive.with_suffix(archive.suffix + ".sha256").exists(),
            "hasDeletionList": archive.with_suffix(archive.suffix + ".deletions.txt").exists(),
            "hasVerificationEvidence": any(p.exists() for p in [
                archive.with_name(archive.name + ".verify-evidence.json"),
                archive.with_name(archive.name + ".remote-verify-evidence.json"),
            ]),
        })

    latest_trust = latest_kind = latest_record_number = latest_assurance = None
    if latest:
        latest_record_number = len(records)
        latest_kind = latest.get("deploymentKind", "forward")
        latest_evidence = latest.get("evidence") or {}
        latest_trust = latest_evidence.get("trustMode")
        latest_assurance = (latest_evidence.get("assurance") or {}).get("level")
        if latest.get("unsafeNoEvidence"):
            latest_trust = "unsafe-no-evidence"

    # HARDEN-17: compare the native deployment-ledger chain with the latest offline custody anchor.
    live_ledger_head = history_chain_state.get("headHash")
    live_ledger_mode = history_chain_state.get("mode")
    ledger_anchor = (latest_checkpoint or {}).get("deploymentLedger") if latest_checkpoint else None
    ledger_anchor_status = "UNANCHORED"
    ledger_anchor_error = None
    if ledger_anchor:
        anchored_count = ledger_anchor.get("recordCount")
        anchored_hash = ledger_anchor.get("headRecordHash")
        if live_ledger_mode != "native":
            ledger_anchor_status = "INVALID"
            ledger_anchor_error = "offline custody anchors a deployment ledger but live ledger is not a valid native chain"
        elif not isinstance(anchored_count, int) or anchored_count < 0:
            ledger_anchor_status = "INVALID"
            ledger_anchor_error = "offline custody deployment-ledger anchor has invalid recordCount"
        elif anchored_count > len(records):
            ledger_anchor_status = "MISMATCH"
            ledger_anchor_error = "live deployment ledger is shorter than the offline custody anchor"
        elif anchored_count == 0:
            expected = None
            if anchored_hash is not None:
                ledger_anchor_status = "MISMATCH"; ledger_anchor_error = "empty deployment-ledger anchor has unexpected head hash"
            else:
                ledger_anchor_status = "CURRENT" if len(records) == 0 else "ADVANCED"
        else:
            prefix_hash = records[anchored_count - 1].get("recordHash")
            if prefix_hash != anchored_hash:
                ledger_anchor_status = "MISMATCH"
                ledger_anchor_error = "live deployment ledger no longer matches the offline anchored history prefix"
            elif len(records) == anchored_count:
                ledger_anchor_status = "CURRENT"
            else:
                ledger_anchor_status = "ADVANCED"

    transaction_journal = root / MARK_JOURNAL_FILE
    transaction_recovery_pending = transaction_journal.exists()

    health = "PASS"; problems = []; warnings = []
    if transaction_recovery_pending:
        health = "FAIL"
        problems.append("transaction: incomplete mark transaction requires recovery")
    if live_ledger_mode == "legacy-unhashed" and records:
        warnings.append("deployment history is legacy/unhashed; next mark will upgrade it to the native ledger chain")
    if ledger_anchor_status == "ADVANCED":
        warnings.append("offline custody deployment-ledger anchor is valid but does not cover the latest deployment records")
    if ledger_anchor_error:
        health = "FAIL"
        problems.append(f"deployment ledger anchor: {ledger_anchor_error}")
    if custody_stale and not custody_stale_overdue:
        if custody_grace_due_at is not None:
            warnings.append(
                "offline custody checkpoint is stale relative to current "
                f"recovery trust; grace expires {custody_grace_due_at.isoformat()}"
            )
        else:
            warnings.append(
                "offline custody checkpoint is stale relative to current recovery trust; "
                "stale age could not be determined"
            )
    if custody_stale_overdue:
        health = "FAIL"
        problems.append(
            "offline custody: stale checkpoint exceeded configured grace period "
            f"({custody_grace_days} day(s))"
        )
    if baseline_error:
        health = "FAIL"; problems.append(f"baseline: {baseline_error}")
    if recovery_trust_error:
        health = "FAIL"; problems.append(f"recovery trust: {recovery_trust_error}")
    if recovery_counts["invalid"]:
        health = "FAIL"; problems.append(
            f"recovery trust: {recovery_counts['invalid']} signer record(s) have invalid status"
        )
    if checkpoint_errors:
        health = "FAIL"
        problems.extend(
            f"offline custody: {error}"
            for error in checkpoint_errors
        )
    if custody_error:
        health = "FAIL"
        problems.append(f"offline custody: {custody_error}")
    if not history_ok:
        health = "FAIL"; problems.extend(f"history: {e}" for e in history_errors)
    if baseline_ref and latest and baseline_commit != latest.get("newBaselineCommit"):
        health = "FAIL"; problems.append("baseline does not match latest ledger deployment commit")

    return {
        "health": health,
        "problems": problems,
        "warnings": warnings,
        "baseline": {"ref": baseline_ref, "commit": baseline_commit},
        "history": {
            "records": len(records), "latestRecord": latest_record_number,
            "latestRef": latest.get("newBaselineRef") if latest else None,
            "latestCommit": latest.get("newBaselineCommit") if latest else None,
            "latestKind": latest_kind, "latestTrustMode": latest_trust, "latestAssuranceLevel": latest_assurance,
            "verified": history_ok,
            "chainMode": live_ledger_mode,
            "headRecordHash": live_ledger_head,
            "offlineAnchor": {
                "status": ledger_anchor_status,
                "checkpointSequence": custody_sequence if ledger_anchor else None,
                "anchoredRecordCount": ledger_anchor.get("recordCount") if ledger_anchor else None,
                "anchoredHeadRecordHash": ledger_anchor.get("headRecordHash") if ledger_anchor else None,
                "liveRecordCount": len(records),
                "liveHeadRecordHash": live_ledger_head,
            },
        },
        "verifiers": {"active": active, "expired": expired, "revoked": revoked,
                      "activeIds": active_ids, "expiredIds": expired_ids, "revokedIds": revoked_ids},
        "replay": {"consumedEvidence": len(replay_state.get("consumed", {}))},
        "transaction": {
            "recoveryPending": transaction_recovery_pending,
            "journal": MARK_JOURNAL_FILE if transaction_recovery_pending else None,
            "healthy": not transaction_recovery_pending,
        },
        "recoveryTrust": {
            "activeSigner": recovery_active_signer,
            "active": recovery_counts["active"],
            "trusted": recovery_counts["trusted"],
            "retired": recovery_counts["retired"],
            "revoked": recovery_counts["revoked"],
            "invalid": recovery_counts["invalid"],
            "total": len(recovery_signers),
            "healthy": recovery_trust_error is None and recovery_counts["invalid"] == 0,
        },
        "offlineCustody": {
            "configured": custody_configured,
            "healthy": custody_healthy,
            "checkpointChainHealthy": custody_chain_healthy,
            "checkpointRecords": len(checkpoint_records),
            "latestSequence": custody_sequence,
            "latestCheckpointId": custody_checkpoint_id,
            "recoveryTrustSha256": custody_trust_sha,
            "currentRecoveryTrustSha256": current_recovery_trust_sha,
            "stale": custody_stale,
            "freshness": custody_freshness,
            "staleCauses": custody_stale_causes,
            "staleCauseEvidence": custody_stale_cause_evidence,
            "remediation": custody_remediation,
            "staleOverdue": custody_stale_overdue,
            "staleSince": (
                custody_stale_since.isoformat()
                if custody_stale_since is not None
                else None
            ),
            "staleAgeDays": custody_stale_age_days,
            "staleGraceDays": custody_grace_days,
            "staleGraceDueAt": (
                custody_grace_due_at.isoformat()
                if custody_grace_due_at is not None
                else None
            ),
            "staleGraceRemainingDays": custody_grace_remaining_days,
            "declaredCopyCount": custody_copy_count,
            "declaredQuorum": custody_quorum,
            "recordedCopies": custody_recorded_copies,
            "uniqueCopyIds": custody_unique_copy_ids,
            "independentSigners": custody_unique_signers,
            "quorumMet": custody_quorum_met,
            "errors": checkpoint_errors + ([custody_error] if custody_error else []),
        },
        "pending": {"packages": pending_packages, "artifacts": artifacts},
    }
