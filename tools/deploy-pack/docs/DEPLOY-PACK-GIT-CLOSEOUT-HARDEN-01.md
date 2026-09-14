# DEPLOY-PACK-GIT-CLOSEOUT-HARDEN-01

Release: 1.13.0

## Scope

1. Generated verifier/evidence artifacts are protected by content signature even when the operator chooses a custom filename.
2. Signed PHP/Python verifier CLIs parse options independently of ROOT; ROOT defaults to `.` and `--help` documents usage.
3. `deploy-pack reconcile-baseline` provides an audited correction path when production bytes are already correct but the recorded Git baseline is wrong.

## Reconciliation invariant

Reconciliation is **not** an evidence bypass. It requires a correction archive and fresh signed remote evidence whose manifest `headCommit` equals the requested ref. The target must descend from the current recorded baseline. Replay protection, repository locking, transactional state mutation, history hashing, and evidence consumption remain active.

Example:

```sh
deploy-pack reconcile-baseline 444b22c \
  --archive baseline-correction-444b22c.zip \
  --evidence baseline-correction-444b22c-evidence.json \
  --reason "Production bytes were deployed from a dirty tree and later committed exactly as 444b22c; earlier closeout recorded the pre-commit HEAD."
```
