# deploy-pack

`deploy-pack` creates reviewable deployment ZIPs from Git changes while preserving repository-relative paths. It lives in the `dut-tools` monorepo but installs as an independent command: **no `dut` prefix**.

## Commands

```bash
deploy-pack inspect [BASELINE]
deploy-pack pack [BASELINE]
deploy-pack baseline
deploy-pack mark [REF]
```

The baseline means **what production actually has**, not merely the last ZIP created.

```text
last successfully deployed Git ref
              ↓
committed changes through HEAD
              +
staged / unstaged / untracked changes
              ↓
        deployment package
```

Creating a package never advances the baseline. After upload, extraction, applying deletions, and production verification:

```bash
deploy-pack mark HEAD
```

Prefer an immutable tag when practical:

```bash
git tag -a production-2026-08-27 -m 'Production deployment 2026-08-27'
deploy-pack mark production-2026-08-27
```

## First use

```bash
deploy-pack mark <currently-deployed-ref>
deploy-pack inspect
deploy-pack pack
```

Or use an explicit baseline without recording it:

```bash
deploy-pack inspect v1.4.2
deploy-pack pack v1.4.2
```

## Output

```text
my-project-20260827-182200.deploy.zip
my-project-20260827-182200.deploy.zip.sha256
my-project-20260827-182200.deploy.zip.deletions.txt   # when needed
```

The ZIP embeds `.deploy-pack-manifest.json` containing the baseline ref/commit, current HEAD, packaged paths, per-file SHA-256 values, and required remote deletions.

## Git-aware deployment selection

`deploy-pack pack` is allowlist-first. Git determines which repository paths changed; `.deploy-pack.toml` determines which paths are deployment material. New repositories fail closed until a policy exists.

Initialize a conservative policy:

```bash
deploy-pack init
```

Then review `.deploy-pack.toml`:

```toml
schema = 2

[pack]
policy = "allowlist"
include = [
  ".htaccess",
  "*.php",
  "assets/**",
  "admin/**",
  "scripts/commerce-backup.php",
]
exclude = [
  "assets/**/*.map",
]
require = [
  ".htaccess",
]
```

Selection rules:

- tests, snapshots, `.env`/`.env.*`, VCS internals, and deploy-pack control artifacts are non-deployable even when broadly allowlisted;
- scripts, package/build metadata, and hidden paths other than an explicitly selected `.htaccess` are excluded unless the project allowlist selects them;
- `[pack].exclude` narrows the project allowlist;
- CLI `--ignore` narrows selection further;
- in allowlist mode CLI `--include` is also a **narrowing filter** and cannot expand beyond `[pack].include`;
- `[pack].require` contains exact repository-relative paths that must exist before a package can be planned.

Use `deploy-pack inspect <baseline>` to see excluded changed paths together with their reason.

Pre-1.11 `[deploy-pack] ignore/include` configuration remains readable as legacy compatibility, but `deploy-pack init` only creates the safer schema-2 allowlist form.

## Reproducible committed-only package

```bash
deploy-pack pack --committed-only
```

## Installation

From `dut-tools/tools/deploy-pack`:

```bash
python3 -m pip install --user .
```

or:

```bash
./install.sh
```


## DEPLOY-PACK-02 — production verification

Verify the package before upload:

```bash
deploy-pack verify app.deploy.zip
```

The `.sha256` sidecar is auto-detected when present. Verification also checks
the embedded manifest, every declared file's SHA-256 and size, and rejects
undeclared ZIP entries.

Verify an extracted tree:

```bash
deploy-pack verify app.deploy.zip --root /tmp/deployment
```

Generate a standalone shared-hosting verifier (PHP by default):

```bash
deploy-pack remote-verifier app.deploy.zip
php app.deploy.zip.verify.php /home/account/public_html
```

Python is also available:

```bash
deploy-pack remote-verifier app.deploy.zip --language python
python3 app.deploy.zip.verify.py /home/account/public_html
```

The remote verifier validates deployed file hashes and confirms that every
recorded deletion has actually been applied. It requires no deploy-pack
installation on the server.

Recommended deployment sequence:

```text
inspect -> pack -> local verify -> upload/extract -> apply deletions
-> remote verify -> application smoke test -> mark deployed ref
```

`deploy-pack mark` never runs automatically.


### Permission verification

Manifest entries now record path type and permission bits.

For shared hosting, normal verification intentionally tolerates benign owner/group
write-bit normalization while requiring the executable-bit class to match:

```bash
deploy-pack verify app.deploy.zip --root /tmp/deployment
```

For environments where permissions must match exactly:

```bash
deploy-pack verify app.deploy.zip \
  --root /tmp/deployment \
  --strict-permissions
```

The generated CLI remote verifiers also accept:

```bash
php app.deploy.zip.verify.php /home/account/public_html --strict-permissions
python3 app.deploy.zip.verify.py /home/account/public_html --strict-permissions
```

### Symlink verification

Symlinks are recorded as symlinks rather than silently dereferenced. The manifest
records their target string, hash, size, and mode. Archive and deployed-tree
verification fail if:

- a symlink became a regular file;
- a regular file became a symlink;
- the symlink target changed;
- a symlink scheduled for deletion still exists.

This is particularly important for shared-host deployments using `current`,
`storage`, release directories, or framework-managed symlinks.

### Browser verification for cPanel hosts without terminal access

Generate a temporary token-protected browser endpoint:

```bash
deploy-pack remote-verifier app.deploy.zip --browser
```

The command prints a high-entropy token and creates:

```text
app.deploy.zip.verify-browser.php
```

Upload the verifier into a temporary web-accessible location, then visit:

```text
https://example.com/path/app.deploy.zip.verify-browser.php
```

If the verifier lives outside the application root:

```bash
deploy-pack remote-verifier app.deploy.zip \
  --browser \
  --relative-root ../public_html
```

For exact permission checking in-browser, add:

```text
&strict_permissions=1
```

The endpoint returns HTTP 200 on PASS, HTTP 409 on verification failure, and
HTTP 404 for a missing/wrong token. It also sends `no-store`, `noindex`, and
`nosniff` headers.

**Delete the browser verifier immediately after verification.** It is intentionally
a temporary deployment instrument, not a permanent production endpoint.

## DEPLOY-PACK-03 — durable deployment evidence

Successful verification now writes a durable evidence record automatically:

```bash
deploy-pack verify app.deploy.zip --root /tmp/extracted
```

produces:

```text
app.deploy.zip.verify-evidence.json
```

The record binds the exact archive SHA-256, canonical manifest SHA-256, baseline
and head commits, verification scope/root, permission mode, file/deletion counts,
and UTC verification time.

Archive-only verification is intentionally not sufficient to advance production.
A normal mark now requires deployment-level evidence:

```bash
deploy-pack mark production-2026-08-27 \
  --evidence app.deploy.zip.verify-evidence.json \
  --archive app.deploy.zip
```

The mark is refused if the evidence is missing, is not PASS, describes another
archive/manifest, names another head commit, or has only `archive` scope.

For the one-time adoption of deploy-pack around an already-running production
revision:

```bash
deploy-pack mark <known-production-ref> --unsafe-no-evidence
```

That bypass is deliberately explicit and noisy.

Recommended lifecycle:

```text
bootstrap known baseline once
        ↓
inspect → pack → archive verify
        ↓
upload/extract/apply deletions
        ↓
extracted-tree or remote verify
        ↓
durable PASS evidence
        ↓
application smoke test
        ↓
evidence-bound mark
```
\n\n## DEPLOY-PACK-04 — remote evidence ingestion\n\nRemote CLI verification can emit evidence with `--evidence-out`. Download that JSON and bind it to the exact local archive with:\n\n```bash\ndeploy-pack ingest-remote-evidence app.remote-evidence.json app.deploy.zip\n```\n\nThe normalized `app.deploy.zip.remote-verify-evidence.json` can then authorize evidence-bound `deploy-pack mark`. The browser verifier returns the same PASS evidence as JSON directly; save that response, delete the temporary verifier, and ingest it locally. Ingestion rejects evidence whose manifest SHA-256, baseline commit, or head commit differs from the archive. Remote evidence intentionally omits the archive SHA-256 because the remote side verifies the deployed tree; ingestion binds that verified manifest to the exact local ZIP and records its archive SHA-256.\n

## Deployment history ledger

Every successful `deploy-pack mark` now appends an immutable-style local record to:

```text
.deploy-pack-history.jsonl
```

The baseline remains the fast pointer to current production:

```text
.deploy-pack-baseline
```

while the ledger preserves how production got there.

A safe deployment record captures:

- UTC record timestamp;
- previous production commit;
- new baseline ref and resolved commit;
- verification evidence file path and SHA-256;
- evidence scope/method/root/verifier;
- evidence manifest SHA-256 and verified head commit;
- deployment archive path/name/SHA-256 when supplied.

Initial bootstrap via:

```bash
deploy-pack mark <ref> --unsafe-no-evidence
```

also writes a ledger entry, but explicitly marks it:

```json
"unsafeNoEvidence": true
```

### View history

```bash
deploy-pack history
deploy-pack history --limit 5
deploy-pack history --json
```

### Verify ledger continuity

```bash
deploy-pack history-verify
```

The verifier checks that:

- each record uses the supported ledger schema;
- each record's `previousBaseline` equals the preceding deployed commit;
- safe records carry evidence;
- evidence `headCommit` equals the recorded deployed commit;
- the current `.deploy-pack-baseline` resolves to the latest ledger commit.

The ledger is append-only by convention. Do not rewrite old lines to "clean them up";
deployment history is useful precisely because it records what actually happened.
\n\n## Rollback ancestry and record inspection\n\nThe ledger distinguishes ordinary forward deployments from intentional rollbacks.\nA rollback is declared at mark time by naming the historical deployment being restored:\n\n```bash\ndeploy-pack mark production-rollback-2026-08-28 \\\n  --evidence rollback.deploy.zip.remote-verify-evidence.json \\\n  --archive rollback.deploy.zip \\\n  --rollback-to 3\n```\n\n`--rollback-to 3` is accepted only when the ref being marked resolves to the same\ncommit deployed by history record 3. The new ledger record captures:\n\n```text\ndeploymentKind: rollback\nrollback.fromRecord: immediately preceding production record\nrollback.fromCommit: production commit being replaced\nrollback.targetRecord: historical deployment intentionally restored\nrollback.targetCommit: commit from that historical record\n```\n\nThis makes a rollback a new deployment event with its own verification evidence; it does\nnot rewrite or jump backward in the history ledger. Subsequent deployments continue from\nthe rollback record normally.\n\nInspect one deployment in detail:\n\n```bash\ndeploy-pack history show 3\ndeploy-pack history show latest\ndeploy-pack history show 3 --json\n```\n\nThe normal history listing now includes stable 1-based record numbers and marks rollback\nentries explicitly. `deploy-pack history-verify` additionally validates rollback ancestry:\nits target must be an earlier record, its target commit must match that historical record,\nand its source must be the immediately preceding production deployment.\n

## Rollback planning

Prepare a rollback without changing production state:

```bash
deploy-pack rollback plan 3
```

It validates ledger continuity, compares current production to deployment record
`#3`, reports the exact restore/delete set, and produces rollback artifacts whose
file bytes come from the historical target commit—not the current working tree.

```bash
deploy-pack rollback plan 3 --dry-run
deploy-pack rollback plan 3 -o rollback-to-3.deploy.zip
```

Generated artifacts include the rollback ZIP, SHA-256 sidecar, deletion list when
required, and `.rollback-plan.json`.

The command does not alter `.deploy-pack-baseline`, append the ledger, upload,
extract, or mark anything deployed. After remote verification, complete the
existing evidence-bound `mark ... --rollback-to 3` flow.
\n\n### Hypothetical rollback planning\n\nBy default, `deploy-pack rollback plan 3` plans from actual current production.\nTo model a historical path instead:\n\n```bash\ndeploy-pack rollback plan 3 --from 7\n```\n\nThis computes the exact restore/delete package required to move deployment record\n`#7` back to record `#3` without requiring `#7` to be current production. The\ntarget must be strictly earlier than the source. No baseline or ledger state is\nchanged, and package bytes still come from the historical target commit.\n

### Rollback diff

Use `deploy-pack rollback diff 3` for a report-only comparison from actual current production to record #3. Use `deploy-pack rollback diff 3 --from 7` for a hypothetical historical comparison. Add `--json` for machine-readable output. The command creates no ZIP, checksum, deletion file, rollback manifest, evidence, baseline mutation, or ledger record.


## DEPLOY-PACK-05 — signed remote evidence

Generate a short-lived Ed25519 signing verifier with `deploy-pack remote-verifier app.deploy.zip --sign`. It emits the verifier plus a local `.public-key.json`. The verifier contains an ephemeral private key valid only for that artifact.

On the server run `php app.deploy.zip.verify-signed.php /home/account/public_html --signed-evidence-out app.remote.signed.json` (PHP Sodium required), or the Python signed verifier (Python `cryptography` required). Retrieve the JSON and ingest with `deploy-pack ingest-signed-remote-evidence app.remote.signed.json app.deploy.zip --public-key <verifier.public-key.json>`.

Ingestion verifies Ed25519 signature, signer fingerprint, payload hash, manifest/head identity and exact archive binding before producing mark-eligible normalized evidence. Any signed-field tampering fails. Browser signing remains disabled. Delete the temporary signed verifier from shared hosting immediately after use.


## Signed evidence required by default

Normal `deploy-pack mark` now requires normalized signed remote evidence.

```bash
deploy-pack mark production-2026-08-29 \
  --evidence app.deploy.zip.remote-verify-evidence.json \
  --archive app.deploy.zip
```

Legacy unsigned evidence is rejected unless compatibility is explicit:

```bash
deploy-pack mark production-2026-08-29 \
  --evidence old-evidence.json \
  --archive app.deploy.zip \
  --allow-unsigned-evidence
```

The compatibility path prints a warning and records `trustMode = legacy-unsigned-compatibility`. Signed marks record `trustMode = signed-remote`. Initial bootstrap remains available through `--unsafe-no-evidence`.


## DEPLOY-PACK-06 — verifier lifecycle and replay resistance

Issue a short-lived verifier identity before generating a signed verifier:

```bash
deploy-pack verifier issue --ttl-minutes 30
deploy-pack remote-verifier app.deploy.zip --sign --verifier-id <ID>
```

The signed evidence is bound to a unique verifier ID, issuance time, expiry time, and nonce. `mark` rejects evidence from expired or revoked verifier identities, evidence whose remote verification timestamp falls outside the identity validity window, and evidence whose identity differs from locally issued state.

Revoke or inspect identities with:

```bash
deploy-pack verifier show <ID>
deploy-pack verifier revoke <ID>
```

After a successful signed-evidence `mark`, deploy-pack consumes a replay key in `.deploy-pack-replay.json`. The same signed evidence cannot authorize another mark. Verifier/replay state files are excluded from deployment packages.


## Deployment status

```bash
deploy-pack deploy status
deploy-pack deploy status --json
```

Read-only summary of the current baseline, latest ledger record/trust mode, verifier lifecycle health, replay-consumption count, and pending deployment packages/evidence/verifier artifacts. It exits non-zero when baseline/history invariants fail.


### Quiet / CI gate mode

```bash
deploy-pack deploy status --quiet
```

Emits no output. Exit `0` means healthy; exit `1` means unhealthy. Suitable for Make and CI gates.


## Root Make / checker convention

The repository-level integration convention is:

```make
-include mk/deploy-pack.inc
```

which exposes:

```bash
make deploy-status-check
```

That target delegates directly to:

```bash
deploy-pack deploy status --quiet
```

For structural verification of the root wiring:

```bash
./scripts/check-deploy-status-target.sh
```

The structural checker and operational deployment gate are intentionally
separate: one validates Make architecture; the other validates deployment
state.


## DEPLOY-PACK-07 — verifier key history, recovery, and compromise handling

DEPLOY-PACK keeps the ephemeral-per-verifier signing design. There is no
long-lived repository private signing key to recover or rotate.

Every successful signed-evidence ingestion now registers the verifier's Ed25519
public-key fingerprint in `.deploy-pack-keyring.json`, bound to the verifier ID.
Each newly generated signed verifier is therefore a new key generation — natural
rotation without reusing secret material.

Inspect retained signer history:

```bash
deploy-pack keys show
```

If a verifier/private key is believed compromised, revoke its retained public
fingerprint:

```bash
deploy-pack keys revoke <SHA256_FINGERPRINT> --reason 'verifier leaked'
```

Revocation also revokes verifier identities bound to that signer. Previously
recorded deployment history remains intact, while new `mark` attempts using that
signer are rejected.

Export durable trust/recovery state (never private keys):

```bash
deploy-pack recovery export deploy-pack-recovery.json
```

The bundle contains public-key history, verifier lifecycle state, replay state,
deployment history, and the current baseline. Restore conservatively with:

```bash
deploy-pack recovery import deploy-pack-recovery.json
```

Because signer private keys are ephemeral and embedded only in temporary remote
verifiers, they are intentionally absent from recovery bundles.


## DEPLOY-PACK-08 — recovery integrity

Recovery export is signed and hash-chained by default:

```bash
deploy-pack recovery export deploy-pack-recovery.signed.json
```

It also writes a retained public-key sidecar. Verify before import:

```bash
deploy-pack recovery verify deploy-pack-recovery.signed.json \
  --public-key deploy-pack-recovery.signed.json.public-key.json
```

Import performs the same verification before mutating local state:

```bash
deploy-pack recovery import deploy-pack-recovery.signed.json \
  --public-key deploy-pack-recovery.signed.json.public-key.json
```

The signed payload covers the keyring, verifier state, replay state, baseline and
an explicit hash chain over every deployment-history record. Editing, removing,
reordering or inserting history records invalidates verification.

Legacy recovery remains explicit compatibility only:

```bash
deploy-pack recovery export legacy.json --unsigned
deploy-pack recovery import legacy.json --allow-unsigned
```

Private signing material is never written to the recovery bundle or public-key
sidecar. The recovery signing key is ephemeral and discarded after export; retain
the generated public-key sidecar with the bundle.


## DEPLOY-PACK-09 — recovery trust anchoring

Recovery signature verification now requires a locally pinned signer in:

```text
.deploy-pack-recovery-trust.json
```

Trust and activate a signer:

```bash
deploy-pack recovery trust add recovery-public-key.json \
  --activate \
  --reason 'primary offline recovery signer'
```

A cryptographically valid bundle from an untrusted signer is rejected.

Rotate to a successor with explicit ancestry:

```bash
deploy-pack recovery trust add successor-public-key.json \
  --activate \
  --predecessor <OLD_SIGNER_ID> \
  --reason 'scheduled rotation'
```

The former active signer becomes retired and remains usable for historical
bundle verification.

Revoke compromise:

```bash
deploy-pack recovery trust revoke <SIGNER_ID> \
  --reason 'offline recovery signer compromised'
```

Revoked signers cannot verify or import recovery bundles. Existing deployment
history is preserved unchanged.


### Recovery trust health in deploy status

`deploy-pack deploy status` and `--json` now include active/trusted/retired/revoked recovery signer counts, the active anchor, and trust health. Status fails when the active signer pointer is missing/inconsistent or signer records use unknown states. Retired/revoked signers are valid lifecycle states. `deploy-pack deploy status --quiet` remains silent and fail-closed for CI.


## DEPLOY-PACK-10 — offline recovery trust-anchor export

Export the recovery trust root for independent/offline custody:

```bash
deploy-pack recovery trust export-offline recovery-trust-anchor.json
```

The command creates three artifacts:

```text
recovery-trust-anchor.json
recovery-trust-anchor.json.public-key.json
recovery-trust-anchor.json.fingerprint.txt
```

The anchor contains the complete recovery signer lifecycle snapshot: active,
trusted, retired, and revoked signers; signer fingerprints; rotation ancestry;
reasons/timestamps; an integrity hash of the trust state; and a hash chain over
all signer records.

The artifact is signed with a fresh ephemeral Ed25519 key. No private key is
written to disk. The detached public-key sidecar verifies the signature.

The fingerprint record is intentionally human-readable and includes:

- trust-anchor artifact SHA-256;
- verification public-key SHA-256;
- signed payload SHA-256;
- active recovery signer ID;
- export timestamp.

Store the fingerprint record separately from the anchor/key when practical
(e.g. another USB, paper record, password manager, or other offline custody).

Verify later:

```bash
deploy-pack recovery trust verify-offline \
  recovery-trust-anchor.json \
  --public-key recovery-trust-anchor.json.public-key.json
```

For stronger sidecar-substitution protection, supply the independently retained
public-key fingerprint:

```bash
deploy-pack recovery trust verify-offline \
  recovery-trust-anchor.json \
  --public-key recovery-trust-anchor.json.public-key.json \
  --expected-fingerprint <SHA256>
```

Verification checks Ed25519 signature validity, public-key fingerprint,
payload hash, full recovery-trust-state hash, signer-record hash chain, active
anchor consistency, predecessor existence, lifecycle states, and rotation-cycle
absence.

This is an export/verification mechanism only; it does not mutate live recovery
trust state during verification.


## DEPLOY-PACK-11 — multiple offline custody copies and checkpoint history

Create one logical offline custody checkpoint with independently signed copies:

```bash
deploy-pack recovery trust export-copies recovery-trust.json --copies 3
```

This produces `recovery-trust.copy-01.json` through `copy-03`, each with its own
independent ephemeral Ed25519 key, public-key sidecar, and fingerprint record,
plus `recovery-trust.checkpoint.json` describing the shared checkpoint.

A practical custody layout is: copy 1 in the primary safe, copy 2 in a separate
physical location, copy 3 with another trusted offline custodian. Keep each
fingerprint record separate from the anchor/public-key pair when possible.

Verify that copies agree and are independently signed:

```bash
deploy-pack recovery trust verify-copy-set \
  recovery-trust.copy-01.json recovery-trust.copy-02.json recovery-trust.copy-03.json \
  --public-key recovery-trust.copy-01.json.public-key.json \
  --public-key recovery-trust.copy-02.json.public-key.json \
  --public-key recovery-trust.copy-03.json.public-key.json
```

Every multi-copy export appends to `.deploy-pack-offline-checkpoints.jsonl`.
Checkpoint records are monotonic and hash-linked to the prior checkpoint.

```bash
deploy-pack recovery trust checkpoint-history
deploy-pack recovery trust checkpoint-show latest
deploy-pack recovery trust checkpoint-verify
```

A new checkpoint never rewrites an old one; recovery signer rotation/revocation
therefore leaves a durable chronological custody trail.


## DEPLOY-PACK-12 — threshold quorum custody

Multi-copy custody checkpoints now declare a threshold quorum. Three copies default to **2-of-3**; five copies default to **3-of-5**.

```bash
deploy-pack recovery trust export-copies recovery-trust.json --copies 3
```

Override explicitly:

```bash
deploy-pack recovery trust export-copies recovery-trust.json --copies 5 --quorum 3
```

Verify a threshold even when a copy is unavailable/corrupt:

```bash
deploy-pack recovery trust verify-quorum \
  recovery-trust.copy-01.json recovery-trust.copy-03.json \
  --public-key recovery-trust.copy-01.json.public-key.json \
  --public-key recovery-trust.copy-03.json.public-key.json
```

The agreeing copies must share checkpoint identity/sequence and recovery-trust SHA-256, and must have unique copy IDs and independent signer fingerprints. `verify-copy-set` remains strict all-supplied-copies verification.


### Quorum custody in `deploy status`

`deploy-pack deploy status` now includes the latest offline custody checkpoint:

```text
Offline custody
  configured : YES
  health     : PASS
  chain      : PASS
  checkpoints: 4
  latest     : 4
  checkpoint : ocp-000004-...
  copies     : 3
  quorum     : 2
  recorded   : 3
  copy IDs   : 3
  signers    : 3
  quorum met : YES
```

JSON output exposes the same state under `offlineCustody`.

A configured custody checkpoint is healthy only when:

- checkpoint-history hash continuity is valid;
- `copyCount >= 2`;
- `1 <= quorum <= copyCount`;
- the latest checkpoint records exactly the declared number of copies;
- at least `quorum` unique custody copy IDs are recorded;
- at least `quorum` independent signer fingerprints are recorded.

Any of those failures changes overall deployment status to `FAIL`, so:

```bash
deploy-pack deploy status --quiet
```

also fails closed in Make/CI.

A repository with no offline custody checkpoint remains healthy and reports:

```text
configured : NO
quorum met : -
```

because absence of optional offline custody is different from corruption of an
already-established custody chain.


### Offline checkpoint staleness

`deploy-pack deploy status` compares the latest offline checkpoint's captured
recovery-trust SHA-256 with the current canonical recovery-trust snapshot.

A current checkpoint reports `freshness: CURRENT` and `stale: NO`. After signer
activation, rotation, revocation, or trust addition, it reports `freshness: STALE`,
`stale: YES`, both snapshot hashes, and a warning. Re-exporting custody copies
creates a new checkpoint and returns freshness to `CURRENT`.

Staleness is a warning, not structural corruption; `deploy status --quiet` keeps
returning zero for a valid-but-stale checkpoint. No checkpoint reports
`UNCONFIGURED`.
\n\n### Stale custody grace period\n\nA stale offline checkpoint is allowed a configurable grace period before it\nbecomes a deployment-status failure. The default is **7 days**.\n\nConfigure per repository in `.deploy-pack.toml`:\n\n```toml\n[deploy-pack]\noffline_custody_stale_grace_days = 7\n```\n\nStatus phases are:\n\n```text\nCURRENT         checkpoint matches current recovery trust\nSTALE_GRACE     trust changed, but refresh grace period remains\nSTALE_OVERDUE   grace expired; deployment status fails\nUNCONFIGURED    no offline custody checkpoint exists\n```\n\nDuring `STALE_GRACE`, normal status shows a warning and `deploy status --quiet`\nstill exits `0`. Once `STALE_OVERDUE`, overall status becomes `FAIL` and quiet\nmode exits nonzero, making the existing Make/CI gate fail closed.\n\nThe stale clock is based on the latest known recovery-trust lifecycle change\n(`trustedAt`, `activatedAt`, `retiredAt`, or `revokedAt`) relative to the latest\ncheckpoint creation time. If a stale age cannot be determined, status warns but\ndoes not invent a deadline; setting the grace to `0` makes any detectable stale\ncheckpoint immediately overdue.\n\nJSON status adds:\n\n```json\n"offlineCustody": {\n  "freshness": "STALE_GRACE",\n  "staleOverdue": false,\n  "staleSince": "...",\n  "staleAgeDays": 1.5,\n  "staleGraceDays": 7,\n  "staleGraceDueAt": "...",\n  "staleGraceRemainingDays": 5.5\n}\n```\n

### Stale recovery-trust cause reporting

`deploy-pack deploy status` now explains **what changed** after the latest
offline custody checkpoint. New checkpoints retain the captured recovery-trust
snapshot in the local checkpoint ledger, allowing an exact structural diff.

Examples include:

```text
stale cause:
  - recovery signer backup-... was added with status 'trusted'
  - recovery signer old-... status changed from 'active' to 'retired'
  - active recovery signer changed from old-... to new-...
  - recovery signer compromised-... status changed from 'active' to 'revoked'
```

JSON exposes `offlineCustody.staleCauses` and
`offlineCustody.staleCauseEvidence`. Cause types include:

```text
active-signer-changed
signer-added
signer-removed
signer-activated
signer-retired
signer-revoked
signer-status-changed
signer-metadata-changed
trust-schema-changed
```

For checkpoints created before this capability, only the captured hash and active
signer were retained. Those checkpoints report `staleCauseEvidence = "partial"`
and never invent an exact historical diff. Creating the next custody checkpoint
upgrades future cause reporting to `exact`.


### Exact stale-custody remediation

When the latest offline checkpoint is stale, `deploy-pack deploy status` now
prints the concrete command required to refresh custody using the checkpoint's
existing copy/quorum policy:

```text
remediation:
  priority : recommended
  action   : refresh-offline-custody-checkpoint
  command  : deploy-pack recovery trust export-copies recovery-trust-refresh.json --copies 3 --quorum 2
  because  : the active recovery signer changed after the latest custody checkpoint
```

Once stale custody exceeds its configured grace period, remediation priority
changes from `recommended` to `required` because the stale condition is already
failing `deploy status --quiet`.

JSON exposes the same data at:

```text
offlineCustody.remediation
```

with `action`, `priority`, `command`, `copyCount`, `quorum`, `causeEvidence`,
`causeTypes`, `reasons`, and `expectedResult`.

For legacy stale checkpoints whose older format cannot reconstruct every exact
trust change, deploy-pack still recommends the safe re-checkpoint command and
marks `causeEvidence` as `partial`.

### Remediation safety classification

When offline custody is stale, `deploy-pack deploy status` classifies the
recommended refresh as either `automatic-safe` or `operator-review-required`.

`automatic-safe` is intentionally narrow: exact checkpoint evidence must show
that the only recovery-trust change was the additive registration of one or
more non-active signers with status `trusted`. The existing custody refresh may
then be automated without silently accepting an active-anchor or lifecycle
change.

`operator-review-required` applies to active-signer changes, activation,
retirement, revocation, signer removal, lifecycle/status changes, signer/key
metadata drift, trust-schema changes, unclassified drift, and legacy/partial
cause evidence.

Human status prints `class` and any `review` reasons. JSON remediation includes
`classification`, `automaticSafe`, `operatorReviewRequired`, and
`operatorReviewReasons` so CI/orchestration can branch without parsing prose.

### HARDEN-13: protected deployment-control artifacts

`deploy-pack 1.3.0` introduces a non-overridable protection layer for
security-sensitive deploy-pack artifacts. These files are filtered before
`.deploy-pack.toml` `include` rules or CLI `--include` rules are evaluated.

Protected artifacts include deploy-pack state (`.deploy-pack-*`), generated
archives and archive sidecars, signed verifiers, browser verifiers, deployment
verification evidence, custody fingerprints, offline custody checkpoint files,
and recognizable deploy-pack public-key/control envelopes.

Secret-bearing signed verifiers and token-bearing browser verifiers are also
recognized by their generated content. This closes the custom-output loophole:
renaming a signed verifier to an ordinary filename such as `verify.php` does
not make it eligible for packaging.

Protected artifacts are also suppressed from remote deletion instructions. A
normal deployment plan can therefore neither upload nor delete deploy-pack
control-plane material as application payload.

Generated secret-bearing verifier permissions are now explicit:

```text
signed Python verifier : 0700
signed PHP verifier    : 0600
browser PHP verifier   : 0600
```

Project policy cannot override this boundary. If deploy-pack control-plane
material must ever be moved intentionally, do so outside the normal pack path
rather than weakening the deployment payload policy.

### HARDEN-14: custody authenticity root

`deploy-pack 1.4.0` makes threshold custody fail closed against replacement keypairs. `verify-quorum` and `verify-copy-set` now count a custody copy only when its `copyId` and signing-key fingerprint were pre-enrolled in the trusted checkpoint commitment.

Repository-local verification uses the validated `.deploy-pack-offline-checkpoints.jsonl` chain automatically. Portable verification must provide both `--trusted-checkpoint <checkpoint.json>` and a separately pinned `--expected-checkpoint-hash <sha256>`; the checkpoint file cannot authenticate itself.

This changes custody public keys from *authority supplied with the evidence* into *verification material checked against prior authority*.


## DEPLOY-PACK-HARDEN-15 — verifier key precommitment

`deploy-pack 1.5.0` removes trust-on-first-use from signed remote evidence. When a signed remote verifier is generated, its ephemeral Ed25519 public-key fingerprint is committed to the issued verifier identity in `.deploy-pack-verifiers.json` as `expectedPublicKeySha256` before any evidence can be ingested. The committed fingerprint is also embedded in the verifier identity carried by the signed evidence.

Ingestion now requires local issuance state, embedded verifier identity, and supplied public-key fingerprint to agree before signature verification is normalized or the signer is registered in key history. A valid signature from a replacement key is rejected. A verifier identity cannot be rebound to a second signing key; issue a new verifier identity for rotation.

Existing identities issued by earlier releases may be used to generate a new 1.5.0 signed verifier, which establishes the commitment. Signed verifier artifacts generated before HARDEN-15 must be regenerated because their keys were never precommitted. See `docs/DEPLOY-PACK-HARDEN-15.md`.


## DEPLOY-PACK-HARDEN-16 — transactional state (1.6.0)

`deploy-pack 1.6.0` serializes control-state mutations with a repository-wide advisory lock and replaces direct state writes with fsync-backed atomic replacement. `mark` is now a recoverable transaction over baseline, deployment history, and replay consumption: before-images are journaled before mutation, exceptions restore them immediately, and an interrupted process leaves a recovery-pending journal that makes `deploy status` fail until the next mutating operation safely restores the prior state. See `docs/DEPLOY-PACK-HARDEN-16.md`.

## Native deployment-ledger anchoring (1.7.0)

Deployment history is now a native SHA-256 hash chain (`previousRecordHash` → `recordHash`). New offline custody checkpoints commit to the ledger head and embed that commitment in every signed custody copy. `deploy-pack deploy status` distinguishes `CURRENT`, `ADVANCED`, `UNANCHORED`, and failing `MISMATCH`/`INVALID` ledger-anchor states. See `docs/DEPLOY-PACK-HARDEN-17.md`.

## Trust/path containment closure (1.8.0)

`DEPLOY-PACK-HARDEN-18` closes the remaining recovery-trust and deployment-path invariants. Recovery trust now permits at most one `active` signer and requires it to match `activeSigner`. Manifest and ZIP paths are canonical repository-relative POSIX paths; absolute, parent-traversal, drive-prefixed, backslash-ambiguous, duplicate, or deploy-and-delete-conflicting paths fail closed. Extracted-tree and generated remote verifiers refuse paths that traverse a symlink parent, and deployment symlinks must resolve within the deployment root. Safe internal relative symlinks remain supported.

## DEPLOY-PACK-ASSURANCE-01 — evidence assurance taxonomy

`deploy-pack 1.9.0` makes the verification authority explicit. Use:

```bash
deploy-pack assurance show
deploy-pack assurance explain host-cooperative-remote
```

Current evidence levels are `local` and `host-cooperative-remote`. The levels
`independent-observer` and `platform-attested` are reserved for future producers with a genuinely
independent trust root.

**A signed remote verifier is not hostile-host attestation.** Ed25519 signing with a precommitted
verifier key protects the evidence record after signing; the verifier still executes on, and reports
state from, the target host. Signed and unsigned remote verifier results are therefore classified
`host-cooperative-remote`, with machine-readable `claims` and `doesNotClaim` fields. Evidence that
tries to self-promote to an unsupported assurance authority is rejected during ingestion.

See `docs/DEPLOY-PACK-ASSURANCE-01.md` for the full trust model.

## Frozen architecture baseline

`deploy-pack 1.9.1` is the **DEPLOY-PACK-FREEZE-01** architecture/security baseline. Run `make freeze-check` before release or after security-sensitive maintenance. Architecture expansion is closed; post-freeze work should be defects, security/compatibility/dependency maintenance, or needs demonstrated by real deployments. See `docs/freezes/deploy-pack/DEPLOY-PACK-FREEZE-01.md`.


## DEPLOY-PACK-ARTIFACT-01 — built artifact packaging

`artifact` packages an already-built deployment directory without consulting Git. The source directory is authoritative; its contents are placed directly at archive root and Git/change-set exclusions are not applied. `.DS_Store` is the only default junk omission.

```bash
deploy-pack artifact --source dist --format zip --output site-deploy.zip
deploy-pack artifact --source build/shared-hosting --format zip --output deploy.zip --require index.html --require .htaccess
deploy-pack artifact --source dist --format tar.gz --output site-deploy.tar.gz
```

Output inside source and escaping symlinks are rejected. Dotfiles and empty directories are preserved.
