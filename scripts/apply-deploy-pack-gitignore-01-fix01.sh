#!/usr/bin/env bash
set -euo pipefail
export GIT_PAGER=cat PAGER=cat GIT_TERMINAL_PROMPT=0

REPO="${1:-.}"
REPO="$(cd "$REPO" && pwd)"
TOOL="$REPO/tools/deploy-pack"
MODULE="$TOOL/src/deploy_pack/gitignore_managed.py"
TEST="$TOOL/tests/test_gitignore01.py"
VERSION="$TOOL/VERSION"
INIT="$TOOL/src/deploy_pack/__init__.py"
PYPROJECT="$TOOL/pyproject.toml"
DOCS="$TOOL/docs"
MANIFESTS="$DOCS/manifests"
DOC="$DOCS/DEPLOY-PACK-GITIGNORE-01-FIX01.md"
MANIFEST="$MANIFESTS/DEPLOY-PACK-GITIGNORE-01-FIX01.json"

fail() {
  printf '%s\n' "DEPLOY-PACK-GITIGNORE-01-FIX01: FAIL — $*" >&2
  exit 1
}

for f in "$MODULE" "$TEST" "$VERSION" "$INIT"; do
  [[ -f "$f" ]] || fail "missing required file: $f"
done
mkdir -p "$DOCS" "$MANIFESTS"

echo "== patch managed runtime-security ignores =="
python3 - "$MODULE" "$TEST" <<'PY'
from pathlib import Path
import sys

module = Path(sys.argv[1])
test = Path(sys.argv[2])

s = module.read_text(encoding="utf-8")

rules_anchor = '''IGNORE_RULES = (
    ".deploy-pack.lock",
    ".deploy-pack-closeout-session.json",
'''
rules_replacement = '''IGNORE_RULES = (
    ".deploy-pack.lock",
    ".deploy-pack-closeout-session.json",
    "",
    "# Mutable deploy-pack runtime/security state",
    ".deploy-pack-keyring.json",
    ".deploy-pack-replay.json",
    ".deploy-pack-verifiers.json",
'''

if ".deploy-pack-keyring.json" not in s:
    if rules_anchor not in s:
        raise SystemExit("FIX01: cannot find IGNORE_RULES anchor")
    s = s.replace(rules_anchor, rules_replacement, 1)

module.write_text(s, encoding="utf-8")

t = test.read_text(encoding="utf-8")
if "test_mutable_security_state_is_ignored" not in t:
    anchor = '''    def test_durable_state_is_unignored_after_legacy_broad_rule(self):
'''
    if anchor not in t:
        raise SystemExit("FIX01: cannot find durable-state test anchor")

    new_test = '''    def test_mutable_security_state_is_ignored(self):
        install_managed_gitignore(self.root)

        for path in (
            ".deploy-pack-keyring.json",
            ".deploy-pack-replay.json",
            ".deploy-pack-verifiers.json",
        ):
            p = git(self.root, "check-ignore", "-q", "--", path)
            self.assertEqual(
                p.returncode,
                0,
                f"{path} is unexpectedly visible to Git",
            )

'''
    t = t.replace(anchor, new_test + anchor, 1)

test.write_text(t, encoding="utf-8")
PY

echo
echo "== version 1.15.1 =="
printf '%s\n' "1.15.1" > "$VERSION"

python3 - "$INIT" "$PYPROJECT" <<'PY'
from pathlib import Path
import re
import sys

init = Path(sys.argv[1])
s = init.read_text(encoding="utf-8")
s, n = re.subn(
    r'(__version__\s*=\s*["\'])\d+\.\d+\.\d+(["\'])',
    r'\g<1>1.15.1\2',
    s,
    count=1,
)
if n == 0:
    raise SystemExit("FIX01: could not update __version__")
init.write_text(s, encoding="utf-8")

pyproject = Path(sys.argv[2])
if pyproject.exists():
    p = pyproject.read_text(encoding="utf-8")
    p, n = re.subn(
        r'(?m)^(version\s*=\s*["\'])\d+\.\d+\.\d+(["\'])',
        r'\g<1>1.15.1\2',
        p,
        count=1,
    )
    if n:
        pyproject.write_text(p, encoding="utf-8")
PY

cat > "$DOC" <<'MD'
# DEPLOY-PACK-GITIGNORE-01-FIX01

Version: **1.15.1**

`DEPLOY-PACK-GITIGNORE-01` omitted three mutable deploy-pack runtime/security
state files from the managed `.gitignore` block:

```text
.deploy-pack-keyring.json
.deploy-pack-replay.json
.deploy-pack-verifiers.json
```

These files are now managed ignores.

The durable project/deployment records remain visible to Git:

```text
.deploy-pack.toml
.deploy-pack-baseline
.deploy-pack-history.jsonl
```

For existing projects, reconcile the managed block:

```bash
dp gitignore install
dp gitignore status --check
```
MD

cat > "$MANIFEST" <<'JSON'
{
  "increment": "DEPLOY-PACK-GITIGNORE-01-FIX01",
  "version": "1.15.1",
  "type": "bugfix",
  "adds_managed_ignores": [
    ".deploy-pack-keyring.json",
    ".deploy-pack-replay.json",
    ".deploy-pack-verifiers.json"
  ],
  "durable_visible_paths": [
    ".deploy-pack.toml",
    ".deploy-pack-baseline",
    ".deploy-pack-history.jsonl"
  ]
}
JSON

PY="$TOOL/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

echo
echo "== syntax =="
PYTHONPATH="$TOOL/src" "$PY" -m py_compile "$MODULE" "$TEST"

echo
echo "== focused regression =="
(
  cd "$TOOL"
  PYTHONPATH="$TOOL/src" "$PY" -m unittest discover -s tests -p 'test_gitignore01.py' -v
)

echo
echo "== managed-ignore integration smoke =="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git -C "$TMP" init -q -b primary
(
  cd "$TMP"
  PYTHONPATH="$TOOL/src" DEPLOY_PACK_COLOR=never "$PY" -m deploy_pack.cli gitignore install >/dev/null

  for p in .deploy-pack-keyring.json .deploy-pack-replay.json .deploy-pack-verifiers.json; do
    git check-ignore -q -- "$p" || fail "$p is not ignored after gitignore install"
  done

  for p in .deploy-pack.toml .deploy-pack-baseline .deploy-pack-history.jsonl; do
    if git check-ignore -q -- "$p"; then
      fail "$p must remain visible to Git"
    fi
  done

  PYTHONPATH="$TOOL/src" DEPLOY_PACK_COLOR=never "$PY" -m deploy_pack.cli gitignore status --check >/dev/null
)
rm -rf "$TMP"
trap - EXIT

if [[ "${DEPLOY_PACK_GITIGNORE01_SKIP_FULL_TESTS:-0}" != "1" ]]; then
  echo
echo "== full deploy-pack regression =="
  (
    cd "$REPO"
    make test TOOL=deploy-pack
  )
else
  echo
echo "== full deploy-pack regression =="
  echo "SKIPPED by DEPLOY_PACK_GITIGNORE01_SKIP_FULL_TESTS=1"
fi

echo
echo "DEPLOY-PACK-GITIGNORE-01-FIX01: PASS"
echo "Version: 1.15.1"
echo
echo "Next:"
echo "  make install TOOL=deploy-pack"
echo "  rehash"
echo "  cd /path/to/project"
echo "  dp gitignore install"
echo "  dp gitignore status --check"
