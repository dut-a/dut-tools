# Repository Consolidation / Release Gate

The repository-wide gate is:

```bash
make release-gate
```

It is deliberately stricter than ordinary `make check`.

The gate verifies:

1. registry validity;
2. every registered tool is `active`;
3. repository lint/tests pass;
4. every active command supports `--help` and `--version`;
5. symlink installation works from a clean temporary prefix;
6. installed commands execute their version smoke tests;
7. root and per-tool documentation contracts are complete;
8. a basic private-key/token heuristic finds no obvious committed secrets.

Success ends with:

```text
DUT-TOOLS-RELEASE-GATE: PASS
```

This is the baseline gate to run before treating a `dut-tools` snapshot as a consolidated release candidate.


## Implementation-test authority

The gate now runs every active tool's registered implementation test command. Pull-request selective testing is an optimization only; it is no longer the sole place where implementation suites execute.

## Distribution authority

A release archive must additionally pass `scripts/distribution-smoke`; `ZipFile.testzip()` alone is not considered a drop-in validation.
