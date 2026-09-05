# repo-patch

`repo-patch` is a plan-first repository mutation utility.

Its core invariant is:

> Any failed post-change validation causes the complete mutation set from that run to be rolled back automatically.

Rollback is **mandatory**, not optional.

## v1 adapter

Production adapters:

```text
maven-spring
gradle
composer-php
generic
```

## Commands

```bash
repo-patch plan --patch change.diff
repo-patch check --patch change.diff
repo-patch apply --patch change.diff
repo-patch doctor
repo-patch --version
```

## Safety

Mutation is refused on a dirty Git worktree unless explicitly overridden:

```bash
repo-patch apply --patch change.diff --allow-dirty
```

Every apply run:

1. resolves the Git repository root;
2. parses and bounds-checks all paths;
3. snapshots every touched file;
4. applies the requested mutations;
5. runs adapter validations;
6. keeps the changes only if all validations pass;
7. otherwise restores every touched path automatically;
8. writes a run manifest under `.repo-patch/runs/<run-id>/manifest.json`.

## Maven/Spring validation

The default v1 pipeline is:

```text
affected module discovery
  -> mvn -pl <affected> -am test-compile
  -> spotless:check when Spotless is configured
  -> optional Spring context-test smoke probe
```

Disable only the startup/context probe when it is inappropriate:

```bash
repo-patch apply --patch change.diff --no-startup-probe
```

Rollback itself cannot be disabled.

## Direct replacements

```bash
repo-patch apply \
  --replace pom.xml=/tmp/new-pom.xml \
  --replace src/main/java/example/App.java=/tmp/App.java
```

## Exit codes

```text
0 SUCCESS
1 VALIDATION_FAILED_ROLLED_BACK
2 INVALID_INPUT
3 DIRTY_REPOSITORY
4 APPLY_FAILED_ROLLED_BACK
5 ROLLBACK_FAILED
6 UNSUPPORTED_REPOSITORY
```


## Gradle adapter

```bash
repo-patch plan --adapter gradle --patch change.diff
repo-patch apply --adapter gradle --patch change.diff
```

The Gradle adapter discovers included projects from `settings.gradle` /
`settings.gradle.kts`, maps changed files to the nearest project, prefers
`./gradlew`, validates `classes` and `testClasses`, runs `spotlessCheck` when
configured, and can run context-oriented tests for touched Spring Boot projects.

Mandatory rollback is identical across Maven/Spring and Gradle.


## Composer/PHP adapter

```bash
repo-patch plan --adapter composer-php --patch change.diff
repo-patch apply --adapter composer-php --patch change.diff
```

Auto-detection selects `composer-php` for a Git repository with a root
`composer.json` when it is not a root Maven or Gradle build.

The adapter is Composer-centric rather than Laravel-specific. It:

1. discovers Composer package roots from `composer.json` files;
2. maps changed files to the nearest containing Composer package;
3. prefers repository-local `composer.phar` over global `composer`;
4. runs `php -l` on touched PHP files;
5. runs `composer validate --no-check-publish` in every affected package;
6. runs local `vendor/bin/phpunit` when available;
7. otherwise uses `php artisan test --no-interaction` for Laravel packages that expose Artisan.

A package without PHPUnit or Artisan still receives PHP syntax and Composer
metadata validation. The adapter does not invent framework-specific tests.

Mandatory rollback is identical across all adapters.


## Generic adapter

The generic adapter is the fallback for Git repositories that do not match Maven/Spring, Gradle, or Composer/PHP. It does **not** guess a build system.

```bash
repo-patch plan --adapter generic --patch change.diff
repo-patch apply --adapter generic --patch change.diff
```

Built-in checks validate JSON, TOML, Python, shell syntax, unresolved Git conflict markers, and basic readability/binary sanity. Repository-specific validations belong in `.repo-patch.toml`:

```toml
version = 1

[[generic.validations]]
name = "tests"
argv = ["./scripts/test"]

[[generic.validations]]
name = "lint"
argv = ["./scripts/lint"]
cwd = "."
```

Validation commands are argv arrays, not shell strings. Every command runs inside the same mandatory rollback boundary.
