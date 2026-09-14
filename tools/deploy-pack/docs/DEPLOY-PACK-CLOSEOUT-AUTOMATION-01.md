# DEPLOY-PACK-CLOSEOUT-AUTOMATION-01

Release: **1.14.0**

## Goal

Reduce routine Git-aware deployment closeout to two local operator commands without weakening the trust model or pretending that a local process proves a remote deployment occurred.

## Workflow

### 1. Prepare

```sh
make deploy-prepare
```

This performs, in order:

1. refuses tracked/index dirt so the deployed bytes have a canonical Git commit;
2. `deploy-pack inspect`;
3. `deploy-pack pack` into a sibling `deploy-pack-closeout-<ref>/` directory;
4. local archive verification;
5. verifier identity issuance;
6. signed remote-verifier generation;
7. durable session metadata creation outside the repository;
8. prints the exact production verifier command and finish command.

The operator then deploys the archive contents and runs the generated verifier on production. That remote boundary remains explicit because deploy-pack cannot infer that cPanel/shared-hosting upload occurred successfully.

### 2. Finish

After bringing the production signed-evidence JSON back locally:

```sh
make deploy-closeout EVIDENCE=/path/to/deploy-<ref>.signed-evidence.json
```

This performs:

1. signed evidence ingestion;
2. public-key binding;
3. normalization;
4. `deploy-pack mark` of the exact prepared commit;
5. baseline check;
6. recent history display;
7. history-chain verification;
8. deployment status gate;
9. session transition from `prepared` to `closed`.

If multiple prepared sessions exist, specify the intended one:

```sh
make deploy-closeout \
  EVIDENCE=/path/to/evidence.json \
  SESSION=../deploy-pack-closeout-<ref>
```

## Safety properties

- No automatic closeout from a tracked-dirty or staged-dirty tree.
- Generated archives/verifiers/evidence remain outside the project repository by default.
- No `--unsafe-no-evidence` path.
- No remote upload is claimed or inferred.
- The prepared Git commit is persisted in session metadata and is the only ref the finish phase marks.
- Existing signed evidence, replay, verifier lifecycle, transaction, ledger, and history protections remain authoritative.

## Make targets

```text
make deploy-prepare
make deploy-closeout EVIDENCE=<signed-evidence.json>
make deploy-closeout-status
```

Optional prepare variables: `REF`, `TTL`, `LANGUAGE`, `OUT_DIR`.
Optional finish/status variable: `SESSION`.
