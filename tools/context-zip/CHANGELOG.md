# Changelog

## 2.0.1 - 2026-09-05

- Canonicalized both source roots and candidate paths before relative-path comparisons.
- Fixed macOS `/var` versus `/private/var` alias handling that could silently drop tracked files and binary-exclusion evidence.
- Added alias-root regression coverage.

## 2.0.0 - 2026-08-22

- Consolidated the prior Spring v12 / PHP v5 parity baseline under one `context-zip` command.
- Kept stack-specific selection rules while sharing one CLI contract and archive engine.
- Added `--version`, `--config`, `--init-config`, `--print-config`, `--force`.
- Preserved `--whole-project`, `--include-untracked`, `--include-binaries`, `--max-file-mb`, and `--dry-run`.
- Preserved config include/exclude patterns, archive manifest, and `EXCLUDED-FILES.tsv`.
- Added auto-detection plus explicit `--stack spring|php`.
- Added `spring` and `php` compatibility launcher names.
- Added deterministic sorted archive membership and SHA-256 manifest data.
- Added tests covering config precedence, stack detection, exclusion rules, and archive output.

## 1.x - Historical standalone lineages

- Spring context zip evolved through the v12 parity baseline.
- PHP context zip evolved through the v5 parity baseline.
