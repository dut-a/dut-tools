from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .signed import write_signed_remote_verifier, ingest_signed_remote_evidence
from .lifecycle import issue as issue_verifier, get as get_verifier, revoke as revoke_verifier, validate as validate_verifier, assert_usable as assert_signed_evidence_usable, consume as consume_signed_evidence
from .keyring import load as load_keyring, register as register_signer, revoke as revoke_signer, export_bundle as export_recovery_bundle, import_bundle as import_recovery_bundle, verify_bundle as verify_recovery_bundle, trust_recovery_signer, revoke_recovery_signer, load_recovery_trust, verify_bundle_trusted as verify_recovery_bundle_trusted, import_bundle_trusted as import_recovery_bundle_trusted, export_offline_trust_anchor, verify_offline_trust_anchor, export_offline_trust_copies, verify_offline_trust_copy_set, verify_offline_trust_quorum, load_offline_checkpoints, verify_offline_checkpoint_history, offline_checkpoint_record
from .state import repository_lock, begin_mark_transaction, commit_mark_transaction, recover_mark_transaction
from .assurance import taxonomy as assurance_taxonomy, ASSURANCE_LEVELS
from .core import (
    BASELINE_FILE, DeployPackError, build_plan, read_baseline, repo_root, resolve_ref,
    verify_archive, verify_extracted_tree, write_baseline, write_package,
    write_remote_verifier, write_verification_evidence, validate_mark_evidence, ingest_remote_evidence,
    append_deployment_history, read_deployment_history, verify_deployment_history,
    deployment_history_record, format_deployment_history_record,
    build_rollback_plan,
    write_rollback_package,
    evidence_is_signed_remote,
    deployment_status,
)

def parser():
    p = argparse.ArgumentParser(prog="deploy-pack", description="Package and verify Git-based production deployments.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command")

    pack = sub.add_parser("pack")
    pack.add_argument("baseline", nargs="?")
    pack.add_argument("-o", "--output")
    pack.add_argument("--ignore", action="append", default=[])
    pack.add_argument("--include", action="append", default=[])
    pack.add_argument("--committed-only", action="store_true")
    pack.add_argument("--dry-run", action="store_true")

    inspect = sub.add_parser("inspect")
    inspect.add_argument("baseline", nargs="?")
    inspect.add_argument("--ignore", action="append", default=[])
    inspect.add_argument("--include", action="append", default=[])
    inspect.add_argument("--committed-only", action="store_true")

    mark = sub.add_parser("mark")
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

    assurance = sub.add_parser("assurance", help="Show the deployment-evidence assurance taxonomy.")
    asub = assurance.add_subparsers(dest="assurance_command")
    ashow = asub.add_parser("show", help="Show all assurance levels.")
    ashow.add_argument("--json", action="store_true")
    aex = asub.add_parser("explain", help="Explain one assurance level.")
    aex.add_argument("level", choices=list(ASSURANCE_LEVELS))
    aex.add_argument("--json", action="store_true")

    sub.add_parser("baseline")
    deploy = sub.add_parser("deploy")
    deploy_sub = deploy.add_subparsers(dest="deploy_command")
    deploy_status = deploy_sub.add_parser("status", help="Summarize deployment state and health.")
    deploy_status.add_argument("--json", action="store_true")
    deploy_status.add_argument("--quiet", action="store_true", help="Emit no output; communicate health via exit code only.")
    history = sub.add_parser("history")
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

    sub.add_parser("history-verify")
    rollback = sub.add_parser("rollback")
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

    verify = sub.add_parser("verify")
    verify.add_argument("archive")
    verify.add_argument("--checksum")
    verify.add_argument("--root")
    verify.add_argument("--evidence-out")
    verify.add_argument(
        "--strict-permissions",
        action="store_true",
        help="Require exact recorded permission bits for extracted-tree verification.",
    )

    rv = sub.add_parser("remote-verifier")
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

    ingest = sub.add_parser("ingest-remote-evidence")
    ingest.add_argument("remote_evidence")
    ingest.add_argument("archive")
    ingest.add_argument("-o", "--output")
    singest = sub.add_parser("ingest-signed-remote-evidence")
    singest.add_argument("signed_remote_evidence")
    singest.add_argument("archive")
    singest.add_argument("--public-key", required=True)
    singest.add_argument("-o", "--output")
    verifier = sub.add_parser("verifier")
    vsub = verifier.add_subparsers(dest="verifier_command")
    vi = vsub.add_parser("issue")
    vi.add_argument("--ttl-minutes", type=int, default=30)
    vs = vsub.add_parser("show")
    vs.add_argument("verifier_id")
    vr = vsub.add_parser("revoke")
    vr.add_argument("verifier_id")
    keys = sub.add_parser("keys")
    ksub = keys.add_subparsers(dest="keys_command")
    ksub.add_parser("show")
    krev = ksub.add_parser("revoke")
    krev.add_argument("fingerprint")
    krev.add_argument("--reason", required=True)
    recovery = sub.add_parser("recovery")
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
    return p

def show_plan(plan):
    print(f"Repository      : {plan.root}")
    print(f"Baseline ref    : {plan.baseline_ref}")
    print(f"Baseline commit : {plan.baseline_commit}")
    print(f"Current HEAD    : {plan.head_commit}")
    print(f"Deployable files: {len(plan.deployable)}")
    for c in plan.deployable:
        print(f"  {c.status:<8} {c.path} [{c.source}]")
    print(f"Ignored files   : {len(plan.ignored)}")
    for c in plan.ignored:
        print(f"  IGNORED  {c.path} [{c.source}]")
    print(f"Remote deletions: {len(plan.deletions)}")
    for c in plan.deletions:
        print(f"  DELETE   {c.path}")

def resolve_baseline(root, explicit):
    value = explicit or read_baseline(root)
    if not value:
        raise DeployPackError(
            "no deployment baseline recorded; use `deploy-pack mark <ref>` "
            "or pass a baseline explicitly"
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
                print("Remote usage: add --signed-evidence-out <file.json>")
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

        root = repo_root()

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
