#!/usr/bin/env bash
set -euo pipefail
export GIT_PAGER=cat PAGER=cat GIT_TERMINAL_PROMPT=0

REPO="${1:-.}"
REPO="$(cd "$REPO" && pwd)"
TOOL="$REPO/tools/deploy-pack"
BOOTSTRAP="$TOOL/scripts/bootstrap-runtime.sh"

[[ -f "$BOOTSTRAP" ]] || { echo "DEPLOY-PACK-HELP-COLOR-ALIAS-01-FIX03: FAIL — missing $BOOTSTRAP" >&2; exit 2; }
grep -q 'BEGIN DEPLOY-PACK-SHORT-ALIAS-01' "$BOOTSTRAP" || {
  echo "DEPLOY-PACK-HELP-COLOR-ALIAS-01-FIX03: FAIL — short-alias installer block is missing; apply HELP-COLOR-ALIAS-01 first" >&2
  exit 2
}

SHORT="${DEPLOY_PACK_SHORT_COMMAND:-dp}"
ALIAS="$HOME/.local/bin/$SHORT"

echo "== install/update deploy-pack + short alias =="
(
  cd "$REPO"
  make install TOOL=deploy-pack
)

echo
echo "== alias filesystem verification =="
[[ -L "$ALIAS" ]] || {
  echo "DEPLOY-PACK-HELP-COLOR-ALIAS-01-FIX03: FAIL — expected symlink not created: $ALIAS" >&2
  exit 1
}
printf '%s -> %s\n' "$ALIAS" "$(readlink "$ALIAS")"
[[ -x "$ALIAS" ]] || {
  echo "DEPLOY-PACK-HELP-COLOR-ALIAS-01-FIX03: FAIL — alias is not executable through its target" >&2
  exit 1
}

echo
echo "== alias behavior smoke =="
DEPLOY_PACK_COLOR=never "$ALIAS" --version
DEPLOY_PACK_COLOR=never "$ALIAS" deploy status >/dev/null

echo
echo "== PATH visibility =="
case ":${PATH}:" in
  *":$HOME/.local/bin:"*)
    echo "$HOME/.local/bin is on PATH"
    ;;
  *)
    echo "WARNING: $HOME/.local/bin is not on PATH in this shell." >&2
    echo 'Add it to PATH before using the short command.' >&2
    ;;
esac

echo
echo "DEPLOY-PACK-HELP-COLOR-ALIAS-01-FIX03: PASS"
echo "If zsh still reports a cached 'command not found', run: rehash"
