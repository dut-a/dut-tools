# Architecture

`dut-tools` is a registry-driven, polyglot monorepo.

The root repository supplies only cross-cutting mechanics:

- tool discovery
- installation
- health checks
- structural quality gates
- release conventions
- shared documentation

Individual tools retain ownership of their implementation, dependencies, tests, versions, and changelog.

The monorepo must not become a framework that forces every tool into one runtime or release cadence.


## Release responsibility boundaries

`release-flow`: version transitions. `repo-verify`: repository policy. `build-diagnostics`: diagnostics. `release-archive`: packaging. `scripts/release`: monorepo tool release only.
