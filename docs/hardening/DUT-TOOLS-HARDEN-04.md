# DUT-TOOLS-HARDEN-04 — Canonical Path Semantics

Status: implemented.

## Invariant

For containment and relative-path decisions, canonicalize **both** the trusted root and the candidate.
Lexical/user-facing paths may be preserved where no security or membership decision depends on them.

This closes macOS `/var` → `/private/var` alias failures in:

- `context-zip`
- `module-name-normalizer`
- `repo-patch`

`repo-patch` retains strict repository-escape rejection after canonicalization.
