# DEPLOY-PACK-HARDEN-16 — Transactional State Mutations + Repository Locking

## Objective

Close DP-AUD-006 and DP-AUD-007 by preventing partial `mark` state and lost updates from concurrent deploy-pack processes.

## Guarantees

- Mutating verifier, signer, recovery-trust, recovery-import, replay, and offline-checkpoint paths serialize through `.deploy-pack.lock`.
- The repository lock is process-wide, advisory, re-entrant within one deploy-pack process, owner-only (`0600`), and times out instead of waiting forever.
- Deploy-pack JSON/JSONL control state is written through same-directory temporary files, flushed with `fsync`, atomically replaced, and followed by a directory `fsync` where supported.
- `mark` is journaled in `.deploy-pack-mark-transaction.json` before baseline/history/replay mutation.
- The journal contains before-images of the three mark state files. Successful completion removes the journal only after all mutations complete.
- An exception during mark restores all before-images immediately.
- If the process dies, `deploy status` reports the transaction as unhealthy/recovery-pending. The next mutating operation acquires the repository lock and restores the before-images before continuing.
- Deployment-history JSONL extension is now an atomic rewrite instead of an in-place append, eliminating partial trailing records after crashes.

## Crash semantics

HARDEN-16 deliberately chooses rollback-to-before-image for an ambiguous interrupted mark. A process dying after all data writes but before journal removal will therefore be rolled back on recovery. This favors a provably consistent deployment-control state over guessing that an unacknowledged mark completed.

## Status

Human `deploy status` includes `State transaction` health, recovery state, and journal name. JSON includes:

```json
"transaction": {
  "healthy": true,
  "recoveryPending": false,
  "journal": null
}
```

An outstanding mark journal makes overall deployment status `FAIL` until recovery occurs.

## Security gate

Tests cover interrupted mark recovery, absent-file restoration, concurrent verifier issuance without lost updates, competing-process lock timeout, owner-only state-file permissions, and status failure on a pending transaction journal.
