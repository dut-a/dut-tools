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

# BEGIN DEPLOY-PACK-SHORT-ALIAS-01
# Install a short alias to the canonical installed deploy-pack command.
#
# IMPORTANT:
#   The alias intentionally does NOT point into the source checkout. The root
#   dut-tools installer owns ~/.local/bin/deploy-pack; the short command is a
#   sibling symlink:
#
#       ~/.local/bin/dp -> deploy-pack
#
#   A relative sibling symlink survives repo moves and launcher-layout changes.
DEPLOY_PACK_SHORT_COMMAND="${DEPLOY_PACK_SHORT_COMMAND:-dp}"
case "$DEPLOY_PACK_SHORT_COMMAND" in
  ''|*[!A-Za-z0-9._-]*)
    printf '%s
' "ERROR: invalid DEPLOY_PACK_SHORT_COMMAND: $DEPLOY_PACK_SHORT_COMMAND" >&2
    exit 1
    ;;
esac

DEPLOY_PACK_ALIAS_DIR="${HOME}/.local/bin"
DEPLOY_PACK_CANONICAL_NAME="deploy-pack"
DEPLOY_PACK_CANONICAL="$DEPLOY_PACK_ALIAS_DIR/$DEPLOY_PACK_CANONICAL_NAME"
DEPLOY_PACK_ALIAS_TARGET="$DEPLOY_PACK_ALIAS_DIR/$DEPLOY_PACK_SHORT_COMMAND"
mkdir -p "$DEPLOY_PACK_ALIAS_DIR"

if [ "$DEPLOY_PACK_SHORT_COMMAND" = "$DEPLOY_PACK_CANONICAL_NAME" ]; then
  printf '%s
' "deploy-pack short command uses canonical name: $DEPLOY_PACK_CANONICAL"
else
  replace_alias=0

  if [ -L "$DEPLOY_PACK_ALIAS_TARGET" ]; then
    current_link=$(readlink "$DEPLOY_PACK_ALIAS_TARGET")

    case "$current_link" in
      "$DEPLOY_PACK_CANONICAL_NAME"|"$DEPLOY_PACK_CANONICAL")
        ;;
      */tools/deploy-pack/bin/deploy-pack)
        # Known broken alias from HELP-COLOR-ALIAS-01/FIX03.
        replace_alias=1
        ;;
      *)
        printf '%s
' \
          "ERROR: refusing to overwrite unrelated $DEPLOY_PACK_ALIAS_TARGET -> $current_link" >&2
        printf '%s
' \
          "Set DEPLOY_PACK_SHORT_COMMAND to another unused name and rerun installation." >&2
        exit 1
        ;;
    esac
  elif [ -e "$DEPLOY_PACK_ALIAS_TARGET" ]; then
    printf '%s
' \
      "ERROR: refusing to overwrite unrelated command: $DEPLOY_PACK_ALIAS_TARGET" >&2
    printf '%s
' \
      "Set DEPLOY_PACK_SHORT_COMMAND to another unused name and rerun installation." >&2
    exit 1
  else
    replace_alias=1
  fi

  if [ "$replace_alias" -eq 1 ]; then
    rm -f "$DEPLOY_PACK_ALIAS_TARGET"
    ln -s "$DEPLOY_PACK_CANONICAL_NAME" "$DEPLOY_PACK_ALIAS_TARGET"
  fi

  printf '%s
' \
    "deploy-pack short command ready: $DEPLOY_PACK_SHORT_COMMAND -> $DEPLOY_PACK_CANONICAL_NAME"
fi
# END DEPLOY-PACK-SHORT-ALIAS-01
