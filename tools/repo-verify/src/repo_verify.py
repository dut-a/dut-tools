#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import stat
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

VERSION = "1.0.0"

VERIFIED = 0
VERIFICATION_FAILED = 1
INVALID_CONFIG = 2
EXECUTION_ERROR = 3

DEFAULT_CONFIG = ".repo-verify.toml"
VALID_TYPES = {
    "file_exists",
    "directory_exists",
    "path_absent",
    "executable",
    "contains",
    "matches",
    "command",
}
VALID_SEVERITIES = {"error", "warning"}

class VerifyError(RuntimeError):
    exit_code = EXECUTION_ERROR

class ConfigError(VerifyError):
    exit_code = INVALID_CONFIG

@dataclasses.dataclass
class CheckSpec:
    index: int
    name: str
    type: str
    severity: str
    path: str | None = None
    text: str | None = None
    regex: str | None = None
    argv: list[str] | None = None
    cwd: str = "."
    timeout_seconds: int = 120

@dataclasses.dataclass
class Result:
    index: int
    name: str
    type: str
    severity: str
    status: str
    evidence: str
    duration_ms: int
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""

def safe_path(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as e:
        raise ConfigError(f"path escapes repository root: {value}") from e
    return path

def load_config(path: Path) -> tuple[dict[str, Any], list[CheckSpec]]:
    if not path.is_file():
        raise ConfigError(f"config not found: {path}")
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read config {path}: {e}") from e

    if raw.get("version") != 1:
        raise ConfigError("config must contain version = 1")

    settings = raw.get("settings", {})
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise ConfigError("[settings] must be a table")

    raw_checks = raw.get("check", [])
    if not isinstance(raw_checks, list):
        raise ConfigError("[[check]] entries are required")

    checks: list[CheckSpec] = []
    for idx, item in enumerate(raw_checks):
        if not isinstance(item, dict):
            raise ConfigError(f"check[{idx}] must be a table")

        name = str(item.get("name", "")).strip()
        kind = str(item.get("type", "")).strip()
        severity = str(item.get("severity", "error")).strip()

        if not name:
            raise ConfigError(f"check[{idx}] requires name")
        if kind not in VALID_TYPES:
            raise ConfigError(f"check[{idx}] has invalid type {kind!r}")
        if severity not in VALID_SEVERITIES:
            raise ConfigError(f"check[{idx}] has invalid severity {severity!r}")

        path_value = item.get("path")
        text_value = item.get("text")
        regex_value = item.get("regex")
        argv_value = item.get("argv")
        cwd_value = str(item.get("cwd", "."))
        timeout = item.get("timeout_seconds", 120)

        if not isinstance(timeout, int) or timeout < 1 or timeout > 86400:
            raise ConfigError(f"check[{idx}].timeout_seconds must be 1..86400")

        if kind in {"file_exists", "directory_exists", "path_absent", "executable", "contains", "matches"}:
            if not isinstance(path_value, str) or not path_value.strip():
                raise ConfigError(f"check[{idx}] type={kind} requires path")

        if kind == "contains":
            if not isinstance(text_value, str):
                raise ConfigError(f"check[{idx}] type=contains requires text")

        if kind == "matches":
            if not isinstance(regex_value, str) or not regex_value:
                raise ConfigError(f"check[{idx}] type=matches requires regex")
            try:
                re.compile(regex_value)
            except re.error as e:
                raise ConfigError(f"check[{idx}] invalid regex: {e}") from e

        if kind == "command":
            if not isinstance(argv_value, list) or not argv_value or not all(
                isinstance(x, str) and x for x in argv_value
            ):
                raise ConfigError(f"check[{idx}] type=command requires non-empty string argv array")

        checks.append(CheckSpec(
            index=idx,
            name=name,
            type=kind,
            severity=severity,
            path=path_value.strip() if isinstance(path_value, str) else None,
            text=text_value if isinstance(text_value, str) else None,
            regex=regex_value if isinstance(regex_value, str) else None,
            argv=list(argv_value) if isinstance(argv_value, list) else None,
            cwd=cwd_value,
            timeout_seconds=timeout,
        ))

    return settings, checks

def finish(spec: CheckSpec, start: float, status: str, evidence: str,
           exit_code: int | None = None, stdout: str = "", stderr: str = "") -> Result:
    duration = int((time.perf_counter() - start) * 1000)
    return Result(
        index=spec.index,
        name=spec.name,
        type=spec.type,
        severity=spec.severity,
        status=status,
        evidence=evidence,
        duration_ms=duration,
        exit_code=exit_code,
        stdout=stdout[-4000:],
        stderr=stderr[-4000:],
    )

def run_check(root: Path, spec: CheckSpec) -> Result:
    start = time.perf_counter()

    if spec.type == "file_exists":
        path = safe_path(root, spec.path or "")
        ok = path.is_file()
        return finish(spec, start, "pass" if ok else "fail",
                      f"{path} {'is a file' if ok else 'is missing or not a file'}")

    if spec.type == "directory_exists":
        path = safe_path(root, spec.path or "")
        ok = path.is_dir()
        return finish(spec, start, "pass" if ok else "fail",
                      f"{path} {'is a directory' if ok else 'is missing or not a directory'}")

    if spec.type == "path_absent":
        path = safe_path(root, spec.path or "")
        ok = not path.exists()
        return finish(spec, start, "pass" if ok else "fail",
                      f"{path} {'is absent' if ok else 'exists'}")

    if spec.type == "executable":
        path = safe_path(root, spec.path or "")
        ok = path.is_file() and os.access(path, os.X_OK)
        return finish(spec, start, "pass" if ok else "fail",
                      f"{path} {'is executable' if ok else 'is not executable'}")

    if spec.type == "contains":
        path = safe_path(root, spec.path or "")
        if not path.is_file():
            return finish(spec, start, "fail", f"{path} is missing")
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return finish(spec, start, "fail", f"cannot read {path}: {e}")
        ok = (spec.text or "") in body
        return finish(spec, start, "pass" if ok else "fail",
                      f"{path} {'contains' if ok else 'does not contain'} required text")

    if spec.type == "matches":
        path = safe_path(root, spec.path or "")
        if not path.is_file():
            return finish(spec, start, "fail", f"{path} is missing")
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return finish(spec, start, "fail", f"cannot read {path}: {e}")
        ok = re.search(spec.regex or "", body, re.MULTILINE) is not None
        return finish(spec, start, "pass" if ok else "fail",
                      f"{path} {'matches' if ok else 'does not match'} regex")

    if spec.type == "command":
        cwd = safe_path(root, spec.cwd)
        if not cwd.is_dir():
            return finish(spec, start, "fail", f"command cwd is not a directory: {cwd}")
        try:
            cp = subprocess.run(
                spec.argv or [],
                cwd=cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=spec.timeout_seconds,
            )
            ok = cp.returncode == 0
            return finish(
                spec, start,
                "pass" if ok else "fail",
                f"command exited {cp.returncode}",
                exit_code=cp.returncode,
                stdout=cp.stdout,
                stderr=cp.stderr,
            )
        except FileNotFoundError as e:
            return finish(spec, start, "fail", f"command not found: {e.filename}")
        except subprocess.TimeoutExpired as e:
            stdout = e.stdout if isinstance(e.stdout, str) else ""
            stderr = e.stderr if isinstance(e.stderr, str) else ""
            return finish(
                spec, start, "fail",
                f"command timed out after {spec.timeout_seconds}s",
                exit_code=None,
                stdout=stdout,
                stderr=stderr,
            )
        except OSError as e:
            return finish(spec, start, "fail", f"command execution failed: {e}")

    raise ConfigError(f"unsupported check type {spec.type}")

def render_text(results: list[Result]) -> None:
    for result in results:
        label = "PASS" if result.status == "pass" else (
            "WARN" if result.severity == "warning" else "FAIL"
        )
        print(f"{label:4}  {result.name} [{result.type}]")
        print(f"      {result.evidence}")
        if result.stdout:
            print("      stdout:")
            for line in result.stdout.splitlines():
                print(f"        {line}")
        if result.stderr:
            print("      stderr:")
            for line in result.stderr.splitlines():
                print(f"        {line}")

    errors = sum(1 for r in results if r.status == "fail" and r.severity == "error")
    warnings = sum(1 for r in results if r.status == "fail" and r.severity == "warning")
    print(f"SUMMARY checks={len(results)} errors={errors} warnings={warnings}")

def render_json(root: Path, config: Path, results: list[Result]) -> None:
    payload = {
        "tool": "repo-verify",
        "version": VERSION,
        "root": str(root),
        "config": str(config),
        "summary": {
            "checks": len(results),
            "errors": sum(1 for r in results if r.status == "fail" and r.severity == "error"),
            "warnings": sum(1 for r in results if r.status == "fail" and r.severity == "warning"),
            "passed": sum(1 for r in results if r.status == "pass"),
        },
        "results": [dataclasses.asdict(r) for r in results],
    }
    print(json.dumps(payload, indent=2))

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="repo-verify",
        description="Declarative repository verification engine with repository-owned policy.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  repo-verify
  repo-verify /path/to/repo
  repo-verify --config .tooling/repo-verify.toml
  repo-verify --fail-fast
  repo-verify --format json
""",
    )
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--version", action="version", version=f"repo-verify {VERSION}")
    p.add_argument("--config")
    p.add_argument("--fail-fast", action="store_true")
    p.add_argument("--format", choices=["text", "json"], default="text")
    return p

def main() -> int:
    args = parser().parse_args()

    try:
        root = Path(args.root).expanduser().resolve()
        if not root.is_dir():
            raise ConfigError(f"root is not a directory: {root}")

        config = (
            Path(args.config).expanduser().resolve()
            if args.config
            else root / DEFAULT_CONFIG
        )

        settings, checks = load_config(config)
        fail_fast = bool(settings.get("fail_fast", False)) or args.fail_fast

        results: list[Result] = []
        for spec in checks:
            result = run_check(root, spec)
            results.append(result)
            if fail_fast and result.status == "fail" and result.severity == "error":
                break

        if args.format == "json":
            render_json(root, config, results)
        else:
            render_text(results)

        failed = any(r.status == "fail" and r.severity == "error" for r in results)
        return VERIFICATION_FAILED if failed else VERIFIED

    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return INVALID_CONFIG
    except Exception as e:
        print(f"ERROR: execution failure: {e}", file=sys.stderr)
        return EXECUTION_ERROR

if __name__ == "__main__":
    raise SystemExit(main())
