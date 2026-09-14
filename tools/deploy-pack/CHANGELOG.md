## 1.10.0 — DEPLOY-PACK-ARTIFACT-01

- Add Git-independent `deploy-pack artifact` directory packaging.
- Support deterministic ZIP and TAR.GZ archives rooted at source contents.
- Preserve dotfiles, nested paths, empty directories, and safe internal symlinks.
- Add repeatable canonical `--require` assertions.
- Reject output recursion, path ambiguity/traversal, and escaping symlinks.

# Changelog

## 1.9.1 — DEPLOY-PACK-FREEZE-01

- Integrated the cumulative frozen deploy-pack baseline into `dut-tools`.
- Includes DEPLOY-PACK-01 through 12, HARDEN-13 through 18, ASSURANCE-01, and FREEZE-01.
- Architecture is frozen; post-freeze changes are limited by the freeze policy in `docs/freezes/deploy-pack/DEPLOY-PACK-FREEZE-01.md`.

## 1.10.1 — DEPLOY-PACK-UX-01

- Expand deploy-pack CLI help.
- List individual tests in default dut-tools test output.

## 1.12.0 — DEPLOY-PACK-ARTIFACT-POLICY-01

- Add explicit `[artifact].include` / `[artifact].exclude` deployment-surface policy.
- Keep hard artifact hygiene non-overridable and require required paths to survive policy.
