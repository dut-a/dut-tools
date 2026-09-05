# Changelog

## 1.0.0 - 2026-08-23

- Added `repo-verify` as an active `dut-tools` utility.
- Added project-owned `.repo-verify.toml` policy.
- Added built-in file/directory existence and absence checks.
- Added exact-text and regex content checks.
- Added executable-bit checks.
- Added argv-based external command checks without implicit shell execution.
- Added working-directory scoping for command checks.
- Added timeout support per command.
- Added fail-fast and full-report modes.
- Added text and JSON output.
- Added stable CI-oriented exit codes.
- Added deterministic evidence ordering and bounded command output capture.
- Kept all company/project-specific policy outside the generic engine.
