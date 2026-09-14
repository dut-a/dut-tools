# DEPLOY-PACK-CI-01

This delivery implements deploy-pack **1.16.0**.

## New CI façade

```bash
dp ci check
dp ci check --json

dp ci build --output dist/deploy.zip
dp ci build --output dist/deploy.zip --json
```

### `ci check`

Aggregates:

```text
gitignore status --check
inspect
history-verify
deploy status
```

### `ci build`

Runs:

```text
inspect -> pack -> verify
```

and reports the exact artifact path, size, and SHA-256.

### Non-mutation invariant

Both workflows fingerprint deploy-pack state before and after execution and fail if
authoritative `.deploy-pack*` state changes unexpectedly. The transient lock and
closeout-session files are exempt.

This intentionally keeps production closeout (`mark`, baseline reconciliation, trust
mutation, recovery/custody mutation) outside ordinary CI.

## Apply

```bash
bash scripts/apply-deploy-pack-ci-01.sh .
make install TOOL=deploy-pack
rehash
```

Then, from a consuming project:

```bash
dp ci check
dp ci build --output /tmp/deploy.zip
```

For GitHub Actions, use a full checkout (`fetch-depth: 0`) because deploy-pack is
history-aware.
