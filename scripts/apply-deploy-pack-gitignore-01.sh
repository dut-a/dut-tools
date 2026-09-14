#!/usr/bin/env bash
set -euo pipefail
export GIT_PAGER=cat PAGER=cat GIT_TERMINAL_PROMPT=0

REPO="${1:-.}"
REPO="$(cd "$REPO" && pwd)"
TOOL="$REPO/tools/deploy-pack"
SRC="$TOOL/src/deploy_pack"
CLI="$SRC/cli.py"
MODULE="$SRC/gitignore_managed.py"
TEST="$TOOL/tests/test_gitignore01.py"
VERSION="$TOOL/VERSION"
INIT="$SRC/__init__.py"
PYPROJECT="$TOOL/pyproject.toml"
DOCS="$TOOL/docs"
MANIFESTS="$DOCS/manifests"
DOC="$DOCS/DEPLOY-PACK-GITIGNORE-01.md"
MANIFEST="$MANIFESTS/DEPLOY-PACK-GITIGNORE-01.json"

fail() {
  printf '%s\n' "DEPLOY-PACK-GITIGNORE-01: FAIL — $*" >&2
  exit 1
}

for f in "$CLI" "$VERSION" "$INIT"; do
  [[ -f "$f" ]] || fail "missing required file: $f"
done
mkdir -p "$DOCS" "$MANIFESTS"

echo "== write managed-gitignore implementation =="
cat > "$MODULE" <<'PY'
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
PY

cat > "$TEST" <<'PY'
from __future__ import annotations

import argparse
import subprocess
import tempfile
import unittest
from pathlib import Path

from deploy_pack.gitignore_managed import (
    BEGIN_MARKER,
    DURABLE_TRACKED_PATHS,
    END_MARKER,
    GitignoreManagedError,
    install_managed_gitignore,
    remove_managed_gitignore,
    run_gitignore_command,
)


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class Gitignore01Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        p = git(self.root, "init", "-q", "-b", "primary")
        self.assertEqual(p.returncode, 0, p.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_preserves_existing_bytes_and_is_idempotent(self):
        original = b"node_modules/\n.env\n"
        (self.root / ".gitignore").write_bytes(original)
        self.assertEqual(install_managed_gitignore(self.root), "installed")
        first = (self.root / ".gitignore").read_bytes()
        self.assertTrue(first.startswith(original))
        self.assertIn(BEGIN_MARKER.encode(), first)
        self.assertTrue(first.endswith((END_MARKER + "\n").encode()))
        self.assertEqual(install_managed_gitignore(self.root), "current")
        self.assertEqual((self.root / ".gitignore").read_bytes(), first)

    def test_stale_block_moves_to_eof_without_changing_user_bytes(self):
        before = b"alpha/\n"
        stale = ((BEGIN_MARKER + "\n") + "old-generated-rule\n" + (END_MARKER + "\n")).encode()
        after = b"omega/\n"
        (self.root / ".gitignore").write_bytes(before + stale + after)
        self.assertEqual(install_managed_gitignore(self.root), "updated")
        data = (self.root / ".gitignore").read_bytes()
        self.assertTrue(data.startswith(before + after))
        self.assertTrue(data.endswith((END_MARKER + "\n").encode()))

    def test_durable_state_is_unignored_after_legacy_broad_rule(self):
        (self.root / ".gitignore").write_text(".deploy-pack-*\n", encoding="utf-8")
        install_managed_gitignore(self.root)
        for path in DURABLE_TRACKED_PATHS:
            p = git(self.root, "check-ignore", "-q", "--", path)
            self.assertEqual(p.returncode, 1, f"{path} unexpectedly ignored")

    def test_status_check_distinguishes_missing_and_current(self):
        args = argparse.Namespace(gitignore_command="status", check=True)
        self.assertEqual(run_gitignore_command(self.root, args), 1)
        install_managed_gitignore(self.root)
        self.assertEqual(run_gitignore_command(self.root, args), 0)

    def test_remove_only_removes_managed_block(self):
        original = b"vendor/\n"
        (self.root / ".gitignore").write_bytes(original)
        install_managed_gitignore(self.root)
        self.assertEqual(remove_managed_gitignore(self.root), "removed")
        data = (self.root / ".gitignore").read_bytes()
        self.assertIn(original, data)
        self.assertNotIn(BEGIN_MARKER.encode(), data)
        self.assertNotIn(END_MARKER.encode(), data)

    def test_malformed_markers_fail_closed(self):
        (self.root / ".gitignore").write_text(BEGIN_MARKER + "\n", encoding="utf-8")
        with self.assertRaises(GitignoreManagedError):
            install_managed_gitignore(self.root)


if __name__ == "__main__":
    unittest.main()
PY

echo
echo "== patch CLI =="
python3 - "$CLI" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
s = path.read_text(encoding="utf-8")

import_line = "from .gitignore_managed import GitignoreManagedError, install_managed_gitignore, run_gitignore_command\n"
if import_line not in s:
    anchor = "from .core import (\n"
    if anchor not in s:
        raise SystemExit("GITIGNORE-01: cannot find core import anchor")
    s = s.replace(anchor, import_line + anchor, 1)

if 'gitignore_cmd = sub.add_parser(' not in s:
    anchor = '    pack = sub.add_parser(\n'
    if anchor not in s:
        raise SystemExit("GITIGNORE-01: cannot find pack parser anchor")
    block = '''    gitignore_cmd = sub.add_parser(
        "gitignore",
        help="Manage deploy-pack generated-artifact rules in the repository .gitignore.",
        description=(
            "Install, inspect, or remove deploy-pack's bounded managed .gitignore block. "
            "Project-authored rules outside the block are preserved."
        ),
    )
    gitignore_sub = gitignore_cmd.add_subparsers(dest="gitignore_command")
    gitignore_status = gitignore_sub.add_parser(
        "status",
        help="Report whether the managed deploy-pack .gitignore block is current.",
    )
    gitignore_status.add_argument(
        "--check",
        action="store_true",
        help="Exit nonzero when the managed block is missing/stale or durable state is ignored.",
    )
    gitignore_sub.add_parser(
        "install",
        help="Install or reconcile the managed block at the end of .gitignore.",
    )
    gitignore_sub.add_parser(
        "remove",
        help="Remove only the deploy-pack managed block.",
    )

'''
    s = s.replace(anchor, block + anchor, 1)

if 'if args.command == "gitignore":' not in s:
    anchor = '        root = repo_root()\n\n        if args.command == "init":'
    if anchor not in s:
        raise SystemExit("GITIGNORE-01: cannot find init dispatch anchor")
    replacement = '''        if args.command == "gitignore":
            root = repo_root()
            try:
                return run_gitignore_command(root, args)
            except GitignoreManagedError as exc:
                raise DeployPackError(str(exc)) from exc

        root = repo_root()

        if args.command == "init":'''
    s = s.replace(anchor, replacement, 1)

if 'gitignore_outcome = install_managed_gitignore(root)' not in s:
    anchor = '            atomic_write_text(config_path, "\\n".join(starter))\n'
    if anchor not in s:
        raise SystemExit("GITIGNORE-01: cannot find init config-write anchor")
    s = s.replace(
        anchor,
        anchor + '''            try:
                gitignore_outcome = install_managed_gitignore(root)
            except GitignoreManagedError as exc:
                raise DeployPackError(
                    f"{CONFIG_FILE} was created, but managed .gitignore installation failed: {exc}"
                ) from exc
''',
        1,
    )
    out_anchor = '            print("DEPLOY-PACK INIT")\n'
    if out_anchor not in s:
        raise SystemExit("GITIGNORE-01: cannot find init output anchor")
    s = s.replace(out_anchor, out_anchor + '            print(f"  gitignore: {gitignore_outcome}")\n', 1)

if "gitignore                  Manage generated-artifact ignore rules." not in s:
    needle = "    inspect                   Preview Git-selected deployable/excluded files.\n"
    if needle in s:
        s = s.replace(needle, needle + "    gitignore                  Manage generated-artifact ignore rules.\n", 1)

path.write_text(s, encoding="utf-8")
PY

echo
echo "== version 1.15.0 =="
printf '%s\n' "1.15.0" > "$VERSION"
python3 - "$INIT" "$PYPROJECT" <<'PY'
from pathlib import Path
import re
import sys

init = Path(sys.argv[1])
s = init.read_text(encoding="utf-8")
s, n = re.subn(r'(__version__\s*=\s*["\'])\d+\.\d+\.\d+(["\'])', r'\g<1>1.15.0\2', s, count=1)
if n == 0:
    raise SystemExit("GITIGNORE-01: could not update __version__")
init.write_text(s, encoding="utf-8")

pyproject = Path(sys.argv[2])
if pyproject.exists():
    p = pyproject.read_text(encoding="utf-8")
    p, n = re.subn(r'(?m)^(version\s*=\s*["\'])\d+\.\d+\.\d+(["\'])', r'\g<1>1.15.0\2', p, count=1)
    if n:
        pyproject.write_text(p, encoding="utf-8")
PY

cat > "$DOC" <<'MD'
# DEPLOY-PACK-GITIGNORE-01

Version: **1.15.0**

## Commands

```bash
dp gitignore status
dp gitignore status --check
dp gitignore install
dp gitignore remove
```

`deploy-pack` works identically to `dp`.

## Ownership

deploy-pack owns only the bounded block between:

```text
# BEGIN deploy-pack managed ignores
# END deploy-pack managed ignores
```

Project-authored bytes outside that block are preserved. Reconciliation places the
managed block at EOF so durable-state negations can override older broad ignore rules.

## Durable deploy-pack files

These remain visible to Git:

```text
.deploy-pack.toml
.deploy-pack-baseline
.deploy-pack-history.jsonl
```

## Init integration

`dp init` creates the normal fail-closed selection policy and installs/reconciles the
managed `.gitignore` block.

## CI

`dp gitignore status --check` exits nonzero when the block is missing/stale or durable
state is still ignored.
MD

cat > "$MANIFEST" <<'JSON'
{
  "increment": "DEPLOY-PACK-GITIGNORE-01",
  "version": "1.15.0",
  "type": "feature",
  "commands": ["gitignore status", "gitignore install", "gitignore remove"],
  "invariants": [
    "project-authored .gitignore bytes outside the managed block are preserved",
    "exactly one managed block is permitted",
    "managed block is reconciled at EOF",
    ".deploy-pack.toml remains visible to Git",
    ".deploy-pack-baseline remains visible to Git",
    ".deploy-pack-history.jsonl remains visible to Git",
    "init installs the managed block",
    "status --check is suitable for CI"
  ]
}
JSON

echo
echo "== syntax =="
PY="$TOOL/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
PYTHONPATH="$TOOL/src" "$PY" -m py_compile "$CLI" "$MODULE" "$TEST"

echo
echo "== focused regression =="
PYTHONPATH="$TOOL/src" "$PY" -m unittest discover -s "$TOOL/tests" -p "test_gitignore01.py" -v

echo
echo "== parser smoke =="
PYTHONPATH="$TOOL/src" DEPLOY_PACK_COLOR=never "$PY" -m deploy_pack.cli gitignore --help >/dev/null
PYTHONPATH="$TOOL/src" DEPLOY_PACK_COLOR=never "$PY" -m deploy_pack.cli gitignore status --help >/dev/null

echo
echo "== init integration smoke =="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git -C "$TMP" init -q -b primary
(
  cd "$TMP"
  PYTHONPATH="$TOOL/src" DEPLOY_PACK_COLOR=never "$PY" -m deploy_pack.cli init >/dev/null
  grep -Fq '# BEGIN deploy-pack managed ignores' .gitignore
  grep -Fq '!.deploy-pack.toml' .gitignore
  grep -Fq '!.deploy-pack-baseline' .gitignore
  grep -Fq '!.deploy-pack-history.jsonl' .gitignore
  PYTHONPATH="$TOOL/src" DEPLOY_PACK_COLOR=never "$PY" -m deploy_pack.cli gitignore status --check >/dev/null
)
rm -rf "$TMP"
trap - EXIT

if [[ "${DEPLOY_PACK_GITIGNORE01_SKIP_FULL_TESTS:-0}" != "1" ]]; then
  echo
  echo "== full deploy-pack regression =="
  (cd "$REPO" && make test TOOL=deploy-pack)
else
  echo
  echo "== full deploy-pack regression =="
  echo "SKIPPED by DEPLOY_PACK_GITIGNORE01_SKIP_FULL_TESTS=1"
fi

echo
echo "DEPLOY-PACK-GITIGNORE-01: PASS"
echo "Version: 1.15.0"
echo
echo "Next:"
echo "  make install TOOL=deploy-pack"
echo "  dp gitignore install"
echo "  dp gitignore status --check"
