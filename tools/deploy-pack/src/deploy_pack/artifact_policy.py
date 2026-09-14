from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .core import DeployPackError

_POLICY_FILE = ".deploy-pack.toml"
_ALLOWED_KEYS = {"include", "exclude"}


def _normalize_pattern(raw: str, *, field: str) -> str:
    if not isinstance(raw, str):
        raise DeployPackError(f"{field} entries must be strings")
    value = raw.strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    if not value:
        raise DeployPackError(f"{field} entries must not be empty")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise DeployPackError(f"{field} pattern must be artifact-relative: {raw}")
    parts = value.split("/")
    if any(part in {".", ".."} for part in parts):
        raise DeployPackError(f"{field} pattern must not contain . or .. segments: {raw}")
    if "\x00" in value:
        raise DeployPackError(f"{field} pattern contains a NUL byte")
    if value.endswith("/"):
        value += "**"
    return value


def _glob_body(pattern: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                if i + 2 < len(pattern) and pattern[i + 2] == "/":
                    out.append(r"(?:.*/)?")
                    i += 3
                    continue
                out.append(r".*")
                i += 2
                continue
            out.append(r"[^/]*")
            i += 1
            continue
        if ch == "?":
            out.append(r"[^/]")
            i += 1
            continue
        out.append(re.escape(ch))
        i += 1
    return "".join(out)


@lru_cache(maxsize=512)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return re.compile("^" + _glob_body(prefix) + r"(?:/.*)?$")
    return re.compile("^" + _glob_body(pattern) + "$")


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(_glob_regex(pattern).fullmatch(path) is not None for pattern in patterns)


@dataclass(frozen=True)
class ArtifactPolicy:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    source: Path | None = None

    def includes(self, path: str) -> bool:
        return not self.include or _matches(path, self.include)

    def excludes(self, path: str) -> bool:
        return _matches(path, self.exclude)


def _string_list(section: dict, key: str) -> tuple[str, ...]:
    value = section.get(key, [])
    if not isinstance(value, list):
        raise DeployPackError(f"[artifact].{key} must be an array of strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in value:
        item = _normalize_pattern(raw, field=f"[artifact].{key}")
        if item not in seen:
            seen.add(item)
            normalized.append(item)
    return tuple(normalized)


def _has_artifact_table(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise DeployPackError(f"cannot read artifact policy {path}: {exc}") from exc
    return "artifact" in data


def _policy_file(source_root: Path) -> Path | None:
    # Artifact mode is source-authoritative. A config in --source wins over
    # the invocation directory. A config without [artifact] must not shadow
    # a candidate that actually defines artifact policy.
    candidates = [source_root / _POLICY_FILE, Path.cwd() / _POLICY_FILE]
    seen: set[Path] = set()
    fallback: Path | None = None

    for candidate in candidates:
        resolved = candidate.expanduser().resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        if not candidate.is_file():
            continue
        if fallback is None:
            fallback = candidate
        if _has_artifact_table(candidate):
            return candidate

    return fallback


def load_artifact_policy(source_root: Path) -> ArtifactPolicy:
    path = _policy_file(source_root)
    if path is None:
        return ArtifactPolicy()

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise DeployPackError(f"cannot read artifact policy {path}: {exc}") from exc

    section = data.get("artifact")
    if section is None:
        return ArtifactPolicy(source=path.resolve())
    if not isinstance(section, dict):
        raise DeployPackError("[artifact] must be a TOML table")

    unknown = sorted(set(section) - _ALLOWED_KEYS)
    if unknown:
        rendered = ", ".join(unknown)
        raise DeployPackError(f"unknown [artifact] setting(s): {rendered}")

    return ArtifactPolicy(
        include=_string_list(section, "include"),
        exclude=_string_list(section, "exclude"),
        source=path.resolve(),
    )
