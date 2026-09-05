# Changelog

## 1.0.0 - 2026-08-22

- Migrated the established standalone `migration_version_fixer 1.0.0` contract into `dut-tools`.
- Dry-run remains the default; `--apply` is the only mutating mode.
- Added/retained non-mutating `--check` CI mode.
- Preserved stable exit codes:
  - `0 CLEAN`
  - `1 INTERNAL_ERROR`
  - `2 CONFLICT`
  - `3 PROTECTED_CONFLICT`
  - `4 CHANGES_REQUIRED`
  - `5 CONFIG_ERROR`
  - `6 GIT_ERROR`
- Preserved Flyway strategies `auto`, `sequential`, `timestamp`, and `compound`.
- Restored compound underscore and dot sequences such as `V1_1`, `V1_2` -> `V1_3` and `V1.1`, `V1.2` -> `V1.3`.
- Preserved Spring/Flyway configuration-aware grouping with `--scope auto|global|directory`.
- `auto` analyzes Spring Flyway locations and Maven/Gradle module relationships to infer execution domains.
- Preserved Laravel timestamp migration normalization.
- Preserved Git-history protection and base comparison support.
- Preserved GitHub Actions annotations with `--ci-output auto|text|github`.
- Preserved `--print-exit-codes`, comprehensive help/examples, and `--version`.

## Pre-monorepo lineage

The tool existed as a standalone script before this migration. This version is the canonical monorepo baseline.
