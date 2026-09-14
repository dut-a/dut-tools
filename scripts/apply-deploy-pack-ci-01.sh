#!/usr/bin/env bash
set -euo pipefail
export GIT_PAGER=cat PAGER=cat GIT_TERMINAL_PROMPT=0

REPO="${1:-.}"
REPO="$(cd "$REPO" && pwd)"
TOOL="$REPO/tools/deploy-pack"
SRC="$TOOL/src/deploy_pack"
CLI="$SRC/cli.py"
MODULE="$SRC/ci.py"
TEST="$TOOL/tests/test_ci01.py"
VERSION="$TOOL/VERSION"
INIT="$SRC/__init__.py"
PYPROJECT="$TOOL/pyproject.toml"
DOCS="$TOOL/docs"
MANIFESTS="$DOCS/manifests"
DOC="$DOCS/DEPLOY-PACK-CI-01.md"
MANIFEST="$MANIFESTS/DEPLOY-PACK-CI-01.json"

fail() {
  printf '%s\n' "DEPLOY-PACK-CI-01: FAIL — $*" >&2
  exit 1
}

for f in "$CLI" "$VERSION" "$INIT"; do
  [[ -f "$f" ]] || fail "missing required file: $f"
done
mkdir -p "$DOCS" "$MANIFESTS"

echo "== write CI façade =="

cat > "$MODULE" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


class CiError(RuntimeError):
    pass


@dataclass(frozen=True)
class CiStep:
    name: str
    command: tuple[str, ...]
    status: str
    exit_code: int
    stdout: str
    stderr: str


# deploy-pack CI workflows are deliberately non-authoritative.  These files are
# fingerprints, not an exhaustive business model: if a future deploy-pack state
# file uses the .deploy-pack* namespace it is automatically covered below.
_MUTATION_EXEMPT_NAMES = {
    ".deploy-pack.lock",
    ".deploy-pack-closeout-session.json",
}


def _state_files(root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    for path in root.glob(".deploy-pack*"):
        if path.name in _MUTATION_EXEMPT_NAMES:
            continue
        if path.is_file() or path.is_symlink():
            paths.append(path)
    return tuple(sorted(paths, key=lambda p: p.name))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _state_fingerprint(root: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for path in _state_files(root):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[rel] = {
                "kind": "symlink",
                "target": os.readlink(path),
            }
        else:
            result[rel] = {
                "kind": "file",
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
    return result


def _state_diff(
    before: dict[str, dict[str, object]],
    after: dict[str, dict[str, object]],
) -> dict[str, object]:
    before_keys = set(before)
    after_keys = set(after)
    changed = sorted(
        key
        for key in before_keys & after_keys
        if before[key] != after[key]
    )
    return {
        "added": sorted(after_keys - before_keys),
        "removed": sorted(before_keys - after_keys),
        "changed": changed,
    }


def _has_state_diff(diff: dict[str, object]) -> bool:
    return bool(diff["added"] or diff["removed"] or diff["changed"])


def _run_step(root: Path, name: str, args: Iterable[str]) -> CiStep:
    args_tuple = tuple(args)
    env = os.environ.copy()
    # Avoid ANSI in captured CI logs/JSON while leaving the caller free to
    # control formatting of the outer command.
    env["DEPLOY_PACK_COLOR"] = "never"

    proc = subprocess.run(
        [sys.executable, "-m", "deploy_pack.cli", *args_tuple],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return CiStep(
        name=name,
        command=("deploy-pack", *args_tuple),
        status="pass" if proc.returncode == 0 else "fail",
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def _print_text_result(title: str, result: dict[str, object]) -> None:
    print(title)
    for step in result.get("steps", []):
        mark = "PASS" if step["status"] == "pass" else "FAIL"
        print(f"  {mark:<4}  {step['name']}")
        if step["status"] != "pass":
            detail = (step.get("stderr") or step.get("stdout") or "").strip()
            if detail:
                for line in detail.splitlines():
                    print(f"        {line}")

    mutation = result.get("state_mutation")
    if mutation and mutation.get("detected"):
        print("  FAIL  authoritative state mutation detected")
        diff = mutation["diff"]
        for kind in ("added", "removed", "changed"):
            for item in diff[kind]:
                print(f"        {kind}: {item}")

    artifact = result.get("artifact")
    if artifact:
        print(f"  ARTIFACT  {artifact['path']}")
        print(f"            sha256={artifact['sha256']}")
        print(f"            bytes={artifact['size']}")

    print(f"DEPLOY-PACK CI: {str(result['status']).upper()}")


def _emit(result: dict[str, object], json_output: bool, title: str) -> int:
    if json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_text_result(title, result)
    return 0 if result["status"] == "pass" else 1


def _serialized_step(step: CiStep) -> dict[str, object]:
    return asdict(step)


def run_ci_check(root: Path, *, json_output: bool = False) -> int:
    root = root.resolve()
    before = _state_fingerprint(root)

    specs = (
        ("gitignore", ("gitignore", "status", "--check")),
        ("inspect", ("inspect",)),
        ("history", ("history-verify",)),
        ("deployment_status", ("deploy", "status")),
    )

    steps: list[CiStep] = []
    for name, args in specs:
        step = _run_step(root, name, args)
        steps.append(step)

    after = _state_fingerprint(root)
    diff = _state_diff(before, after)
    mutated = _has_state_diff(diff)
    passed = all(step.status == "pass" for step in steps) and not mutated

    result: dict[str, object] = {
        "schema": 1,
        "workflow": "check",
        "status": "pass" if passed else "fail",
        "steps": [_serialized_step(step) for step in steps],
        "state_mutation": {
            "detected": mutated,
            "diff": diff,
        },
    }
    return _emit(result, json_output, "DEPLOY-PACK CI CHECK")


def run_ci_build(
    root: Path,
    *,
    output: str,
    json_output: bool = False,
) -> int:
    root = root.resolve()
    out = Path(output)
    if not out.is_absolute():
        out = root / out
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    before = _state_fingerprint(root)

    steps: list[CiStep] = []

    inspect = _run_step(root, "inspect", ("inspect",))
    steps.append(inspect)

    if inspect.status == "pass":
        pack = _run_step(root, "pack", ("pack", "--output", str(out)))
    else:
        pack = CiStep(
            name="pack",
            command=("deploy-pack", "pack", "--output", str(out)),
            status="skipped",
            exit_code=-1,
            stdout="",
            stderr="inspect failed",
        )
    steps.append(pack)

    if pack.status == "pass":
        if not out.exists() or not out.is_file():
            verify = CiStep(
                name="verify",
                command=("deploy-pack", "verify", str(out)),
                status="fail",
                exit_code=2,
                stdout="",
                stderr=f"pack reported success but artifact does not exist: {out}",
            )
        else:
            verify = _run_step(root, "verify", ("verify", str(out)))
    else:
        verify = CiStep(
            name="verify",
            command=("deploy-pack", "verify", str(out)),
            status="skipped",
            exit_code=-1,
            stdout="",
            stderr="pack did not complete",
        )
    steps.append(verify)

    artifact = None
    if out.exists() and out.is_file():
        artifact = {
            "path": str(out),
            "size": out.stat().st_size,
            "sha256": _sha256_file(out),
        }

    after = _state_fingerprint(root)
    diff = _state_diff(before, after)
    mutated = _has_state_diff(diff)

    passed = (
        inspect.status == "pass"
        and pack.status == "pass"
        and verify.status == "pass"
        and artifact is not None
        and not mutated
    )

    result: dict[str, object] = {
        "schema": 1,
        "workflow": "build",
        "status": "pass" if passed else "fail",
        "steps": [_serialized_step(step) for step in steps],
        "state_mutation": {
            "detected": mutated,
            "diff": diff,
        },
        "artifact": artifact,
    }
    return _emit(result, json_output, "DEPLOY-PACK CI BUILD")


def run_ci_command(root: Path, args) -> int:
    command = getattr(args, "ci_command", None)
    json_output = bool(getattr(args, "json", False))

    if command == "check":
        return run_ci_check(root, json_output=json_output)

    if command == "build":
        output = getattr(args, "output", None)
        if not output:
            raise CiError("ci build requires --output")
        return run_ci_build(root, output=output, json_output=json_output)

    raise CiError("ci requires `check` or `build`")
PY

cat > "$TEST" <<'PY'
from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from deploy_pack.ci import CiStep, run_ci_build, run_ci_check


def passing(name, *command):
    return CiStep(
        name=name,
        command=("deploy-pack", *command),
        status="pass",
        exit_code=0,
        stdout="",
        stderr="",
    )


class Ci01Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_check_runs_read_only_gate_set(self):
        calls = []

        def fake(root, name, args):
            calls.append((name, tuple(args)))
            return passing(name, *tuple(args))

        with patch("deploy_pack.ci._run_step", side_effect=fake):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = run_ci_check(self.root, json_output=True)

        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(
            calls,
            [
                ("gitignore", ("gitignore", "status", "--check")),
                ("inspect", ("inspect",)),
                ("history", ("history-verify",)),
                ("deployment_status", ("deploy", "status")),
            ],
        )
        self.assertFalse(payload["state_mutation"]["detected"])

    def test_check_fails_when_authoritative_state_mutates(self):
        counter = {"n": 0}

        def fake(root, name, args):
            counter["n"] += 1
            if counter["n"] == 2:
                (root / ".deploy-pack-baseline").write_text("changed\n", encoding="utf-8")
            return passing(name, *tuple(args))

        with patch("deploy_pack.ci._run_step", side_effect=fake):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = run_ci_check(self.root, json_output=True)

        self.assertEqual(rc, 1)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["state_mutation"]["detected"])
        self.assertIn(".deploy-pack-baseline", payload["state_mutation"]["diff"]["added"])

    def test_build_is_inspect_pack_verify_and_reports_hash(self):
        artifact = self.root / "dist" / "deploy.zip"

        def fake(root, name, args):
            args = tuple(args)
            if name == "pack":
                out_index = args.index("--output") + 1
                path = Path(args[out_index])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"ci-artifact")
            return passing(name, *args)

        with patch("deploy_pack.ci._run_step", side_effect=fake):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = run_ci_build(
                    self.root,
                    output="dist/deploy.zip",
                    json_output=True,
                )

        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(
            [step["name"] for step in payload["steps"]],
            ["inspect", "pack", "verify"],
        )
        self.assertEqual(payload["artifact"]["path"], str(artifact.resolve()))
        self.assertEqual(payload["artifact"]["size"], len(b"ci-artifact"))
        self.assertEqual(len(payload["artifact"]["sha256"]), 64)

    def test_build_stops_after_inspect_failure(self):
        def fake(root, name, args):
            if name == "inspect":
                return CiStep(
                    name="inspect",
                    command=("deploy-pack", "inspect"),
                    status="fail",
                    exit_code=1,
                    stdout="",
                    stderr="bad deployment surface",
                )
            self.fail(f"unexpected nested command: {name}")

        with patch("deploy_pack.ci._run_step", side_effect=fake):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = run_ci_build(
                    self.root,
                    output="deploy.zip",
                    json_output=True,
                )

        self.assertEqual(rc, 1)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["steps"][1]["status"], "skipped")
        self.assertEqual(payload["steps"][2]["status"], "skipped")

    def test_transient_lock_is_exempt_from_mutation_guard(self):
        def fake(root, name, args):
            (root / ".deploy-pack.lock").write_text("pid\n", encoding="utf-8")
            return passing(name, *tuple(args))

        with patch("deploy_pack.ci._run_step", side_effect=fake):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = run_ci_check(self.root, json_output=True)

        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["state_mutation"]["detected"])


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

import_line = "from .ci import CiError, run_ci_command\n"
if import_line not in s:
    anchors = [
        "from .gitignore_managed import ",
        "from .core import (\n",
    ]
    inserted = False
    for anchor in anchors:
        idx = s.find(anchor)
        if idx >= 0:
            s = s[:idx] + import_line + s[idx:]
            inserted = True
            break
    if not inserted:
        raise SystemExit("CI-01: cannot find CLI import anchor")

if 'ci_cmd = sub.add_parser(' not in s:
    anchors = [
        '    gitignore_cmd = sub.add_parser(\n',
        '    pack = sub.add_parser(\n',
    ]
    anchor = next((a for a in anchors if a in s), None)
    if anchor is None:
        raise SystemExit("CI-01: cannot find parser insertion anchor")

    block = '''    ci_cmd = sub.add_parser(
        "ci",
        help="Run stable non-authoritative deploy-pack CI workflows.",
        description=(
            "CI façade for deploy-pack. `check` aggregates read-only repository/"
            "deployment gates; `build` performs inspect -> pack -> verify. "
            "Neither workflow may advance deployment truth."
        ),
    )
    ci_sub = ci_cmd.add_subparsers(dest="ci_command")

    ci_check = ci_sub.add_parser(
        "check",
        help="Run read-only CI gates and fail if deploy-pack state mutates.",
    )
    ci_check.add_argument(
        "--json",
        action="store_true",
        help="Emit one machine-readable JSON result document.",
    )

    ci_build = ci_sub.add_parser(
        "build",
        help="Inspect, package, and verify one CI deployment artifact.",
    )
    ci_build.add_argument(
        "--output",
        required=True,
        help="Path for the generated deployment archive.",
    )
    ci_build.add_argument(
        "--json",
        action="store_true",
        help="Emit one machine-readable JSON result document.",
    )

'''
    s = s.replace(anchor, block + anchor, 1)

if 'if args.command == "ci":' not in s:
    # Prefer insertion immediately before gitignore dispatch when available.
    anchor = '        if args.command == "gitignore":'
    if anchor in s:
        block = '''        if args.command == "ci":
            root = repo_root()
            try:
                return run_ci_command(root, args)
            except CiError as exc:
                raise DeployPackError(str(exc)) from exc

'''
        s = s.replace(anchor, block + anchor, 1)
    else:
        # Older lineage: insert before the first root = repo_root() in main dispatch.
        anchor = '        root = repo_root()\n'
        if anchor not in s:
            raise SystemExit("CI-01: cannot find dispatch insertion anchor")
        block = '''        if args.command == "ci":
            root = repo_root()
            try:
                return run_ci_command(root, args)
            except CiError as exc:
                raise DeployPackError(str(exc)) from exc

'''
        s = s.replace(anchor, block + anchor, 1)

# Help-surface orientation when the long command inventory exists.
needle = "    gitignore                  Manage generated-artifact ignore rules.\n"
line = "    ci                         Run stable non-authoritative CI workflows.\n"
if line not in s:
    if needle in s:
        s = s.replace(needle, needle + line, 1)
    else:
        alt = "    inspect                   Preview Git-selected deployable/excluded files.\n"
        if alt in s:
            s = s.replace(alt, alt + line, 1)

path.write_text(s, encoding="utf-8")
PY

echo
echo "== version 1.16.0 =="

printf '%s\n' "1.16.0" > "$VERSION"

python3 - "$INIT" "$PYPROJECT" <<'PY'
from pathlib import Path
import re
import sys

init = Path(sys.argv[1])
s = init.read_text(encoding="utf-8")
s, n = re.subn(
    r'(__version__\s*=\s*["\'])\d+\.\d+\.\d+(["\'])',
    r'\g<1>1.16.0\2',
    s,
    count=1,
)
if n == 0:
    raise SystemExit("CI-01: could not update __version__")
init.write_text(s, encoding="utf-8")

pyproject = Path(sys.argv[2])
if pyproject.exists():
    p = pyproject.read_text(encoding="utf-8")
    p, n = re.subn(
        r'(?m)^(version\s*=\s*["\'])\d+\.\d+\.\d+(["\'])',
        r'\g<1>1.16.0\2',
        p,
        count=1,
    )
    if n:
        pyproject.write_text(p, encoding="utf-8")
PY

cat > "$DOC" <<'MD'
# DEPLOY-PACK-CI-01

Version: **1.16.0**

## Purpose

Provide a stable CI contract so consuming repositories do not need to understand
deploy-pack's internal command graph.

## Commands

```bash
dp ci check
dp ci check --json

dp ci build --output dist/deploy.zip
dp ci build --output dist/deploy.zip --json
```

`deploy-pack` works identically to `dp`.

## `ci check`

Runs these existing read-only gates in a fixed order:

1. `gitignore status --check`
2. `inspect`
3. `history-verify`
4. `deploy status`

The workflow fingerprints `.deploy-pack*` state before and after the run, excluding
the transient lock and closeout-session files. Any authoritative-state mutation makes
the CI workflow fail even when all nested commands returned zero.

`ci check` never calls `mark`, `reconcile-baseline`, key rotation, verifier mutation,
recovery mutation, custody mutation, or any other deployment-truth-changing command.

## `ci build`

Runs:

```text
inspect
  -> pack --output <artifact>
  -> verify <exact artifact>
```

Later phases are skipped after an earlier failure.

On success it reports:

- absolute artifact path
- byte size
- SHA-256

The same authoritative-state mutation guard used by `ci check` applies.

## JSON schema

Both commands support `--json` and emit one JSON document with:

```json
{
  "schema": 1,
  "workflow": "check|build",
  "status": "pass|fail",
  "steps": [],
  "state_mutation": {
    "detected": false,
    "diff": {
      "added": [],
      "removed": [],
      "changed": []
    }
  }
}
```

`ci build` additionally includes `artifact`.

## GitHub Actions baseline

A consuming project should use a full Git history:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
```

Then:

```yaml
- name: deploy-pack CI checks
  run: dp ci check

- name: build verified deployment package
  run: dp ci build --output "$RUNNER_TEMP/deploy.zip" --json
```

Do not run deployment closeout (`mark`/reconciliation) in ordinary PR/build CI.
MD

cat > "$MANIFEST" <<'JSON'
{
  "increment": "DEPLOY-PACK-CI-01",
  "version": "1.16.0",
  "type": "feature",
  "commands": [
    "ci check",
    "ci build"
  ],
  "check_gates": [
    "gitignore status --check",
    "inspect",
    "history-verify",
    "deploy status"
  ],
  "build_pipeline": [
    "inspect",
    "pack",
    "verify"
  ],
  "invariants": [
    "ci workflows do not advance deployment truth",
    "authoritative .deploy-pack state is fingerprinted before and after CI execution",
    "ci check aggregates stable read-only gates",
    "ci build verifies the exact archive it produces",
    "json output is one machine-readable document",
    "failed earlier build phases skip dependent later phases"
  ]
}
JSON

PY="$TOOL/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

echo
echo "== syntax =="
PYTHONPATH="$TOOL/src" "$PY" -m py_compile "$MODULE" "$TEST" "$CLI"

echo
echo "== focused regression =="
(
  cd "$TOOL"
  PYTHONPATH="$TOOL/src" "$PY" -m unittest discover \
    -s tests \
    -p 'test_ci01.py' \
    -v
)

echo
echo "== CLI surface smoke =="
PYTHONPATH="$TOOL/src" DEPLOY_PACK_COLOR=never \
  "$PY" -m deploy_pack.cli ci --help >/dev/null
PYTHONPATH="$TOOL/src" DEPLOY_PACK_COLOR=never \
  "$PY" -m deploy_pack.cli ci check --help >/dev/null
PYTHONPATH="$TOOL/src" DEPLOY_PACK_COLOR=never \
  "$PY" -m deploy_pack.cli ci build --help >/dev/null

echo
echo "== mutation-surface audit =="
# Audit command spellings rather than arbitrary identifiers.  In particular,
# bare `mark` is a normal presentation variable in ci.py and must not be treated
# as invocation of the deploy-pack `mark` command.
if grep -Eq '["'"'"']mark["'"'"']|reconcile-baseline|verifier issue|verifier revoke|key rotate|recovery.*(create|apply)|custody.*(write|update)' "$MODULE"; then
  # Documentation/error text should not introduce mutation commands either; keep the
  # implementation surface intentionally narrow and auditable.
  fail "CI module contains a forbidden deployment-truth mutation command"
fi

if [[ "${DEPLOY_PACK_CI01_SKIP_FULL_TESTS:-0}" != "1" ]]; then
  echo
  echo "== full deploy-pack regression =="
  (
    cd "$REPO"
    make test TOOL=deploy-pack
  )
else
  echo
  echo "== full deploy-pack regression =="
  echo "SKIPPED by DEPLOY_PACK_CI01_SKIP_FULL_TESTS=1"
fi

echo
echo "DEPLOY-PACK-CI-01: PASS"
echo "Version: 1.16.0"
echo
echo "Next:"
echo "  make install TOOL=deploy-pack"
echo "  rehash"
echo "  dp ci check"
echo "  dp ci build --output /tmp/deploy.zip"
