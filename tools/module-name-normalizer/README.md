# module-name-normalizer

`module-name-normalizer` standardizes Maven project/module display names conservatively.

Its purpose is intentionally narrow:

> Normalize Maven `<project><name>` values without changing Maven coordinates or dependency semantics.

## Safety

Dry-run is the default:

```bash
module-name-normalizer .
```

Write changes explicitly:

```bash
module-name-normalizer . --write
```

CI check mode:

```bash
module-name-normalizer . --check
```

`--write` and `--check` are mutually exclusive.

## What it changes

Only the direct `<name>` child of the Maven `<project>` element.

Example:

```xml
<project>
  <artifactId>customer-api</artifactId>
  <name>customer-api</name>
</project>
```

with `--style title` becomes:

```xml
<project>
  <artifactId>customer-api</artifactId>
  <name>Customer Api</name>
</project>
```

## What it never changes

- project `<artifactId>`
- parent coordinates
- dependency coordinates
- plugin coordinates
- `<modules><module>` paths
- group IDs
- versions

This is a display-name normalizer, not a Maven refactoring engine.

## Styles

### artifact

Set `<name>` exactly to the project artifact ID.

```bash
module-name-normalizer . --style artifact
```

### title

Convert common separators to title-like display names.

```text
customer-api -> Customer Api
financial_operations -> Financial Operations
```

```bash
module-name-normalizer . --style title
```

### prefix-title

Prefix the title form:

```bash
module-name-normalizer . \
  --style prefix-title \
  --prefix "Tembeek"
```

Result:

```text
Tembeek Customer Api
```

## Missing `<name>`

By default, a missing top-level `<name>` is added immediately after `<artifactId>`.

Disable insertion:

```bash
--no-add-missing
```

## Scope

All `pom.xml` files beneath the root are discovered, excluding typical generated/dependency directories such as:

```text
.git
target
build
node_modules
vendor
```

## Output

```bash
--format text
--format json
```

## Exit codes

```text
0 CLEAN
1 CHANGES_REQUIRED
2 INVALID_INPUT
3 WRITE_FAILED
```

In normal dry-run mode, proposed changes are printed but exit code remains `0`.

In `--check` mode, proposed changes return `1`.

## Examples

```bash
module-name-normalizer .

module-name-normalizer . --style title

module-name-normalizer . \
  --style prefix-title \
  --prefix "Tembeek"

module-name-normalizer . --check

module-name-normalizer . --write
```
