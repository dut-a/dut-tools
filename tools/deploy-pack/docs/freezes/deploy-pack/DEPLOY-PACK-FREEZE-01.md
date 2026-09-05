# DEPLOY-PACK-FREEZE-01

Status: **FROZEN**  
Release: **1.9.1**  
Freeze date: **2026-09-05**  
Intended Git tag: **deploy-pack-v1.9.1-freeze**

## Scope

This freeze closes the DEPLOY-PACK-01→12 architecture, HARDEN-13→18 security hardening, and ASSURANCE-01 evidence taxonomy. No additional architecture is authorized by this freeze.

Post-freeze changes are limited to defect fixes, security maintenance, compatibility maintenance, dependency maintenance, and operational requirements demonstrated by real deployments. New architecture requires an explicit unfreeze/reconstitution decision rather than another numbered feature increment.

## Freeze cleanup

Before freezing, the release closes the remaining low-severity audit debt that is safe to resolve without changing the architecture:

- removed duplicate/shadowed remote-verifier generator implementations;
- browser verifier tokens no longer use query strings by default: GET renders a token form; authentication is POST or `Authorization: Bearer`;
- runtime/build dependencies have bounded major-version compatibility ranges;
- an exact tested dependency baseline is recorded in `constraints-freeze.txt`;
- a deterministic freeze gate executes every test module in an isolated subprocess so state leakage or a single slow monolithic discovery process cannot hide coverage.

## Security posture frozen

The release retains these established boundaries:

- protected internal/secret artifact containment;
- pre-enrolled offline-custody quorum authenticity;
- signed verifier key precommitment;
- repository-wide mutation locking, atomic state writes, and recoverable `mark` transactions;
- native hash-chained deployment history with offline custody anchoring;
- strict recovery-trust, manifest-path, ZIP-member, and symlink containment rules;
- explicit evidence assurance taxonomy distinguishing `host-cooperative-remote` from independent or platform attestation.

`host-cooperative-remote` remains intentionally **not hostile-host attestation**.

## Freeze gate

The canonical gate is:

```bash
make freeze-check
```

or:

```bash
python3 scripts/check-freeze.py
```

The gate must execute every `tests/test_*.py` module and must also pass Python compilation and CLI version checks. Generated PHP/Python verifier syntax is separately smoke-tested as part of release preparation.

## Dependency policy

Package metadata permits only the tested major families:

- `cryptography>=46,<47`
- build backend `setuptools>=82,<83`

The exact freeze validation environment is recorded in `constraints-freeze.txt`. Updating to another major dependency family is a post-freeze maintenance event requiring full freeze-gate execution.

## Compatibility policy

The command surface, evidence schemas, trust-state semantics, and assurance names present in 1.9.1 are the compatibility baseline. Breaking changes require a deliberate major-version decision and migration notes.

## Disposition

**DEPLOY-PACK-FREEZE-01: FROZEN.**

The next work on deploy-pack should be usage, maintenance, and bug fixes—not speculative architecture. DEPLOY-PACK-47 remains forbidden. 😜
