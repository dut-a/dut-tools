# Changelog

## 1.0.1 - 2026-09-05

- Preserved lexical discovery paths instead of resolving macOS `/var` paths to `/private/var`.
- Kept generated-directory pruning unchanged while preventing false path-identity test failures.
- Added alias-root discovery regression coverage.

## 1.0.0 - 2026-08-23

- Migrated the prior Maven module-name normalizer lineage into `dut-tools`.
- Dry-run is the default.
- Added explicit `--write` mutation mode.
- Added CI-friendly `--check` mode.
- Normalizes only top-level Maven project `<name>` elements.
- Never mutates `<artifactId>` coordinates in project, parent, dependency, plugin, or module declarations.
- Preserves XML namespaces and non-target elements.
- Supports repository-wide multi-module discovery.
- Supports style strategies `artifact`, `title`, and `prefix-title`.
- Supports optional prefix configuration.
- Added JSON/text output and stable exit codes.
