# DEPLOY-PACK-HELP-COLOR-ALIAS-01

Release: 1.14.2

## Purpose

Improve deploy-pack's interactive help readability and reduce command-entry friction without creating a second implementation.

## Help coloring

All argparse help levels now use one TTY-aware formatter:

- section headings and `usage:`: bold cyan;
- option invocations: green;
- positional arguments: yellow;
- uppercase workflow headings in long help text: bold cyan.

Color is enabled automatically only for an interactive terminal. Piped/redirected output remains plain. `NO_COLOR=1` disables styling. `DEPLOY_PACK_COLOR=auto|always|never` controls the mode explicitly.

## Short command

`dp` is installed as a symlink to the canonical `tools/deploy-pack/bin/deploy-pack` launcher. Both commands therefore execute exactly the same code and state:

```sh
deploy-pack deploy status
dp deploy status
```

The installer refuses to replace an unrelated existing `~/.local/bin/dp`. `DEPLOY_PACK_SHORT_COMMAND=<name>` may be supplied during installation if a workstation already owns `dp`.

`deploy-pack` remains the canonical command name in documentation, scripts, evidence, and automation.
