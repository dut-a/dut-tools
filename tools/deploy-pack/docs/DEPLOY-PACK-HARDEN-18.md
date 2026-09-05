# DEPLOY-PACK-HARDEN-18 — Trust Invariant + Path Containment Closure

Release: **1.8.0**

## Objective

Close the remaining trust-state and deployment-path findings from the DEPLOY-PACK-01→12 architecture/security audit.

## Security invariants

### Exactly one active recovery signer

Recovery trust may contain zero active signers, or exactly one active signer whose ID equals `activeSigner`. Any state containing multiple `status=active` records is invalid and fails closed on load/save, offline trust operations, and deployment status.

### Canonical manifest paths

Manifest file and remote-deletion paths must be canonical POSIX repository-relative paths. Deploy-pack rejects:

- absolute paths;
- `.` or `..` path components;
- empty path components;
- NUL bytes;
- Windows drive prefixes;
- backslash-based path forms;
- duplicate file/deletion paths;
- a path simultaneously declared for deployment and deletion.

ZIP members are independently checked for the same unsafe path forms.

### Parent-symlink containment

A lexically safe path is still unsafe if one of its parent components is a symlink. Extracted-tree verification and generated remote verifiers reject such traversal. The final component may be a symlink only when the manifest explicitly declares a symlink entry.

### Symlink target policy

Symlink targets must remain within the deployment root. Absolute, drive-prefixed, and normalized root-escaping targets are rejected while normal internal relative symlinks remain supported.

## Verification coverage

`tests/test_harden18_trust_path_closure.py` covers:

1. duplicate active recovery signer rejection;
2. deploy-status failure on malformed active signer state;
3. `..` manifest traversal rejection;
4. Windows drive/backslash ambiguity rejection;
5. parent-symlink traversal rejection;
6. root-escaping symlink-target rejection;
7. safe internal symlink compatibility;
8. unsafe undeclared ZIP member rejection;
9. remote-deletion traversal through a symlink parent rejection.

Generated Python/PHP/browser/signed verifiers also use the same fail-closed path-containment model; verifier-generation APIs validate manifests before embedding them.

## Audit findings addressed

This increment closes the substantive parts of:

- **DP-AUD-009** — recovery trust permits multiple active signers;
- **DP-AUD-010** — path containment was assumed;
- **DP-AUD-011** — escaping symlink targets were allowed.

It also strengthens the path-policy portion of HARDEN-13/HARDEN-18 by applying the same containment semantics to generated target-host verifiers.
