# DEPLOY-PACK-GITIGNORE-01

Version: **1.15.0**

## Commands

```bash
dp gitignore status
dp gitignore status --check
dp gitignore install
dp gitignore remove
```

`deploy-pack` works identically to `dp`.

## Ownership

deploy-pack owns only the bounded block between:

```text
# BEGIN deploy-pack managed ignores
# END deploy-pack managed ignores
```

Project-authored bytes outside that block are preserved. Reconciliation places the
managed block at EOF so durable-state negations can override older broad ignore rules.

## Durable deploy-pack files

These remain visible to Git:

```text
.deploy-pack.toml
.deploy-pack-baseline
.deploy-pack-history.jsonl
```

## Init integration

`dp init` creates the normal fail-closed selection policy and installs/reconciles the
managed `.gitignore` block.

## CI

`dp gitignore status --check` exits nonzero when the block is missing/stale or durable
state is still ignored.
