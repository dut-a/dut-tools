# DEPLOY-PACK-HARDEN-15 — Verifier Key Precommitment

Status: IMPLEMENTED  
Release: `deploy-pack 1.5.0`

## Objective

Remove trust-on-first-use from signed remote evidence. The Ed25519 public-key
fingerprint used by a signed remote verifier is committed to the verifier
issuance record when the signed verifier is generated, before any remote
evidence can be accepted.

## Trust invariant

For signed remote evidence to be accepted, all three values must agree:

1. `.deploy-pack-verifiers.json` → `expectedPublicKeySha256`
2. signed evidence → `payload.verifierIdentity.expectedPublicKeySha256`
3. supplied verifier public-key file → SHA-256 fingerprint

The keyring registration step is downstream of this check and therefore no
longer acts as first-use trust establishment.

## Lifecycle

`deploy-pack verifier issue` creates an identity with an unbound signing-key
slot:

```json
{
  "expectedPublicKeySha256": null,
  "signingKeyCommittedAt": null
}
```

Generating a signed verifier:

```sh
deploy-pack remote-verifier app.deploy.zip \
  --sign \
  --verifier-id <ID> \
  --language php
```

generates the ephemeral Ed25519 keypair and immediately commits its public-key
fingerprint into that verifier identity. The updated identity is embedded into
the generated verifier and therefore into the signed evidence it emits.

A verifier identity may not be rebound to another signing key. Generate a new
verifier identity to rotate to another ephemeral signer.

## Ingestion

`ingest-signed-remote-evidence` fails before normalization/keyring registration
when:

- the verifier identity has no precommitted signing key;
- the supplied public key differs from the precommitted fingerprint; or
- the evidence's embedded verifier identity does not carry the same committed
  fingerprint.

A cryptographically valid signature from a replacement key is therefore not
sufficient.

## Upgrade behavior

Verifier identities issued by older releases can be upgraded naturally by
generating a new signed verifier under 1.5.0; generation fills the previously
missing key commitment.

Signed verifier artifacts generated before HARDEN-15 must be regenerated.
Their old evidence is intentionally rejected because there was no prior local
key commitment with which to authenticate it.

## Security property

Before HARDEN-15:

`valid signature + supplied public key -> register key on ingestion`

After HARDEN-15:

`issued verifier -> precommit fingerprint -> generate verifier -> remote sign ->
compare against precommit -> verify signature -> register signer history`

This closes audit finding `DP-AUD-004`.
