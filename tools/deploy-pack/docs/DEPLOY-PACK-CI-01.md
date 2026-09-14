# DEPLOY-PACK-CI-01

Version: **1.16.0**

## Purpose

Provide a stable CI contract so consuming repositories do not need to understand
deploy-pack's internal command graph.

## Commands

```bash
dp ci check
dp ci check --json

dp ci build --output dist/deploy.zip
dp ci build --output dist/deploy.zip --json
```

`deploy-pack` works identically to `dp`.

## `ci check`

Runs these existing read-only gates in a fixed order:

1. `gitignore status --check`
2. `inspect`
3. `history-verify`
4. `deploy status`

The workflow fingerprints `.deploy-pack*` state before and after the run, excluding
the transient lock and closeout-session files. Any authoritative-state mutation makes
the CI workflow fail even when all nested commands returned zero.

`ci check` never calls `mark`, `reconcile-baseline`, key rotation, verifier mutation,
recovery mutation, custody mutation, or any other deployment-truth-changing command.

## `ci build`

Runs:

```text
inspect
  -> pack --output <artifact>
  -> verify <exact artifact>
```

Later phases are skipped after an earlier failure.

On success it reports:

- absolute artifact path
- byte size
- SHA-256

The same authoritative-state mutation guard used by `ci check` applies.

## JSON schema

Both commands support `--json` and emit one JSON document with:

```json
{
  "schema": 1,
  "workflow": "check|build",
  "status": "pass|fail",
  "steps": [],
  "state_mutation": {
    "detected": false,
    "diff": {
      "added": [],
      "removed": [],
      "changed": []
    }
  }
}
```

`ci build` additionally includes `artifact`.

## GitHub Actions baseline

A consuming project should use a full Git history:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
```

Then:

```yaml
- name: deploy-pack CI checks
  run: dp ci check

- name: build verified deployment package
  run: dp ci build --output "$RUNNER_TEMP/deploy.zip" --json
```

Do not run deployment closeout (`mark`/reconciliation) in ordinary PR/build CI.
