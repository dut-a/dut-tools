from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .core import BASELINE_FILE, DeployPackError, LEDGER_FILE, read_baseline, read_deployment_history, deployment_history_anchor, deployment_history_chain_state, _upgrade_legacy_deployment_history
from .lifecycle import REPLAY_STATE_FILE, VERIFIER_STATE_FILE, _load, _write, assert_precommitted_signing_key
from .signed import read_public_key_file, public_fingerprint, generate_ephemeral_keypair, write_public_key_file
from .state import repository_lock, atomic_write_text

KEYRING_FILE = ".deploy-pack-keyring.json"
RECOVERY_SCHEMA_VERSION = 1


def _path(root: Path) -> Path:
    return root / KEYRING_FILE


def load(root: Path) -> dict:
    return _load(_path(root), {"schemaVersion":1,"signers":{}})


def save(root: Path, state: dict) -> None:
    _write(_path(root), state)


def register(root: Path, verifier_id: str, public_key_file: Path) -> dict:
    with repository_lock(root):
        return _locked_register(root, verifier_id, public_key_file)

def _locked_register(root: Path, verifier_id: str, public_key_file: Path) -> dict:
    raw=read_public_key_file(public_key_file); fp=public_fingerprint(raw); now=datetime.now(timezone.utc).isoformat()
    assert_precommitted_signing_key(root, verifier_id, fp)
    state=load(root); signers=state.setdefault("signers",{})
    rec=signers.get(fp)
    if rec is None:
        rec={"publicKeySha256":fp,"algorithm":"Ed25519","firstSeenAt":now,"lastSeenAt":now,"status":"active","revokedAt":None,"revocationReason":None,"verifierIds":[]}
        signers[fp]=rec
    rec["lastSeenAt"]=now
    if verifier_id not in rec.setdefault("verifierIds",[]): rec["verifierIds"].append(verifier_id)
    save(root,state); return rec


def revoke(root: Path, fingerprint: str, *, reason: str) -> dict:
    with repository_lock(root):
        return _locked_revoke(root, fingerprint, reason=reason)

def _locked_revoke(root: Path, fingerprint: str, *, reason: str) -> dict:
    state=load(root); rec=state.get("signers",{}).get(fingerprint)
    if not rec: raise DeployPackError(f"unknown signer public key: {fingerprint}")
    rec["status"]="revoked"; rec["revokedAt"]=datetime.now(timezone.utc).isoformat(); rec["revocationReason"]=reason; save(root,state)
    vpath=root/VERIFIER_STATE_FILE; vstate=_load(vpath,{"schemaVersion":1,"verifiers":{}})
    now=datetime.now(timezone.utc).isoformat()
    for vid in rec.get("verifierIds",[]):
        v=vstate.get("verifiers",{}).get(vid)
        if v and v.get("status") != "revoked": v["status"]="revoked"; v["revokedAt"]=now; v["revocationReason"]="signer-key-revoked"
    _write(vpath,vstate); return rec


def assert_signer_usable(root: Path, evidence: dict) -> None:
    state=load(root); signers=state.get("signers",{})
    signed=evidence.get("signedRemoteEvidence") or {}; fp=signed.get("publicKeySha256")
    # Backward compatibility for evidence created before DEPLOY-PACK-07. Once a
    # repository has key history, signer provenance becomes mandatory.
    if not signers and not fp:
        return
    if not signers and fp:
        return
    if not fp: raise DeployPackError("signed evidence lacks signer public-key fingerprint")
    rec=signers.get(fp)
    if not rec: raise DeployPackError("signed evidence signer is not registered in local key history")
    if rec.get("status") == "revoked": raise DeployPackError(f"signed evidence signer is revoked: {fp}")
    ident=(evidence.get("remoteEvidence") or {}).get("verifierIdentity") or {}; vid=ident.get("verifierId")
    if vid and vid not in rec.get("verifierIds",[]): raise DeployPackError("signed evidence verifier is not bound to signer key history")


RECOVERY_SIGNED_KIND = "deploy-pack.recovery-bundle.signed"
RECOVERY_SIGNED_SCHEMA_VERSION = 1


def _canonical(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _history_chain(history: list[dict]) -> list[dict]:
    chain=[]; previous="0"*64
    for index,record in enumerate(history,start=1):
        record_hash=hashlib.sha256(_canonical(record)).hexdigest()
        link={"index":index,"previousHash":previous,"recordHash":record_hash}
        link_hash=hashlib.sha256(_canonical(link)).hexdigest()
        chain.append({**link,"linkHash":link_hash}); previous=link_hash
    return chain


def _payload(root: Path) -> dict:
    history=read_deployment_history(root)
    return {
      "schemaVersion":RECOVERY_SCHEMA_VERSION,
      "exportedAt":datetime.now(timezone.utc).isoformat(),
      "keyring":load(root),
      "verifiers":_load(root/VERIFIER_STATE_FILE,{"schemaVersion":1,"verifiers":{}}),
      "replay":_load(root/REPLAY_STATE_FILE,{"schemaVersion":1,"consumed":{}}),
      "history":history,
      "historyChain":_history_chain(history),
      "baseline":read_baseline(root),
    }


def _verify_chain(payload: dict) -> None:
    if payload.get("historyChain") != _history_chain(payload.get("history") or []):
        raise DeployPackError("recovery bundle deployment-history hash chain is invalid")


def export_bundle(root: Path, output: Path, *, unsigned: bool=False):
    payload=_payload(root); output=output.resolve()
    if unsigned:
        output.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
        return output,None
    private_raw,public_raw=generate_ephemeral_keypair()
    msg=_canonical(payload); sig=Ed25519PrivateKey.from_private_bytes(private_raw).sign(msg)
    envelope={
      "schemaVersion":RECOVERY_SIGNED_SCHEMA_VERSION,
      "kind":RECOVERY_SIGNED_KIND,
      "payload":payload,
      "signature":{"algorithm":"Ed25519","payloadSha256":hashlib.sha256(msg).hexdigest(),"signatureBase64":base64.b64encode(sig).decode("ascii")},
      "signer":{"publicKeySha256":public_fingerprint(public_raw)},
    }
    output.write_text(json.dumps(envelope,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    public_file=output.with_suffix(output.suffix+".public-key.json")
    write_public_key_file(public_file,public_raw)
    return output,public_file


def verify_bundle(bundle: Path, public_key_file: Path) -> dict:
    try: envelope=json.loads(bundle.read_text(encoding="utf-8"))
    except Exception as exc: raise DeployPackError(f"invalid signed recovery bundle: {bundle}") from exc
    if envelope.get("schemaVersion")!=RECOVERY_SIGNED_SCHEMA_VERSION or envelope.get("kind")!=RECOVERY_SIGNED_KIND:
        raise DeployPackError("not a supported signed recovery bundle")
    payload=envelope.get("payload"); signature=envelope.get("signature") or {}; signer=envelope.get("signer") or {}
    if not isinstance(payload,dict) or signature.get("algorithm")!="Ed25519": raise DeployPackError("signed recovery bundle is incomplete")
    public_raw=read_public_key_file(public_key_file)
    if signer.get("publicKeySha256")!=public_fingerprint(public_raw): raise DeployPackError("recovery signer fingerprint mismatch")
    msg=_canonical(payload)
    if signature.get("payloadSha256")!=hashlib.sha256(msg).hexdigest(): raise DeployPackError("recovery payload SHA-256 mismatch")
    try:
        sig=base64.b64decode(signature["signatureBase64"],validate=True)
        Ed25519PublicKey.from_public_bytes(public_raw).verify(sig,msg)
    except Exception as exc: raise DeployPackError("recovery bundle signature verification failed") from exc
    if payload.get("schemaVersion")!=RECOVERY_SCHEMA_VERSION: raise DeployPackError("unsupported recovery payload schema")
    _verify_chain(payload); return payload


def _import_payload(root: Path, value: dict) -> dict:
    result={"signers":0,"verifiers":0,"replay":0,"history":0,"baseline":False}
    ks=load(root); ks.setdefault("signers",{})
    for fp,rec in (value.get("keyring") or {}).get("signers",{}).items():
        if fp not in ks["signers"]: ks["signers"][fp]=rec; result["signers"]+=1
    save(root,ks)
    vs=_load(root/VERIFIER_STATE_FILE,{"schemaVersion":1,"verifiers":{}}); vs.setdefault("verifiers",{})
    for vid,rec in (value.get("verifiers") or {}).get("verifiers",{}).items():
        if vid not in vs["verifiers"]: vs["verifiers"][vid]=rec; result["verifiers"]+=1
    _write(root/VERIFIER_STATE_FILE,vs)
    rs=_load(root/REPLAY_STATE_FILE,{"schemaVersion":1,"consumed":{}}); rs.setdefault("consumed",{})
    for k,rec in (value.get("replay") or {}).get("consumed",{}).items():
        if k not in rs["consumed"]: rs["consumed"][k]=rec; result["replay"]+=1
    _write(root/REPLAY_STATE_FILE,rs)
    hist=value.get("history") or []
    if not read_deployment_history(root) and hist:
        atomic_write_text(root/LEDGER_FILE, ''.join(json.dumps(rec,sort_keys=True,separators=(",",":"))+"\n" for rec in hist))
        result["history"]=len(hist)
    baseline=value.get("baseline")
    if not read_baseline(root) and baseline: atomic_write_text(root/BASELINE_FILE, baseline+"\n"); result["baseline"]=True
    return result


def import_bundle(root: Path, bundle: Path, *, public_key_file: Path|None=None, allow_unsigned: bool=False) -> dict:
    with repository_lock(root):
        return _locked_import_bundle(root, bundle, public_key_file=public_key_file, allow_unsigned=allow_unsigned)

def _locked_import_bundle(root: Path, bundle: Path, *, public_key_file: Path|None=None, allow_unsigned: bool=False) -> dict:
    if allow_unsigned:
        try: value=json.loads(bundle.read_text(encoding="utf-8"))
        except Exception as exc: raise DeployPackError(f"invalid recovery bundle: {bundle}") from exc
        if value.get("schemaVersion")!=RECOVERY_SCHEMA_VERSION: raise DeployPackError("unsupported recovery bundle schema")
        return _import_payload(root,value)
    if public_key_file is None: raise DeployPackError("signed recovery import requires --public-key")
    return _import_payload(root,verify_bundle(bundle,public_key_file))


RECOVERY_TRUST_FILE = ".deploy-pack-recovery-trust.json"
RECOVERY_TRUST_SCHEMA_VERSION = 1


def _trust_path(root: Path) -> Path:
    return root / RECOVERY_TRUST_FILE


def load_recovery_trust(root: Path) -> dict:
    state = _load(
        _trust_path(root),
        {"schemaVersion": RECOVERY_TRUST_SCHEMA_VERSION, "activeSigner": None, "signers": {}},
    )
    _validate_recovery_trust_semantics(state)
    return state


def save_recovery_trust(root: Path, state: dict) -> None:
    _validate_recovery_trust_semantics(state)
    _write(_trust_path(root), state)


def recovery_signer_id(public_key_file: Path) -> str:
    return public_fingerprint(read_public_key_file(public_key_file))[:24]


def trust_recovery_signer(
    root: Path,
    public_key_file: Path,
    *,
    activate: bool = False,
    predecessor: str | None = None,
    reason: str | None = None,
) -> dict:
    with repository_lock(root):
        return _locked_trust_recovery_signer(
            root, public_key_file, activate=activate, predecessor=predecessor, reason=reason
        )

def _locked_trust_recovery_signer(
    root: Path,
    public_key_file: Path,
    *,
    activate: bool = False,
    predecessor: str | None = None,
    reason: str | None = None,
) -> dict:
    raw = read_public_key_file(public_key_file)
    fp = public_fingerprint(raw)
    signer_id = fp[:24]
    state = load_recovery_trust(root)
    signers = state.setdefault("signers", {})
    now = datetime.now(timezone.utc).isoformat()

    if predecessor:
        prior = signers.get(predecessor)
        if not prior:
            raise DeployPackError(f"unknown predecessor recovery signer: {predecessor}")
        if prior.get("status") == "revoked":
            raise DeployPackError("cannot rotate from a revoked recovery signer")

    rec = signers.get(signer_id)
    if rec is None:
        rec = {
            "signerId": signer_id,
            "publicKeySha256": fp,
            "algorithm": "Ed25519",
            "trustedAt": now,
            "activatedAt": now if activate else None,
            "retiredAt": None,
            "revokedAt": None,
            "revocationReason": None,
            "status": "active" if activate else "trusted",
            "predecessorSignerId": predecessor,
            "trustReason": reason,
        }
        signers[signer_id] = rec
    elif rec.get("publicKeySha256") != fp:
        raise DeployPackError("recovery signer fingerprint mismatch")

    if activate:
        old_id = state.get("activeSigner")
        if old_id and old_id != signer_id:
            old = signers.get(old_id)
            if old and old.get("status") == "active":
                old["status"] = "retired"
                old["retiredAt"] = now
        rec["status"] = "active"
        rec["activatedAt"] = rec.get("activatedAt") or now
        state["activeSigner"] = signer_id

    save_recovery_trust(root, state)
    return rec


def revoke_recovery_signer(root: Path, signer_id: str, *, reason: str) -> dict:
    with repository_lock(root):
        return _locked_revoke_recovery_signer(root, signer_id, reason=reason)

def _locked_revoke_recovery_signer(root: Path, signer_id: str, *, reason: str) -> dict:
    state = load_recovery_trust(root)
    rec = state.get("signers", {}).get(signer_id)
    if not rec:
        raise DeployPackError(f"unknown recovery signer: {signer_id}")
    rec["status"] = "revoked"
    rec["revokedAt"] = datetime.now(timezone.utc).isoformat()
    rec["revocationReason"] = reason
    if state.get("activeSigner") == signer_id:
        state["activeSigner"] = None
    save_recovery_trust(root, state)
    return rec


def assert_recovery_signer_trusted(
    root: Path,
    public_key_file: Path,
    *,
    allow_retired: bool = True,
) -> dict:
    signer_id = recovery_signer_id(public_key_file)
    state = load_recovery_trust(root)
    rec = state.get("signers", {}).get(signer_id)
    if not rec:
        raise DeployPackError(
            "recovery signer is not trusted locally; "
            "use `deploy-pack recovery trust add <public-key>` first"
        )
    fp = public_fingerprint(read_public_key_file(public_key_file))
    if rec.get("publicKeySha256") != fp:
        raise DeployPackError("trusted recovery signer fingerprint mismatch")
    if rec.get("status") == "revoked":
        raise DeployPackError(f"recovery signer is revoked: {signer_id}")
    if rec.get("status") == "retired" and not allow_retired:
        raise DeployPackError(f"recovery signer is retired: {signer_id}")
    return rec


def verify_bundle_trusted(root: Path, bundle: Path, public_key_file: Path) -> dict:
    assert_recovery_signer_trusted(root, public_key_file)
    return verify_bundle(bundle, public_key_file)


def import_bundle_trusted(
    root: Path,
    bundle: Path,
    *,
    public_key_file: Path,
) -> dict:
    with repository_lock(root):
        return _locked_import_bundle_trusted(root, bundle, public_key_file=public_key_file)

def _locked_import_bundle_trusted(root: Path, bundle: Path, *, public_key_file: Path) -> dict:
    assert_recovery_signer_trusted(root, public_key_file)
    return _import_payload(root, verify_bundle(bundle, public_key_file))


OFFLINE_TRUST_ANCHOR_KIND = "deploy-pack.recovery-trust.offline-anchor"
OFFLINE_TRUST_ANCHOR_SCHEMA_VERSION = 1


def _offline_signer_chain(signers: dict) -> list[dict]:
    chain = []
    previous = "0" * 64
    for index, signer_id in enumerate(sorted(signers), start=1):
        record = signers[signer_id]
        record_hash = hashlib.sha256(_canonical(record)).hexdigest()
        link = {
            "index": index,
            "signerId": signer_id,
            "previousHash": previous,
            "recordHash": record_hash,
        }
        link_hash = hashlib.sha256(_canonical(link)).hexdigest()
        chain.append({**link, "linkHash": link_hash})
        previous = link_hash
    return chain


def _validate_recovery_trust_semantics(state: dict) -> None:
    signers = state.get("signers", {})
    active = state.get("activeSigner")
    allowed = {"active", "trusted", "retired", "revoked"}

    for signer_id, record in signers.items():
        status = record.get("status")
        if status not in allowed:
            raise DeployPackError(
                f"recovery trust signer {signer_id} has invalid status: {status!r}"
            )
        predecessor = record.get("predecessorSignerId")
        if predecessor and predecessor not in signers:
            raise DeployPackError(
                f"recovery trust signer {signer_id} references missing predecessor {predecessor}"
            )

    active_records = [
        signer_id for signer_id, record in signers.items()
        if record.get("status") == "active"
    ]
    if active:
        record = signers.get(active)
        if not record:
            raise DeployPackError(f"active recovery signer is missing: {active}")
        if record.get("status") != "active":
            raise DeployPackError(
                f"active recovery signer {active} has status {record.get('status')!r}"
            )
        if len(active_records) != 1 or active_records[0] != active:
            raise DeployPackError(
                "recovery trust must contain exactly one active signer record "
                "matching activeSigner"
            )
    elif active_records:
        raise DeployPackError(
            "recovery trust has active signer record(s) but activeSigner is unset"
        )

    # Detect predecessor cycles independently of record ordering.
    for signer_id in signers:
        seen = set()
        cursor = signer_id
        while cursor:
            if cursor in seen:
                raise DeployPackError(
                    f"recovery trust predecessor cycle detected at signer {cursor}"
                )
            seen.add(cursor)
            record = signers.get(cursor)
            cursor = record.get("predecessorSignerId") if record else None


def _offline_trust_payload(root: Path) -> dict:
    state = load_recovery_trust(root)
    _validate_recovery_trust_semantics(state)
    signers = state.get("signers", {})
    state_hash = hashlib.sha256(_canonical(state)).hexdigest()
    return {
        "schemaVersion": OFFLINE_TRUST_ANCHOR_SCHEMA_VERSION,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "activeSigner": state.get("activeSigner"),
        "recoveryTrust": state,
        "recoveryTrustSha256": state_hash,
        "signerChain": _offline_signer_chain(signers),
        "signerCounts": {
            status: sum(1 for rec in signers.values() if rec.get("status") == status)
            for status in ("active", "trusted", "retired", "revoked")
        },
    }


def export_offline_trust_anchor(root: Path, output: Path):
    payload = _offline_trust_payload(root)
    private_raw, public_raw = generate_ephemeral_keypair()
    message = _canonical(payload)
    signature = Ed25519PrivateKey.from_private_bytes(private_raw).sign(message)
    envelope = {
        "schemaVersion": OFFLINE_TRUST_ANCHOR_SCHEMA_VERSION,
        "kind": OFFLINE_TRUST_ANCHOR_KIND,
        "payload": payload,
        "signature": {
            "algorithm": "Ed25519",
            "payloadSha256": hashlib.sha256(message).hexdigest(),
            "signatureBase64": base64.b64encode(signature).decode("ascii"),
        },
        "signer": {
            "publicKeySha256": public_fingerprint(public_raw),
        },
    }

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    public_file = output.with_suffix(output.suffix + ".public-key.json")
    write_public_key_file(public_file, public_raw)

    artifact_sha = hashlib.sha256(output.read_bytes()).hexdigest()
    key_fp = public_fingerprint(public_raw)
    fingerprint_file = output.with_suffix(output.suffix + ".fingerprint.txt")
    fingerprint_file.write_text(
        "\n".join([
            "DEPLOY-PACK OFFLINE RECOVERY TRUST ANCHOR",
            f"artifact={output.name}",
            f"artifactSha256={artifact_sha}",
            f"publicKeySha256={key_fp}",
            f"payloadSha256={envelope['signature']['payloadSha256']}",
            f"activeRecoverySigner={payload.get('activeSigner') or '-'}",
            f"exportedAt={payload['exportedAt']}",
            "",
        ]),
        encoding="utf-8",
    )
    return output, public_file, fingerprint_file


def verify_offline_trust_anchor(
    anchor: Path,
    public_key_file: Path,
    *,
    expected_fingerprint: str | None = None,
) -> dict:
    try:
        envelope = json.loads(anchor.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DeployPackError(f"invalid offline trust-anchor artifact: {anchor}") from exc

    if envelope.get("schemaVersion") != OFFLINE_TRUST_ANCHOR_SCHEMA_VERSION:
        raise DeployPackError("unsupported offline trust-anchor schema")
    if envelope.get("kind") != OFFLINE_TRUST_ANCHOR_KIND:
        raise DeployPackError("not a deploy-pack offline recovery trust anchor")

    payload = envelope.get("payload")
    signature = envelope.get("signature") or {}
    signer = envelope.get("signer") or {}
    if not isinstance(payload, dict) or signature.get("algorithm") != "Ed25519":
        raise DeployPackError("offline trust-anchor envelope is incomplete")

    public_raw = read_public_key_file(public_key_file)
    actual_fp = public_fingerprint(public_raw)
    if signer.get("publicKeySha256") != actual_fp:
        raise DeployPackError("offline trust-anchor signer fingerprint mismatch")
    if expected_fingerprint and expected_fingerprint.strip().lower() != actual_fp.lower():
        raise DeployPackError("offline trust-anchor does not match expected fingerprint")

    message = _canonical(payload)
    payload_sha = hashlib.sha256(message).hexdigest()
    if signature.get("payloadSha256") != payload_sha:
        raise DeployPackError("offline trust-anchor payload SHA-256 mismatch")

    try:
        sig = base64.b64decode(signature["signatureBase64"], validate=True)
        Ed25519PublicKey.from_public_bytes(public_raw).verify(sig, message)
    except Exception as exc:
        raise DeployPackError("offline trust-anchor signature verification failed") from exc

    state = payload.get("recoveryTrust")
    if not isinstance(state, dict):
        raise DeployPackError("offline trust-anchor recovery trust state is missing")
    if payload.get("recoveryTrustSha256") != hashlib.sha256(_canonical(state)).hexdigest():
        raise DeployPackError("offline recovery trust-state SHA-256 mismatch")

    _validate_recovery_trust_semantics(state)
    expected_chain = _offline_signer_chain(state.get("signers", {}))
    if payload.get("signerChain") != expected_chain:
        raise DeployPackError("offline recovery signer hash chain is invalid")

    return payload


OFFLINE_CHECKPOINT_FILE = ".deploy-pack-offline-checkpoints.jsonl"
OFFLINE_CHECKPOINT_SCHEMA_VERSION = 1


def _offline_checkpoint_path(root: Path) -> Path:
    return root / OFFLINE_CHECKPOINT_FILE


def load_offline_checkpoints(root: Path) -> list[dict]:
    path = _offline_checkpoint_path(root)
    if not path.exists():
        return []
    records = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DeployPackError(f"invalid offline checkpoint history at {path}:{line_no}: {exc}") from exc
        if rec.get("schemaVersion") != OFFLINE_CHECKPOINT_SCHEMA_VERSION:
            raise DeployPackError(f"unsupported offline checkpoint schema at {path}:{line_no}")
        records.append(rec)
    return records


def _checkpoint_record_hash(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "checkpointHash"}
    return hashlib.sha256(_canonical(body)).hexdigest()


def verify_offline_checkpoint_history(root: Path) -> tuple[bool, list[str]]:
    records = load_offline_checkpoints(root)
    errors = []
    previous = "0" * 64
    expected_sequence = 1
    for rec in records:
        sequence = rec.get("sequence")
        if sequence != expected_sequence:
            errors.append(f"checkpoint sequence discontinuity: expected {expected_sequence}, got {sequence!r}")
        if rec.get("previousCheckpointHash") != previous:
            errors.append(f"checkpoint {sequence}: previousCheckpointHash mismatch")
        actual = _checkpoint_record_hash(rec)
        if rec.get("checkpointHash") != actual:
            errors.append(f"checkpoint {sequence}: checkpointHash mismatch")
        previous = rec.get("checkpointHash") or actual
        expected_sequence += 1
    return not errors, errors


def _checkpoint_identity(root: Path) -> tuple[int, str, str]:
    records = load_offline_checkpoints(root)
    if records:
        ok, errors = verify_offline_checkpoint_history(root)
        if not ok:
            raise DeployPackError("offline checkpoint history is invalid: " + "; ".join(errors))
        previous_hash = records[-1]["checkpointHash"]
        sequence = records[-1]["sequence"] + 1
    else:
        previous_hash = "0" * 64
        sequence = 1
    seed = f"{previous_hash}:{sequence}:{datetime.now(timezone.utc).isoformat()}"
    checkpoint_id = f"ocp-{sequence:06d}-{hashlib.sha256(seed.encode()).hexdigest()[:12]}"
    return sequence, previous_hash, checkpoint_id


def _write_offline_anchor_copy(payload: dict, output: Path):
    private_raw, public_raw = generate_ephemeral_keypair()
    message = _canonical(payload)
    signature = Ed25519PrivateKey.from_private_bytes(private_raw).sign(message)
    envelope = {
        "schemaVersion": OFFLINE_TRUST_ANCHOR_SCHEMA_VERSION,
        "kind": OFFLINE_TRUST_ANCHOR_KIND,
        "payload": payload,
        "signature": {
            "algorithm": "Ed25519",
            "payloadSha256": hashlib.sha256(message).hexdigest(),
            "signatureBase64": base64.b64encode(signature).decode("ascii"),
        },
        "signer": {"publicKeySha256": public_fingerprint(public_raw)},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(envelope, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    public_file = output.with_suffix(output.suffix + ".public-key.json")
    write_public_key_file(public_file, public_raw)
    artifact_sha = hashlib.sha256(output.read_bytes()).hexdigest()
    key_fp = public_fingerprint(public_raw)
    fingerprint_file = output.with_suffix(output.suffix + ".fingerprint.txt")
    fingerprint_file.write_text("\n".join([
        "DEPLOY-PACK OFFLINE RECOVERY TRUST ANCHOR",
        f"artifact={output.name}", f"artifactSha256={artifact_sha}",
        f"publicKeySha256={key_fp}", f"payloadSha256={envelope['signature']['payloadSha256']}",
        f"checkpointId={payload.get('checkpoint',{}).get('checkpointId','-')}",
        f"checkpointSequence={payload.get('checkpoint',{}).get('sequence','-')}",
        f"copyIndex={payload.get('custodyCopy',{}).get('copyIndex','-')}",
        f"activeRecoverySigner={payload.get('activeSigner') or '-'}", f"exportedAt={payload['exportedAt']}", "",
    ]), encoding="utf-8")
    return output, public_file, fingerprint_file, {"artifact":output.name,"artifactSha256":artifact_sha,"publicKeySha256":key_fp,"payloadSha256":envelope['signature']['payloadSha256']}


def _default_custody_quorum(copy_count: int) -> int:
    return (copy_count // 2) + 1


def export_offline_trust_copies(root: Path, output: Path, *, copies: int = 3, quorum: int | None = None) -> dict:
    with repository_lock(root):
        return _locked_export_offline_trust_copies(root, output, copies=copies, quorum=quorum)

def _locked_export_offline_trust_copies(root: Path, output: Path, *, copies: int = 3, quorum: int | None = None) -> dict:
    if copies < 2:
        raise DeployPackError("independent offline custody requires at least 2 copies")
    if quorum is None:
        quorum = _default_custody_quorum(copies)
    if quorum < 1 or quorum > copies:
        raise DeployPackError(f"custody quorum must be between 1 and copy count ({copies})")

    base_payload = _offline_trust_payload(root)
    sequence, previous_hash, checkpoint_id = _checkpoint_identity(root)
    checkpoint_created_at = datetime.now(timezone.utc).isoformat()
    trust_snapshot_sha = base_payload["recoveryTrustSha256"]
    suffix = output.suffix or ".json"
    stem = output.name[:-len(suffix)] if output.name.endswith(suffix) else output.name
    output_dir = output.resolve().parent

    ledger_records = read_deployment_history(root)
    ledger_state = deployment_history_chain_state(ledger_records)
    if ledger_state.get("mode") == "legacy-unhashed" and ledger_records:
        # Custody export is itself a security mutation: upgrade legacy history before anchoring it.
        ledger_records = _upgrade_legacy_deployment_history(ledger_records)
        atomic_write_text(root / LEDGER_FILE, ''.join(
            json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n" for rec in ledger_records
        ))
    ledger_anchor = deployment_history_anchor(root)
    signed_ledger_anchor = {
        "schemaVersion": 1,
        "chainMode": ledger_anchor.get("chainMode"),
        "recordCount": ledger_anchor.get("recordCount"),
        "headRecordHash": ledger_anchor.get("headRecordHash"),
        "headCommit": ledger_anchor.get("headCommit"),
    }

    copy_records=[]; artifacts=[]
    for index in range(1,copies+1):
        copy_id=f"{checkpoint_id}-copy-{index:02d}"
        payload=dict(base_payload)
        payload["checkpoint"]={
            "schemaVersion": OFFLINE_CHECKPOINT_SCHEMA_VERSION,
            "checkpointId": checkpoint_id,
            "sequence": sequence,
            "createdAt": checkpoint_created_at,
            "previousCheckpointHash": previous_hash,
            "recoveryTrustSha256": trust_snapshot_sha,
            "custodyPolicy": {"copyCount": copies, "quorum": quorum, "mode": "threshold"},
            "deploymentLedger": signed_ledger_anchor,
        }
        payload["custodyCopy"]={
            "copyId": copy_id, "copyIndex": index, "copyCount": copies,
            "quorum": quorum, "independentSigningKey": True,
        }
        copy_output=output_dir/f"{stem}.copy-{index:02d}{suffix}"
        anchor,pub,fingerprint,metadata=_write_offline_anchor_copy(payload,copy_output)
        copy_records.append({"copyId":copy_id,"copyIndex":index,**metadata})
        artifacts.append({"anchor":anchor,"publicKey":pub,"fingerprint":fingerprint})

    checkpoint_record={
        "schemaVersion": OFFLINE_CHECKPOINT_SCHEMA_VERSION,
        "sequence": sequence, "checkpointId": checkpoint_id,
        "createdAt": checkpoint_created_at,
        "previousCheckpointHash": previous_hash,
        "recoveryTrustSha256": trust_snapshot_sha,
        "recoveryTrustSnapshot": base_payload.get("recoveryTrust"),
        "activeRecoverySigner": base_payload.get("activeSigner"),
        "copyCount": copies, "quorum": quorum, "custodyMode": "threshold",
        "deploymentLedger": signed_ledger_anchor,
        "copies": copy_records,
    }
    checkpoint_record["checkpointHash"]=_checkpoint_record_hash(checkpoint_record)
    checkpoint_path = _offline_checkpoint_path(root)
    existing = checkpoint_path.read_text(encoding="utf-8") if checkpoint_path.exists() else ""
    atomic_write_text(checkpoint_path, existing + json.dumps(checkpoint_record,sort_keys=True,separators=(",",":"))+"\n")
    custody_manifest=output_dir/f"{stem}.checkpoint.json"
    custody_manifest.write_text(json.dumps(checkpoint_record,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return {"checkpoint":checkpoint_record,"manifest":custody_manifest,"artifacts":artifacts}


def _load_trusted_custody_checkpoint(
    *,
    checkpoint_id: str,
    root: Path | None = None,
    trusted_checkpoint: Path | None = None,
    expected_checkpoint_hash: str | None = None,
) -> tuple[dict, str]:
    """Resolve the pre-enrolled custody signer commitment for a checkpoint.

    HARDEN-14 deliberately refuses to treat public keys supplied beside custody
    copies as authority.  The authority must pre-exist either in this
    repository's hash-chained checkpoint history or in an explicitly supplied
    checkpoint manifest whose hash is pinned by the caller.
    """
    if trusted_checkpoint is not None:
        try:
            record = json.loads(trusted_checkpoint.read_text(encoding="utf-8"))
        except Exception as exc:
            raise DeployPackError(f"invalid trusted custody checkpoint: {trusted_checkpoint}") from exc
        if record.get("checkpointId") != checkpoint_id:
            raise DeployPackError("trusted checkpoint ID does not match custody copies")
        actual_hash = _checkpoint_record_hash(record)
        recorded_hash = record.get("checkpointHash")
        if not recorded_hash or recorded_hash != actual_hash:
            raise DeployPackError("trusted custody checkpoint hash is invalid")
        if not expected_checkpoint_hash:
            raise DeployPackError(
                "external trusted checkpoint requires --expected-checkpoint-hash; "
                "a checkpoint file cannot authenticate itself"
            )
        if recorded_hash != expected_checkpoint_hash:
            raise DeployPackError("trusted custody checkpoint does not match expected checkpoint hash")
        return record, "externally-pinned-checkpoint"

    if root is None:
        raise DeployPackError(
            "custody authenticity root is required; verify inside the repository or supply "
            "--trusted-checkpoint with --expected-checkpoint-hash"
        )
    ok, errors = verify_offline_checkpoint_history(root)
    if not ok:
        raise DeployPackError("local custody checkpoint history is invalid: " + "; ".join(errors))
    for record in load_offline_checkpoints(root):
        if record.get("checkpointId") == checkpoint_id:
            if expected_checkpoint_hash and record.get("checkpointHash") != expected_checkpoint_hash:
                raise DeployPackError("local custody checkpoint does not match expected checkpoint hash")
            return record, "local-checkpoint-history"
    raise DeployPackError(f"custody checkpoint is not pre-enrolled in trusted history: {checkpoint_id}")


def _authenticate_quorum_copies(records: list[dict], checkpoint: dict) -> None:
    enrolled = {
        rec.get("copyId"): rec.get("publicKeySha256")
        for rec in (checkpoint.get("copies") or [])
        if rec.get("copyId") and rec.get("publicKeySha256")
    }
    if not enrolled:
        raise DeployPackError("trusted checkpoint has no enrolled custody signer fingerprints")
    for rec in records:
        copy_id = rec.get("copyId")
        actual_fp = rec.get("publicKeySha256")
        expected_fp = enrolled.get(copy_id)
        if expected_fp is None:
            raise DeployPackError(f"custody copy is not enrolled by trusted checkpoint: {copy_id}")
        if actual_fp != expected_fp:
            raise DeployPackError(
                f"custody signer fingerprint is not enrolled for {copy_id}: "
                f"expected {expected_fp}, got {actual_fp}"
            )


def verify_offline_trust_quorum(
    anchors: list[Path],
    public_keys: list[Path],
    *,
    quorum: int | None = None,
    expected_fingerprints: list[str] | None = None,
    root: Path | None = None,
    trusted_checkpoint: Path | None = None,
    expected_checkpoint_hash: str | None = None,
) -> dict:
    if not anchors:
        raise DeployPackError("no offline custody copies supplied")
    if len(anchors) != len(public_keys):
        raise DeployPackError("anchor/public-key counts differ")
    if expected_fingerprints and len(expected_fingerprints) != len(anchors):
        raise DeployPackError("expected-fingerprint count differs from anchor count")

    rejected=[]; groups={}
    for idx,(anchor,pub) in enumerate(zip(anchors,public_keys)):
        expected=expected_fingerprints[idx] if expected_fingerprints else None
        try:
            payload=verify_offline_trust_anchor(anchor,pub,expected_fingerprint=expected)
            checkpoint=payload.get("checkpoint") or {}; custody=payload.get("custodyCopy") or {}
            if not checkpoint or not custody:
                raise DeployPackError("anchor lacks custody checkpoint metadata")
            fp=public_fingerprint(read_public_key_file(pub))
            rec={
                "anchor":str(anchor),"copyId":custody.get("copyId"),"copyIndex":custody.get("copyIndex"),
                "publicKeySha256":fp,"checkpointId":checkpoint.get("checkpointId"),"sequence":checkpoint.get("sequence"),
                "recoveryTrustSha256":checkpoint.get("recoveryTrustSha256"),
                "declaredCopyCount":(checkpoint.get("custodyPolicy") or {}).get("copyCount",custody.get("copyCount")),
                "declaredQuorum":(checkpoint.get("custodyPolicy") or {}).get("quorum",custody.get("quorum")),
            }
            key=(rec["checkpointId"],rec["sequence"],rec["recoveryTrustSha256"],rec["declaredCopyCount"],rec["declaredQuorum"])
            groups.setdefault(key,[]).append(rec)
        except Exception as exc:
            rejected.append({"anchor":str(anchor),"reason":str(exc)})
    if not groups:
        raise DeployPackError("no valid offline custody copies were supplied")
    ranked=sorted(groups.items(),key=lambda kv:len(kv[1]),reverse=True)
    best_key,best=ranked[0]
    if len(ranked)>1 and len(ranked[1][1])==len(best):
        raise DeployPackError("offline custody copies split evenly across conflicting checkpoints")
    checkpoint_id,sequence,trust_sha,declared_copy_count,declared_quorum=best_key

    trusted, authenticity_root = _load_trusted_custody_checkpoint(
        checkpoint_id=checkpoint_id,
        root=root,
        trusted_checkpoint=trusted_checkpoint,
        expected_checkpoint_hash=expected_checkpoint_hash,
    )
    if trusted.get("sequence") != sequence or trusted.get("recoveryTrustSha256") != trust_sha:
        raise DeployPackError("custody copies do not match the trusted checkpoint commitment")
    if trusted.get("copyCount") != declared_copy_count or trusted.get("quorum") != declared_quorum:
        raise DeployPackError("custody policy differs from trusted checkpoint commitment")
    _authenticate_quorum_copies(best, trusted)

    effective=quorum if quorum is not None else declared_quorum
    if effective is None:
        effective=_default_custody_quorum(int(declared_copy_count or len(anchors)))
    if effective<1 or (declared_copy_count and effective>int(declared_copy_count)):
        raise DeployPackError("invalid custody quorum")
    copy_ids=[r["copyId"] for r in best]; fps=[r["publicKeySha256"] for r in best]
    if len(set(copy_ids))!=len(copy_ids): raise DeployPackError("agreeing quorum contains duplicate custody copy identities")
    if len(set(fps))!=len(fps): raise DeployPackError("agreeing quorum is not independently signed")
    if len(best)<effective:
        raise DeployPackError(f"offline custody quorum not met: {len(best)} valid agreeing copy/copies, need {effective}")
    return {
        "checkpointId":checkpoint_id,"sequence":sequence,"recoveryTrustSha256":trust_sha,
        "checkpointHash":trusted.get("checkpointHash"),
        "authenticityRoot":authenticity_root,"custodySignersPreEnrolled":True,
        "declaredCopyCount":declared_copy_count,"declaredQuorum":declared_quorum,"effectiveQuorum":effective,
        "validAgreeingCopies":len(best),"quorumMet":True,"verifiedCopies":best,"rejectedCopies":rejected,
        "conflictingValidCopies":[r for _k,rs in ranked[1:] for r in rs],
    }

def verify_offline_trust_copy_set(
    anchors: list[Path],
    public_keys: list[Path],
    *,
    expected_fingerprints: list[str] | None = None,
    root: Path | None = None,
    trusted_checkpoint: Path | None = None,
    expected_checkpoint_hash: str | None = None,
) -> dict:
    result=verify_offline_trust_quorum(
        anchors, public_keys, quorum=len(anchors), expected_fingerprints=expected_fingerprints,
        root=root, trusted_checkpoint=trusted_checkpoint, expected_checkpoint_hash=expected_checkpoint_hash,
    )
    return {
        "checkpointId":result["checkpointId"],"sequence":result["sequence"],
        "checkpointHash":result["checkpointHash"],"authenticityRoot":result["authenticityRoot"],
        "custodySignersPreEnrolled":result["custodySignersPreEnrolled"],
        "recoveryTrustSha256":result["recoveryTrustSha256"],"verifiedCopies":result["verifiedCopies"],
        "copyCount":result["validAgreeingCopies"],
        "independentSigners":len({r["publicKeySha256"] for r in result["verifiedCopies"]}),
    }

def offline_checkpoint_record(root: Path, record: str):
    records=load_offline_checkpoints(root)
    if not records: raise DeployPackError("offline checkpoint history is empty")
    if record=="latest": return len(records),records[-1]
    try: number=int(record)
    except ValueError as exc: raise DeployPackError("checkpoint record must be a positive number or 'latest'") from exc
    if number<1 or number>len(records): raise DeployPackError(f"offline checkpoint record does not exist: {number}")
    return number,records[number-1]
