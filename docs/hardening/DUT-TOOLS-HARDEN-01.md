# DUT-TOOLS-HARDEN-01

Status: implemented.

## Closed defects

### P0: release gate omitted implementation tests

`./scripts/release-gate` now executes `./scripts/test`, which runs every active tool's implementation suite.

The release authority now means:

```text
registry
+ lifecycle
+ structural/lint checks
+ every active tool implementation test
+ CLI contracts
+ clean-prefix installation
+ documentation
+ no-secret heuristic
```

### P0: cumulative ZIPs were not extraction-tested distributions

`./scripts/package-distribution` builds a deterministic ZIP with Unix POSIX mode bits stored in each member.

`./scripts/distribution-smoke` extracts the ZIP into a clean temporary directory and verifies:

- root operator scripts remain executable;
- all active tool entrypoints remain executable;
- `./scripts/check` runs;
- `./scripts/test` runs;
- clean-prefix installation succeeds;
- every installed command supports `--version`;
- the extracted full `./scripts/release-gate` succeeds.

A readable ZIP is no longer considered sufficient evidence of a valid drop-in distribution.

## Adjacent release hardening

`scripts/release` now:

- invokes the full `release-gate`, not the weaker repository check;
- validates the namespaced release tag before commit/tag creation;
- restores the original tool `VERSION` if validation fails;
- leaves dry-run releases unchanged.

## Command execution safety

`scripts/test` no longer uses `shell=True`.

Registry `test_command` values remain backward-compatible strings for now, parsed with `shlex.split`; list-form argv values are also accepted for future registry migration.
