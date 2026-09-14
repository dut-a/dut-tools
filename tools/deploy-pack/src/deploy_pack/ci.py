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
