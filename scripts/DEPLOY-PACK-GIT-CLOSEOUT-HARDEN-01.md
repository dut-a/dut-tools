# DEPLOY-PACK-GIT-CLOSEOUT-HARDEN-01

Target release: **deploy-pack 1.13.0**

## What this increment hardens

### 1. Generated verifier/evidence contamination

`protected_artifact_reason()` now recognizes deploy-pack generated artifacts by content, not only by conventional filename. This prevents custom-named outputs such as:

- ordinary remote verifiers;
- signed verifiers;
- verifier public-key sidecars;
- signed remote evidence;
- normalized verification evidence;

from silently entering a later Git-aware deployment merely because a project allowlist accepts `*.php`, `*.json`, or another broad pattern.

The protection remains non-overridable and is evaluated before project/CLI inclusion policy.

### 2. Signed verifier CLI parsing

Generated PHP and Python signed verifiers now use actual option parsing semantics:

```sh
php verifier.php --signed-evidence-out evidence.json
php verifier.php . --signed-evidence-out evidence.json
php verifier.php --signed-evidence-out evidence.json .
php verifier.php --help
```

`ROOT` defaults to `.`. Options no longer depend on occupying `argv[2]`, and unknown options / duplicate positional roots fail closed.

`deploy-pack remote-verifier --sign` also prints an executable remote invocation rather than the old ambiguous “add --signed-evidence-out” hint.

### 3. Audited production-baseline reconciliation

Adds:

```sh
deploy-pack reconcile-baseline <ref> \
  --archive <correction-archive.zip> \
  --evidence <fresh-normalized-signed-evidence.json> \
  --reason "<audit reason>"
```

This is for the specific case exercised in production closeout: production bytes are already correct, but the recorded Git baseline points at the wrong commit.

It is **not** an evidence bypass. Reconciliation requires:

- an existing recorded baseline;
- a correction archive whose manifest `headCommit` matches `<ref>`;
- fresh deployment-level signed remote evidence bound to that archive;
- unconsumed replay state;
- a non-empty audit reason;
- a target commit that descends from the currently recorded baseline.

The operation keeps repository locking, mark transactions, replay consumption, evidence binding, ledger hashing, and history verification intact. The history record is explicitly typed `reconciliation`, rather than being misrepresented as an ordinary forward deployment.

Non-descendant corrections are rejected and must use the existing rollback/recovery semantics instead.

## Apply

From the `dut-tools` repository root:

```sh
bash /path/to/apply-deploy-pack-git-closeout-harden-01.sh .
```

The patch runs:

1. Python syntax checks;
2. the focused `DEPLOY-PACK-GIT-CLOSEOUT-HARDEN-01` tests;
3. `make test TOOL=deploy-pack`.

It only prints `DEPLOY-PACK-GIT-CLOSEOUT-HARDEN-01: PASS` after all three gates succeed.

## Focused tests

The increment adds tests covering:

- custom-named unsigned verifier content protection;
- custom-named normalized evidence protection;
- PHP signed-verifier option-first invocation and `--help`;
- Python signed-verifier option parsing;
- reconciliation descendant enforcement;
- successful reconciliation-target validation.
