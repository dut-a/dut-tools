# DEPLOY-PACK-HELP-COLOR-ALIAS-01-FIX01

## Problem

On Python 3.14, `argparse.ArgumentParser` has a native ANSI color layer enabled by default. `deploy-pack` 1.14.2 correctly disabled its own formatter colors when `DEPLOY_PACK_COLOR=never`, but Python's native argparse layer could still emit ANSI escape sequences.

This caused:

```text
test_never_is_plain ... FAIL
AssertionError: '\x1b[' unexpectedly found
```

`NO_COLOR` happened to pass because Python's own argparse implementation also honors that convention, masking the split ownership of color behavior.

## Fix

`_apply_help_formatter_tree()` now sets:

```python
parser_obj.color = False
```

for the top-level parser and every nested parser before help is rendered.

This deliberately disables Python 3.14+'s native argparse coloring. `DeployPackHelpFormatter` remains the only component allowed to emit ANSI, controlled by:

- `NO_COLOR`
- `DEPLOY_PACK_COLOR=auto`
- `DEPLOY_PACK_COLOR=always`
- `DEPLOY_PACK_COLOR=never`

On Python <= 3.13 the assigned `color` attribute is inert and harmless.

## Regression coverage

The patch verifies:

1. `DEPLOY_PACK_COLOR=never` contains no ANSI.
2. `NO_COLOR` suppresses ANSI.
3. `DEPLOY_PACK_COLOR=always` emits ANSI.
4. every parser in the nested argparse tree has native argparse color disabled.
5. the complete deploy-pack suite remains green.

## Version

Bugfix release: `1.14.3`.
