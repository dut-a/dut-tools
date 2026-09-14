# DEPLOY-PACK-GITIGNORE-01

Implements deploy-pack **1.15.0**.

Adds:

- `dp gitignore status`
- `dp gitignore status --check`
- `dp gitignore install`
- `dp gitignore remove`
- `dp init` integration
- bounded managed `.gitignore` ownership
- durable-state negations for `.deploy-pack.toml`, `.deploy-pack-baseline`, and `.deploy-pack-history.jsonl`
- malformed/duplicate marker fail-closed behavior
- focused regression coverage plus the normal full deploy-pack suite

Apply:

```bash
bash scripts/apply-deploy-pack-gitignore-01.sh .
make install TOOL=deploy-pack
rehash
```

Then, in any project using deploy-pack:

```bash
dp gitignore install
dp gitignore status --check
```
