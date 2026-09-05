#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "ERROR: $PYTHON not found" >&2
  exit 1
fi

if ! "$PYTHON" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
then
  echo "ERROR: deploy-pack requires Python >= 3.11" >&2
  exit 1
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "SETUP deploy-pack runtime: creating $VENV"
  "$PYTHON" -m venv "$VENV"
fi

# Runtime provisioning intentionally installs only runtime dependencies.
# deploy-pack itself executes directly from the checked-out src/ tree, so we do
# not invoke a PEP 517 build or require the frozen build dependency here.
if "$VENV/bin/python" - <<'PY' >/dev/null 2>&1
import cryptography
raise SystemExit(0 if cryptography.__version__ == "46.0.4" else 1)
PY
then
  echo "SETUP deploy-pack runtime: frozen runtime dependencies already satisfied"
else
  echo "SETUP deploy-pack runtime: installing frozen runtime dependencies"
  "$VENV/bin/python" -m pip install --disable-pip-version-check \
    -c "$ROOT/constraints-freeze.txt" \
    "cryptography>=46,<47"
fi

"$VENV/bin/python" - <<'PY'
import cryptography
print(f"deploy-pack runtime ready (cryptography {cryptography.__version__})")
PY
