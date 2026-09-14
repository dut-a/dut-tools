#!/usr/bin/env bash
set -euo pipefail
export GIT_PAGER=cat PAGER=cat GIT_TERMINAL_PROMPT=0

REPO="${1:-.}"
REPO="$(cd "$REPO" && pwd)"
TOOL="$REPO/tools/deploy-pack"
BOOTSTRAP="$TOOL/scripts/bootstrap-runtime.sh"

fail() {
  printf '%s\n' "DEPLOY-PACK-HELP-COLOR-ALIAS-01-FIX04: FAIL — $*" >&2
  exit 1
}

[[ -f "$BOOTSTRAP" ]] || fail "missing $BOOTSTRAP"

echo "== patch short-alias installer =="

python3 - "$BOOTSTRAP" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

begin = "# BEGIN DEPLOY-PACK-SHORT-ALIAS-01"
end = "# END DEPLOY-PACK-SHORT-ALIAS-01"

if begin not in text or end not in text:
    raise SystemExit(
        "FIX04: short-alias installer block missing; "
        "apply DEPLOY-PACK-HELP-COLOR-ALIAS-01 first"
    )

replacement = r'''# BEGIN DEPLOY-PACK-SHORT-ALIAS-01
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
    printf '%s\n' "ERROR: invalid DEPLOY_PACK_SHORT_COMMAND: $DEPLOY_PACK_SHORT_COMMAND" >&2
    exit 1
    ;;
esac

DEPLOY_PACK_ALIAS_DIR="${HOME}/.local/bin"
DEPLOY_PACK_CANONICAL_NAME="deploy-pack"
DEPLOY_PACK_CANONICAL="$DEPLOY_PACK_ALIAS_DIR/$DEPLOY_PACK_CANONICAL_NAME"
DEPLOY_PACK_ALIAS_TARGET="$DEPLOY_PACK_ALIAS_DIR/$DEPLOY_PACK_SHORT_COMMAND"
mkdir -p "$DEPLOY_PACK_ALIAS_DIR"

if [ "$DEPLOY_PACK_SHORT_COMMAND" = "$DEPLOY_PACK_CANONICAL_NAME" ]; then
  printf '%s\n' "deploy-pack short command uses canonical name: $DEPLOY_PACK_CANONICAL"
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
        printf '%s\n' \
          "ERROR: refusing to overwrite unrelated $DEPLOY_PACK_ALIAS_TARGET -> $current_link" >&2
        printf '%s\n' \
          "Set DEPLOY_PACK_SHORT_COMMAND to another unused name and rerun installation." >&2
        exit 1
        ;;
    esac
  elif [ -e "$DEPLOY_PACK_ALIAS_TARGET" ]; then
    printf '%s\n' \
      "ERROR: refusing to overwrite unrelated command: $DEPLOY_PACK_ALIAS_TARGET" >&2
    printf '%s\n' \
      "Set DEPLOY_PACK_SHORT_COMMAND to another unused name and rerun installation." >&2
    exit 1
  else
    replace_alias=1
  fi

  if [ "$replace_alias" -eq 1 ]; then
    rm -f "$DEPLOY_PACK_ALIAS_TARGET"
    ln -s "$DEPLOY_PACK_CANONICAL_NAME" "$DEPLOY_PACK_ALIAS_TARGET"
  fi

  printf '%s\n' \
    "deploy-pack short command ready: $DEPLOY_PACK_SHORT_COMMAND -> $DEPLOY_PACK_CANONICAL_NAME"
fi
# END DEPLOY-PACK-SHORT-ALIAS-01'''

pattern = re.compile(
    re.escape(begin) + r".*?" + re.escape(end),
    flags=re.DOTALL,
)
new_text, count = pattern.subn(replacement, text, count=1)
if count != 1:
    raise SystemExit(f"FIX04: expected exactly one alias block, replaced {count}")

path.write_text(new_text, encoding="utf-8")
PY

echo
echo "== shell syntax =="
bash -n "$BOOTSTRAP"

echo
echo "== install/update canonical command + alias =="
(
  cd "$REPO"
  make install TOOL=deploy-pack
)

SHORT="${DEPLOY_PACK_SHORT_COMMAND:-dp}"
BIN_DIR="$HOME/.local/bin"
CANONICAL="$BIN_DIR/deploy-pack"
ALIAS="$BIN_DIR/$SHORT"

echo
echo "== canonical launcher verification =="
[[ -e "$CANONICAL" || -L "$CANONICAL" ]] || fail "canonical launcher missing: $CANONICAL"
[[ -x "$CANONICAL" ]] || fail "canonical launcher is not executable: $CANONICAL"
printf '%s\n' "$CANONICAL"
DEPLOY_PACK_COLOR=never "$CANONICAL" --version

if [ "$SHORT" != "deploy-pack" ]; then
  echo
  echo "== alias filesystem verification =="
  [[ -L "$ALIAS" ]] || fail "expected symlink not created: $ALIAS"

  LINK="$(readlink "$ALIAS")"
  printf '%s -> %s\n' "$ALIAS" "$LINK"

  case "$LINK" in
    deploy-pack|"$CANONICAL")
      ;;
    *)
      fail "alias target is not canonical deploy-pack: $LINK"
      ;;
  esac

  [[ -x "$ALIAS" ]] || fail "alias is not executable through canonical launcher"

  echo
  echo "== alias equivalence smoke =="
  CANONICAL_VERSION="$(DEPLOY_PACK_COLOR=never "$CANONICAL" --version)"
  ALIAS_VERSION="$(DEPLOY_PACK_COLOR=never "$ALIAS" --version)"
  [[ "$CANONICAL_VERSION" = "$ALIAS_VERSION" ]] || \
    fail "dp and deploy-pack report different versions"

  DEPLOY_PACK_COLOR=never "$ALIAS" deploy status >/dev/null
fi

echo
echo "== PATH visibility =="
case ":${PATH}:" in
  *":$BIN_DIR:"*)
    echo "$BIN_DIR is on PATH"
    ;;
  *)
    echo "WARNING: $BIN_DIR is not on PATH in this shell." >&2
    echo "The alias is installed correctly, but your shell cannot find it by name." >&2
    ;;
esac

echo
echo "DEPLOY-PACK-HELP-COLOR-ALIAS-01-FIX04: PASS"
echo "If zsh cached the earlier command-not-found result, run: rehash"
