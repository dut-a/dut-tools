# Contributing

This is primarily a Dut-owned working monorepo, but changes should still satisfy repository discipline.

Before committing:

```bash
make check
```

For a specific tool:

```bash
make test TOOL=<tool-name>
```

When adding a tool, register it in `tools.toml` and satisfy the tool contract documented in `docs/conventions.md`.
