# DEPLOY-PACK-HELP-SURFACE-01

Release: 1.14.1

## Purpose

Audit and complete deploy-pack's operator-facing help surface. This increment is primarily help/documentation, and also repairs the missing `reconcile-baseline` parser/dispatch registration caused by the earlier broad string-presence guard. Trust validation semantics are unchanged.

## Audit finding

Several mature commands exposed only terse one-line parser labels. Nested verifier/key/recovery/custody commands were especially under-documented, and generated signed verifiers showed only a usage line. The new closeout Make workflow also needed a single discoverable help entry point.

## Updated surfaces

- Rich descriptions, examples, safety notes, and argument semantics for Git selection, baseline/history, verification/evidence ingestion, verifier/key lifecycle, rollback, assurance, and recovery/custody commands.
- Full `reconcile-baseline --help` explanation and constraints.
- Expanded generated PHP/Python signed-verifier `--help`.
- Expanded `tools/deploy-pack/scripts/closeout.sh --help`.
- New `make deploy-help` workflow entry point.
- Help regression test that walks the important command tree.

## Routine operator entry points

```sh
make deploy-help
deploy-pack --help
deploy-pack mark --help
deploy-pack reconcile-baseline --help
deploy-pack recovery --help
```
