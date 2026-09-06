# DEPLOY-PACK-ARTIFACT-01 — Built Artifact Packaging

## Status

Implemented in deploy-pack 1.10.0.

## Invariant

> Given a deployment-ready directory, package its contents faithfully at archive root without inferring Git state.

## Boundary

`deploy-pack artifact` is a Git-independent packaging mode. It does not resolve a repository root, deployment baseline, Git changes, untracked files, or Git-oriented production exclusions. Its source directory is authoritative.

The artifact implementation has a separate planner/writer (`deploy_pack.artifact`). It reuses deploy-pack's hardened canonical relative-path and symlink-target validators, but not Git-selection logic.

## Formats

- ZIP
- TAR.GZ

Both use deterministic member ordering and normalized archive metadata. Empty directories are retained. Dotfiles, including `.htaccess`, are ordinary artifact members. `.DS_Store` is omitted as narrow platform junk.

## Safety

- source must exist and be a directory;
- output may not reside inside source;
- archive members are canonical POSIX-relative paths;
- absolute, traversal, drive-prefixed, backslash-ambiguous, or normalized-noncanonical names fail closed;
- duplicate canonical member names fail closed;
- symlinks are archived as symlinks and never dereferenced;
- symlink targets must remain within the logical artifact root;
- writes use a sibling temporary file and atomic replace, so a failed build does not leave a successful-looking output archive.

## Required-path assertions

Repeatable `--require PATH` assertions validate canonical artifact-root-relative paths before writing the archive.

## Non-goals

Artifact mode does not decide what is production-ready, inject deploy-pack manifests, add repository metadata, consult Git, or apply the working-tree/change-set ignore policy.
