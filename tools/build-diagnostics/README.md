# build-diagnostics

`build-diagnostics` runs a build command, captures its output, classifies diagnostics,
and emits a structured report.

The engine is generic. Repositories own their warning/error policy.

```text
build-diagnostics
    capture + classify + report

.build-diagnostics.toml
    repository-specific classifiers/budgets
```

## Run a build

```bash
build-diagnostics -- mvn clean verify
```

Gradle:

```bash
build-diagnostics -- ./gradlew build
```

PHP:

```bash
build-diagnostics -- composer test
```

Everything after `--` is executed as argv. No implicit shell is introduced.

## Output

Default text report:

```bash
build-diagnostics -- mvn test
```

JSON:

```bash
build-diagnostics --format json -- mvn test
```

Persist a report:

```bash
build-diagnostics \
  --report build/diagnostics.json \
  --format json \
  -- mvn verify
```

## Default classification

The built-in classifier recognizes broad forms such as:

```text
ERROR / [ERROR]
WARNING / WARN / [WARNING]
NOTE / INFO
```

Repository-specific classifiers can be declared:

```toml
version = 1

[[classifier]]
name = "deprecation"
severity = "warning"
regex = '(?i)deprecated'

[[classifier]]
name = "spotbugs"
severity = "warning"
regex = 'SpotBugs.*(?:warning|bug)'

[[classifier]]
name = "compiler-error"
severity = "error"
regex = '(?i)compilation (?:error|failure)'
```

First matching classifier wins. Custom classifiers run before built-ins.

## Budgets

Budgets are repository policy, not hard-coded tool policy.

```toml
[budget]
max_warnings = 25
max_errors = 0

[budget.category]
deprecation = 5
spotbugs = 0
```

`--check` evaluates the configured budget:

```bash
build-diagnostics --check -- mvn verify
```

A successful build can therefore still fail diagnostics policy when a warning budget is exceeded.

## Forbidden patterns

Repositories can define zero-tolerance diagnostics:

```toml
[[forbidden]]
name = "illegal-reflection"
regex = '(?i)illegal reflective access'
```

A match is reported as a policy violation in `--check`.

## Baseline comparison

Compare warning/category counts with a previous JSON report:

```bash
build-diagnostics \
  --baseline build/previous-diagnostics.json \
  --format json \
  -- mvn verify
```

The report includes deltas. Baseline comparison is informational unless the repository
expresses a budget that makes the new count unacceptable.

## Configuration

Default:

```text
.build-diagnostics.toml
```

Override:

```bash
--config .tooling/build-diagnostics.toml
```

## Exit codes

Normal mode returns the wrapped build command's success/failure semantics:

```text
0 BUILD_SUCCEEDED
1 BUILD_FAILED
```

`--check` additionally enforces diagnostics policy:

```text
0 CLEAN
1 BUILD_OR_POLICY_FAILED
2 INVALID_CONFIG_OR_ARGUMENTS
3 EXECUTION_ERROR
```

## Non-goals

`build-diagnostics` does not:

- decide acceptable warning budgets for Tembeek or any other company;
- rewrite build output;
- fix warnings;
- install Maven/Gradle/Composer dependencies;
- invoke shell strings implicitly;
- replace the underlying build system.
