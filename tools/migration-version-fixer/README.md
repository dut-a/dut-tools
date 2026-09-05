# migration-version-fixer

Repository-wide migration version normalizer for Flyway and Laravel projects.

The tool solves a common generated-code problem: two branches, modules, or feature increments create migrations using the same migration version. It inventories migrations, reasons about their execution domain, and proposes deterministic renames without silently rewriting history.

## Version

```bash
migration-version-fixer --version
```

```text
migration_version_fixer 1.0.0
```

## Safety model

The default is **dry-run**:

```bash
migration-version-fixer .
```

Apply changes explicitly:

```bash
migration-version-fixer . --apply
```

CI check mode never mutates:

```bash
migration-version-fixer . --check
```

`--check` and `--apply` are mutually exclusive.

## Stable exit codes

```bash
migration-version-fixer --print-exit-codes
```

| Code | Name | Meaning |
|---:|---|---|
| 0 | CLEAN | No normalization required |
| 1 | INTERNAL_ERROR | Unexpected internal failure |
| 2 | CONFLICT | Conflicts exist and cannot be normalized safely |
| 3 | PROTECTED_CONFLICT | Required rename would alter protected/history migration |
| 4 | CHANGES_REQUIRED | Safe normalization exists; CI should fail until applied |
| 5 | CONFIG_ERROR | Invalid CLI/configuration/project metadata |
| 6 | GIT_ERROR | Required Git operation failed |

## Flyway strategies

```text
auto
sequential
timestamp
compound
```

### auto

Infer the existing migration style for each execution domain.

### sequential

Use contiguous integer versions:

```text
V1__init.sql
V2__customer.sql
V3__invoice.sql
```

### timestamp

Use deterministic UTC-like numeric timestamp versions:

```text
V20260822143000__customer.sql
V20260822143001__invoice.sql
```

### compound

Continue an existing compound sequence and preserve the delimiter style:

```text
V1_1__init.sql
V1_2__customer.sql
V1_3__invoice.sql
```

or:

```text
V1.1__init.sql
V1.2__customer.sql
V1.3__invoice.sql
```

Explicit `--flyway-strategy compound` continues the protected history's compound style instead of collapsing it to plain integers.

## Scope / execution domains

```text
auto
global
directory
```

### auto

For Spring Boot/Flyway repositories, `auto` attempts to infer which migration directories execute together by considering:

- `spring.flyway.locations`;
- Maven reactor modules and runtime dependencies;
- Gradle project dependencies;
- default module migration locations;
- physical migration directory relationships.

The goal is to prevent both false positives and false negatives. Two migration directories that feed the same application runtime should share a version domain even if they live in different modules.

### global

Every Flyway migration in the repository shares one version namespace.

### directory

Each physical migration directory is independent.

## Laravel

Laravel timestamp migrations are recognized:

```text
2026_08_19_120500_create_users_table.php
2026_08_19_120500_create_orders_table.php
```

Normalization preserves descriptions and deterministically increments timestamps:

```text
2026_08_19_120500_create_users_table.php
2026_08_19_120501_create_orders_table.php
```

## Git history protection

By default, a migration already present in the selected Git base is treated as protected.

Use:

```bash
--git-base origin/main
```

or:

```bash
--git-base HEAD~10
```

The fixer prefers renaming new/unprotected migrations. If normalization would require changing a protected migration, it exits with `PROTECTED_CONFLICT`.

Disable history checks only deliberately:

```bash
--no-git-protect
```

## CI / GitHub Actions

```bash
migration-version-fixer . --check --ci-output auto
```

Modes:

```text
auto
text
github
```

`auto` emits GitHub workflow annotations when `GITHUB_ACTIONS=true`, otherwise normal text.

Explicit:

```bash
migration-version-fixer . --check --ci-output github
```

Annotations use:

```text
::error
::warning
::notice
```

## Examples

Inspect the whole repository without mutation:

```bash
migration-version-fixer .
```

Normalize safely:

```bash
migration-version-fixer . --apply
```

CI check:

```bash
migration-version-fixer . --check
```

Force global Flyway uniqueness:

```bash
migration-version-fixer . --scope global
```

Treat physical migration directories independently:

```bash
migration-version-fixer . --scope directory
```

Continue compound Flyway versions:

```bash
migration-version-fixer . --flyway-strategy compound
```

Use a known Git base:

```bash
migration-version-fixer . --git-base origin/main --check
```

Laravel only:

```bash
migration-version-fixer . --kind laravel
```

Flyway only:

```bash
migration-version-fixer . --kind flyway
```

Print the planned renames as JSON:

```bash
migration-version-fixer . --format json
```

## Configuration

Optional project config:

```text
.migration-version-fixer.toml
```

Example:

```toml
version = 1

kind = "auto"
scope = "auto"
flyway_strategy = "auto"
git_protect = true
git_base = "origin/main"

[flyway]
locations = [
  "classpath:db/migration"
]

[exclude]
patterns = [
  "**/target/**",
  "**/build/**",
  "**/vendor/**"
]
```

CLI values override configuration values.
