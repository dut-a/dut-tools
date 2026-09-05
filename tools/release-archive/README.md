# release-archive

`release-archive` builds reproducible distributable archives from a project tree. It is deliberately distinct from `context-zip`: `context-zip` selects review/diagnostic context, while `release-archive` packages the files intended for delivery or release.

## Formats

Both are first-class in v1:

```bash
release-archive --format zip
release-archive --format tar.gz
```

Both formats are produced from the same selected-file inventory and contain the same embedded `RELEASE-MANIFEST.json`.

## Safety defaults

By default the tool:

- uses Git tracked files when run inside a Git repository;
- excludes obvious VCS/build/cache/secret files;
- refuses a dirty Git worktree unless `--allow-dirty` is supplied;
- excludes untracked files unless `--include-untracked` is supplied;
- refuses to overwrite output unless `--force` is supplied;
- writes files in deterministic lexical order;
- normalizes archive timestamps and metadata for reproducibility.

## Examples

```bash
release-archive . --format zip
release-archive . --format tar.gz
release-archive . --format both
release-archive . --dry-run
release-archive . --check
release-archive . --include-untracked --allow-dirty
release-archive . --config .release-archive.toml
release-archive . --output dist/my-product
release-archive . --source-date-epoch 0
```

`--format both` creates sibling `.zip` and `.tar.gz` files from one plan.

## Configuration

Default project file:

```text
.release-archive.toml
```

Example:

```toml
version = 1
format = "both"
require_clean = true
include_untracked = false
source_date_epoch = 0

include = [
  "README.md",
  "src/**",
  "docs/**"
]

exclude = [
  "docs/internal/**",
  "**/*.tmp"
]
```

CLI values override configuration.

## Manifest

Each archive contains `RELEASE-MANIFEST.json` with:

- tool/version;
- source root;
- Git commit when available;
- dirty-state acknowledgement;
- source date epoch;
- selected file list;
- byte size;
- SHA-256 for every selected file.

A `<archive>.sha256` sidecar is written beside each archive.

## CI check

```bash
release-archive . --check
```

This validates configuration, Git cleanliness, selection, output naming and reproducibility inputs without writing archives.
