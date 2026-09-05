# release-flow

Generic release-version mechanics with repository-owned policy.

```bash
release-flow prepare 1.4.0
release-flow verify 1.4.0
release-flow next 1.4.0 --bump minor --suffix SNAPSHOT
```

Dry-run is default for prepare/next; use `--write` to mutate. Default config is `.release-flow.toml`.

```toml
version = 1

[settings]
require_clean_git = true

[[target]]
name = "root version"
type = "plain"
path = "VERSION"

[[target]]
name = "metadata"
type = "regex"
path = "pyproject.toml"
pattern = '(?m)^version = "[^"]+"$'
replacement = 'version = "{version}"'

[[verify]]
name = "tests"
argv = ["./scripts/test"]
```

Verification checks targets plus configured argv commands. No implicit shell. `next` can derive major/minor/patch SemVer plus a suffix.

Exit codes: 0 success, 1 verification failed, 2 invalid config/argument, 3 dirty repository, 4 write failed, 5 execution error.

`release-flow` does not create or push Git tags, publish packages, build release archives, sign artifacts, or define company-specific release policy.


## Transactional writes

```text
ALL VERSION TARGETS UPDATED
or
ALL VERSION TARGETS RESTORED
```
