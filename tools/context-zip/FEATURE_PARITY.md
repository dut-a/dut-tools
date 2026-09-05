# Spring / PHP Feature Parity

The previous standalone variants established a common CLI baseline. The monorepo consolidation keeps that baseline in one engine.

| Capability | Spring | PHP |
|---|---:|---:|
| `--version` | ✓ | ✓ |
| `--config` | ✓ | ✓ |
| `--init-config` | ✓ | ✓ |
| `--print-config` | ✓ | ✓ |
| `--force` | ✓ | ✓ |
| `--whole-project` | ✓ | ✓ |
| `--include-untracked` | ✓ | ✓ |
| `--include-binaries` | ✓ | ✓ |
| `--max-file-mb` | ✓ | ✓ |
| `--dry-run` | ✓ | ✓ |
| config include patterns | ✓ | ✓ |
| config exclude patterns | ✓ | ✓ |
| manifest | ✓ | ✓ |
| `EXCLUDED-FILES.tsv` | ✓ | ✓ |
| stack-specific default selection | Spring rules | PHP rules |
| shared `.context-zip.json` | ✓ | ✓ |

The two stack launchers are intentionally thin wrappers over the shared archive engine. This prevents future parity drift while retaining stack-specific selection semantics.
