#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

VERSION = "1.0.1"

CLEAN = 0
CHANGES_REQUIRED = 1
INVALID_INPUT = 2
WRITE_FAILED = 3

SKIP_DIRS = {".git", "target", "build", "node_modules", "vendor", ".gradle", "dist", "out"}

@dataclasses.dataclass(frozen=True)
class Change:
    pom: str
    artifact_id: str
    old_name: str | None
    new_name: str
    action: str

class NormalizerError(RuntimeError):
    pass

def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

def namespace_uri(tag: str) -> str | None:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return None

def qname(ns: str | None, name: str) -> str:
    return f"{{{ns}}}{name}" if ns else name

def discover_poms(root: Path) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if "pom.xml" in filenames:
            out.append(Path(dirpath) / "pom.xml")
    return sorted(out)

def title_name(artifact_id: str) -> str:
    parts = [p for p in re.split(r"[-_.]+", artifact_id.strip()) if p]
    return " ".join(p[:1].upper() + p[1:] for p in parts)

def desired_name(artifact_id: str, style: str, prefix: str | None) -> str:
    if style == "artifact":
        return artifact_id
    title = title_name(artifact_id)
    if style == "title":
        return title
    if style == "prefix-title":
        if not prefix or not prefix.strip():
            raise NormalizerError("--prefix is required for prefix-title style")
        return f"{prefix.strip()} {title}".strip()
    raise NormalizerError(f"unsupported style: {style}")

def direct_child(root: ET.Element, name: str) -> ET.Element | None:
    for child in list(root):
        if local_name(child.tag) == name:
            return child
    return None

def parse_pom(path: Path) -> tuple[ET.ElementTree, ET.Element, str | None, str | None]:
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        raise NormalizerError(f"{path}: invalid XML: {e}") from e
    root = tree.getroot()
    if local_name(root.tag) != "project":
        raise NormalizerError(f"{path}: root element is not <project>")
    artifact = direct_child(root, "artifactId")
    name = direct_child(root, "name")
    artifact_id = artifact.text.strip() if artifact is not None and artifact.text else None
    current_name = name.text.strip() if name is not None and name.text else None
    return tree, root, artifact_id, current_name

def plan_pom(path: Path, style: str, prefix: str | None, add_missing: bool) -> Change | None:
    tree, root, artifact_id, current_name = parse_pom(path)
    if not artifact_id:
        raise NormalizerError(f"{path}: missing direct project <artifactId>")
    wanted = desired_name(artifact_id, style, prefix)
    name_el = direct_child(root, "name")
    if name_el is None and not add_missing:
        return None
    if current_name == wanted:
        return None
    return Change(
        pom=str(path),
        artifact_id=artifact_id,
        old_name=current_name,
        new_name=wanted,
        action="add" if name_el is None else "update",
    )

def write_pom(path: Path, new_name: str) -> None:
    tree, root, artifact_id, current_name = parse_pom(path)
    ns = namespace_uri(root.tag)
    if ns:
        ET.register_namespace("", ns)

    name_el = direct_child(root, "name")
    if name_el is None:
        artifact_el = direct_child(root, "artifactId")
        if artifact_el is None:
            raise NormalizerError(f"{path}: missing artifactId during write")
        name_el = ET.Element(qname(ns, "name"))
        children = list(root)
        idx = children.index(artifact_el)
        root.insert(idx + 1, name_el)

    name_el.text = new_name

    # Preserve declaration and write atomically.
    try:
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent, prefix=".module-name-", suffix=".xml") as fh:
            tmp = Path(fh.name)
            tree.write(fh, encoding="utf-8", xml_declaration=True)
        os.replace(tmp, path)
    except Exception:
        try:
            if 'tmp' in locals() and tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise

def render_text(changes: list[Change]) -> None:
    if not changes:
        print("Module names are already normalized.")
        return
    for c in changes:
        old = c.old_name if c.old_name is not None else "<missing>"
        print(f"{c.action.upper():6} {c.pom}")
        print(f"       artifactId={c.artifact_id}")
        print(f"       {old!r} -> {c.new_name!r}")
    print(f"Changes: {len(changes)}")

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="module-name-normalizer",
        description="Conservatively normalize top-level Maven project <name> values.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  module-name-normalizer .
  module-name-normalizer . --style title
  module-name-normalizer . --style prefix-title --prefix Tembeek
  module-name-normalizer . --check
  module-name-normalizer . --write
""",
    )
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--version", action="version", version=f"module-name-normalizer {VERSION}")
    p.add_argument("--style", choices=["artifact", "title", "prefix-title"], default="title")
    p.add_argument("--prefix")
    p.add_argument("--add-missing", action=argparse.BooleanOptionalAction, default=True)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    p.add_argument("--format", choices=["text", "json"], default="text")
    return p

def main() -> int:
    args = parser().parse_args()
    try:
        root = Path(args.root).expanduser().resolve()
        if not root.is_dir():
            raise NormalizerError(f"root is not a directory: {root}")

        poms = discover_poms(root)
        changes: list[Change] = []
        for pom in poms:
            change = plan_pom(pom, args.style, args.prefix, args.add_missing)
            if change:
                changes.append(change)

        if args.format == "json":
            print(json.dumps({
                "tool": "module-name-normalizer",
                "version": VERSION,
                "root": str(root),
                "changes": [dataclasses.asdict(c) for c in changes],
            }, indent=2))
        else:
            render_text(changes)

        if args.write:
            try:
                for change in changes:
                    write_pom(Path(change.pom), change.new_name)
            except Exception as e:
                print(f"ERROR: write failed: {e}", file=sys.stderr)
                return WRITE_FAILED
            if args.format == "text" and changes:
                print("Applied changes.")
            return CLEAN

        if args.check and changes:
            return CHANGES_REQUIRED
        return CLEAN

    except NormalizerError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return INVALID_INPUT

if __name__ == "__main__":
    raise SystemExit(main())
