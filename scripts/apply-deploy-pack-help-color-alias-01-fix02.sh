#!/usr/bin/env bash
set -euo pipefail
export GIT_PAGER=cat PAGER=cat GIT_TERMINAL_PROMPT=0

REPO="${1:-.}"
REPO="$(cd "$REPO" && pwd)"
TOOL="$REPO/tools/deploy-pack"
CLI="$TOOL/src/deploy_pack/cli.py"
TEST="$TOOL/tests/test_help_color_alias01.py"
VERSION="$TOOL/VERSION"
INIT="$TOOL/src/deploy_pack/__init__.py"
PYPROJECT="$TOOL/pyproject.toml"
PY="$TOOL/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3

for f in "$CLI" "$TEST" "$VERSION" "$INIT" "$PYPROJECT"; do
  [[ -f "$f" ]] || { echo "DEPLOY-PACK-HELP-COLOR-ALIAS-01-FIX02: FAIL — missing $f" >&2; exit 2; }
done

python3 - "$CLI" "$TEST" "$VERSION" "$INIT" "$PYPROJECT" <<'PY'
from pathlib import Path
import re, sys
cli,test,version,init,pyproject = map(Path, sys.argv[1:])

s=cli.read_text(encoding='utf-8')
old='''def _apply_help_formatter_tree(parser_obj):\n    \"\"\"Apply one help formatter to every nested argparse parser.\"\"\"\n    parser_obj.formatter_class = DeployPackHelpFormatter\n    for action in getattr(parser_obj, \"_actions\", ()):\n'''
new='''def _apply_help_formatter_tree(parser_obj):\n    \"\"\"Apply deploy-pack help rendering to every nested argparse parser.\n\n    Python 3.14+ argparse has its own ANSI color layer enabled by default. Disable\n    that layer explicitly so DEPLOY_PACK_COLOR/NO_COLOR remain the single source\n    of truth across every supported Python version. DeployPackHelpFormatter then\n    adds our styling only when _help_color_enabled() permits it.\n    \"\"\"\n    parser_obj.formatter_class = DeployPackHelpFormatter\n    # argparse <= 3.13 does not consume this attribute; assigning it is harmless.\n    # argparse 3.14+ does consume it and would otherwise emit ANSI independently.\n    parser_obj.color = False\n    for action in getattr(parser_obj, \"_actions\", ()):\n'''
if old in s:
    s=s.replace(old,new,1)
elif 'parser_obj.color = False' not in s:
    raise SystemExit('FIX01: help formatter tree anchor missing')
cli.write_text(s,encoding='utf-8')

# Strengthen the regression so future refactors cannot re-enable argparse's native layer.
t=test.read_text(encoding='utf-8')
needle='''    def test_never_is_plain(self):\n        env=dict(os.environ)\n        env.pop("NO_COLOR",None)\n        env["DEPLOY_PACK_COLOR"]="never"\n        with patch.dict(os.environ, env, clear=True):\n            text=parser().format_help()\n        self.assertNotIn("\\x1b[",text)\n'''
replacement='''    def test_never_is_plain(self):\n        env=dict(os.environ)\n        env.pop("NO_COLOR",None)\n        env["DEPLOY_PACK_COLOR"]="never"\n        with patch.dict(os.environ, env, clear=True):\n            p=parser()\n            text=p.format_help()\n        self.assertFalse(getattr(p, "color", False))\n        self.assertNotIn("\\x1b[",text)\n\n    def test_argparse_native_color_disabled_across_parser_tree(self):\n        def walk(p):\n            yield p\n            for action in getattr(p, "_actions", ()):\n                if isinstance(action, argparse._SubParsersAction):\n                    for child in action.choices.values():\n                        yield from walk(child)\n\n        import argparse\n        for p in walk(parser()):\n            self.assertFalse(getattr(p, "color", False), p.prog)\n'''
if 'test_argparse_native_color_disabled_across_parser_tree' not in t:
    if needle not in t:
        raise SystemExit('FIX01: never-color test anchor missing')
    t=t.replace(needle,replacement,1)
test.write_text(t,encoding='utf-8')

# Patch release number only when still at the affected release.
version.write_text('1.14.3\n',encoding='utf-8')
for p in (init,pyproject):
    x=p.read_text(encoding='utf-8')
    x=re.sub(r'(?m)(__version__\s*=\s*["\'])1\.14\.2(["\'])',r'\g<1>1.14.3\2',x)
    x=re.sub(r'(?m)^(version\s*=\s*["\'])1\.14\.2(["\']\s*)$',r'\g<1>1.14.3\2',x)
    p.write_text(x,encoding='utf-8')
PY

echo "== python syntax =="
"$PY" -m py_compile "$CLI" "$TEST"

echo
echo "== focused color regression =="
(
  cd "$TOOL"
  "$PY" -m unittest tests.test_help_color_alias01 -v
)

echo
echo "== explicit never smoke =="
TEXT="$(cd "$REPO" && PYTHONPATH="$TOOL/src${PYTHONPATH:+:$PYTHONPATH}" DEPLOY_PACK_COLOR=never "$PY" -m deploy_pack.cli --help)"
case "$TEXT" in
  *$'\033['*)
    echo "DEPLOY-PACK-HELP-COLOR-ALIAS-01-FIX02: FAIL — ANSI present under DEPLOY_PACK_COLOR=never" >&2
    exit 1
    ;;
esac

echo
echo "== explicit always smoke =="
TEXT="$(cd "$REPO" && PYTHONPATH="$TOOL/src${PYTHONPATH:+:$PYTHONPATH}" DEPLOY_PACK_COLOR=always "$PY" -m deploy_pack.cli --help)"
case "$TEXT" in
  *$'\033['*) : ;;
  *)
    echo "DEPLOY-PACK-HELP-COLOR-ALIAS-01-FIX02: FAIL — ANSI absent under DEPLOY_PACK_COLOR=always" >&2
    exit 1
    ;;
esac

echo
echo "== full deploy-pack regression =="
(
  cd "$REPO"
  make test TOOL=deploy-pack
)

echo
echo "DEPLOY-PACK-HELP-COLOR-ALIAS-01-FIX02: PASS"
