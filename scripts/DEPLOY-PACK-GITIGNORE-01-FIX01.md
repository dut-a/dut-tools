# DEPLOY-PACK-GITIGNORE-01-FIX01

This bugfix updates deploy-pack to **1.15.1**.

It adds these mutable runtime/security files to the managed `.gitignore` block:

```text
.deploy-pack-keyring.json
.deploy-pack-replay.json
.deploy-pack-verifiers.json
```

It keeps these durable files visible to Git:

```text
.deploy-pack.toml
.deploy-pack-baseline
.deploy-pack-history.jsonl
```

## Apply

```bash
bash scripts/apply-deploy-pack-gitignore-01-fix01.sh .
make install TOOL=deploy-pack
rehash
```

Then reconcile each project using deploy-pack:

```bash
dp gitignore install
dp gitignore status --check
```
