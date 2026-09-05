# repo-verify

`repo-verify` is a generic, declarative repository verification engine.

The engine belongs to `dut-tools`; verification policy belongs to each consuming repository.

That boundary is intentional:

```text
repo-verify
    mechanism

.repo-verify.toml
    repository/company policy
```

## Basic usage

From a repository root:

```bash
repo-verify
```

Explicit root:

```bash
repo-verify /path/to/repository
```

Explicit config:

```bash
repo-verify --config .tooling/repo-verify.toml
```

## Default config

```text
.repo-verify.toml
```

Example:

```toml
version = 1

[settings]
fail_fast = false

[[check]]
name = "README exists"
type = "file_exists"
path = "README.md"

[[check]]
name = "No committed secrets file"
type = "path_absent"
path = ".env"

[[check]]
name = "Build script executable"
type = "executable"
path = "scripts/build"

[[check]]
name = "Project marker"
type = "contains"
path = "README.md"
text = "Architecture"

[[check]]
name = "Version syntax"
type = "matches"
path = "VERSION"
regex = '^[0-9]+\.[0-9]+\.[0-9]+$'

[[check]]
name = "Tests"
type = "command"
argv = ["./scripts/test"]
timeout_seconds = 120
cwd = "."
```

## Built-in check types

### `file_exists`

```toml
[[check]]
name = "License"
type = "file_exists"
path = "LICENSE"
```

Requires a regular file.

### `directory_exists`

```toml
[[check]]
name = "Docs directory"
type = "directory_exists"
path = "docs"
```

### `path_absent`

```toml
[[check]]
name = "No local env file"
type = "path_absent"
path = ".env"
```

### `executable`

```toml
[[check]]
name = "Bootstrap is executable"
type = "executable"
path = "scripts/bootstrap"
```

### `contains`

```toml
[[check]]
name = "README documents support"
type = "contains"
path = "README.md"
text = "Support"
```

### `matches`

```toml
[[check]]
name = "SemVer"
type = "matches"
path = "VERSION"
regex = '^[0-9]+\.[0-9]+\.[0-9]+$'
```

Python regular-expression syntax is used.

### `command`

```toml
[[check]]
name = "Tests"
type = "command"
argv = ["python3", "-m", "unittest", "discover"]
cwd = "."
timeout_seconds = 120
```

Commands use argv arrays. `repo-verify` does not silently invoke a shell.

## Check severity

Checks default to `error`.

Optional:

```toml
severity = "warning"
```

Warnings are reported but do not fail the verification run.

## Fail-fast

Default behavior runs every configured check.

Enable fail-fast:

```toml
[settings]
fail_fast = true
```

or:

```bash
repo-verify --fail-fast
```

## Output

Human-readable:

```bash
repo-verify --format text
```

Machine-readable:

```bash
repo-verify --format json
```

JSON includes each check's type, severity, outcome, evidence, duration, and command exit code when applicable.

## CI

```bash
repo-verify --format json > repo-verify.json
```

Stable exits:

```text
0 VERIFIED
1 VERIFICATION_FAILED
2 INVALID_CONFIG
3 EXECUTION_ERROR
```

## Security boundary

`repo-verify` validates repositories; it does not repair them.

It does not:

- edit files;
- run shell strings;
- rewrite Git history;
- install dependencies;
- mutate repository configuration;
- apply project-specific assumptions unless explicitly declared by the repository.

## Why this stays generic

Tembeek may require particular migration checks, release manifests, warning budgets, Trust evidence, or deployment contracts. Those rules belong in Tembeek repositories as `.repo-verify.toml` policy.

Another company can use the same engine with entirely different checks.
