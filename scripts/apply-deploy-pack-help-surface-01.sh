#!/usr/bin/env bash
set -euo pipefail
export GIT_PAGER=cat PAGER=cat GIT_TERMINAL_PROMPT=0

REPO="${1:-.}"
REPO="$(cd "$REPO" && pwd)"
TOOL="$REPO/tools/deploy-pack"
CLI="$TOOL/src/deploy_pack/cli.py"
SIGNED="$TOOL/src/deploy_pack/signed.py"
CLOSEOUT="$TOOL/scripts/closeout.sh"
MK="$REPO/mk/deploy-pack.inc"
VERSION="$TOOL/VERSION"
INIT="$TOOL/src/deploy_pack/__init__.py"
PYPROJECT="$TOOL/pyproject.toml"
DOCS="$TOOL/docs"
MANIFESTS="$DOCS/manifests"
TEST="$TOOL/tests/test_help_surface01.py"
PY="$TOOL/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3

for f in "$CLI" "$SIGNED" "$CLOSEOUT" "$MK" "$VERSION" "$INIT" "$PYPROJECT"; do
  [[ -f "$f" ]] || { echo "DEPLOY-PACK-HELP-SURFACE-01: FAIL — missing $f" >&2; exit 2; }
done
mkdir -p "$MANIFESTS" "$TOOL/tests"

python3 - "$CLI" "$SIGNED" "$CLOSEOUT" "$MK" "$VERSION" "$INIT" "$PYPROJECT" "$DOCS" "$MANIFESTS" <<'PY'
from pathlib import Path
import json, re, sys
cli,signed,closeout,mk,version,init,pyproject,docs,manifests = map(Path, sys.argv[1:])

s=cli.read_text(encoding='utf-8')

# Repair a registration bug in GIT-CLOSEOUT-HARDEN-01: that patch added the
# word "reconcile-baseline" to top-level help before using a broad string-presence
# guard for parser/dispatch installation. On affected 1.13/1.14 trees the help
# could advertise a command that argparse did not actually register.
if 'reconcile = sub.add_parser(' not in s:
    mark_anchor='''    mark.add_argument(
        "--rollback-to",
        type=int,
        help="Record this mark as an intentional rollback to the given history record.",
    )

'''
    reconcile_parser='''    reconcile = sub.add_parser(
        "reconcile-baseline",
        help="Correct a recorded production baseline using fresh target-bound signed evidence.",
    )
    reconcile.add_argument("ref", help="Git ref/commit that exactly represents current production bytes.")
    reconcile.add_argument("--archive", required=True, help="Correction archive whose manifest headCommit resolves to REF.")
    reconcile.add_argument("--evidence", required=True, help="Fresh normalized signed remote evidence for --archive.")
    reconcile.add_argument("--reason", required=True, help="Operator/audit reason for correcting the recorded baseline.")

'''
    if mark_anchor not in s:
        raise SystemExit('HELP-SURFACE-01: cannot repair reconcile parser; mark anchor missing')
    s=s.replace(mark_anchor, mark_anchor+reconcile_parser, 1)

if 'if args.command == "reconcile-baseline":' not in s:
    branch_anchor='''        if args.command == "mark":
'''
    branch='''        if args.command == "reconcile-baseline":
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

'''
    if branch_anchor not in s:
        raise SystemExit('HELP-SURFACE-01: cannot repair reconcile dispatch; mark branch missing')
    s=s.replace(branch_anchor, branch+branch_anchor, 1)

if 'from .core import validate_baseline_reconciliation' not in s:
    first_import='from .core import ('
    idx=s.find(first_import)
    close=s.find('\n)',idx)
    if idx<0 or close<0:
        raise SystemExit('HELP-SURFACE-01: cannot repair reconciliation import')
    close+=2
    s=s[:close]+'\nfrom .core import validate_baseline_reconciliation'+s[close:]

HELP_FN = r'''
def _decorate_help(parser_obj, *, description=None, epilog=None):
    if parser_obj is None:
        return
    if description:
        parser_obj.description = description
    if epilog:
        parser_obj.epilog = epilog
        parser_obj.formatter_class = argparse.RawDescriptionHelpFormatter


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

'''

if 'def _apply_complete_help_surface(' not in s:
    anchor='def parser():\n'
    if anchor not in s:
        raise SystemExit('HELP-SURFACE-01: parser() anchor missing')
    s=s.replace(anchor, HELP_FN+'\n'+anchor, 1)

call='''    _apply_complete_help_surface(sub, asub, deploy_sub, rollback_sub, vsub, ksub, rsub, rtrustsub)\n    return p\n'''
if call not in s:
    old='''    return p\n\ndef show_plan'''
    if old not in s:
        raise SystemExit('HELP-SURFACE-01: parser return anchor missing')
    s=s.replace(old, call+'\ndef show_plan',1)

# Add automated closeout and reconciliation to top-level orientation without replacing
# pre-existing detailed closeout help.
needle='Run `deploy-pack <command> --help` for command-specific options and examples.'
if 'make deploy-help' not in s and needle in s:
    s=s.replace(needle, '''ROUTINE GIT-AWARE CLOSEOUT\n  make deploy-help              Show the condensed two-phase workflow.\n  make deploy-prepare           Prepare archive + signed production verifier.\n  make deploy-closeout EVIDENCE=/path/to/signed-evidence.json\n                                Ingest, mark, and run all post-closeout gates.\n\nBASELINE CORRECTION\n  `reconcile-baseline` is only for an already-correct production tree whose recorded\n  Git baseline is wrong. It still requires fresh signed remote evidence.\n\n'''+needle,1)
cli.write_text(s,encoding='utf-8')

# Enrich generated signed-verifier --help text. Keep parsing behavior unchanged.
t=signed.read_text(encoding='utf-8')
old='''print(f"usage: {Path(sys.argv[0]).name} [ROOT] --signed-evidence-out FILE [--strict-permissions]"); return 0'''
new='''print(f"usage: {Path(sys.argv[0]).name} [ROOT] --signed-evidence-out FILE [--strict-permissions]")\n   print("Verify the deployed tree against the manifest embedded in this one-time deploy-pack verifier.")\n   print("ROOT defaults to the current directory and may appear before or after options.")\n   print("--signed-evidence-out FILE  Required path for signed verification evidence JSON.")\n   print("--strict-permissions        Require exact recorded permission bits.")\n   print("Exit 0 only when deployment verification passes and signed evidence is written."); return 0'''
if old in t: t=t.replace(old,new,1)
oldphp='''if($arg==='-h'||$arg==='--help'){fwrite(STDOUT,"usage: ".basename($argv[0])." [ROOT] --signed-evidence-out FILE [--strict-permissions]\\n");exit(0);}'''
newphp='''if($arg==='-h'||$arg==='--help'){fwrite(STDOUT,"usage: ".basename($argv[0])." [ROOT] --signed-evidence-out FILE [--strict-permissions]\\n\\nVerify the deployed tree against the manifest embedded in this one-time deploy-pack verifier.\\nROOT defaults to the current directory and may appear before or after options.\\n\\n  --signed-evidence-out FILE  Required path for signed verification evidence JSON.\\n  --strict-permissions        Require exact recorded permission bits.\\n\\nExit 0 only when deployment verification passes and signed evidence is written.\\n");exit(0);}'''
if oldphp in t: t=t.replace(oldphp,newphp,1)
signed.write_text(t,encoding='utf-8')

# Replace the closeout workflow usage with complete phase/options/examples help.
c=closeout.read_text(encoding='utf-8')
start=c.find('usage() {\n')
end=c.find('\n}\n\ndie()',start)
if start<0 or end<0:
    raise SystemExit('HELP-SURFACE-01: closeout usage() block missing')
usage=r'''usage() {
  cat <<'EOF'
Usage:
  closeout.sh prepare [--ref REF] [--out-dir DIR] [--ttl MINUTES] [--language php|python]
  closeout.sh finish --evidence FILE [--session DIR]
  closeout.sh status [--session DIR]

Safe two-phase Git-aware deployment closeout.

WORKFLOW
  1. Commit the exact deployment candidate; automated closeout refuses tracked/staged dirt
     and stray untracked source files.
  2. Run `make deploy-prepare` (or `closeout.sh prepare`).
  3. Deploy the prepared archive contents and the generated signed verifier.
  4. Run the exact verifier command printed by prepare against production.
  5. Bring the resulting signed evidence JSON back locally.
  6. Run `make deploy-closeout EVIDENCE=/path/to/file.json`.
  7. Closeout automatically ingests evidence, marks the prepared ref, then runs baseline,
     history, history-verify, and deploy-status gates.

PREPARE OPTIONS
  --ref REF              Git ref to prepare. Must resolve to current HEAD. Default: HEAD.
  --out-dir DIR          Session/artifact directory. Default: sibling deploy-pack-closeout-<ref>/.
  --ttl MINUTES          Signed verifier identity lifetime. Default: 60.
  --language php|python  Generated production verifier runtime. Default: php.

FINISH OPTIONS
  --evidence FILE        Required signed JSON emitted by the production verifier.
  --session DIR          Prepared closeout session. If omitted, the newest sibling session is used.

STATUS
  `status` prints closeout session metadata only. For deployment governance health use:
      deploy-pack deploy status

OUTPUTS FROM PREPARE
  deploy-<ref>.zip
  deploy-<ref>.verify-signed.<php|py>
  deploy-<ref>.verify-signed.<php|py>.public-key.json
  .deploy-pack-closeout-session.json

SAFETY / TRUST BOUNDARY
  This workflow does not upload files or claim that local preparation proves remote deployment.
  The production verifier is archive-bound and host-cooperative; it is not hostile-host or
  hardware attestation. Remove the signed verifier from production after evidence retrieval.
  Keep closeout artifacts outside the repository deployment surface.

EXAMPLES
  make deploy-prepare
  make deploy-prepare REF=HEAD TTL=60 LANGUAGE=php
  make deploy-closeout EVIDENCE=../deploy-pack-closeout-abc/deploy-abc.signed-evidence.json
  make deploy-closeout-status

For low-level command semantics use:
  deploy-pack remote-verifier --help
  deploy-pack ingest-signed-remote-evidence --help
  deploy-pack mark --help
  deploy-pack reconcile-baseline --help
EOF
}'''
c=c[:start]+usage+c[end+2:]
closeout.write_text(c,encoding='utf-8')

# Add one discoverable Make help target without disturbing the automation block.
m=mk.read_text(encoding='utf-8')
start_marker='# BEGIN DEPLOY-PACK-HELP-SURFACE-01'
end_marker='# END DEPLOY-PACK-HELP-SURFACE-01'
block='''# BEGIN DEPLOY-PACK-HELP-SURFACE-01\n.PHONY: deploy-help\n\ndeploy-help:\n\t@$(DEPLOY_PACK_CLOSEOUT_SCRIPT) --help\n# END DEPLOY-PACK-HELP-SURFACE-01'''
if start_marker in m:
    a=m.index(start_marker); b=m.index(end_marker,a)+len(end_marker)
    m=m[:a]+block+m[b:]
else:
    if m and not m.endswith('\n'): m+='\n'
    m+='\n'+block+'\n'
mk.write_text(m,encoding='utf-8')

# Patch version: help/documentation-only behavior, no deployment-state semantics changed.
version.write_text('1.14.1\n',encoding='utf-8')
for p in (init,pyproject):
    x=p.read_text(encoding='utf-8')
    x=re.sub(r'(?m)(__version__\s*=\s*["\'])1\.14\.0(["\'])',r'\g<1>1.14.1\2',x)
    x=re.sub(r'(?m)^(version\s*=\s*["\'])1\.14\.0(["\']\s*)$',r'\g<1>1.14.1\2',x)
    p.write_text(x,encoding='utf-8')

(doc:=docs/'DEPLOY-PACK-HELP-SURFACE-01.md').write_text('''# DEPLOY-PACK-HELP-SURFACE-01\n\nRelease: 1.14.1\n\n## Purpose\n\nAudit and complete deploy-pack's operator-facing help surface. This increment is primarily help/documentation, and also repairs the missing `reconcile-baseline` parser/dispatch registration caused by the earlier broad string-presence guard. Trust validation semantics are unchanged.\n\n## Audit finding\n\nSeveral mature commands exposed only terse one-line parser labels. Nested verifier/key/recovery/custody commands were especially under-documented, and generated signed verifiers showed only a usage line. The new closeout Make workflow also needed a single discoverable help entry point.\n\n## Updated surfaces\n\n- Rich descriptions, examples, safety notes, and argument semantics for Git selection, baseline/history, verification/evidence ingestion, verifier/key lifecycle, rollback, assurance, and recovery/custody commands.\n- Full `reconcile-baseline --help` explanation and constraints.\n- Expanded generated PHP/Python signed-verifier `--help`.\n- Expanded `tools/deploy-pack/scripts/closeout.sh --help`.\n- New `make deploy-help` workflow entry point.\n- Help regression test that walks the important command tree.\n\n## Routine operator entry points\n\n```sh\nmake deploy-help\ndeploy-pack --help\ndeploy-pack mark --help\ndeploy-pack reconcile-baseline --help\ndeploy-pack recovery --help\n```\n''',encoding='utf-8')
(manifests/'DEPLOY-PACK-HELP-SURFACE-01-MANIFEST.json').write_text(json.dumps({
  'increment':'DEPLOY-PACK-HELP-SURFACE-01','releaseVersion':'1.14.1','behaviorChange':'registration-repair-only',
  'surfaces':['top-level-cli','subcommand-cli','generated-signed-verifier','closeout-script','make-target'],
  'newMakeTarget':'deploy-help'
},indent=2)+'\n',encoding='utf-8')
PY

cat > "$TEST" <<'PY'
import contextlib, io, unittest
from deploy_pack.cli import parser

class HelpSurface01Tests(unittest.TestCase):
    def help_for(self,*argv):
        buf=io.StringIO()
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(buf):
            parser().parse_args([*argv,'--help'])
        self.assertEqual(cm.exception.code,0)
        return buf.getvalue()

    def assertHelp(self, argv, *needles):
        text=self.help_for(*argv)
        for needle in needles:
            self.assertIn(needle,text, f"{' '.join(argv)} help missing {needle!r}")

    def test_core_operator_help(self):
        self.assertHelp(('inspect',),'deployment surface','deployment ceiling')
        self.assertHelp(('baseline',),'currently running in production')
        self.assertHelp(('verify',),'local verification','remote-verifier')
        self.assertHelp(('remote-verifier',),'SIGNED WORKFLOW','public-key')
        self.assertHelp(('ingest-signed-remote-evidence',),'Public-key sidecar','normalized')
        self.assertHelp(('history-verify',),'hash-chain','post-closeout')

    def test_lifecycle_help(self):
        self.assertHelp(('verifier',),'short-lived verifier identities')
        self.assertHelp(('verifier','issue'),'fresh verifier identity')
        self.assertHelp(('verifier','show'),'lifecycle')
        self.assertHelp(('keys','revoke'),'audit reason')
        self.assertHelp(('rollback','plan'),'normal evidence/mark path')

    def test_recovery_help(self):
        self.assertHelp(('recovery',),'offline custody','SAFETY')
        self.assertHelp(('recovery','export'),'Signed export')
        self.assertHelp(('recovery','trust'),'offline trust-anchor custody')
        self.assertHelp(('recovery','trust','verify-quorum'),'quorum')
        self.assertHelp(('recovery','trust','checkpoint-verify'),'checkpoint')

    def test_reconciliation_is_registered_and_documented(self):
        p=parser()
        sub=next(a for a in p._actions if getattr(a,'choices',None) and 'mark' in a.choices)
        self.assertIn('reconcile-baseline', sub.choices)
        self.assertHelp(('reconcile-baseline',),'audited reconciliation','fresh signed remote evidence')
PY

# Syntax first.
echo "== syntax =="
"$PY" -m py_compile "$CLI" "$SIGNED" "$TEST"
bash -n "$CLOSEOUT"

run_cli() { PYTHONPATH="$TOOL/src${PYTHONPATH:+:$PYTHONPATH}" "$PY" -m deploy_pack.cli "$@"; }

echo
echo "== help audit smoke =="
for args in \
  "--help" \
  "inspect --help" \
  "baseline --help" \
  "verify --help" \
  "remote-verifier --help" \
  "ingest-signed-remote-evidence --help" \
  "verifier --help" \
  "verifier issue --help" \
  "keys revoke --help" \
  "rollback plan --help" \
  "recovery --help" \
  "recovery trust --help" \
  "recovery trust verify-quorum --help" \
  "recovery trust checkpoint-verify --help" \
  "reconcile-baseline --help"; do
  echo "  deploy-pack $args"
  # shellcheck disable=SC2086
  run_cli $args >/dev/null
done

"$CLOSEOUT" --help | grep -Fq 'SAFETY / TRUST BOUNDARY'
make -C "$REPO" -n deploy-help >/dev/null

echo
echo "== focused help regression =="
(
  cd "$TOOL"
  PYTHONPATH=src "$PY" -m unittest -v tests.test_help_surface01
)

echo
echo "== full deploy-pack regression =="
make -C "$REPO" test TOOL=deploy-pack

echo
echo "DEPLOY-PACK-HELP-SURFACE-01: PASS"
