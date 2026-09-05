# Changelog

## 1.3.1 - 2026-09-05

- Canonicalized both trusted repository roots and candidate paths before containment checks.
- Fixed macOS `/var` versus `/private/var` aliases being misclassified as repository escapes.
- Preserved the mandatory path-escape security invariant; no containment checks were weakened.
- Added alias-root rollback/snapshot regression coverage.

## 1.3.0 - 2026-08-23

- Added production `generic` adapter for repositories without Maven, Gradle, or Composer.
- Generic is now the auto-detection fallback for any Git repository.
- Added `.repo-patch.toml` configuration.
- Added project-defined generic validation commands using argv arrays (no implicit shell).
- Added built-in touched-file validation for JSON, TOML, Python, and shell files.
- Added unresolved merge-conflict-marker detection for text files.
- Added generic file readability / binary sanity validation.
- Generic project commands run inside the same mandatory rollback boundary.
- Added `--config` override for repo-patch configuration.
- Added generic-adapter doctor output.
- Preserved mandatory rollback across every adapter.

## 1.2.0 - 2026-08-23

- Added production `composer-php` adapter.
- Added Composer package discovery, including nested package roots.
- Added nearest-package mapping for changed files.
- Prefer repository-local `composer.phar` before global Composer.
- Added PHP syntax lint for touched `.php` files.
- Added `composer validate --no-check-publish` for affected packages.
- Added PHPUnit validation when an affected package exposes local PHPUnit.
- Added Laravel `artisan test --no-interaction` fallback when PHPUnit is not local but Artisan exists.
- Avoided assuming every Composer project is Laravel.
- Preserved mandatory rollback across Maven/Spring, Gradle, and Composer/PHP.

## 1.1.0 - 2026-08-23

- Added production `gradle` adapter.
- Added Gradle root/subproject discovery.
- Added nearest-project mapping for changed files.
- Prefer Gradle Wrapper over system Gradle.
- Added affected-project `classes` and `testClasses` validation.
- Added Spotless validation when configured.
- Added optional Spring context-test smoke for touched Spring Boot Gradle projects.
- Preserved mandatory rollback across Maven/Spring and Gradle.

## 1.0.0 - 2026-08-23

- Added `repo-patch` to `dut-tools`.
- Added `plan`, `check`, `apply`, and `doctor`.
- Locked mandatory rollback on every validation failure.
- Added before-state snapshots and rollback journal manifests.
- Added Maven/Spring as the first production adapter.
- Added affected-module discovery and Maven `-pl ... -am` validation.
- Added optional Spotless validation when configured.
- Added reactor `test-compile` validation.
- Added optional Spring context-test smoke probe.
- Added dirty-worktree refusal by default.
- Added unified-diff and direct-replacement inputs.
- Added stable exit codes.
