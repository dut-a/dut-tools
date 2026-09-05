# Versioning

`dut-tools` uses independent per-tool Semantic Versioning.

## Rules

- The monorepo has no synchronized release version.
- Every registered tool has `tools/<name>/VERSION`.
- Versions use `MAJOR.MINOR.PATCH`.
- Pre-release identifiers may be used when needed.
- Git tags are namespaced by tool.

Examples:

```text
git-context/v1.4.0
context-zip/v2.1.3
migration-version-fixer/v1.0.1
```

## Change interpretation

- **MAJOR**: incompatible CLI, configuration, output contract, or other public behavior.
- **MINOR**: backward-compatible capability.
- **PATCH**: backward-compatible correction or hardening.

A monorepo commit may change multiple tools without forcing them to share a version.
