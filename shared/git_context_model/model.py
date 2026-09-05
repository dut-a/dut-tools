from __future__ import annotations
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

class ConfigError(RuntimeError):
    pass

@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    email: str

@dataclass(frozen=True)
class Context:
    name: str
    root: Path
    profile: str

@dataclass(frozen=True)
class GitContextModel:
    profiles: dict[str, Profile]
    contexts: list[Context]

def expand(value: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve()

def load_git_context(path: Path) -> GitContextModel:
    if not path.is_file():
        raise ConfigError(f"git-context configuration not found: {path}")
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    if raw.get("version") != 1:
        raise ConfigError("git-context contexts.toml must contain version = 1")

    profiles = {}
    for key, data in raw.get("profiles", {}).items():
        if not isinstance(data, dict):
            raise ConfigError(f"profile {key!r} must be a table")
        name = str(data.get("name", "")).strip()
        email = str(data.get("email", "")).strip()
        if not name or not email:
            raise ConfigError(f"profile {key!r} requires name and email")
        profiles[key] = Profile(key, name, email)

    contexts = []
    for idx, data in enumerate(raw.get("contexts", [])):
        if not isinstance(data, dict):
            raise ConfigError(f"contexts[{idx}] must be a table")
        name = str(data.get("name", "")).strip()
        root = data.get("root")
        profile = str(data.get("profile", "")).strip()
        if not name or not root or not profile:
            raise ConfigError(f"contexts[{idx}] requires name, root, profile")
        if profile not in profiles:
            raise ConfigError(f"context {name!r} references unknown profile {profile!r}")
        contexts.append(Context(name, expand(root), profile))
    contexts.sort(key=lambda c: len(c.root.parts), reverse=True)
    return GitContextModel(profiles, contexts)

def resolve_profile_for_path(model: GitContextModel, path: Path, pinned_profile: str | None = None):
    path = path.resolve()
    if pinned_profile:
        profile = model.profiles.get(pinned_profile)
        if profile is None:
            raise ConfigError(f"unknown pinned profile {pinned_profile!r}")
        return profile, f"pin:{pinned_profile}"
    for context in model.contexts:
        try:
            path.relative_to(context.root.resolve())
            return model.profiles[context.profile], context.name
        except ValueError:
            pass
    return None, None
