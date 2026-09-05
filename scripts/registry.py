#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools.toml"
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
VALID_STATUSES = {"placeholder", "active", "deprecated", "retired"}

def load() -> dict:
    with REGISTRY.open("rb") as fh:
        return tomllib.load(fh)

def tools() -> dict:
    return load().get("tools", {})

def validate() -> list[str]:
    errors: list[str] = []
    data = tools()
    seen_commands: dict[str, str] = {}

    if not data:
        errors.append("registry has no [tools] entries")
        return errors

    for name, meta in sorted(data.items()):
        for key in ("command", "path", "language", "status", "version_file", "description"):
            if not meta.get(key):
                errors.append(f"{name}: missing {key}")

        status = meta.get("status")
        if status not in VALID_STATUSES:
            errors.append(f"{name}: invalid status {status!r}")

        command = meta.get("command", "")
        previous = seen_commands.get(command)
        if previous:
            errors.append(f"{name}: command {command!r} duplicates {previous}")
        elif command:
            seen_commands[command] = name

        tool_path = ROOT / meta.get("path", "")
        if not tool_path.is_dir():
            errors.append(f"{name}: missing tool path {tool_path.relative_to(ROOT)}")

        version_path = ROOT / meta.get("version_file", "")
        if not version_path.is_file():
            errors.append(f"{name}: missing version file {version_path.relative_to(ROOT)}")
        else:
            version = version_path.read_text(encoding="utf-8").strip()
            if not SEMVER.match(version):
                errors.append(f"{name}: invalid SemVer {version!r}")

        for required in ("README.md", "CHANGELOG.md"):
            candidate = tool_path / required
            if not candidate.is_file():
                errors.append(f"{name}: missing {candidate.relative_to(ROOT)}")

        install_hook = meta.get("install_hook")
        if install_hook:
            hook = ROOT / install_hook
            if not hook.is_file():
                errors.append(f"{name}: missing install hook {install_hook}")
            elif not hook.stat().st_mode & 0o111:
                errors.append(f"{name}: install hook {install_hook} is not executable")

        if status == "active":
            entrypoint = meta.get("entrypoint")
            if not entrypoint:
                errors.append(f"{name}: active tool requires entrypoint")
            else:
                ep = ROOT / entrypoint
                if not ep.is_file():
                    errors.append(f"{name}: missing entrypoint {entrypoint}")
                elif not ep.stat().st_mode & 0o111:
                    errors.append(f"{name}: entrypoint {entrypoint} is not executable")

    return errors

def get_tool(name: str) -> tuple[str, dict]:
    data = tools()
    if name not in data:
        raise KeyError(name)
    return name, data[name]

def cmd_list(_: argparse.Namespace) -> int:
    data = tools()
    width = max(len(n) for n in data)
    for name, meta in sorted(data.items()):
        print(f"{name:<{width}}  {meta['status']:<11}  {meta['command']:<25}  {meta['description']}")
    return 0

def cmd_versions(_: argparse.Namespace) -> int:
    data = tools()
    width = max(len(n) for n in data)
    for name, meta in sorted(data.items()):
        version = (ROOT / meta["version_file"]).read_text(encoding="utf-8").strip()
        print(f"{name:<{width}}  {version}")
    return 0

def cmd_validate(_: argparse.Namespace) -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Registry validation: PASS")
    return 0

def cmd_json(args: argparse.Namespace) -> int:
    import json
    name, meta = get_tool(args.tool)
    print(json.dumps({"name": name, **meta}, sort_keys=True))
    return 0

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Read and validate the dut-tools registry.")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)
    sub.add_parser("versions").set_defaults(func=cmd_versions)
    sub.add_parser("validate").set_defaults(func=cmd_validate)

    q = sub.add_parser("json")
    q.add_argument("tool")
    q.set_defaults(func=cmd_json)
    return p

def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except KeyError as exc:
        print(f"ERROR: unknown tool {exc.args[0]!r}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
