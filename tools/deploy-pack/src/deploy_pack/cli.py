from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .signed import write_signed_remote_verifier, ingest_signed_remote_evidence
from .lifecycle import issue as issue_verifier, get as get_verifier, revoke as revoke_verifier, validate as validate_verifier, assert_usable as assert_signed_evidence_usable, consume as consume_signed_evidence
from .keyring import load as load_keyring, register as register_signer, revoke as revoke_signer, export_bundle as export_recovery_bundle, import_bundle as import_recovery_bundle, verify_bundle as verify_recovery_bundle, trust_recovery_signer, revoke_recovery_signer, load_recovery_trust, verify_bundle_trusted as verify_recovery_bundle_trusted, import_bundle_trusted as import_recovery_bundle_trusted, export_offline_trust_anchor, verify_offline_trust_anchor, export_offline_trust_copies, verify_offline_trust_copy_set, verify_offline_trust_quorum, load_offline_checkpoints, verify_offline_checkpoint_history, offline_checkpoint_record
from .state import repository_lock, begin_mark_transaction, commit_mark_transaction, recover_mark_transaction, atomic_write_text
from .assurance import taxonomy as assurance_taxonomy, ASSURANCE_LEVELS
from .artifact import build_artifact_plan, write_artifact, result_dict as artifact_result_dict
from .gitignore_managed import GitignoreManagedError, install_managed_gitignore, run_gitignore_command
from .core import (
    BASELINE_FILE, CONFIG_FILE, DeployPackError, build_plan, load_project_policy, read_baseline, repo_root, resolve_ref,
    verify_archive, verify_extracted_tree, write_baseline, write_package,
    write_remote_verifier, write_verification_evidence, validate_mark_evidence, ingest_remote_evidence,
    append_deployment_history, read_deployment_history, verify_deployment_history,
    deployment_history_record, format_deployment_history_record,
    build_rollback_plan,
    write_rollback_package,
    evidence_is_signed_remote,
    deployment_status,
)
from .core import validate_baseline_reconciliation

# BEGIN DEPLOY-PACK-HELP-COLOR-ALIAS-01
_ANSI_RESET = "\033[0m"
_ANSI_BOLD = "\033[1m"
_ANSI_CYAN = "\033[36m"
_ANSI_GREEN = "\033[32m"
_ANSI_YELLOW = "\033[33m"
_ANSI_DIM = "\033[2m"


def _help_color_enabled() -> bool:
    """Return whether human help should contain ANSI styling.

    NO_COLOR always disables color. DEPLOY_PACK_COLOR accepts auto/always/never;
    auto styles only an interactive stdout with a non-dumb terminal.
    """
    if os.environ.get("NO_COLOR") is not None:
        return False
    mode = os.environ.get("DEPLOY_PACK_COLOR", "auto").strip().lower()
    if mode in {"never", "0", "false", "no", "off"}:
        return False
    if mode in {"always", "1", "true", "yes", "on"}:
        return True
    if mode not in {"", "auto"}:
        # Fail soft for help rendering: unknown values behave like auto.
        mode = "auto"
    return bool(getattr(sys.stdout, "isatty", lambda: False)()) and os.environ.get("TERM", "") != "dumb"


def _paint(text: str, *codes: str) -> str:
    if not _help_color_enabled() or not text:
        return text
    return "".join(codes) + text + _ANSI_RESET


class DeployPackHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Readable, TTY-aware help styling without contaminating redirected output."""

    def start_section(self, heading):
        super().start_section(_paint(heading, _ANSI_BOLD, _ANSI_CYAN))

    def _format_action_invocation(self, action):
        rendered = super()._format_action_invocation(action)
        if not _help_color_enabled():
            return rendered
        if action.option_strings:
            return _paint(rendered, _ANSI_GREEN)
        return _paint(rendered, _ANSI_YELLOW)

    def _format_text(self, text):
        rendered = super()._format_text(text)
        if not _help_color_enabled():
            return rendered
        lines=[]
        for line in rendered.splitlines(keepends=True):
            bare=line.rstrip("\r\n")
            ending=line[len(bare):]
            if re.fullmatch(r"[A-Z][A-Z0-9 /&_.+-]{2,}", bare.strip()):
                indent=bare[:len(bare)-len(bare.lstrip())]
                title=bare.strip()
                line=indent + _paint(title, _ANSI_BOLD, _ANSI_CYAN) + ending
            lines.append(line)
        return "".join(lines)


def _apply_help_formatter_tree(parser_obj):
    """Apply deploy-pack help rendering to every nested argparse parser.

    Python 3.14+ argparse has its own ANSI color layer enabled by default. Disable
    that layer explicitly so DEPLOY_PACK_COLOR/NO_COLOR remain the single source
    of truth across every supported Python version. DeployPackHelpFormatter then
    adds our styling only when _help_color_enabled() permits it.
    """
    parser_obj.formatter_class = DeployPackHelpFormatter
    # argparse <= 3.13 does not consume this attribute; assigning it is harmless.
    # argparse 3.14+ does consume it and would otherwise emit ANSI independently.
    parser_obj.color = False
    for action in getattr(parser_obj, "_actions", ()):
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                _apply_help_formatter_tree(child)


def _program_name() -> str:
    invoked = Path(sys.argv[0]).name
    return invoked if invoked in {"deploy-pack", "dp"} else "deploy-pack"
# END DEPLOY-PACK-HELP-COLOR-ALIAS-01

DEPLOY_PACK_DESCRIPTION = """Production deployment packaging, verification, and deployment-state governance.

Supports two deliberately distinct packaging workflows:
  • Git-aware change-set packaging from a repository deployment baseline.
  • Git-independent packaging of an already-built deployment artifact directory.
"""

DEPLOY_PACK_HELP_EPILOG = """COMMAND GROUPS
  Packaging
    init                      Create a conservative deployment allowlist config.
    pack                      Package a Git-selected deployment change set.
    artifact                  Package an already-built deployment directory.
    inspect                   Preview Git-selected deployable/excluded files.
    gitignore                  Manage generated-artifact ignore rules.

  Deployment state
    baseline                  Show the recorded production baseline.
    mark                      Record a verified deployment as deployed.
    reconcile-baseline        Correct a recorded production baseline with fresh signed evidence.
    deploy status             Report deployment/recovery health.

  Verification
    verify                    Verify an archive or extracted deployment tree.
    remote-verifier           Generate a temporary remote verifier.
    ingest-remote-evidence    Bind unsigned remote verification evidence.
    ingest-signed-remote-evidence
                              Bind signed remote verification evidence.

  History and rollback
    history                   Show deployment ledger records.
    history-verify            Verify deployment-ledger integrity.
    rollback plan             Prepare a rollback package.
    rollback diff             Report a rollback delta without packaging.

  Trust and recovery
    assurance                 Explain evidence-assurance semantics.
    verifier                  Manage verifier identities.
    keys                      Inspect/revoke evidence signing keys.
    recovery                  Recovery bundles, trust anchors, and custody.

EXAMPLES
  deploy-pack artifact --source dist --format zip --output site-deploy.zip
  deploy-pack artifact --source build/shared-hosting --format tar.gz --output deploy.tar.gz
  deploy-pack pack --output deploy.zip
  deploy-pack deploy status
  deploy-pack history --limit 10

GIT-AWARE CLOSEOUT
  After deploying a `pack` archive, use signed remote verification, ingest the
  resulting evidence, then `mark` the deployed ref. Run `deploy-pack mark --help`.

ROUTINE GIT-AWARE CLOSEOUT
  make deploy-help              Show the condensed two-phase workflow.
  make deploy-prepare           Prepare archive + signed production verifier.
  make deploy-closeout EVIDENCE=/path/to/signed-evidence.json
                                Ingest, mark, and run all post-closeout gates.

BASELINE CORRECTION
  `reconcile-baseline` is only for an already-correct production tree whose recorded
  Git baseline is wrong. It still requires fresh signed remote evidence.

SHORT COMMAND
  dp                        Exact short alias for deploy-pack.
  Example: dp deploy status

HELP COLOR
  Interactive help is colored automatically. Redirected/piped help stays plain.
  NO_COLOR=1 disables styling. DEPLOY_PACK_COLOR=always|never|auto overrides mode.

Run `deploy-pack <command> --help` for command-specific options and examples.
"""

ARTIFACT_HELP = """Package an already-built deployment-ready directory without consulting Git.

The source directory is authoritative. Its selected contents are placed directly at archive root;
the source directory name itself is never added as a prefix. Artifact mode applies its hard hygiene
layer plus optional [artifact].include/[artifact].exclude policy from .deploy-pack.toml.
Git-aware [pack] policy and .gitignore are not consulted.
"""

ARTIFACT_EPILOG = """EXAMPLES
  Static-site build:
    deploy-pack artifact --source dist --format zip --output site-deploy.zip

  Shared-hosting build with required-path assertions:
    deploy-pack artifact --source build/shared-hosting --format zip --output deploy.zip --require index.html --require .htaccess

  TAR.GZ:
    deploy-pack artifact --source dist --format tar.gz --output site-deploy.tar.gz

SAFETY
  • --source must exist and be a directory.
  • Archive members are clean artifact-root-relative POSIX paths.
  • Output inside --source is rejected to prevent archive self-inclusion.
  • Symlinks that escape the artifact root are rejected.
  • --require is repeatable and validated before archive creation.
  • Artifact mode does not consult Git state.
"""


PACK_HELP = """Package Git changes only when they are inside the repository's declared deployment surface.

Git answers "what changed?". `.deploy-pack.toml` answers "what may deploy?".
New repositories fail closed until `deploy-pack init` creates a policy and the
operator reviews its explicit [pack].include allowlist.

In allowlist mode, --include is a narrowing filter; it cannot expand the project allowlist.
"""

PACK_EPILOG = """SELECTION
  Hard denied even if allowlisted:
    deploy-pack control/state artifacts, .env/.env.*, Git internals, conventional tests/snapshots.

  Require explicit [pack].include:
    application files, scripts, package/build metadata, hidden paths other than ordinary .htaccess.

EXAMPLES
  Initialize a repository policy:
    deploy-pack init

  Review selection before packaging:
    deploy-pack inspect HEAD~1

  Package from the recorded deployment baseline:
    deploy-pack pack --output deploy.zip
"""

GIT_AWARE_CLOSEOUT_HELP = """Record a verified Git-aware deployment as the production baseline.

`mark` is the final closeout step for a deployment created with `deploy-pack pack`.
Its --evidence input is deploy-pack verification evidence for the exact deployed archive;
it is not the ZIP itself and not an operator-written note.

A successful mark records the deployed revision in deployment history and advances the
production baseline used by the next `deploy-pack inspect` / `deploy-pack pack`.
"""

GIT_AWARE_CLOSEOUT_EPILOG = """GIT-AWARE DEPLOYMENT CLOSEOUT

If the archive has already been deployed, continue with that exact archive:

  1. Verify the archive locally.
       deploy-pack verify --help

  2. Generate a signed remote verifier bound to that archive.
       deploy-pack remote-verifier --help

  3. Run the generated verifier against the production deployment.
     Bring the signed verifier output/evidence back to this repository.

  4. Ingest and bind the signed remote evidence to the exact local archive.
       deploy-pack ingest-signed-remote-evidence --help

  5. Close the deployment using the normalized evidence produced by ingestion.
       deploy-pack mark <deployed-ref> --evidence <evidence.json>

  6. Confirm deployment state.
       deploy-pack baseline
       deploy-pack history --limit 5
       deploy-pack history-verify
       deploy-pack deploy status

DONE MEANS
  • baseline resolves to the Git revision now running in production;
  • deployment history contains the new record;
  • history verification passes;
  • deployment status is healthy.

The next Git-aware inspect/pack then compares from this recorded production baseline.

Do not use --unsafe-no-evidence as the normal closeout path. It is an explicit
bootstrap/recovery escape hatch, not a substitute for production verification.

EVIDENCE ASSURANCE
Signed remote verification proves host-cooperative remote verification and archive/evidence
provenance. It is not hostile-host or hardware attestation.

Run the referenced subcommand --help pages for their exact file/format options.
"""



def _decorate_help(parser_obj, *, description=None, epilog=None):
    if parser_obj is None:
        return
    if description:
        parser_obj.description = description
    if epilog:
        parser_obj.epilog = epilog
        parser_obj.formatter_class = DeployPackHelpFormatter


def _argument_help(parser_obj, dest, text):
    if parser_obj is None:
        return
    for action in parser_obj._actions:
        if action.dest == dest:
            action.help = text
            return


def _choice(subparsers_action, name):
    return subparsers_action.choices.get(name) if subparsers_action is not None else None


def _choice_help(subparsers_action, name, text):
    if subparsers_action is None:
        return
    for action in getattr(subparsers_action, "_choices_actions", ()):
        if getattr(action, "dest", None) == name:
            action.help = text
            return


def _apply_complete_help_surface(sub, asub, deploy_sub, rollback_sub, vsub, ksub, rsub, rtrustsub):
    # Top-level Git selection and deployment state.
    inspect = _choice(sub, "inspect")
    _decorate_help(inspect,
        description="Preview the exact Git-aware deployment surface without creating an archive. Shows deployable changes, excluded changes with reasons, and remote deletions relative to the selected production baseline.",
        epilog="""EXAMPLES
  deploy-pack inspect
  deploy-pack inspect HEAD~1
  deploy-pack inspect --committed-only

SELECTION
  The project [pack].include allowlist is the deployment ceiling. --include and --ignore can only narrow it. Use this command before pack when changing deployment policy.""")
    _argument_help(inspect, "baseline", "Git revision representing current production; defaults to the recorded deployment baseline.")
    _argument_help(inspect, "ignore", "Additional narrowing exclusion glob; repeatable.")
    _argument_help(inspect, "include", "Additional narrowing inclusion glob; repeatable and cannot expand project policy.")
    _argument_help(inspect, "committed_only", "Ignore working-tree changes and inspect committed Git changes only.")

    baseline = _choice(sub, "baseline")
    _decorate_help(baseline,
        description="Show the recorded production Git baseline used by Git-aware inspect and pack operations.",
        epilog="""The baseline must represent the Git commit whose deployable bytes are currently running in production. It may legitimately be behind local HEAD. Do not edit .deploy-pack-baseline manually; use verified mark/reconciliation workflows.""")

    mark = _choice(sub, "mark")
    if mark is not None:
        # Preserve the detailed closeout help already installed by GIT-CLOSEOUT-HELP-01.
        _argument_help(mark, "ref", "Git ref/commit that exactly represents the deployed production bytes. Default: HEAD.")
        _argument_help(mark, "evidence", "Normalized deploy-pack verification evidence for the exact deployed archive.")
        _argument_help(mark, "archive", "Exact local archive to which the evidence is bound; strongly recommended/required for normal signed closeout.")
        _argument_help(mark, "unsafe_no_evidence", "Emergency/bootstrap escape hatch. Record without verification evidence; never use for routine closeout.")

    reconcile = _choice(sub, "reconcile-baseline")
    if reconcile is not None:
        _decorate_help(reconcile,
            description="Correct a previously recorded production baseline when production bytes are already correct but the recorded Git ref is wrong. This is an audited reconciliation, not another deployment and not an evidence bypass.",
            epilog="""REQUIRES
  • a fresh correction archive whose manifest headCommit is the intended target ref;
  • fresh signed remote evidence proving production matches that exact archive;
  • a non-empty operator reason;
  • a target that satisfies deploy-pack's reconciliation ancestry rules.

EXAMPLE
  deploy-pack reconcile-baseline 444b22c \\
    --archive ../deploy-pack-closeout-444b22c/baseline-correction-444b22c.zip \\
    --evidence ../deploy-pack-closeout-444b22c/baseline-correction-444b22c-evidence.json \\
    --reason "Earlier closeout recorded the pre-commit ref; production matches 444b22c."

Do not use this when a normal verified `mark` is sufficient.""")

    deploy = _choice(sub, "deploy")
    _decorate_help(deploy,
        description="Deployment-state health operations. Use `deploy status` after closeout, recovery, signer rotation, or custody changes.",
        epilog="Run `deploy-pack deploy status --help` for health semantics and machine-readable/quiet modes.")
    _choice_help(deploy_sub, "status", "Evaluate baseline, ledger, verifier/replay, recovery, custody, and transaction health.")
    status = _choice(deploy_sub, "status")
    _decorate_help(status,
        description="Evaluate the current deploy-pack operational state and report whether deployment governance is healthy.",
        epilog="""USE AFTER
  deploy-pack mark ...
  deploy-pack reconcile-baseline ...
  recovery/trust changes
  offline-custody checkpoint updates

Exit status is suitable for CI/operator gates. Use --json for structured output and --quiet when only the exit code matters.""")
    _argument_help(status, "json", "Emit structured machine-readable deployment status.")
    _argument_help(status, "quiet", "Suppress normal output; communicate health through the process exit code.")

    history = _choice(sub, "history")
    _decorate_help(history,
        description="Read the append-only deployment ledger, including forward deployments, rollbacks, and audited reconciliations.",
        epilog="""EXAMPLES
  deploy-pack history
  deploy-pack history --limit 5
  deploy-pack history --json

Use `deploy-pack history-verify` to verify the ledger/hash-chain integrity rather than merely displaying records.""")
    _argument_help(history, "limit", "Show only the most recent N deployment records.")
    _argument_help(history, "json", "Emit deployment history as machine-readable JSON.")

    hv = _choice(sub, "history-verify")
    _decorate_help(hv,
        description="Verify deployment-ledger integrity, including record linkage/hash-chain invariants and supported custody anchoring checks.",
        epilog="A successful display of history is not equivalent to verification. Run this as a post-closeout gate and before relying on rollback ancestry.")

    # Verification and evidence lifecycle.
    verify = _choice(sub, "verify")
    _decorate_help(verify,
        description="Verify a deploy-pack archive, and optionally compare its manifest against an extracted deployment tree.",
        epilog="""EXAMPLES
  deploy-pack verify deploy.zip
  deploy-pack verify deploy.zip --root /path/to/extracted/tree
  deploy-pack verify deploy.zip --evidence-out verify-evidence.json

`verify` is local verification. For production verification, generate a remote verifier with `deploy-pack remote-verifier`.""")
    _argument_help(verify, "archive", "Deploy-pack archive whose embedded manifest/content will be verified.")
    _argument_help(verify, "checksum", "Expected archive checksum, when independently supplied.")
    _argument_help(verify, "root", "Extracted deployment root to compare against the archive manifest.")
    _argument_help(verify, "evidence_out", "Write durable local verification evidence JSON to this path.")

    rv = _choice(sub, "remote-verifier")
    _decorate_help(rv,
        description="Generate a temporary verifier bound to one exact deploy-pack archive for execution against a remote/production deployment tree.",
        epilog="""SIGNED WORKFLOW
  1. deploy-pack verifier issue --ttl-minutes 60
  2. deploy-pack remote-verifier deploy.zip --language php --sign \\
       --verifier-id <issued-id> --output deploy.verify-signed.php
  3. Upload/run only the generated verifier against production.
  4. Bring its signed evidence JSON back locally.
  5. deploy-pack ingest-signed-remote-evidence ...

OUTPUTS
  --sign also creates <verifier>.public-key.json. That sidecar is the --public-key input for signed-evidence ingestion.

SECURITY
  Signed remote verification is host-cooperative evidence. Remove the generated signed verifier from production after evidence retrieval; it contains ephemeral signing material.""")
    _argument_help(rv, "archive", "Exact local deploy-pack archive whose manifest the generated verifier will enforce.")
    _argument_help(rv, "language", "Generated verifier runtime: php (default) or python.")
    _argument_help(rv, "output", "Generated verifier path. Keep it outside the application deployment surface when practical.")
    _argument_help(rv, "sign", "Generate an ephemeral Ed25519 signing verifier and public-key sidecar.")
    _argument_help(rv, "verifier_id", "Active verifier identity returned by `deploy-pack verifier issue`; required with --sign.")

    ingest = _choice(sub, "ingest-remote-evidence")
    _decorate_help(ingest,
        description="Validate unsigned remote verifier output and bind it to the exact local archive, producing normalized deploy-pack evidence.",
        epilog="Unsigned remote evidence is a legacy/compatibility path. Prefer `ingest-signed-remote-evidence` for routine production closeout.")
    _argument_help(ingest, "remote_evidence", "Remote verifier evidence JSON returned from the deployment host.")
    _argument_help(ingest, "archive", "Exact local archive the remote evidence must match.")
    _argument_help(ingest, "output", "Write normalized evidence JSON to this path.")

    singest = _choice(sub, "ingest-signed-remote-evidence")
    _decorate_help(singest,
        description="Verify signed remote evidence, verifier identity/signature/replay bindings, and archive identity; emit normalized evidence suitable for `mark` or reconciliation.",
        epilog="""EXAMPLE
  deploy-pack ingest-signed-remote-evidence \\
    deploy.signed-evidence.json deploy.zip \\
    --public-key deploy.verify-signed.php.public-key.json \\
    --output deploy.normalized-evidence.json

The public-key file is generated beside a signed remote verifier. Do not substitute an unrelated key.""")
    _argument_help(singest, "signed_remote_evidence", "Signed JSON emitted by the generated production verifier.")
    _argument_help(singest, "archive", "Exact local archive whose identity must match the signed evidence.")
    _argument_help(singest, "public_key", "Public-key sidecar generated with the signed verifier.")
    _argument_help(singest, "output", "Write normalized signed evidence JSON here.")

    # Verifier identities and signing keys.
    verifier = _choice(sub, "verifier")
    _decorate_help(verifier,
        description="Manage short-lived verifier identities used to precommit the signing key/nonce/expiry for signed remote verification.",
        epilog="Typical flow: `verifier issue` → `remote-verifier --sign --verifier-id ...` → production verification → signed evidence ingestion. Identities are intentionally short-lived.")
    _choice_help(vsub, "issue", "Issue a short-lived verifier identity for a new signed remote verification cycle.")
    _choice_help(vsub, "show", "Show verifier identity, key commitment, expiry, nonce, and revocation state.")
    _choice_help(vsub, "revoke", "Revoke a verifier identity so it can no longer be accepted.")
    vi=_choice(vsub,"issue"); vs=_choice(vsub,"show"); vr=_choice(vsub,"revoke")
    _decorate_help(vi, description="Issue a fresh verifier identity. The returned ID must be supplied to `remote-verifier --sign`.")
    _argument_help(vi,"ttl_minutes","Verifier lifetime in minutes. Default: 30.")
    _decorate_help(vs, description="Display one verifier identity and its lifecycle/key-precommitment state.")
    _argument_help(vs,"verifier_id","Verifier ID returned by `deploy-pack verifier issue`.")
    _decorate_help(vr, description="Revoke one verifier identity immediately.", epilog="Revocation is appropriate when a generated verifier or its signing material may have been exposed before a verification cycle completed.")
    _argument_help(vr,"verifier_id","Verifier ID to revoke.")

    keys=_choice(sub,"keys")
    _decorate_help(keys, description="Inspect or revoke registered evidence-signing keys used by deploy-pack trust/evidence workflows.")
    _choice_help(ksub,"show","Show registered signing keys and revocation state.")
    _choice_help(ksub,"revoke","Revoke a signing-key fingerprint with an auditable reason.")
    krev=_choice(ksub,"revoke")
    _argument_help(krev,"fingerprint","Signing-key fingerprint to revoke.")
    _argument_help(krev,"reason","Required audit reason for the revocation.")

    # Assurance taxonomy.
    assurance=_choice(sub,"assurance")
    _decorate_help(assurance,
        description="Explain what deploy-pack evidence can and cannot prove. This distinguishes archive integrity, host-cooperative signed verification, and reserved stronger assurance models.",
        epilog="Signed remote evidence protects provenance and post-signing integrity; it does not prove that a hostile/compromised host reported truthfully.")
    _choice_help(asub,"show","List all evidence assurance levels and support status.")
    _choice_help(asub,"explain","Explain claims and non-claims for one assurance level.")
    ashow=_choice(asub,"show"); aex=_choice(asub,"explain")
    _argument_help(ashow,"json","Emit the assurance taxonomy as JSON.")
    _argument_help(aex,"level","Assurance level to explain.")
    _argument_help(aex,"json","Emit the selected assurance definition as JSON.")

    # Rollback.
    rollback=_choice(sub,"rollback")
    _decorate_help(rollback,
        description="Plan and inspect rollbacks using deployment history. Planning does not itself mutate the production baseline.",
        epilog="Use `rollback diff` first when you only need to understand the delta. Use `rollback plan` when you need the rollback package/artifacts.")
    _choice_help(rollback_sub,"plan","Build/preview a rollback package from current production to a historical deployment record.")
    _choice_help(rollback_sub,"diff","Show rollback additions/changes/deletions without creating an archive.")
    rp=_choice(rollback_sub,"plan"); rd=_choice(rollback_sub,"diff")
    _decorate_help(rp, description="Prepare a rollback deployment package targeting a historical deployment record. Does not advance the baseline by itself.", epilog="After deploying a rollback package, verify production and close it through the normal evidence/mark path so history records the rollback explicitly.")
    _argument_help(rp,"record","Historical deployment record to roll back to.")
    _argument_help(rp,"from_record","Hypothetical historical source record; otherwise use actual current production.")
    _argument_help(rp,"output","Output rollback archive path.")
    _argument_help(rp,"dry_run","Plan only; do not write the rollback package.")
    _decorate_help(rd, description="Report the file-level rollback delta between actual/current production (or --from history record) and a historical target.")

    # Recovery/trust/custody: previously almost entirely undocumented in --help.
    recovery=_choice(sub,"recovery")
    _decorate_help(recovery,
        description="Create, validate, import, and govern recovery material; manage recovery signer trust and offline custody/checkpoints.",
        epilog="""SAFETY
  Recovery/trust operations can change which recovery material is accepted. Inspect the exact subcommand help before use, retain offline custody copies, and run `deploy-pack deploy status` afterward.

GROUPS
  export / verify / import       Recovery bundles
  trust add/show/revoke          Recovery-signer trust
  trust export-offline/...       Offline trust-anchor custody
  trust checkpoint-*             Custody checkpoint history/integrity""")

    for name,text in {
        "export":"Export a recovery bundle; signed by default.",
        "verify":"Verify a recovery bundle with the supplied public key.",
        "import":"Import validated recovery material; unsigned import requires an explicit override.",
        "trust":"Manage recovery signer trust, offline custody copies, quorum, and checkpoints.",
    }.items(): _choice_help(rsub,name,text)
    rex=_choice(rsub,"export"); rver=_choice(rsub,"verify"); rim=_choice(rsub,"import"); trust=_choice(rsub,"trust")
    _decorate_help(rex, description="Export current recovery state as a portable recovery bundle.", epilog="Signed export is the normal path. --unsigned exists for explicitly controlled compatibility/recovery scenarios.")
    _argument_help(rex,"output","Recovery bundle output path."); _argument_help(rex,"unsigned","Export without a signature; weaker and not the routine path.")
    _decorate_help(rver, description="Cryptographically verify a recovery bundle without importing it.")
    _argument_help(rver,"bundle","Recovery bundle to verify."); _argument_help(rver,"public_key","Public key expected to verify the bundle signature.")
    _decorate_help(rim, description="Import recovery material after validating its signature/trust requirements.", epilog="Prefer signed/trusted recovery imports. --allow-unsigned is an explicit weakening override.")
    _argument_help(rim,"bundle","Recovery bundle to import."); _argument_help(rim,"public_key","Public key used to verify the bundle when required."); _argument_help(rim,"allow_unsigned","Explicitly permit an unsigned recovery bundle.")
    _decorate_help(trust, description="Manage accepted recovery signers plus offline trust-anchor custody and checkpoint verification.")

    trust_help={
      "add":"Add/trust a recovery signer; optionally activate it and record predecessor/reason metadata.",
      "show":"Show trusted/revoked recovery signer state.",
      "revoke":"Revoke a trusted recovery signer with a required reason.",
      "export-offline":"Export a signed offline trust anchor/checkpoint for separate custody.",
      "verify-offline":"Verify one offline trust anchor against a public key and optional expected fingerprint.",
      "export-copies":"Create multiple custody copies and encode the required quorum.",
      "verify-copy-set":"Verify a set of offline custody copies and checkpoint expectations.",
      "verify-quorum":"Verify that supplied offline custody material satisfies a quorum.",
      "checkpoint-history":"Show offline custody checkpoint history.",
      "checkpoint-show":"Show one checkpoint record.",
      "checkpoint-verify":"Verify offline checkpoint history/integrity.",
    }
    for name,text in trust_help.items(): _choice_help(rtrustsub,name,text)
    for name in trust_help:
        _decorate_help(_choice(rtrustsub,name), description=trust_help[name])
    add=_choice(rtrustsub,"add"); revoke=_choice(rtrustsub,"revoke"); off=_choice(rtrustsub,"export-offline"); voff=_choice(rtrustsub,"verify-offline")
    _argument_help(add,"public_key","Recovery signer public-key file to trust."); _argument_help(add,"activate","Make this signer active after trust registration."); _argument_help(add,"predecessor","Expected predecessor signer identifier for controlled rotation."); _argument_help(add,"reason","Audit reason for trust addition/rotation.")
    _argument_help(revoke,"signer_id","Trusted recovery signer identifier to revoke."); _argument_help(revoke,"reason","Required audit reason for revocation.")
    _argument_help(off,"output","Offline trust-anchor output path.")
    _argument_help(voff,"anchor","Offline trust-anchor file to verify."); _argument_help(voff,"public_key","Public key used to verify the anchor."); _argument_help(voff,"expected_fingerprint","Require this signer/key fingerprint.")


def parser():
    p = argparse.ArgumentParser(
        prog=_program_name(),
        description=DEPLOY_PACK_DESCRIPTION,
        epilog=DEPLOY_PACK_HELP_EPILOG,
        formatter_class=DeployPackHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", title="commands", metavar="<command>")

    init = sub.add_parser(
        "init",
        help="Create a conservative .deploy-pack.toml allowlist policy.",
        description=(
            "Create a fail-closed Git-aware deployment selection policy. "
            "The generated include list is intentionally conservative and must be reviewed."
        ),
    )
    init.add_argument("--force", action="store_true", help="Replace an existing .deploy-pack.toml.")

    gitignore_cmd = sub.add_parser(
        "gitignore",
        help="Manage deploy-pack generated-artifact rules in the repository .gitignore.",
        description=(
            "Install, inspect, or remove deploy-pack's bounded managed .gitignore block. "
            "Project-authored rules outside the block are preserved."
        ),
    )
    gitignore_sub = gitignore_cmd.add_subparsers(dest="gitignore_command")
    gitignore_status = gitignore_sub.add_parser(
        "status",
        help="Report whether the managed deploy-pack .gitignore block is current.",
    )
    gitignore_status.add_argument(
        "--check",
        action="store_true",
        help="Exit nonzero when the managed block is missing/stale or durable state is ignored.",
    )
    gitignore_sub.add_parser(
        "install",
        help="Install or reconcile the managed block at the end of .gitignore.",
    )
    gitignore_sub.add_parser(
        "remove",
        help="Remove only the deploy-pack managed block.",
    )

    pack = sub.add_parser(
        "pack",
        help="Package a Git-selected deployment change set.",
        description=PACK_HELP,
        epilog=PACK_EPILOG,
        formatter_class=DeployPackHelpFormatter,
    )
    pack.add_argument("baseline", nargs="?", help="Git revision representing current production; defaults to recorded baseline.")
    pack.add_argument("-o", "--output")
    pack.add_argument("--ignore", action="append", default=[])
    pack.add_argument("--include", action="append", default=[])
    pack.add_argument("--committed-only", action="store_true")
    pack.add_argument("--dry-run", action="store_true")

    artifact = sub.add_parser(
        "artifact",
        help="Package an already-built deployment directory without consulting Git.",
        description=ARTIFACT_HELP,
        epilog=ARTIFACT_EPILOG,
        formatter_class=DeployPackHelpFormatter,
    )
    artifact.add_argument("--source", required=True, help="Deployment-ready source directory.")
    artifact.add_argument("--format", choices=["zip", "tar.gz"], default="zip", help="Archive format. Default: zip.")
    artifact.add_argument("-o", "--output", required=True, help="Output archive path.")
    artifact.add_argument(
        "--require", action="append", default=[], metavar="PATH",
        help="Require a canonical artifact-root-relative path; repeatable.",
    )
    artifact.add_argument("--json", action="store_true", help="Emit the stable machine-readable result instead of human output.")

    inspect = sub.add_parser("inspect", help="Preview deployable and excluded Git changes with reasons.")
    inspect.add_argument("baseline", nargs="?")
    inspect.add_argument("--ignore", action="append", default=[])
    inspect.add_argument("--include", action="append", default=[])
    inspect.add_argument("--committed-only", action="store_true")

    mark = sub.add_parser("mark", help="Record a verified deployment baseline/history entry.")
    mark.description = GIT_AWARE_CLOSEOUT_HELP
    mark.epilog = GIT_AWARE_CLOSEOUT_EPILOG
    mark.formatter_class = DeployPackHelpFormatter
    mark.add_argument("ref", nargs="?", default="HEAD")
    mark.add_argument("--evidence")
    mark.add_argument("--archive")
    mark.add_argument("--unsafe-no-evidence", action="store_true")
    mark.add_argument(
        "--allow-unsigned-evidence",
        action="store_true",
        help=(
            "Compatibility override for legacy unsigned deployment evidence. "
            "Signed remote evidence is required by default."
        ),
    )
    mark.add_argument(
        "--rollback-to",
        type=int,
        help="Record this mark as an intentional rollback to the given history record.",
    )

    reconcile = sub.add_parser(
        "reconcile-baseline",
        help="Correct a recorded production baseline using fresh target-bound signed evidence.",
    )
    reconcile.add_argument("ref", help="Git ref/commit that exactly represents current production bytes.")
    reconcile.add_argument("--archive", required=True, help="Correction archive whose manifest headCommit resolves to REF.")
    reconcile.add_argument("--evidence", required=True, help="Fresh normalized signed remote evidence for --archive.")
    reconcile.add_argument("--reason", required=True, help="Operator/audit reason for correcting the recorded baseline.")

    assurance = sub.add_parser("assurance", help="Show the deployment-evidence assurance taxonomy.")
    asub = assurance.add_subparsers(dest="assurance_command")
    ashow = asub.add_parser("show", help="Show all assurance levels.")
    ashow.add_argument("--json", action="store_true")
    aex = asub.add_parser("explain", help="Explain one assurance level.")
    aex.add_argument("level", choices=list(ASSURANCE_LEVELS))
    aex.add_argument("--json", action="store_true")

    sub.add_parser("baseline", help="Show the recorded deployment baseline.")
    deploy = sub.add_parser("deploy", help="Deployment-state operations and health reporting.")
    deploy_sub = deploy.add_subparsers(dest="deploy_command")
    deploy_status = deploy_sub.add_parser("status", help="Summarize deployment state and health.")
    deploy_status.add_argument("--json", action="store_true")
    deploy_status.add_argument("--quiet", action="store_true", help="Emit no output; communicate health via exit code only.")
    history = sub.add_parser("history", help="Show deployment ledger history.")
    history.add_argument(
        "action",
        nargs="?",
        choices=["show"],
        help="Optional history action. Use `history show <record>` for one record.",
    )
    history.add_argument(
        "record",
        nargs="?",
        help="1-based history record number, or `latest`.",
    )
    history.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum records to show. Default: 20.",
    )
    history.add_argument(
        "--json",
        action="store_true",
        help="Print records as JSON instead of human-readable text.",
    )

    sub.add_parser("history-verify", help="Verify deployment-ledger integrity.")
    rollback = sub.add_parser("rollback", help="Plan or inspect rollback operations.")
    rollback_sub = rollback.add_subparsers(dest="rollback_command")
    rollback_plan = rollback_sub.add_parser(
        "plan",
        help="Prepare a rollback package without changing deployment state.",
    )
    rollback_plan.add_argument("record", type=int)
    rollback_plan.add_argument(
        "--from",
        dest="from_record",
        type=int,
        help=(
            "Plan hypothetically from this historical deployment record "
            "instead of actual current production."
        ),
    )
    rollback_plan.add_argument("-o", "--output")
    rollback_plan.add_argument("--ignore", action="append", default=[], metavar="GLOB")
    rollback_plan.add_argument("--include", action="append", default=[], metavar="GLOB")
    rollback_plan.add_argument("--dry-run", action="store_true")
    rollback_diff = rollback_sub.add_parser(
        "diff",
        help="Report rollback delta without generating deployment artifacts.",
    )
    rollback_diff.add_argument("record", type=int, help="Historical target deployment record.")
    rollback_diff.add_argument(
        "--from", dest="from_record", type=int,
        help="Historical source record. If omitted, use actual current production.",
    )
    rollback_diff.add_argument("--ignore", action="append", default=[], metavar="GLOB")
    rollback_diff.add_argument("--include", action="append", default=[], metavar="GLOB")
    rollback_diff.add_argument("--json", action="store_true", help="Emit machine-readable JSON report.")

    verify = sub.add_parser("verify", help="Verify an archive or extracted deployment tree.")
    verify.add_argument("archive")
    verify.add_argument("--checksum")
    verify.add_argument("--root")
    verify.add_argument("--evidence-out")
    verify.add_argument(
        "--strict-permissions",
        action="store_true",
        help="Require exact recorded permission bits for extracted-tree verification.",
    )

    rv = sub.add_parser("remote-verifier", help="Generate a temporary remote deployment verifier.")
    rv.add_argument("archive")
    rv.add_argument("--language", choices=["php", "python"], default="php")
    rv.add_argument("-o", "--output")
    rv.add_argument("--sign", action="store_true", help="Generate an ephemeral-key Ed25519 signing verifier.")
    rv.add_argument("--verifier-id", help="Issued verifier identity; required with --sign.")
    rv.add_argument(
        "--browser",
        action="store_true",
        help="Generate a temporary token-protected PHP browser endpoint.",
    )
    rv.add_argument(
        "--token",
        help="Browser verifier token. If omitted with --browser, one is generated.",
    )
    rv.add_argument(
        "--relative-root",
        default=".",
        help="Application root relative to the browser verifier file. Default: .",
    )

    ingest = sub.add_parser("ingest-remote-evidence", help="Ingest unsigned remote verification evidence.")
    ingest.add_argument("remote_evidence")
    ingest.add_argument("archive")
    ingest.add_argument("-o", "--output")
    singest = sub.add_parser("ingest-signed-remote-evidence", help="Ingest signed remote verification evidence.")
    singest.add_argument("signed_remote_evidence")
    singest.add_argument("archive")
    singest.add_argument("--public-key", required=True)
    singest.add_argument("-o", "--output")
    verifier = sub.add_parser("verifier", help="Manage verifier identities and lifecycle.")
    vsub = verifier.add_subparsers(dest="verifier_command")
    vi = vsub.add_parser("issue")
    vi.add_argument("--ttl-minutes", type=int, default=30)
    vs = vsub.add_parser("show")
    vs.add_argument("verifier_id")
    vr = vsub.add_parser("revoke")
    vr.add_argument("verifier_id")
    keys = sub.add_parser("keys", help="Inspect and revoke evidence signing keys.")
    ksub = keys.add_subparsers(dest="keys_command")
    ksub.add_parser("show")
    krev = ksub.add_parser("revoke")
    krev.add_argument("fingerprint")
    krev.add_argument("--reason", required=True)
    recovery = sub.add_parser("recovery", help="Recovery bundles, trust anchors, and offline custody.")
    rsub = recovery.add_subparsers(dest="recovery_command")
    rex = rsub.add_parser("export"); rex.add_argument("output"); rex.add_argument("--unsigned",action="store_true")
    rver = rsub.add_parser("verify"); rver.add_argument("bundle"); rver.add_argument("--public-key",required=True)
    rim = rsub.add_parser("import"); rim.add_argument("bundle"); rim.add_argument("--public-key"); rim.add_argument("--allow-unsigned",action="store_true")
    rtrust = rsub.add_parser("trust")
    rtrustsub = rtrust.add_subparsers(dest="recovery_trust_command")
    rta = rtrustsub.add_parser("add"); rta.add_argument("public_key"); rta.add_argument("--activate",action="store_true"); rta.add_argument("--predecessor"); rta.add_argument("--reason")
    rtrustsub.add_parser("show")
    rtr = rtrustsub.add_parser("revoke"); rtr.add_argument("signer_id"); rtr.add_argument("--reason",required=True)
    rtoe = rtrustsub.add_parser("export-offline"); rtoe.add_argument("output")
    rtov = rtrustsub.add_parser("verify-offline"); rtov.add_argument("anchor"); rtov.add_argument("--public-key",required=True); rtov.add_argument("--expected-fingerprint")
    rtoc = rtrustsub.add_parser("export-copies"); rtoc.add_argument("output"); rtoc.add_argument("--copies",type=int,default=3); rtoc.add_argument("--quorum",type=int)
    rtocs = rtrustsub.add_parser("verify-copy-set"); rtocs.add_argument("anchors",nargs="+"); rtocs.add_argument("--public-key",dest="public_keys",action="append",required=True); rtocs.add_argument("--expected-fingerprint",dest="expected_fingerprints",action="append"); rtocs.add_argument("--trusted-checkpoint"); rtocs.add_argument("--expected-checkpoint-hash")
    rtoq = rtrustsub.add_parser("verify-quorum"); rtoq.add_argument("anchors",nargs="+"); rtoq.add_argument("--public-key",dest="public_keys",action="append",required=True); rtoq.add_argument("--quorum",type=int); rtoq.add_argument("--expected-fingerprint",dest="expected_fingerprints",action="append"); rtoq.add_argument("--trusted-checkpoint"); rtoq.add_argument("--expected-checkpoint-hash")
    rtch = rtrustsub.add_parser("checkpoint-history"); rtch.add_argument("--json",action="store_true")
    rtcs = rtrustsub.add_parser("checkpoint-show"); rtcs.add_argument("record"); rtcs.add_argument("--json",action="store_true")
    rtrustsub.add_parser("checkpoint-verify")
    _apply_complete_help_surface(sub, asub, deploy_sub, rollback_sub, vsub, ksub, rsub, rtrustsub)
    _apply_help_formatter_tree(p)
    return p

def show_plan(plan):
    print(f"Repository      : {plan.root}")
    print(f"Baseline ref    : {plan.baseline_ref}")
    print(f"Baseline commit : {plan.baseline_commit}")
    print(f"Current HEAD    : {plan.head_commit}")
    print(f"Deployable files: {len(plan.deployable)}")
    for c in plan.deployable:
        print(f"  {c.status:<8} {c.path} [{c.source}]")
    print(f"Excluded files  : {len(plan.ignored)}")
    reasons = dict(plan.ignored_reasons)
    for c in plan.ignored:
        reason = reasons.get(c.path, "excluded")
        print(f"  [{reason}] {c.status:<8} {c.path} [{c.source}]")
    print(f"Remote deletions: {len(plan.deletions)}")
    for c in plan.deletions:
        print(f"  DELETE   {c.path}")

def resolve_baseline(root, explicit):
    value = explicit or read_baseline(root)
    if not value:
        raise DeployPackError(
            "no deployment baseline is recorded. Git-aware `pack` needs the revision that "
            "currently represents production. For a first deployment or unknown production "
            "state, deploy the complete built output with `deploy-pack artifact`, verify it, "
            "then record the deployed revision with `deploy-pack mark <ref>`. If production "
            "already corresponds to a known revision, pass it explicitly as the baseline. "
            "A <ref> may be a commit SHA, tag, branch, or other Git revision; prefer an "
            "immutable commit SHA or deployment tag."
        )
    return value

def main(argv=None):
    args = parser().parse_args(argv)
    if not args.command:
        parser().print_help()
        return 0
    try:
        if args.command == "assurance":
            value = assurance_taxonomy()
            if args.assurance_command == "show":
                if args.json:
                    print(json.dumps(value, indent=2, sort_keys=True)); return 0
                print("DEPLOY-PACK EVIDENCE ASSURANCE")
                for name, spec in value["levels"].items():
                    support = "SUPPORTED" if spec["supported"] else "RESERVED"
                    print(f"  {name:<24} {support:<9} {spec['description']}")
                print("\nNOTE: Ed25519 signing of host-cooperative evidence protects the evidence record after signing; it does not attest that a compromised host reported honestly.")
                return 0
            if args.assurance_command == "explain":
                spec = {"level": args.level, **value["levels"][args.level]}
                if args.json:
                    print(json.dumps(spec, indent=2, sort_keys=True)); return 0
                print(f"Assurance level : {args.level}")
                print(f"Status          : {'SUPPORTED' if spec['supported'] else 'RESERVED'}")
                print(f"Authority       : {spec['authority']}")
                print(f"Description     : {spec['description']}")
                print("Claims          : " + ", ".join(spec['claims']))
                print("Does not claim  : " + (", ".join(spec['doesNotClaim']) or "-"))
                return 0
            raise DeployPackError("assurance requires `show` or `explain <level>`")

        if args.command == "artifact":
            plan = build_artifact_plan(
                Path(args.source),
                Path(args.output),
                args.format,
                required=args.require,
            )
            output = write_artifact(plan)
            result = artifact_result_dict(plan)
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print("DEPLOY-PACK ARTIFACT: PASS")
                print(f"  source : {plan.source}")
                print(f"  format : {plan.format}")
                print(f"  files  : {plan.file_count}")
                print(f"  output : {output}")
            return 0

        if args.command == "verify":
            checksum = Path(args.checksum) if args.checksum else None
            result = verify_archive(Path(args.archive), checksum)
            issues = list(result.issues)
            if args.root:
                issues.extend(
                    verify_extracted_tree(
                        result.manifest,
                        Path(args.root),
                        strict_permissions=args.strict_permissions,
                    )
                )
            if issues:
                print("DEPLOY-PACK VERIFY: FAIL")
                for issue in issues:
                    print(f"  {issue.kind:<30} {issue.path}")
                    print(f"    {issue.detail}")
                return 1
            print("DEPLOY-PACK VERIFY: PASS")
            print(f"  archive : {result.archive}")
            print(f"  files   : {len(result.manifest.get('files', []))}")
            print(f"  baseline: {result.manifest.get('baselineCommit')}")
            print(f"  head    : {result.manifest.get('headCommit')}")
            if args.root:
                print(f"  root    : {Path(args.root).resolve()}")
            evidence_path = write_verification_evidence(
                result.archive,
                result.manifest,
                verification_scope="extracted-tree" if args.root else "archive",
                verification_root=str(Path(args.root).resolve()) if args.root else None,
                strict_permissions=args.strict_permissions,
                output=Path(args.evidence_out).expanduser() if args.evidence_out else None,
            )
            print(f"  evidence: {evidence_path}")
            print(f"  assurance: {'local'}")
            return 0

        if args.command == "remote-verifier":
            import secrets
            output = Path(args.output) if args.output else None

            token = args.token
            if args.browser and not token:
                token = secrets.token_urlsafe(32)

            if args.sign:
                if args.browser:
                    raise DeployPackError("--sign is not supported for browser verifiers")
                if not args.verifier_id:
                    raise DeployPackError("--sign requires --verifier-id; run `deploy-pack verifier issue`")
                rr = repo_root()
                identity = get_verifier(rr, args.verifier_id)
                validate_verifier(rr, identity)
                path, public_key = write_signed_remote_verifier(Path(args.archive), args.language, identity, output, root=rr)
                print(f"Generated signed {args.language} remote verifier: {path}")
                print(f"Verification public key: {public_key}")
                print(f"Remote usage: {args.language == 'php' and 'php' or 'python3'} {path.name} --signed-evidence-out <file.json>")
                print("Optional deployment root: place ROOT before or after options; default is current directory.")
                print("SECURITY: delete the signed verifier from the server immediately after use.")
                return 0

            path = write_remote_verifier(
                Path(args.archive),
                args.language,
                output,
                browser=args.browser,
                token=token,
                relative_root=args.relative_root,
            )

            if args.browser:
                print(f"Generated token-protected PHP browser verifier: {path}")
                print(f"Token: {token}")
                print("Open the verifier URL in a browser and submit the token using its POST form.")
                print("Or send Authorization: Bearer <TOKEN> from a non-browser client.")
                print("Delete the verifier immediately after successful verification.")
            else:
                print(f"Generated {args.language} remote verifier: {path}")
            return 0

        if args.command == "ingest-remote-evidence":
            normalized = ingest_remote_evidence(Path(args.remote_evidence).expanduser(), Path(args.archive).expanduser(), output=Path(args.output).expanduser() if args.output else None)
            print(f"Ingested remote evidence: {normalized}")
            print("Assurance                    : host-cooperative-remote")
            print("Attestation                  : NO — target host reports its own state")
            return 0

        if args.command == "ingest-signed-remote-evidence":
            signed_path=Path(args.signed_remote_evidence).expanduser(); archive=Path(args.archive).expanduser(); public_key=Path(args.public_key).expanduser()
            normalized = ingest_signed_remote_evidence(signed_path, archive, public_key, root=repo_root(), output=Path(args.output).expanduser() if args.output else None)
            root = repo_root()
            value=json.loads(normalized.read_text(encoding="utf-8")); verifier_id=((value.get("remoteEvidence") or {}).get("verifierIdentity") or {}).get("verifierId")
            if not verifier_id: raise DeployPackError("normalized signed evidence lacks verifier identity")
            signer=register_signer(root, verifier_id, public_key)
            print(f"Ingested signed remote evidence: {normalized}")
            print(f"Registered signer fingerprint : {signer['publicKeySha256']}")
            print("Assurance                    : host-cooperative-remote")
            print("Attestation                  : NO — signature protects evidence integrity, not host honesty")
            return 0

        if args.command == "verifier":
            root = repo_root()
            if args.verifier_command == "issue":
                ident=issue_verifier(root,args.ttl_minutes)
                print(f"Verifier ID : {ident['verifierId']}")
                print(f"Issued at   : {ident['issuedAt']}")
                print(f"Expires at  : {ident['expiresAt']}")
                print(f"Nonce       : {ident['nonce']}")
                return 0
            if args.verifier_command == "show":
                print(json.dumps(get_verifier(root,args.verifier_id),indent=2,sort_keys=True)); return 0
            if args.verifier_command == "revoke":
                rec=revoke_verifier(root,args.verifier_id); print(f"Revoked verifier: {args.verifier_id}"); print(f"Revoked at      : {rec.get('revokedAt')}"); return 0
            raise DeployPackError("verifier requires issue, show, or revoke")

        if args.command == "keys":
            root=repo_root()
            if args.keys_command == "show":
                print(json.dumps(load_keyring(root),indent=2,sort_keys=True)); return 0
            if args.keys_command == "revoke":
                rec=revoke_signer(root,args.fingerprint,reason=args.reason); print(f"Revoked signer: {args.fingerprint}"); print(f"Reason        : {rec.get('revocationReason')}"); return 0
            raise DeployPackError("keys requires show or revoke")

        if args.command == "recovery":
            root=repo_root()
            if args.recovery_command == "export":
                bundle,pub=export_recovery_bundle(root,Path(args.output).expanduser(),unsigned=args.unsigned)
                if args.unsigned: print("WARNING: exported legacy unsigned recovery bundle")
                print(f"Exported recovery bundle: {bundle}")
                if pub: print(f"Recovery public key    : {pub}")
                return 0
            if args.recovery_command == "verify":
                payload=verify_recovery_bundle_trusted(root,Path(args.bundle).expanduser(),Path(args.public_key).expanduser())
                print("DEPLOY-PACK RECOVERY VERIFY: PASS")
                print(f"  history records : {len(payload.get('history') or [])}")
                print(f"  baseline        : {payload.get('baseline') or '-'}")
                return 0
            if args.recovery_command == "trust":
                if args.recovery_trust_command == "add":
                    rec=trust_recovery_signer(root,Path(args.public_key).expanduser(),activate=args.activate,predecessor=args.predecessor,reason=args.reason)
                    print(f"Trusted recovery signer: {rec['signerId']}")
                    print(f"Status                 : {rec['status']}")
                    return 0
                if args.recovery_trust_command == "show":
                    print(json.dumps(load_recovery_trust(root),indent=2,sort_keys=True)); return 0
                if args.recovery_trust_command == "revoke":
                    rec=revoke_recovery_signer(root,args.signer_id,reason=args.reason)
                    print(f"Revoked recovery signer: {args.signer_id}")
                    print(f"Reason                 : {rec.get('revocationReason')}")
                    return 0
                if args.recovery_trust_command == "export-offline":
                    anchor,pub,fingerprint=export_offline_trust_anchor(root,Path(args.output).expanduser())
                    print(f"Offline trust anchor : {anchor}")
                    print(f"Verification key     : {pub}")
                    print(f"Fingerprint record   : {fingerprint}")
                    print("Store the fingerprint record separately from the anchor/key when possible.")
                    return 0
                if args.recovery_trust_command == "verify-offline":
                    payload=verify_offline_trust_anchor(
                        Path(args.anchor).expanduser(),
                        Path(args.public_key).expanduser(),
                        expected_fingerprint=args.expected_fingerprint,
                    )
                    counts=payload.get("signerCounts") or {}
                    print("DEPLOY-PACK OFFLINE TRUST VERIFY: PASS")
                    print(f"  active signer : {payload.get('activeSigner') or '-'}")
                    print(f"  active         : {counts.get('active',0)}")
                    print(f"  trusted        : {counts.get('trusted',0)}")
                    print(f"  retired        : {counts.get('retired',0)}")
                    print(f"  revoked        : {counts.get('revoked',0)}")
                    return 0
                if args.recovery_trust_command == "export-copies":
                    result=export_offline_trust_copies(root,Path(args.output).expanduser(),copies=args.copies,quorum=args.quorum)
                    c=result["checkpoint"]
                    print(f"Offline custody checkpoint : {c['checkpointId']}")
                    print(f"Checkpoint sequence         : {c['sequence']}")
                    print(f"Independent copies          : {c['copyCount']}")
                    print(f"Checkpoint manifest         : {result['manifest']}")
                    for a in result["artifacts"]:
                        print(f"  anchor      : {a['anchor']}")
                        print(f"  public key  : {a['publicKey']}")
                        print(f"  fingerprint : {a['fingerprint']}")
                    print("Store custody copies in independent locations and fingerprint records separately.")
                    return 0
                if args.recovery_trust_command == "verify-copy-set":
                    result=verify_offline_trust_copy_set([Path(x).expanduser() for x in args.anchors],[Path(x).expanduser() for x in args.public_keys],expected_fingerprints=args.expected_fingerprints,root=root,trusted_checkpoint=Path(args.trusted_checkpoint).expanduser() if args.trusted_checkpoint else None,expected_checkpoint_hash=args.expected_checkpoint_hash)
                    print("DEPLOY-PACK OFFLINE COPY SET VERIFY: PASS")
                    print(f"  checkpoint : {result['checkpointId']}")
                    print(f"  sequence   : {result['sequence']}")
                    print(f"  copies     : {result['copyCount']}")
                    print(f"  signers    : {result['independentSigners']}")
                    print(f"  authenticity: {result['authenticityRoot']}")
                    return 0
                if args.recovery_trust_command == "verify-quorum":
                    anchors=[Path(x).expanduser() for x in args.anchors]
                    public_keys=[Path(x).expanduser() for x in args.public_keys]
                    result=verify_offline_trust_quorum(anchors,public_keys,quorum=args.quorum,expected_fingerprints=args.expected_fingerprints,root=root,trusted_checkpoint=Path(args.trusted_checkpoint).expanduser() if args.trusted_checkpoint else None,expected_checkpoint_hash=args.expected_checkpoint_hash)
                    print("DEPLOY-PACK OFFLINE QUORUM VERIFY: PASS")
                    print(f"  checkpoint : {result['checkpointId']}")
                    print(f"  sequence   : {result['sequence']}")
                    print(f"  agreeing   : {result['validAgreeingCopies']}")
                    print(f"  quorum     : {result['effectiveQuorum']}")
                    print(f"  rejected   : {len(result['rejectedCopies'])}")
                    print(f"  conflicts  : {len(result['conflictingValidCopies'])}")
                    print(f"  authenticity: {result['authenticityRoot']}")
                    return 0
                if args.recovery_trust_command == "checkpoint-history":
                    records=load_offline_checkpoints(root)
                    if args.json:
                        print(json.dumps(records,indent=2,sort_keys=True)); return 0
                    if not records: print("No offline custody checkpoints recorded."); return 0
                    for rec in records: print(f"#{rec['sequence']}  {rec['checkpointId']}  copies={rec['copyCount']}  trust={rec['recoveryTrustSha256'][:12]}...")
                    return 0
                if args.recovery_trust_command == "checkpoint-show":
                    number,rec=offline_checkpoint_record(root,args.record)
                    if args.json: print(json.dumps(rec,indent=2,sort_keys=True)); return 0
                    print(f"Checkpoint record : {number}")
                    print(f"Checkpoint ID     : {rec['checkpointId']}")
                    print(f"Created at        : {rec['createdAt']}")
                    print(f"Copies            : {rec['copyCount']}")
                    print(f"Trust SHA-256     : {rec['recoveryTrustSha256']}")
                    print(f"Previous hash     : {rec['previousCheckpointHash']}")
                    print(f"Checkpoint hash   : {rec['checkpointHash']}")
                    return 0
                if args.recovery_trust_command == "checkpoint-verify":
                    ok,errors=verify_offline_checkpoint_history(root)
                    if ok:
                        print("DEPLOY-PACK OFFLINE CHECKPOINT HISTORY: PASS")
                        print(f"  records: {len(load_offline_checkpoints(root))}")
                        return 0
                    print("DEPLOY-PACK OFFLINE CHECKPOINT HISTORY: FAIL")
                    for error in errors: print(f"  {error}")
                    return 1
                raise DeployPackError("recovery trust requires add, show, revoke, export-offline, verify-offline, export-copies, verify-copy-set, verify-quorum, checkpoint-history, checkpoint-show, or checkpoint-verify")

            if args.recovery_command == "import":
                if args.allow_unsigned: print("WARNING: importing legacy unsigned recovery bundle under explicit compatibility override")
                if args.allow_unsigned:
                    result=import_recovery_bundle(root,Path(args.bundle).expanduser(),public_key_file=None,allow_unsigned=True)
                else:
                    if not args.public_key: raise DeployPackError("signed recovery import requires --public-key")
                    result=import_recovery_bundle_trusted(root,Path(args.bundle).expanduser(),public_key_file=Path(args.public_key).expanduser())
                print(json.dumps(result,indent=2,sort_keys=True)); return 0
            raise DeployPackError("recovery requires export, verify, or import")

        if args.command == "gitignore":
            root = repo_root()
            try:
                return run_gitignore_command(root, args)
            except GitignoreManagedError as exc:
                raise DeployPackError(str(exc)) from exc

        root = repo_root()

        if args.command == "init":
            config_path = root / CONFIG_FILE
            if config_path.exists() and not args.force:
                raise DeployPackError(
                    f"{CONFIG_FILE} already exists; review it or pass --force to replace it"
                )
            starter = [
                "# deploy-pack Git-aware selection policy",
                "# Review [pack].include before running pack/inspect.",
                "schema = 2",
                "",
                "[pack]",
                'policy = "allowlist"',
                "include = [",
            ]
            if (root / ".htaccess").exists():
                starter.append('  ".htaccess",')
            starter.extend([
                "  # Add deployable application paths, for example:",
                '  # "*.php",',
                '  # "assets/**",',
                '  # "admin/**",',
                "]",
                "exclude = []",
                "require = []",
                "",
            ])
            atomic_write_text(config_path, "\n".join(starter))
            try:
                gitignore_outcome = install_managed_gitignore(root)
            except GitignoreManagedError as exc:
                raise DeployPackError(
                    f"{CONFIG_FILE} was created, but managed .gitignore installation failed: {exc}"
                ) from exc
            print("DEPLOY-PACK INIT")
            print(f"  gitignore: {gitignore_outcome}")
            print(f"  created : {config_path}")
            print("  policy  : allowlist / fail-closed")
            print("  review  : [pack].include before packaging")
            print("  next    : deploy-pack inspect <baseline>")
            return 0

        if args.command == "deploy":
            if args.deploy_command != "status":
                raise DeployPackError("deploy requires `status`")
            status = deployment_status(root)
            if args.quiet:
                return 0 if status["health"] == "PASS" else 1
            if args.json:
                print(json.dumps(status, indent=2, sort_keys=True))
                return 0 if status["health"] == "PASS" else 1
            print(f"DEPLOY-PACK DEPLOY STATUS: {status['health']}")
            b=status["baseline"]; h=status["history"]; v=status["verifiers"]; r=status["replay"]; p=status["pending"]
            print("\nBaseline")
            print(f"  ref        : {b.get('ref') or '-'}")
            print(f"  commit     : {b.get('commit') or '-'}")
            print("\nLatest deployment")
            print(f"  records    : {h.get('records')}")
            print(f"  record     : {h.get('latestRecord') or '-'}")
            print(f"  ref        : {h.get('latestRef') or '-'}")
            print(f"  commit     : {h.get('latestCommit') or '-'}")
            print(f"  kind       : {h.get('latestKind') or '-'}")
            print(f"  trust      : {h.get('latestTrustMode') or '-'}")
            print(f"  assurance  : {h.get('latestAssuranceLevel') or '-'}")
            print(f"  ledger     : {'PASS' if h.get('verified') else 'FAIL'}")
            print(f"  chain      : {h.get('chainMode') or '-'}")
            print(f"  head hash  : {h.get('headRecordHash') or '-'}")
            la=h.get("offlineAnchor") or {}
            print(f"  anchor     : {la.get('status') or '-'}")
            print(f"  anchored   : {la.get('anchoredRecordCount') if la.get('anchoredRecordCount') is not None else '-'} record(s)")
            print(f"  anchor hash: {la.get('anchoredHeadRecordHash') or '-'}")
            print("\nVerifier health")
            print(f"  active     : {v.get('active')}")
            print(f"  expired    : {v.get('expired')}")
            print(f"  revoked    : {v.get('revoked')}")
            print("\nReplay state")
            print(f"  consumed   : {r.get('consumedEvidence')}")
            tx=status["transaction"]
            print("\nState transaction")
            print(f"  health     : {'PASS' if tx.get('healthy') else 'FAIL'}")
            print(f"  recovery   : {'PENDING' if tx.get('recoveryPending') else 'NONE'}")
            print(f"  journal    : {tx.get('journal') or '-'}")
            rt=status["recoveryTrust"]
            print("\nRecovery trust")
            print(f"  active     : {rt.get('active')}")
            print(f"  trusted    : {rt.get('trusted')}")
            print(f"  retired    : {rt.get('retired')}")
            print(f"  revoked    : {rt.get('revoked')}")
            print(f"  invalid    : {rt.get('invalid')}")
            print(f"  anchor     : {rt.get('activeSigner') or '-'}")
            print(f"  health     : {'PASS' if rt.get('healthy') else 'FAIL'}")
            oc=status["offlineCustody"]
            print("\nOffline custody")
            print(f"  configured : {'YES' if oc.get('configured') else 'NO'}")
            print(f"  health     : {'PASS' if oc.get('healthy') else 'FAIL'}")
            print(f"  chain      : {'PASS' if oc.get('checkpointChainHealthy') else 'FAIL'}")
            print(f"  checkpoints: {oc.get('checkpointRecords')}")
            print(f"  latest     : {oc.get('latestSequence') or '-'}")
            print(f"  checkpoint : {oc.get('latestCheckpointId') or '-'}")
            print(f"  freshness  : {oc.get('freshness') or '-'}")
            print(f"  grace      : {oc.get('staleGraceDays')} day(s)")
            print(f"  stale since: {oc.get('staleSince') or '-'}")
            print(f"  grace due  : {oc.get('staleGraceDueAt') or '-'}")
            overdue_text = (
                "YES" if oc.get("staleOverdue") is True
                else ("NO" if oc.get("stale") is True else "-")
            )
            print(f"  overdue    : {overdue_text}")
            stale_text = (
                "YES" if oc.get("stale") is True
                else ("NO" if oc.get("stale") is False and oc.get("configured") else "-")
            )
            print(f"  stale      : {stale_text}")
            print(f"  cause data : {oc.get('staleCauseEvidence') or '-'}")
            if oc.get("staleCauses"):
                print("  stale cause:")
                for cause in oc["staleCauses"]:
                    changed_at = cause.get("changedAt")
                    suffix = f" @ {changed_at}" if changed_at else ""
                    print(f"    - {cause.get('summary')}{suffix}")
            remediation = oc.get("remediation")
            if remediation:
                print("  remediation:")
                print(f"    priority : {remediation.get('priority')}")
                print(f"    class    : {remediation.get('classification')}")
                print(f"    action   : {remediation.get('action')}")
                print(f"    command  : {remediation.get('command')}")
                for review_reason in remediation.get("operatorReviewReasons") or []:
                    print(f"    review   : {review_reason}")
                for reason in remediation.get("reasons") or []:
                    print(f"    because  : {reason}")
            print(f"  trust snap : {oc.get('recoveryTrustSha256') or '-'}")
            print(f"  trust now  : {oc.get('currentRecoveryTrustSha256') or '-'}")
            print(f"  copies     : {oc.get('declaredCopyCount') if oc.get('declaredCopyCount') is not None else '-'}")
            print(f"  quorum     : {oc.get('declaredQuorum') if oc.get('declaredQuorum') is not None else '-'}")
            print(f"  recorded   : {oc.get('recordedCopies')}")
            print(f"  copy IDs   : {oc.get('uniqueCopyIds')}")
            print(f"  signers    : {oc.get('independentSigners')}")
            quorum_text = (
                "YES" if oc.get("quorumMet") is True
                else ("NO" if oc.get("quorumMet") is False else "-")
            )
            print(f"  quorum met : {quorum_text}")
            print("\nPending deployment artifacts")
            print(f"  packages   : {len(p['packages'])}")
            for package in p['packages']:
                flags=[]
                if package['hasChecksum']: flags.append('checksum')
                if package['hasDeletionList']: flags.append('deletions')
                if package['hasVerificationEvidence']: flags.append('evidence')
                print(f"  - {package['path']} [{', '.join(flags) if flags else 'no sidecars'}]")
            evidence_count=len(p['artifacts']['verificationEvidence'])+len(p['artifacts']['remoteEvidence'])+len(p['artifacts']['signedRemoteEvidence'])
            verifier_count=len(p['artifacts']['remoteVerifiers'])+len(p['artifacts']['browserVerifiers'])+len(p['artifacts']['pythonVerifiers'])
            print(f"  evidence   : {evidence_count}")
            print(f"  verifiers  : {verifier_count}")
            if status.get("warnings"):
                print("\nWarnings")
                for warning in status["warnings"]:
                    print(f"  - {warning}")
            if status['problems']:
                print("\nProblems")
                for problem in status['problems']: print(f"  - {problem}")
            return 0 if status['health'] == 'PASS' else 1

        if args.command == "baseline":
            value = read_baseline(root)
            if not value:
                print("No deployment baseline recorded.")
                return 1
            print(value)
            return 0

        if args.command == "history":
            if args.action == "show":
                if args.record is None:
                    raise DeployPackError("`deploy-pack history show` requires <record>")
                number, record = deployment_history_record(root, args.record)
                if args.json:
                    rendered = dict(record)
                    rendered.setdefault("recordNumber", number)
                    print(json.dumps(rendered, indent=2, sort_keys=True))
                else:
                    print(format_deployment_history_record(number, record))
                return 0
            if args.record is not None:
                raise DeployPackError("history record is valid only with `history show`")

            records = read_deployment_history(root)
            start_number = 1
            if args.limit >= 0:
                if args.limit == 0:
                    records = []
                    start_number = 1
                elif len(records) > args.limit:
                    start_number = len(records) - args.limit + 1
                    records = records[-args.limit:]
            if args.json:
                print(json.dumps(records, indent=2, sort_keys=True))
                return 0
            if not records:
                print("No deployment history recorded.")
                return 0
            for offset, record in enumerate(records):
                number = start_number + offset
                evidence = record.get("evidence") or {}
                method = evidence.get("verificationMethod") or (
                    "UNSAFE" if record.get("unsafeNoEvidence") else "-"
                )
                kind = record.get("deploymentKind") or (
                    "rollback" if record.get("rollback") else "forward"
                )
                rollback = record.get("rollback") or {}
                suffix = (
                    f" -> rollback-to #{rollback.get('targetRecord')}"
                    if kind == "rollback" else ""
                )
                print(
                    f"#{number:<4} {record.get('recordedAt')}  "
                    f"{record.get('newBaselineRef')}  "
                    f"{record.get('newBaselineCommit')}  "
                    f"{method}  {kind}{suffix}"
                )
            return 0

        if args.command == "history-verify":
            ok, errors = verify_deployment_history(root)
            if ok:
                print("DEPLOY-PACK HISTORY: PASS")
                print(f"  records: {len(read_deployment_history(root))}")
                return 0
            print("DEPLOY-PACK HISTORY: FAIL")
            for error in errors:
                print(f"  {error}")
            return 1

        if args.command == "rollback":
            if args.rollback_command not in {"plan", "diff"}:
                raise DeployPackError(
                    "rollback requires a subcommand; use `deploy-pack rollback plan <record>` "
                    "or `deploy-pack rollback diff <record>`"
                )

            ok, errors = verify_deployment_history(root)
            if not ok:
                raise DeployPackError(
                    "deployment history is not valid; run `deploy-pack history-verify`"
                )

            plan = build_rollback_plan(
                root,
                args.record,
                source_record_number=args.from_record,
                cli_ignores=args.ignore,
                cli_includes=args.include,
            )

            if args.rollback_command == "diff":
                report = {
                    "mode": "hypothetical" if args.from_record is not None else "current-production",
                    "sourceRecord": plan.source_record,
                    "sourceCommit": plan.source_commit,
                    "targetRecord": plan.target_record,
                    "targetRef": plan.target_ref,
                    "targetCommit": plan.target_commit,
                    "restore": [{"status": c.status, "path": c.path, "oldPath": c.old_path} for c in plan.deployable],
                    "delete": [c.path for c in plan.deletions],
                    "ignored": [{"status": c.status, "path": c.path, "oldPath": c.old_path} for c in plan.ignored],
                    "skipped": [{"status": c.status, "path": c.path, "reason": reason} for c, reason in plan.skipped],
                }
                if args.json:
                    print(json.dumps(report, indent=2, sort_keys=True))
                    return 0

                print("DEPLOY-PACK ROLLBACK DIFF")
                print(f"  mode           : {report['mode']}")
                print(f"  source record  : {plan.source_record}")
                print(f"  source commit  : {plan.source_commit}")
                print(f"  target record  : {plan.target_record}")
                print(f"  target ref     : {plan.target_ref}")
                print(f"  target commit  : {plan.target_commit}")
                print(f"  restore files  : {len(plan.deployable)}")
                print(f"  delete paths   : {len(plan.deletions)}")
                print(f"  ignored paths  : {len(plan.ignored)}")
                print(f"  skipped paths  : {len(plan.skipped)}")
                if plan.deployable:
                    print("\\nRestore:")
                    for change in plan.deployable:
                        suffix = f" <- {change.old_path}" if change.old_path else ""
                        print(f"  {change.status:<8} {change.path}{suffix}")
                if plan.deletions:
                    print("\\nDelete:")
                    for change in plan.deletions:
                        print(f"  DELETE   {change.path}")
                if plan.ignored:
                    print("\\nIgnored:")
                    for change in plan.ignored:
                        print(f"  IGNORED  {change.path}")
                if plan.skipped:
                    print("\\nSkipped:")
                    for change, reason in plan.skipped:
                        print(f"  SKIP     {change.path} ({reason})")
                print("\\nReport only; no rollback artifacts created.")
                print("No deployment state was changed.")
                return 0

            print("DEPLOY-PACK ROLLBACK PLAN")
            print(f"  mode           : {'hypothetical' if args.from_record is not None else 'current-production'}")
            print(f"  source record  : {plan.source_record}")
            print(f"  source commit  : {plan.source_commit}")
            print(f"  target record  : {plan.target_record}")
            print(f"  target ref     : {plan.target_ref}")
            print(f"  target commit  : {plan.target_commit}")
            print(f"  restore files  : {len(plan.deployable)}")
            print(f"  delete paths   : {len(plan.deletions)}")
            print(f"  ignored paths  : {len(plan.ignored)}")
            print(f"  skipped paths  : {len(plan.skipped)}")
            if plan.deployable:
                print("\\nRestore:")
                for change in plan.deployable: print(f"  {change.status:<8} {change.path}")
            if plan.deletions:
                print("\\nDelete:")
                for change in plan.deletions: print(f"  DELETE   {change.path}")
            if args.dry_run:
                print("\\nDry run only; no rollback artifacts created.")
                return 0
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            output = Path(args.output).expanduser() if args.output else root / f"{root.name}-rollback-record-{plan.target_record}-{timestamp}.deploy.zip"
            if not output.is_absolute(): output = root / output
            archive, deletions, checksum, plan_file = write_rollback_package(plan, output)
            print("\\nRollback artifacts:")
            if archive:
                print(f"  archive   : {archive}")
                print(f"  checksum  : {checksum}")
            if deletions: print(f"  deletions : {deletions}")
            print(f"  plan      : {plan_file}")
            if args.from_record is not None:
                print("\\nHypothetical plan only: source record was not asserted to be current production.")
            print("No deployment state was changed.")
            return 0

        if args.command == "reconcile-baseline":
            from .lifecycle import REPLAY_STATE_FILE
            from .core import LEDGER_FILE
            with repository_lock(root):
                recovered = recover_mark_transaction(root)
                if recovered:
                    print("WARNING: recovered an incomplete prior mark transaction before continuing.")
                evidence_path = Path(args.evidence).expanduser().resolve()
                archive_path = Path(args.archive).expanduser().resolve()
                previous_commit, resolved, evidence = validate_baseline_reconciliation(
                    root, args.ref, evidence_path, archive=archive_path
                )
                if not evidence_is_signed_remote(evidence):
                    raise DeployPackError("baseline reconciliation requires signed remote evidence")
                replay_key = assert_signed_evidence_usable(root, evidence)
                tx_paths = [root / BASELINE_FILE, root / LEDGER_FILE, root / REPLAY_STATE_FILE]
                evidence_for_history = dict(evidence)
                evidence_for_history["trustMode"] = "signed-remote"
                begin_mark_transaction(
                    root, tx_paths,
                    {"command":"reconcile-baseline","ref":args.ref,"unsafe":False,"evidence":str(evidence_path)},
                )
                try:
                    write_baseline(root, args.ref)
                    append_deployment_history(
                        root,
                        previous_baseline=previous_commit,
                        new_baseline_ref=args.ref,
                        new_baseline_commit=resolved,
                        evidence_path=evidence_path,
                        evidence=evidence_for_history,
                        archive=archive_path,
                        unsafe=False,
                        deployment_kind="reconciliation",
                        reconciliation_reason=args.reason,
                    )
                    consume_signed_evidence(
                        root, replay_key, evidence_path=evidence_path,
                        marked_ref=args.ref, marked_commit=resolved,
                    )
                    commit_mark_transaction(root)
                except Exception:
                    recover_mark_transaction(root)
                    raise
                print("DEPLOY-PACK BASELINE RECONCILIATION: PASS")
                print(f"Previous recorded baseline : {previous_commit}")
                print(f"Corrected baseline         : {resolved}")
                print(f"Evidence                   : {evidence_path}")
                print(f"Archive                    : {archive_path}")
                print(f"Reason                     : {args.reason}")
                print("Deployment history         : appended (reconciliation)")
                return 0

        if args.command == "mark":
            from .lifecycle import REPLAY_STATE_FILE
            from .core import LEDGER_FILE
            with repository_lock(root):
                recovered = recover_mark_transaction(root)
                if recovered:
                    print("WARNING: recovered an incomplete prior mark transaction before continuing.")
                tx_paths = [root / BASELINE_FILE, root / LEDGER_FILE, root / REPLAY_STATE_FILE]
                if args.unsafe_no_evidence:
                    previous = read_baseline(root)
                    resolved = resolve_ref(root, args.ref)
                    begin_mark_transaction(root, tx_paths, {"command":"mark","ref":args.ref,"unsafe":True})
                    try:
                        write_baseline(root, args.ref)
                        append_deployment_history(
                            root,
                            previous_baseline=resolve_ref(root, previous) if previous else None,
                            new_baseline_ref=args.ref,
                            new_baseline_commit=resolved,
                            evidence_path=None,
                            evidence=None,
                            archive=None,
                            unsafe=True,
                            rollback_target_record=args.rollback_to,
                        )
                        commit_mark_transaction(root)
                    except Exception:
                        recover_mark_transaction(root)
                        raise
                    print("WARNING: deployment baseline marked without verification evidence.")
                    print(f"Recorded deployment baseline: {args.ref}")
                    print(f"Resolved commit             : {resolved}")
                    print("Deployment history          : appended (unsafe bootstrap record)")
                    return 0
                if not args.evidence:
                    raise DeployPackError(
                        "`deploy-pack mark` requires --evidence. "
                        "Use --unsafe-no-evidence only for initial bootstrap/migration."
                    )
                evidence_path = Path(args.evidence).expanduser().resolve()
                archive_path = Path(args.archive).expanduser().resolve() if args.archive else None
                resolved, evidence = validate_mark_evidence(root, args.ref, evidence_path, archive=archive_path)
                signed_evidence = evidence_is_signed_remote(evidence)
                replay_key = assert_signed_evidence_usable(root, evidence) if signed_evidence else None
                if not signed_evidence and not args.allow_unsigned_evidence:
                    raise DeployPackError(
                        "signed remote evidence is required by default. For legacy unsigned evidence, "
                        "pass `--allow-unsigned-evidence` explicitly."
                    )
                if not signed_evidence and args.allow_unsigned_evidence:
                    print("WARNING: accepting legacy unsigned deployment evidence under explicit compatibility override.")
                previous = read_baseline(root)
                previous_commit = resolve_ref(root, previous) if previous else None
                evidence_for_history = dict(evidence)
                evidence_for_history["trustMode"] = "signed-remote" if signed_evidence else "legacy-unsigned-compatibility"
                begin_mark_transaction(root, tx_paths, {"command":"mark","ref":args.ref,"unsafe":False,"evidence":str(evidence_path)})
                try:
                    write_baseline(root, args.ref)
                    append_deployment_history(
                        root,
                        previous_baseline=previous_commit,
                        new_baseline_ref=args.ref,
                        new_baseline_commit=resolved,
                        evidence_path=evidence_path,
                        evidence=evidence_for_history,
                        archive=archive_path,
                        unsafe=False,
                        rollback_target_record=args.rollback_to,
                    )
                    if replay_key is not None:
                        consume_signed_evidence(root, replay_key, evidence_path=evidence_path, marked_ref=args.ref, marked_commit=resolved)
                    commit_mark_transaction(root)
                except Exception:
                    recover_mark_transaction(root)
                    raise
                print(f"Recorded deployment baseline: {args.ref}")
                print(f"Resolved commit             : {resolved}")
                print(f"Evidence                    : {evidence_path}")
                print(f"Verification scope          : {evidence.get('verificationScope')}")
                print(f"Evidence trust              : {'signed-remote' if signed_evidence else 'legacy-unsigned-compatibility'}")
                print(f"Evidence assurance          : {(evidence_for_history.get('assurance') or {}).get('level') or 'host-cooperative-remote'}")
                if signed_evidence:
                    print("Attestation                  : NO — host-cooperative evidence, not hostile-host attestation")
                print("Deployment history          : appended")
                return 0

        # Operator-facing Git packaging fails closed. Internal build_plan callers
        # retain legacy fixture compatibility, but the CLI never packages a new
        # repository without an explicit policy.
        load_project_policy(root, required=True)
        baseline = resolve_baseline(root, args.baseline)
        plan = build_plan(
            root, baseline,
            committed_only=args.committed_only,
            cli_ignores=args.ignore,
            cli_includes=args.include,
        )
        show_plan(plan)

        if args.command == "inspect" or args.dry_run:
            return 0
        if not plan.deployable and not plan.deletions:
            print("Nothing to deploy.")
            return 0

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = Path(args.output) if args.output else root / f"{root.name}-{stamp}.deploy.zip"
        archive, deletions, checksum = write_package(plan, output)
        if archive:
            print(f"Created archive : {archive}")
            print(f"Archive checksum: {checksum}")
            print(f"Verify locally  : deploy-pack verify {archive}")
            print(f"Remote verifier : deploy-pack remote-verifier {archive}")
        if deletions:
            print(f"Deletion list   : {deletions}")
        print("Baseline was NOT advanced.")
        return 0
    except DeployPackError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
