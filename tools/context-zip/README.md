# context-zip

`context-zip` creates a compact, reviewable ZIP of source/project context for diagnostics, support, AI-assisted review, handoff, or archival.

It consolidates the established Spring and PHP context-zip utilities behind one command while preserving stack-specific project behavior.

## Commands

Auto-detect stack:

```bash
context-zip
```

Explicit:

```bash
context-zip --stack spring
context-zip --stack php
```

Compatibility forms:

```bash
context-zip spring
context-zip php
```

## Shared CLI contract

```text
--version
--config PATH
--init-config
--print-config
--force
--whole-project
--include-untracked
--include-binaries
--max-file-mb N
--dry-run
--include PATTERN
--exclude PATTERN
--output PATH
--stack spring|php|auto
```

## Configuration

Default project configuration:

```text
.context-zip.json
```

This preserves the prior PHP configuration convention and is now the shared default.

Example:

```json
{
  "version": 1,
  "stack": "auto",
  "whole_project": false,
  "include_untracked": false,
  "include_binaries": false,
  "max_file_mb": 4,
  "include": [],
  "exclude": [
    ".git/**",
    "node_modules/**",
    "vendor/**",
    "target/**",
    "build/**"
  ]
}
```

CLI values override config values.

## Selection modes

Default mode selects high-value project context appropriate to the detected stack.

`--whole-project` broadens selection to the repository/project tree while retaining safety exclusions.

Git-aware projects default to tracked files. Use:

```bash
--include-untracked
```

to include non-ignored untracked files as well.

## Binary files

Binary files are excluded by default. Use:

```bash
--include-binaries
```

only when they are useful.

## Size limit

```bash
--max-file-mb 4
```

Files exceeding the configured limit are excluded and reported.

## Archive metadata

Every generated archive contains:

```text
CONTEXT-ZIP-MANIFEST.json
EXCLUDED-FILES.tsv
```

The manifest contains tool version, detected/selected stack, source root, selected files, sizes, and SHA-256 digests.

The exclusion report records why candidate files were omitted.

## Spring defaults

The Spring selector recognizes typical Maven/Gradle projects and prioritizes:

- `pom.xml`
- Gradle build/settings files
- `src/main/**`
- `src/test/**`
- configuration/resources
- migration directories
- API specs
- Docker/CI/project docs where present

Generated build output remains excluded.

## PHP defaults

The PHP selector recognizes typical Composer/Laravel/general PHP projects and prioritizes:

- `composer.json` / `composer.lock`
- `artisan`
- `app/**`
- `bootstrap/**`
- `config/**`
- `database/**`
- `routes/**`
- `resources/**`
- `tests/**`
- public source assets
- project docs/configuration

`vendor/**` remains excluded.

## Examples

```bash
context-zip --dry-run

context-zip --stack spring --output /tmp/order-service-context.zip

context-zip --stack php --whole-project --include-untracked

context-zip --config .context-zip.json --print-config

context-zip --init-config

context-zip --exclude '**/*.log' --include 'docs/**'

context-zip --include-binaries --max-file-mb 10
```
