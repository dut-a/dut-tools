from __future__ import annotations

import gzip
import json
import os
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .core import DeployPackError, _validate_relative_path, _validate_symlink_target

_ARTIFACT_JUNK_BASENAMES = {".DS_Store"}
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


def _walk(source_root: Path) -> tuple[ArtifactEntry, ...]:
    entries: list[ArtifactEntry] = []
    seen: set[str] = set()

    def visit(directory: Path) -> None:
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise DeployPackError(f"cannot read artifact directory {directory}: {exc}") from exc

        for child in children:
            if child.name in _ARTIFACT_JUNK_BASENAMES:
                continue
            local = Path(child.path)
            relative = local.relative_to(source_root)
            member = _canonical_member(relative)
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
    entries = _walk(source_root)
    required_paths = _validate_required(source_root, required)
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
