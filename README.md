# dut-tools

`dut-tools` is the Dut-owned monorepo for small, reusable utilities that span companies, products, and disciplines.

Today that primarily means engineering utilities such as Git workflow helpers, source/context packaging tools, migration utilities, and repository diagnostics. The repository name is intentionally broader than `dut-engineering`: future tools may support public health, epidemiology, biostatistics, data analysis, research workflows, or other Dut-owned work.

The ownership rule is simple:

> If a utility exists because Dut needs it across contexts, it can belong here. If it exists because one company, product, client, or standalone domain system needs it, it belongs with that owner instead.

`Origin`, for example, is a standalone Dut-owned idea system and does **not** belong in this monorepo. Company-specific tooling such as `tembeek-local` remains company-owned unless a genuinely reusable mechanism is later extracted.

## Repository principles

1. **Dut-owned, not company-owned.**
2. **Cross-domain is allowed.** Engineering is the starting point, not the permanent limit.
3. **Independent commands.** There is no umbrella `dut` CLI.
4. **Independent versions.** Each tool has its own SemVer lifecycle.
5. **Polyglot by design.** Use the language that best fits the utility.
6. **Mechanism over company policy.** Company-specific defaults stay outside generic tools.
7. **Small dependency surface.** Utilities should remain easy to run.
8. **No secrets.** Credentials, tokens, private customer data, and keys do not belong here.
9. **Conservative machine mutation.** Bootstrap/doctor prefer diagnosis over silently changing the host.
10. **A tool must earn extraction.** The monorepo is the default home until independent ownership or release needs justify another repository.

## Current registered tools

The initial skeleton reserves homes for three existing utilities:

- `context-zip` — Spring/PHP context and project packaging.
- `git-context` — Git identity/context selection across project ownership boundaries.
- `migration-version-fixer` — migration version normalization and CI checks.

All three initial utilities are now active monorepo tools: `git-context`, `context-zip`, and `migration-version-fixer`.

Use:

```bash
make list
make versions
```

## Layout

```text
dut-tools/
├── Makefile
├── tools.toml
├── bin/                       # stable command facades for active tools
├── tools/                     # independently versioned utility homes
│   ├── context-zip/
│   ├── git-context/
│   └── migration-version-fixer/
├── lib/                       # cautiously shared, language-specific primitives
├── scripts/                   # repository operator commands
├── config/defaults/           # repo-level defaults only
├── docs/                      # conventions and lifecycle policy
├── tests/structural/          # repository-wide structural gates
└── templates/tool/            # template for adding a new tool
```

## Quick start

Requirements:

- macOS or Linux
- Bash
- Git
- Python 3.11+ (`tomllib` is used by the registry tooling)

Run:

```bash
make help
make list
make doctor
make test
make check
```

To bootstrap local directories and install all **active** tools:

```bash
make bootstrap
```

By default, commands are installed as symlinks under:

```text
~/.local/bin
```

Ensure that directory is on `PATH` from your dotfiles, for example:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

`dut-tools` does not own shell startup configuration; that belongs in the separate dotfiles repository.

## Installing tools

Install every active tool:

```bash
make install
```

Install one active tool:

```bash
make install TOOL=git-context
```

Override the installation prefix:

```bash
make install PREFIX="$HOME/.local"
```

Placeholder tools are skipped intentionally.

Uninstall:

```bash
make uninstall
make uninstall TOOL=git-context
```

## Tool registry

`tools.toml` is the authoritative registry for utilities in this monorepo.

Each tool records:

- canonical tool name
- CLI command
- repository path
- language/runtime family
- lifecycle status
- version file
- command entrypoint, when active
- short purpose

Supported lifecycle statuses:

- `placeholder` — reserved home; implementation not migrated yet
- `active` — installable and supported
- `deprecated` — retained temporarily but should not receive new work
- `retired` — historical only; not installable

Do not hard-code the tool list into the Makefile.

## Per-tool contract

Every mature tool should have:

```text
tools/<tool-name>/
├── README.md
├── CHANGELOG.md
├── VERSION
├── src/           # when appropriate
├── tests/
├── examples/
└── config/        # when appropriate
```

Every active CLI must support:

```bash
<tool> --help
<tool> --version
```

Where useful, also support:

```bash
<tool> doctor
```

## Versions

The monorepo itself has no synchronized product version.

Each tool owns a SemVer version in:

```text
tools/<tool>/VERSION
```

Examples:

```text
context-zip              1.0.0
git-context              1.0.0
migration-version-fixer  1.0.0
```

Release tags are namespaced:

```text
context-zip/v1.2.0
git-context/v2.0.1
migration-version-fixer/v1.1.0
```

See [`VERSIONING.md`](VERSIONING.md) and [`docs/release-process.md`](docs/release-process.md).

## Configuration precedence

New tools should follow this precedence unless their ecosystem requires something else:

```text
CLI arguments
    >
environment variables
    >
project configuration
    >
user configuration
    >
built-in defaults
```

Human-maintained configuration should default to TOML for new tools.

Suggested user-level location:

```text
~/.config/dut/
```

Project-level configuration should use a neutral repository location such as:

```text
.tooling/
```

rather than embedding a person's name into company repositories.

See [`docs/configuration.md`](docs/configuration.md).

## Adding a tool

Copy the template:

```bash
cp -R templates/tool tools/my-tool
```

Then:

1. Rename/update its documentation.
2. Add a `[tools."my-tool"]` entry to `tools.toml`.
3. Set a valid SemVer in `VERSION`.
4. Add implementation and tests.
5. Add a stable `bin/my-tool` facade when status becomes `active`.
6. Run:

```bash
make check
```

The structural tests reject duplicate command names, malformed versions, missing registered paths, and incomplete tool metadata.

## Root commands

```bash
make help
make list
make versions
make bootstrap
make install
make uninstall
make doctor
make test
make check
make lint
make release TOOL=<name> VERSION=<x.y.z>
make release-gate
make clean
```

Use `make help` for details.

## Ownership decision

Before adding something here, ask:

```text
Owned by a particular company/client?
    yes -> company/client repository
    no
    |
    v
Standalone application/domain/system?
    yes -> standalone Dut repository
    no
    |
    v
Reusable utility?
    yes -> dut-tools
    no
    |
    v
Workstation configuration?
    yes -> dut-dotfiles
    no
    |
    v
Incubate elsewhere until ownership is clear.
```

See [`docs/tool-lifecycle.md`](docs/tool-lifecycle.md).

## Security

This repository must contain no:

- passwords
- API tokens
- SSH/private keys
- production secrets
- customer records
- private company credentials
- machine-specific secret configuration

Commit example configuration only. Resolve secrets at runtime through environment variables or an appropriate secret store.

## License

The initial skeleton is private/all-rights-reserved by default. Replace `LICENSE` deliberately if a tool or the repository is later open-sourced.


## Consolidation gate

Before treating the repository as a release candidate, run:

```bash
make release-gate
```

This is stricter than `make check`: it requires all registered tools to be active, validates CLI contracts, tests temporary-prefix installation, checks documentation completeness, and runs a basic no-secret heuristic.

See [`docs/release-gate.md`](docs/release-gate.md).


## CI policy

Pull requests run structural checks and selective per-tool tests based on the diff. Cross-cutting repository changes select every active tool.

Pushes to `main` run the full `make release-gate`. Namespaced tags such as `git-context/v1.5.0` must match the tool's `VERSION`, appear in its changelog, and pass the full release gate. See [`docs/ci.md`](docs/ci.md).

## Release archive

`release-archive` is the repository's deterministic distributable-packaging tool. It supports ZIP and TAR.GZ from one file-selection plan, Git cleanliness enforcement, manifests, SHA-256 sidecars, dry-run/check modes, and reproducible metadata.

```bash
release-archive . --format zip
release-archive . --format tar.gz
release-archive . --format both
```


## repo-patch

`repo-patch` provides plan-first repository mutation with a Maven/Spring v1 adapter. Automatic rollback is mandatory on every validation failure.


## git-provenance-audit

`git-provenance-audit` verifies historical Git author/committer provenance against the directory-driven identity model declared by `git-context`.

```bash
git-provenance-audit ~/dev --strict
git-provenance-audit ~/dev --commits 100 --format json
```

It is strictly read-only and never rewrites history.


## module-name-normalizer

`module-name-normalizer` conservatively normalizes Maven project/module `<name>` values without touching artifact/dependency/plugin coordinates.

```bash
module-name-normalizer . --check
module-name-normalizer . --write
```


## repo-verify

`repo-verify` is the generic declarative repository-verification engine. Repositories own their policy through `.repo-verify.toml`.

```bash
repo-verify
repo-verify --format json
```


## build-diagnostics

`build-diagnostics` captures build output, classifies warnings/errors, and reports against repository-owned budgets.

```bash
build-diagnostics -- mvn verify
build-diagnostics --check --format json -- ./gradlew build
```


## release-flow

Generic prepare/verify/next-development release-version workflow with repository-owned policy.
