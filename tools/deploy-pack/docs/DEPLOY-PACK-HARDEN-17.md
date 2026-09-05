# DEPLOY-PACK-HARDEN-17 — Native Deployment-Ledger Hash Chain + Offline Custody Anchoring

Version: 1.7.0

## Goal

Make deployment history natively tamper-evident and connect that history to deploy-pack's offline custody trust domain.

## Native ledger chain

Every deployment-history record now carries:

- `previousRecordHash`
- `recordHash`

`recordHash` is SHA-256 over the canonical JSON record excluding `recordHash` itself. The first record commits to 64 zeroes; every later record commits to the prior record hash.

An entirely pre-1.7 legacy ledger is upgraded atomically on the next history mutation or offline custody export. A mixed partially-hashed ledger is rejected as invalid rather than silently repaired.

## Offline custody anchor

Every new offline custody checkpoint commits to:

- deployment ledger record count
- ledger head record hash
- ledger head deployed commit
- native chain mode

The same commitment is embedded inside every signed custody copy. This is critical: offline media can therefore detect a malicious rewrite of both the local deployment ledger and the local checkpoint database.

## Status semantics

`deploy-pack deploy status` reports a deployment-ledger offline anchor state:

- `UNANCHORED` — no custody checkpoint contains a deployment-ledger commitment
- `CURRENT` — the offline anchor covers the complete live ledger
- `ADVANCED` — the anchored historical prefix is intact, but newer deployment records exist
- `MISMATCH` — the live ledger no longer matches the anchored historical prefix; status FAIL
- `INVALID` — the anchor or native ledger is structurally invalid; status FAIL

`ADVANCED` is a warning, not corruption. It means a new custody checkpoint should eventually be exported to extend offline protection to newer deployments.

## Security property

Recomputing every local ledger hash after rewriting old history is no longer sufficient to hide the rewrite. The recomputed prefix hash will differ from the commitment held by offline custody copies.
