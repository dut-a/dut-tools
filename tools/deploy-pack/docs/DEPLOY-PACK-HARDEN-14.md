# DEPLOY-PACK-HARDEN-14 — Custody Authenticity Root

Version: **1.4.0**

## Security objective

DEPLOY-PACK-12 established threshold quorum over independently signed offline custody copies, but a signature only proves that a copy was signed by the public key supplied with that copy. Before HARDEN-14, a replacement set of anchor/public-key pairs could form a fresh internally consistent quorum unless operators separately pinned fingerprints.

HARDEN-14 makes custody signer enrollment a mandatory trust boundary.

## Authoritative commitment

`export-copies` already records every custody `copyId` and its `publicKeySha256` inside the checkpoint record. The checkpoint record is itself covered by `checkpointHash` and the append-only checkpoint hash chain.

Quorum verification now accepts a copy only when:

1. its anchor signature is cryptographically valid;
2. it agrees on checkpoint ID, sequence, trust SHA, copy count and quorum;
3. the checkpoint exists in a trusted authenticity root;
4. its `copyId` is pre-enrolled in that checkpoint; and
5. its signing-key fingerprint exactly matches the fingerprint committed for that copy.

Keys supplied beside the copies are therefore verification material, not authority.

## Authenticity-root modes

### Repository-local

Inside the repository, `verify-quorum` and `verify-copy-set` resolve the checkpoint from `.deploy-pack-offline-checkpoints.jsonl`, validate the complete checkpoint chain, and enforce the enrolled copy/fingerprint commitment.

### Portable/offline

Outside the original repository, use the exported checkpoint manifest together with a separately retained expected checkpoint hash:

```bash
deploy-pack recovery trust verify-quorum \
  custody.copy-01.json custody.copy-03.json \
  --public-key custody.copy-01.json.public-key.json \
  --public-key custody.copy-03.json.public-key.json \
  --trusted-checkpoint custody.checkpoint.json \
  --expected-checkpoint-hash <PINNED_CHECKPOINT_SHA256>
```

An external checkpoint file without `--expected-checkpoint-hash` is rejected because a manifest cannot authenticate itself.

## Security invariant

A custody copy counts toward quorum **only if its signer identity was committed before verification**.

A newly generated replacement keypair can produce a valid Ed25519 signature, but it cannot become a trusted custody signer merely by arriving with the signed artifact.

## Audit closure

Closes **DP-AUD-002 — Quorum verification lacks mandatory authenticity pinning** from the DEPLOY-PACK-01→12 architecture/security audit.
