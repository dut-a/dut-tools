#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MAKEFILE="$ROOT_DIR/Makefile"
FRAGMENT="$ROOT_DIR/mk/deploy-pack.inc"

fail() {
    printf '%s\n' "DEPLOY-STATUS-CHECKER: FAIL — $*" >&2
    exit 1
}

pass() {
    printf '%s\n' "DEPLOY-STATUS-CHECKER: PASS"
}

[ -f "$MAKEFILE" ] || fail "root Makefile is missing"
[ -f "$FRAGMENT" ] || fail "mk/deploy-pack.inc is missing"

grep -Eq '^[[:space:]]*-?include[[:space:]]+mk/deploy-pack\.inc([[:space:]]|$)' "$MAKEFILE" \
    || fail "root Makefile does not include mk/deploy-pack.inc"

make -C "$ROOT_DIR" -n deploy-status-check >/dev/null 2>&1 \
    || fail "root deploy-status-check target is not resolvable"

grep -Eq '^deploy-status-check:' "$FRAGMENT" \
    || fail "deploy-status-check target is not declared in fragment"

grep -Fq 'deploy-pack deploy status --quiet' "$FRAGMENT" \
    || fail "deploy-status-check does not delegate to quiet deploy status"

pass
