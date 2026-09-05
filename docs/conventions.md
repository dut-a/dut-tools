# Conventions

## Names

- tool directories: `kebab-case`
- CLI commands: `kebab-case`
- Python modules: `snake_case`
- Java/PHP classes: `PascalCase`
- environment variables: `UPPER_SNAKE_CASE`

## Mature tool contract

Every mature tool should provide:

- `README.md`
- `CHANGELOG.md`
- `VERSION`
- implementation source
- tests
- examples where useful
- configuration examples where useful

Every active CLI must support:

```text
--help
--version
```

Prefer stable base exit codes:

- `0` success
- `1` operational failure
- `2` invalid CLI/configuration

Tools with CI contracts may define additional documented codes.

## Shared code

Extract shared primitives cautiously. Duplication is preferable to premature coupling.

Good candidates:

- config discovery
- XDG path handling
- SemVer parsing
- Git root discovery
- terminal capability/color handling

Avoid generic dumping grounds named `Utils`, `Helpers`, or `Common`.
