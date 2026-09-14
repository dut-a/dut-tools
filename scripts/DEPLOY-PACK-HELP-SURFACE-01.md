# DEPLOY-PACK-HELP-SURFACE-01

Release: **1.14.1**

## Audit result

The deploy-pack CLI help surface was incomplete in several important areas. Many mature subcommands exposed only a terse one-line label, nested verifier/key/recovery/custody commands had little or no operator guidance, generated signed verifiers printed only a usage line, and the closeout automation lacked a single discoverable Make help target.

The audit also found a separate correctness defect in the preceding closeout-hardening patch: it could add `reconcile-baseline` to top-level help and then skip registering the actual parser/dispatch branch because its guard checked for the same text globally. The 1.14.1 patch repairs that registration defect and adds a regression test so help cannot advertise the command without argparse actually registering it.

## Updated help surfaces

- `deploy-pack --help`
- `inspect`, `baseline`, `mark`, and `reconcile-baseline`
- `deploy status`
- `history` and `history-verify`
- `verify` and `remote-verifier`
- unsigned and signed remote-evidence ingestion
- verifier identity lifecycle
- evidence-signing key lifecycle
- assurance taxonomy
- rollback planning/diff
- recovery bundle operations
- recovery signer trust
- offline trust-anchor copies/quorum/checkpoints
- generated PHP/Python signed-verifier `--help`
- `tools/deploy-pack/scripts/closeout.sh --help`
- new `make deploy-help`

## Operator entry points

```sh
make deploy-help

deploy-pack --help
deploy-pack remote-verifier --help
deploy-pack ingest-signed-remote-evidence --help
deploy-pack mark --help
deploy-pack reconcile-baseline --help
deploy-pack recovery --help
```

## Validation

The patch performs:

1. Python and shell syntax gates.
2. A help-tree smoke walk over core and nested commands.
3. Focused help regression tests.
4. Root Make integration validation for `deploy-help`.
5. The repository's full `make test TOOL=deploy-pack` suite before declaring PASS.

The reconstructed-current-lineage focused audit is green: **4/4 help regression tests PASS**.
