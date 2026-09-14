#!/usr/bin/env bash
set -euo pipefail

REPO="${1:-.}"
REPO="$(cd "$REPO" && pwd)"
APPLY="$REPO/scripts/apply-deploy-pack-ci-01.sh"

fail() {
  printf '%s\n' "DEPLOY-PACK-CI-01-FIX01: FAIL — $*" >&2
  exit 1
}

[[ -f "$APPLY" ]] || fail "missing $APPLY"

echo "== repair mutation-surface audit =="

python3 - "$APPLY" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
s = path.read_text(encoding="utf-8")

old = """if grep -Eq 'mark|reconcile-baseline|verifier issue|verifier revoke|key rotate|recovery.*(create|apply)|custody.*(write|update)' "$MODULE"; then
  # Documentation/error text should not introduce mutation commands either; keep the
  # implementation surface intentionally narrow and auditable.
  fail "CI module contains a forbidden deployment-truth mutation command"
fi
"""

new = """# Audit command spellings rather than arbitrary identifiers.  In particular,
# bare `mark` is a normal presentation variable in ci.py and must not be treated
# as invocation of the deploy-pack `mark` command.
if grep -Eq '["'\"'\"']mark["'\"'\"']|reconcile-baseline|verifier issue|verifier revoke|key rotate|recovery.*(create|apply)|custody.*(write|update)' "$MODULE"; then
  # Documentation/error text should not introduce mutation commands either; keep the
  # implementation surface intentionally narrow and auditable.
  fail "CI module contains a forbidden deployment-truth mutation command"
fi
"""

if old in s:
    s = s.replace(old, new, 1)
elif "bare `mark` is a normal presentation variable" in s:
    print("FIX01 already applied")
else:
    raise SystemExit("FIX01: expected CI mutation-surface audit not found")

path.write_text(s, encoding="utf-8")
PY

echo
echo "== shell syntax =="
bash -n "$APPLY"

echo
echo "== verify harmless formatter identifier no longer trips audit =="
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
cat > "$TMP" <<'PY'
mark = "PASS" if True else "FAIL"
print(f"{mark}")
PY

if grep -Eq '["'"'"']mark["'"'"']|reconcile-baseline|verifier issue|verifier revoke|key rotate|recovery.*(create|apply)|custody.*(write|update)' "$TMP"; then
  fail "repaired audit still rejects harmless mark variable"
fi

echo
echo "== verify actual mark command spelling is rejected =="
printf '%s\n' 'command = "mark"' > "$TMP"
if ! grep -Eq '["'"'"']mark["'"'"']|reconcile-baseline|verifier issue|verifier revoke|key rotate|recovery.*(create|apply)|custody.*(write|update)' "$TMP"; then
  fail "repaired audit failed to detect quoted mark command"
fi

rm -f "$TMP"
trap - EXIT

echo
echo "== rerun CI-01 apply/gates =="
bash "$APPLY" "$REPO"

echo
echo "== repository changes =="
git -C "$REPO" status --short

echo
echo "DEPLOY-PACK-CI-01-FIX01: PASS"
