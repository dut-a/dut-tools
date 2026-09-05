# DEPLOY-PACK-ASSURANCE-01 — Evidence Assurance Taxonomy

Status: IMPLEMENTED in deploy-pack 1.9.0

## Purpose

Make the authority and limits of deployment verification evidence explicit and machine-readable.
A cryptographic signature over evidence does not, by itself, make the deployment host an
independent or trustworthy observer of its own state.

## Taxonomy

| Level | Status | Evidence authority |
| --- | --- | --- |
| `local` | supported | operator-controlled local verification |
| `host-cooperative-remote` | supported | target deployment host verifies and reports its own state |
| `independent-observer` | reserved | observer outside the target-host trust domain |
| `platform-attested` | reserved | platform/hardware-backed attestation root |

The ranks/order describe increasing independence of the evidence authority. They are not a
universal cryptographic strength score.

## Current signed remote evidence

The signed Python/PHP remote verifier remains `host-cooperative-remote`.

Its precommitted Ed25519 verifier key supports the claim that the evidence record was signed
by the expected temporary verifier key and has not been altered after signing. It does **not**
support any of these claims:

- the target host was uncompromised;
- the target host reported honestly;
- verification was performed by an independent observer;
- the result is hardware/platform attestation.

A host that can read/execute the temporary verifier can potentially control both the inspected
state and the verifier process. That is why the classification does not rise when signing is enabled.

## Machine-readable evidence

Evidence records contain an `assurance` object including:

- `schemaVersion`
- `level`
- `authority`
- `claims`
- `doesNotClaim`
- `verificationMethod`
- `evidenceIntegrity`

Signed remote evidence uses:

```text
level: host-cooperative-remote
evidenceIntegrity: ed25519-precommitted-verifier-key
```

Unsigned remote evidence uses:

```text
level: host-cooperative-remote
evidenceIntegrity: unsigned-record
```

Local archive/extracted-tree verification uses `level: local`.

## Anti-overclaim invariant

Current deploy-pack evidence producers cannot self-declare `independent-observer` or
`platform-attested`. Loading/ingesting current remote evidence with either classification fails.
Those levels are reserved until deploy-pack has a producer and trust-validation path capable of
proving the corresponding authority.

## CLI

```bash
deploy-pack assurance show
deploy-pack assurance show --json
deploy-pack assurance explain host-cooperative-remote
deploy-pack assurance explain platform-attested --json
```

Deployment history and `deploy status` retain/surface the assurance level of marked evidence.

## Security boundary

ASSURANCE-01 addresses audit finding DP-AUD-003 as a semantics/trust-model correction. It does
not convert current remote verification into hostile-host attestation. Stronger assurance requires
a genuinely independent trust domain or a verifiable platform/hardware attestation mechanism.
