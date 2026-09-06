# DEPLOY-PACK-UX-01 — Test Presentation and CLI Help

UX-only increment. `make test` lists each successful unittest case by exact method name, while `QUIET=1` remains suite-only and `VERBOSE=1` remains raw framework output. Failures still expand automatically.

deploy-pack top-level help now explains Git-aware and artifact-directory packaging, groups commands by purpose, and provides representative examples. `deploy-pack artifact --help` documents source authority, archive-root flattening, required-path assertions, supported formats, and safety invariants.

No archive selection, verification, evidence, trust, recovery, or canonical-path semantics change.
