# DEPLOY-PACK-GITIGNORE-01-FIX01

Version: **1.15.1**

`DEPLOY-PACK-GITIGNORE-01` omitted three mutable deploy-pack runtime/security
state files from the managed `.gitignore` block:

```text
.deploy-pack-keyring.json
.deploy-pack-replay.json
.deploy-pack-verifiers.json
```

These files are now managed ignores.

The durable project/deployment records remain visible to Git:

```text
.deploy-pack.toml
.deploy-pack-baseline
.deploy-pack-history.jsonl
```

For existing projects, reconcile the managed block:

```bash
dp gitignore install
dp gitignore status --check
```
