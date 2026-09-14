from __future__ import annotations

import gzip
import json
import os
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .artifact_policy import ArtifactPolicy, load_artifact_policy
from .core import DeployPackError, _validate_relative_path, _validate_symlink_target

# Artifact mode packages an already-built deployment tree, but it must not
# blindly ship repository metadata, local environment state, test/build caches,
# CI/editor configuration, or developer tooling.
#
# This policy is deliberately independent of Git-aware `pack` policy and
# `.gitignore`: build output is often Git-ignored and may contain legitimate
# hidden deployment files such as `.htaccess`, `.user.ini`, and `.well-known/`.
#
# Do NOT add ambiguous deployment directories such as `dist`, `build`, `vendor`,
# `public`, or `assets` here. They may be the intended payload.
_ARTIFACT_EXCLUDED_DIR_BASENAMES = {
    # VCS / CI / project-local metadata
    ".git",
    ".hg",
    ".svn",
    ".github",
    ".gitlab",
    ".circleci",
    ".tembeek",

    # dependency / package-manager stores
    "node_modules",
    ".pnpm-store",
    ".venv",
    "venv",
    ".tox",
    ".nox",

    # caches / temporary state
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    ".tmp",
    "tmp",

    # editor / IDE state
    ".idea",
    ".vscode",

    # test / QA output
    "coverage",
    ".nyc_output",
    "playwright-report",
    "test-results",

    # common repository-only content
    "docs",
    "tests",
    "test",
}

_ARTIFACT_EXCLUDED_FILE_BASENAMES = {
    # OS junk
    ".DS_Store",
    "Thumbs.db",

    # deploy-pack / VCS control files
    ".deploy-pack.toml",
    ".deploy-pack-baseline",
    ".deploy-pack-history.jsonl",
    ".deploy-pack.lock",
    ".gitignore",
    ".gitattributes",

    # local environment / runtime-selection files
    ".env",
    ".env.local",
    ".env.development",
    ".env.test",
    ".nvmrc",

    # editor / formatter / lint configuration
    ".editorconfig",
    ".prettierignore",
    ".prettierrc",
    ".prettierrc.json",
    ".prettierrc.js",
    ".prettierrc.cjs",

    # common repository documentation/build orchestration
    "Makefile",
}

_ARTIFACT_EXCLUDED_FILE_SUFFIXES = {
    ".log",
    ".pyc",
    ".pyo",
}
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_TAR_EPOCH = 0


@dataclass(frozen=True)
class ArtifactEntry:
    path: str
    source: Path
    kind: str
    mode: int
    symlink_target: str | None = None


@dataclass(frozen=True)
class ArtifactPlan:
    source: Path
    output: Path
    format: str
    entries: tuple[ArtifactEntry, ...]
    required: tuple[str, ...]

    @property
    def file_count(self) -> int:
        return sum(1 for entry in self.entries if entry.kind != "directory")


def _canonical_member(relative: Path) -> str:
    value = relative.as_posix()
    return _validate_relative_path(value, field="artifact path")


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_layout(source: Path, output: Path) -> tuple[Path, Path]:
    if not source.exists():
        raise DeployPackError(f"artifact source does not exist: {source}")
    if not source.is_dir():
        raise DeployPackError(f"artifact source is not a directory: {source}")

    source_root = source.resolve(strict=True)
    output_path = output.expanduser().resolve(strict=False)
    if _within(output_path, source_root):
        raise DeployPackError(
            "artifact output must not be inside the source directory: "
            f"source={source_root} output={output_path}"
        )
    if output_path == source_root:
        raise DeployPackError("artifact output collides with the source directory")
    return source_root, output_path


def _artifact_policy_may_descend(directory_member: str, policy: ArtifactPolicy) -> bool:
    if policy.excludes(directory_member) or policy.excludes(directory_member + "/"):
        return False
    if not policy.include:
        return True

    prefix = directory_member.rstrip("/") + "/"
    for pattern in policy.include:
        if policy.includes(directory_member):
            return True
        wildcard_positions = [i for i in (pattern.find("*"), pattern.find("?")) if i >= 0]
        wildcard_at = min(wildcard_positions) if wildcard_positions else len(pattern)
        static_prefix = pattern[:wildcard_at]
        if static_prefix.startswith(prefix) or prefix.startswith(static_prefix):
            return True
    return False

def _walk(source_root: Path, policy: ArtifactPolicy) -> tuple[ArtifactEntry, ...]:
    entries: list[ArtifactEntry] = []
    seen: set[str] = set()

    def visit(directory: Path) -> None:
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise DeployPackError(f"cannot read artifact directory {directory}: {exc}") from exc

        for child in children:
            # Exclusions are basename-based at every depth. Directory exclusions
            # prune the subtree entirely; file exclusions omit only that file.
            #
            # Do not broaden this to "all dotfiles": deployment-significant files
            # such as .htaccess, .user.ini, and .well-known must remain packageable.
            if child.is_dir(follow_symlinks=False):
                if child.name in _ARTIFACT_EXCLUDED_DIR_BASENAMES:
                    continue
            elif not child.is_symlink():
                if child.name in _ARTIFACT_EXCLUDED_FILE_BASENAMES:
                    continue
                if any(child.name.endswith(suffix) for suffix in _ARTIFACT_EXCLUDED_FILE_SUFFIXES):
                    continue

            local = Path(child.path)
            relative = local.relative_to(source_root)
            member = _canonical_member(relative)

            if child.is_dir(follow_symlinks=False):
                if policy.excludes(member) or not _artifact_policy_may_descend(member, policy):
                    continue
            elif not child.is_symlink():
                if policy.excludes(member):
                    continue
                if policy.include and not policy.includes(member):
                    continue

            if member in seen:
                raise DeployPackError(f"duplicate artifact member after canonicalization: {member}")
            seen.add(member)

            try:
                st = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise DeployPackError(f"cannot stat artifact path {local}: {exc}") from exc
            mode = stat.S_IMODE(st.st_mode)

            if child.is_symlink():
                try:
                    target = os.readlink(local)
                except OSError as exc:
                    raise DeployPackError(f"cannot read symlink {local}: {exc}") from exc
                canonical_target = _validate_symlink_target(member, target)
                # _validate_symlink_target enforces a relative target that cannot
                # escape the logical archive root. It does not dereference it.
                entries.append(ArtifactEntry(member, local, "symlink", mode, canonical_target))
                continue

            if child.is_dir(follow_symlinks=False):
                entries.append(ArtifactEntry(member, local, "directory", mode))
                visit(local)
                continue

            if child.is_file(follow_symlinks=False):
                entries.append(ArtifactEntry(member, local, "file", mode))
                continue

            raise DeployPackError(f"unsupported artifact path type: {member}")

    visit(source_root)
    return tuple(sorted(entries, key=lambda entry: entry.path))



def _apply_artifact_policy(
    entries: tuple[ArtifactEntry, ...],
    policy: ArtifactPolicy,
) -> tuple[ArtifactEntry, ...]:
    candidates = tuple(entry for entry in entries if not policy.excludes(entry.path))
    if not policy.include:
        return candidates

    direct = {entry.path for entry in candidates if policy.includes(entry.path)}
    if not direct:
        raise DeployPackError(
            "artifact include policy selected no paths; "
            "check [artifact].include in .deploy-pack.toml"
        )

    selected = set(direct)
    available = {entry.path for entry in candidates}
    for path in tuple(direct):
        parts = path.split("/")
        for index in range(1, len(parts)):
            ancestor = "/".join(parts[:index])
            if ancestor in available:
                selected.add(ancestor)

    return tuple(entry for entry in candidates if entry.path in selected)


def _validate_required_selected(
    required: tuple[str, ...],
    entries: tuple[ArtifactEntry, ...],
) -> None:
    selected = {entry.path for entry in entries}
    for value in required:
        if value not in selected:
            raise DeployPackError(
                f"required artifact path is excluded by artifact policy: {value}"
            )


def _validate_required(source_root: Path, required: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in required:
        value = _validate_relative_path(raw, field="required artifact path")
        if value in seen:
            continue
        seen.add(value)
        target = source_root.joinpath(*value.split("/"))
        # lexists semantics: a required symlink itself counts, but it still had
        # to pass the artifact symlink policy during the walk.
        if not os.path.lexists(target):
            raise DeployPackError(f"required artifact path is absent: {value}")
        normalized.append(value)
    return tuple(normalized)


def build_artifact_plan(
    source: Path,
    output: Path,
    archive_format: str,
    *,
    required: list[str] | tuple[str, ...] = (),
) -> ArtifactPlan:
    if archive_format not in {"zip", "tar.gz"}:
        raise DeployPackError(f"unsupported artifact format: {archive_format}")
    source_root, output_path = _validate_layout(source.expanduser(), output)
    policy = load_artifact_policy(source_root)
    entries = _apply_artifact_policy(_walk(source_root, policy), policy)
    required_paths = _validate_required(source_root, required)
    _validate_required_selected(required_paths, entries)
    return ArtifactPlan(source_root, output_path, archive_format, entries, required_paths)


def _zip_info(name: str, mode: int, *, kind: str) -> zipfile.ZipInfo:
    archive_name = name + "/" if kind == "directory" else name
    info = zipfile.ZipInfo(archive_name, date_time=_ZIP_EPOCH)
    info.create_system = 3
    if kind == "directory":
        file_type = stat.S_IFDIR
        info.external_attr = ((file_type | mode) & 0xFFFF) << 16
        info.external_attr |= 0x10
        info.compress_type = zipfile.ZIP_STORED
    elif kind == "symlink":
        info.external_attr = ((stat.S_IFLNK | mode) & 0xFFFF) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
    else:
        info.external_attr = ((stat.S_IFREG | mode) & 0xFFFF) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _write_zip(plan: ArtifactPlan, temporary: Path) -> None:
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for entry in plan.entries:
            info = _zip_info(entry.path, entry.mode, kind=entry.kind)
            if entry.kind == "directory":
                archive.writestr(info, b"")
            elif entry.kind == "symlink":
                archive.writestr(info, (entry.symlink_target or "").encode("utf-8"))
            else:
                archive.writestr(info, entry.source.read_bytes())


def _tar_info(entry: ArtifactEntry) -> tarfile.TarInfo:
    info = tarfile.TarInfo(entry.path)
    info.mtime = _TAR_EPOCH
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = entry.mode
    if entry.kind == "directory":
        info.type = tarfile.DIRTYPE
        info.size = 0
    elif entry.kind == "symlink":
        info.type = tarfile.SYMTYPE
        info.linkname = entry.symlink_target or ""
        info.size = 0
    else:
        info.type = tarfile.REGTYPE
        info.size = entry.source.stat().st_size
    return info


def _write_tar_gz(plan: ArtifactPlan, temporary: Path) -> None:
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=_TAR_EPOCH) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for entry in plan.entries:
                    info = _tar_info(entry)
                    if entry.kind == "file":
                        with entry.source.open("rb") as stream:
                            archive.addfile(info, fileobj=stream)
                    else:
                        archive.addfile(info)


def write_artifact(plan: ArtifactPlan) -> Path:
    output = plan.output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp-deploy-pack-artifact")
    if temporary.exists():
        temporary.unlink()
    try:
        if plan.format == "zip":
            _write_zip(plan, temporary)
        else:
            _write_tar_gz(plan, temporary)
        os.replace(temporary, output)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return output


def result_dict(plan: ArtifactPlan) -> dict:
    return {
        "mode": "artifact",
        "source": str(plan.source),
        "format": plan.format,
        "files": plan.file_count,
        "members": len(plan.entries),
        "required": list(plan.required),
        "output": str(plan.output),
    }
