# Release Process

Each tool releases independently.

Dry-run a release:

```bash
make release TOOL=git-context VERSION=1.5.0
```

Apply it:

```bash
make release TOOL=git-context VERSION=1.5.0 APPLY=1
```

Applied release behavior:

1. Require an initialized, clean Git repository.
2. Validate the target tool and SemVer.
3. Require the target version to appear in the tool changelog.
4. Update the tool `VERSION`.
5. Run `make check`.
6. Create a release commit.
7. Create an annotated namespaced tag.

Example:

```text
git-context/v1.5.0
```

The release script never pushes automatically.
