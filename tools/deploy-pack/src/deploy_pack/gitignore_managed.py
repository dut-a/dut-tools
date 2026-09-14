from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

BEGIN_MARKER = "# BEGIN deploy-pack managed ignores"
END_MARKER = "# END deploy-pack managed ignores"

DURABLE_TRACKED_PATHS = (
    ".deploy-pack.toml",
    ".deploy-pack-baseline",
    ".deploy-pack-history.jsonl",
)

IGNORE_RULES = (
    ".deploy-pack.lock",
    ".deploy-pack-closeout-session.json",
    "",
    "# Mutable deploy-pack runtime/security state",
    ".deploy-pack-keyring.json",
    ".deploy-pack-replay.json",
    ".deploy-pack-verifiers.json",
    "",
    "# Deployment archives",
    "deploy.zip",
    "deploy.tar.gz",
    "deploy-*.zip",
    "deploy-*.tar.gz",
    "*-deploy.zip",
    "*-deploy.tar.gz",
    "",
    "# Generated remote verifiers",
    "*.verify.php",
    "*.verify.py",
    "*.verify-signed.php",
    "*.verify-signed.py",
    "",
    "# Generated verifier public-key sidecars",
    "*.verify.php.public-key.json",
    "*.verify.py.public-key.json",
    "*.verify-signed.php.public-key.json",
    "*.verify-signed.py.public-key.json",
    "",
    "# Remote verification evidence",
    "*-signed-evidence.json",
    "*-remote-evidence.json",
    "*-normalized-evidence.json",
    "deploy-evidence.json",
    "deploy-normalized-evidence.json",
    "",
    "# Baseline correction / reconciliation working artifacts",
    "baseline-correction-*.zip",
    "baseline-correction-*.tar.gz",
    "baseline-correction-*.verify.php",
    "baseline-correction-*.verify.py",
    "baseline-correction-*-evidence.json",
    "baseline-correction-*-signed-evidence.json",
    "",
    "# Closeout working directories if created inside the repository",
    "deploy-pack-closeout-*/",
    "",
    "# Durable deploy-pack policy/state must remain visible to Git.",
    "!.deploy-pack.toml",
    "!.deploy-pack-baseline",
    "!.deploy-pack-history.jsonl",
)


class GitignoreManagedError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitignoreStatus:
    state: str
    path: Path
    durable_ignored: tuple[str, ...] = ()

    @property
    def current(self) -> bool:
        return self.state == "current" and not self.durable_ignored


def _newline_for(data: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in data else b"\n"


def _managed_block(newline: bytes = b"\n") -> bytes:
    lines = [BEGIN_MARKER, *IGNORE_RULES, END_MARKER]
    return newline.join(line.encode("utf-8") for line in lines) + newline


def _marker_ranges(data: bytes) -> list[tuple[int, int]]:
    begin = BEGIN_MARKER.encode("utf-8")
    end = END_MARKER.encode("utf-8")
    lines = data.splitlines(keepends=True)
    ranges: list[tuple[int, int]] = []
    offset = 0
    open_start: int | None = None

    for raw in lines:
        content = raw.rstrip(b"\r\n")
        if content == begin:
            if open_start is not None:
                raise GitignoreManagedError("nested deploy-pack managed .gitignore markers")
            open_start = offset
        elif content == end:
            if open_start is None:
                raise GitignoreManagedError("deploy-pack managed .gitignore end marker has no begin marker")
            ranges.append((open_start, offset + len(raw)))
            open_start = None
        offset += len(raw)

    if open_start is not None:
        raise GitignoreManagedError("deploy-pack managed .gitignore begin marker has no end marker")
    if len(ranges) > 1:
        raise GitignoreManagedError("multiple deploy-pack managed .gitignore blocks found")
    return ranges


def _git_ignored(root: Path, path: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-q", "--", path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    raise GitignoreManagedError(f"git check-ignore failed for {path} with exit {proc.returncode}")


def _durable_ignored(root: Path) -> tuple[str, ...]:
    return tuple(path for path in DURABLE_TRACKED_PATHS if _git_ignored(root, path))


def status_managed_gitignore(root: Path) -> GitignoreStatus:
    root = root.resolve()
    path = root / ".gitignore"
    if not path.exists():
        return GitignoreStatus("missing", path, _durable_ignored(root))

    data = path.read_bytes()
    ranges = _marker_ranges(data)
    if not ranges:
        return GitignoreStatus("missing", path, _durable_ignored(root))

    start, end = ranges[0]
    expected = _managed_block(_newline_for(data))
    actual = data[start:end]
    state = "current" if actual == expected and end == len(data) else "stale"
    return GitignoreStatus(state, path, _durable_ignored(root))


def install_managed_gitignore(root: Path) -> str:
    root = root.resolve()
    path = root / ".gitignore"
    data = path.read_bytes() if path.exists() else b""
    newline = _newline_for(data)
    ranges = _marker_ranges(data)

    before_status = "missing"
    if ranges:
        start, end = ranges[0]
        expected = _managed_block(newline)
        before_status = "current" if data[start:end] == expected and end == len(data) else "stale"
        user_data = data[:start] + data[end:]
    else:
        user_data = data

    prefix = user_data
    if prefix and not prefix.endswith((b"\n", b"\r")):
        prefix += newline
    if prefix and not prefix.endswith(newline + newline):
        prefix += newline

    desired = prefix + _managed_block(newline)
    if desired != data:
        path.write_bytes(desired)

    after = status_managed_gitignore(root)
    if not after.current:
        if after.durable_ignored:
            joined = ", ".join(after.durable_ignored)
            raise GitignoreManagedError(
                "managed block installed but durable deploy-pack paths are still ignored: "
                f"{joined}; inspect later/global Git ignore rules"
            )
        raise GitignoreManagedError(f"managed .gitignore did not reconcile cleanly: {after.state}")

    if before_status == "current" and desired == data:
        return "current"
    return "installed" if before_status == "missing" else "updated"


def remove_managed_gitignore(root: Path) -> str:
    root = root.resolve()
    path = root / ".gitignore"
    if not path.exists():
        return "absent"
    data = path.read_bytes()
    ranges = _marker_ranges(data)
    if not ranges:
        return "absent"
    start, end = ranges[0]
    path.write_bytes(data[:start] + data[end:])
    return "removed"


def run_gitignore_command(root: Path, args) -> int:
    command = getattr(args, "gitignore_command", None)
    if command == "install":
        outcome = install_managed_gitignore(root)
        status = status_managed_gitignore(root)
        print("DEPLOY-PACK GITIGNORE: " + outcome.upper())
        print(f"  file    : {status.path}")
        print("  managed : generated deployment artifacts + durable-state protections")
        print("  tracked : " + ", ".join(DURABLE_TRACKED_PATHS))
        return 0
    if command == "remove":
        outcome = remove_managed_gitignore(root)
        print("DEPLOY-PACK GITIGNORE: " + outcome.upper())
        print(f"  file    : {root / '.gitignore'}")
        return 0
    if command == "status":
        status = status_managed_gitignore(root)
        label = "CURRENT" if status.current else status.state.upper()
        print(f"DEPLOY-PACK GITIGNORE: {label}")
        print(f"  file    : {status.path}")
        print("  tracked : " + ", ".join(DURABLE_TRACKED_PATHS))
        if status.durable_ignored:
            print("  unsafe  : durable paths currently ignored by Git:")
            for item in status.durable_ignored:
                print(f"            {item}")
        return 1 if getattr(args, "check", False) and not status.current else 0
    raise GitignoreManagedError("gitignore requires `status`, `install`, or `remove`")
