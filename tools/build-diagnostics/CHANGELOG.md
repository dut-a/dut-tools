# Changelog

## 1.0.0 - 2026-08-23

- Added `build-diagnostics` as an active `dut-tools` utility.
- Added argv-based build command execution without implicit shell use.
- Added combined stdout/stderr capture with timestamps and exit evidence.
- Added default warning/error/note classification.
- Added configurable regex classifiers.
- Added warning-category counts and bounded sample capture.
- Added project-owned budgets in `.build-diagnostics.toml`.
- Added text and JSON reports.
- Added baseline comparison support for warning deltas.
- Added `--check` mode with stable CI exit codes.
- Added deterministic report ordering.
- Kept warning thresholds and forbidden-pattern policy outside the generic engine.
