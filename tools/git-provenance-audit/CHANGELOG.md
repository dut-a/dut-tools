# Changelog

## 1.0.0 - 2026-08-23

- Added `git-provenance-audit` as an active `dut-tools` utility.
- Added read-only repository discovery beneath a configurable root/depth.
- Added direct interoperability with `git-context` `contexts.toml`.
- Added most-specific-path context resolution.
- Added repository-local `git-context.profile` pin resolution.
- Added author and committer identity verification for Git history.
- Added commit-count, branch, and author filters.
- Added global identity allowlists and repository-specific allowances.
- Added strict mode for unclassified repositories and unmatched identities.
- Added text, JSON, and CSV output.
- Added stable CI-oriented exit codes.
- Added deterministic finding ordering.
- Explicitly excludes history rewriting or automatic identity correction.
