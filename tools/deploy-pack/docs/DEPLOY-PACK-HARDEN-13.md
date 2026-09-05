# DEPLOY-PACK-HARDEN-13 — Secret and Protected-Artifact Containment

## Status

PASS

## Objective

Prevent deploy-pack control-plane artifacts, verifier secrets, browser tokens,
verification evidence, recovery/custody artifacts, and internal state from
being included in an ordinary deployment archive or remote-deletion plan.

## Security invariants

1. Protected artifacts are evaluated before project and CLI include rules.
2. `.deploy-pack-*` state cannot be re-included through ordinary policy.
3. Standard generated signed/browser verifier filenames are protected.
4. Secret-bearing signed verifier content is detected even with a custom
   output filename.
5. Token-bearing browser verifier content is detected even with a custom
   output filename.
6. Recognizable deploy-pack evidence, public-key, fingerprint, signed control,
   and custody checkpoint artifacts are protected by content where possible.
7. Protected artifacts are not emitted as remote deletions.
8. Signed PHP verifier files are mode `0600`.
9. Signed Python verifier files are mode `0700`.
10. Browser PHP verifier files are mode `0600`.

## Non-goal

This increment does not implement custody signer pre-enrollment, verifier-key
precommitment, transactional state mutation, ledger hash chaining, or path
containment. Those remain HARDEN-14 through HARDEN-18 concerns from the
architecture/security audit.

## Gate

`tests/test_harden13_protected_artifacts.py` plus packaging, signed-evidence,
remote-evidence, verifier-lifecycle, and signed-mark regression suites must pass.
