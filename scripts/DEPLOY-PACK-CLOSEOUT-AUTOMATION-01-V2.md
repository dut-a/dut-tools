# DEPLOY-PACK-CLOSEOUT-AUTOMATION-01 — V2 hotfix

## Fix

The original apply script wrote `mk/deploy-pack.inc` but assumed the repository root `Makefile` already included it. On checkouts where that include was absent, validation failed with:

```text
make: *** No rule to make target `deploy-closeout-status'. Stop.
```

V2 now idempotently ensures the canonical root integration exists:

```make
-include mk/deploy-pack.inc
```

It then validates `make -n deploy-closeout-status` from the repository root before running the full deploy-pack regression suite.

## Apply

From the `dut-tools` repository root:

```sh
bash scripts/apply-deploy-pack-closeout-automation-01-v2.sh .
```

The script is safe to run over a partially applied V1 state.
