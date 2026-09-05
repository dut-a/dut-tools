# CI

Pull requests always run structural checks. A changed-tool detector then selects only affected active tools for implementation tests and CLI contract checks.

Cross-cutting changes to `tools.toml`, `scripts/`, `docs/`, structural tests, the Makefile, README/versioning/contribution policy, or CI workflow select every active tool.

Pushes to `main` run `make release-gate`.

Release tags use `<tool>/v<semver>` and must name an active tool, exactly match its `VERSION`, appear in its `CHANGELOG.md`, and pass the full release gate.
