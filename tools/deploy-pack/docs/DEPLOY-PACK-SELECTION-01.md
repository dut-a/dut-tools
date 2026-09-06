# DEPLOY-PACK-SELECTION-01 — Explicit Git Deployment Surface

Version: 1.11.0

## Purpose

Change Git-aware `pack`/`inspect` from broad changed-file selection to an explicit, fail-closed deployment surface. `artifact` mode is unchanged.

## Contract

- `deploy-pack init` creates schema-2 `.deploy-pack.toml` with `[pack] policy = "allowlist"`.
- Operator-facing `pack` and `inspect` require a project policy.
- Git determines changed paths; `[pack].include` determines the maximum deployable surface.
- `[pack].exclude`, `--ignore`, and allowlist-mode `--include` can only narrow selection.
- Conventional test paths/snapshots, `.env`/`.env.*`, VCS internals, and deploy-pack control/state artifacts are hard denied.
- Scripts, build/package metadata, and other hidden paths are not inferred as deployable; they require explicit project inclusion.
- `.htaccess` is not globally forced into packages, but `deploy-pack init` pre-populates it when present.
- `[pack].require` asserts exact required paths exist before planning.
- `inspect` prints exclusion reasons.
- Legacy `[deploy-pack] ignore/include` remains readable for existing repositories.
- Artifact-directory packaging remains source-authoritative and does not use this Git selection policy.

## Safety rationale

A denylist cannot correctly infer whether arbitrary repository scripts/configuration are production runtime material. The repository therefore declares the deployment surface explicitly. Hard-denied classes are limited to material that should never be emitted by Git-aware packaging.
