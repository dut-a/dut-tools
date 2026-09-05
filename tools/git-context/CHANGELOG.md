# Changelog

## 1.5.0 - 2026-08-22

- Migrated the v1.5.0 `git-context` contract into `dut-tools`.
- Preserved directory-driven context selection with narrowest-path precedence.
- Preserved `user.useConfigOnly=true` and no implicit fallback identity.
- Added source-of-truth TOML config under `~/.config/git-context/contexts.toml`.
- Added generated per-profile Git config and one managed global include.
- Added global identity backup/removal on apply.
- Added machine bootstrap manifest support.
- Added GitHub/GitLab CLI account isolation directories.
- Added `enter`, `audit`, and plan-first `relocate`.
- Preserved explicit opt-ins for tool installation and key provisioning.
- Added structural and behavior tests for selection, generation, isolation, and safe relocation.

## 1.0.0 - Historical baseline

- Initial standalone utility lineage before monorepo migration.
