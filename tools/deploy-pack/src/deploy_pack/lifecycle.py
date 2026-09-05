from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .core import DeployPackError
from .state import atomic_write_json, repository_lock

VERIFIER_STATE_FILE = ".deploy-pack-verifiers.json"
REPLAY_STATE_FILE = ".deploy-pack-replay.json"


def _load(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DeployPackError(f"invalid deploy-pack state file: {path}") from exc


def _write(path: Path, value: dict) -> None:
    atomic_write_json(path, value)


def issue(root: Path, ttl_minutes: int = 30) -> dict:
    with repository_lock(root):
        return _issue_locked(root, ttl_minutes)

def _issue_locked(root: Path, ttl_minutes: int = 30) -> dict:
    if ttl_minutes < 1:
        raise DeployPackError("verifier TTL must be at least 1 minute")
    now = datetime.now(timezone.utc)
    value = {
        "verifierId": secrets.token_urlsafe(18),
        "issuedAt": now.isoformat(),
        "expiresAt": (now + timedelta(minutes=ttl_minutes)).isoformat(),
        "nonce": secrets.token_urlsafe(24),
        "expectedPublicKeySha256": None,
        "signingKeyCommittedAt": None,
    }
    path=root/VERIFIER_STATE_FILE
    state=_load(path,{"schemaVersion":1,"verifiers":{}})
    state.setdefault("verifiers",{})[value["verifierId"]] = {**value,"status":"active","revokedAt":None}
    _write(path,state)
    return value



def precommit_signing_key(root: Path, verifier_id: str, public_key_sha256: str) -> dict:
    with repository_lock(root):
        return _precommit_signing_key_locked(root, verifier_id, public_key_sha256)

def _precommit_signing_key_locked(root: Path, verifier_id: str, public_key_sha256: str) -> dict:
    if not isinstance(public_key_sha256, str) or len(public_key_sha256) != 64:
        raise DeployPackError("invalid verifier public-key fingerprint for precommitment")
    try:
        int(public_key_sha256, 16)
    except ValueError as exc:
        raise DeployPackError("invalid verifier public-key fingerprint for precommitment") from exc
    path = root / VERIFIER_STATE_FILE
    state = _load(path, {"schemaVersion":1,"verifiers":{}})
    rec = state.get("verifiers", {}).get(verifier_id)
    if not rec:
        raise DeployPackError(f"unknown verifier identity: {verifier_id}")
    if rec.get("status") == "revoked":
        raise DeployPackError(f"verifier identity is revoked: {verifier_id}")
    existing = rec.get("expectedPublicKeySha256")
    if existing and existing != public_key_sha256:
        raise DeployPackError(
            "verifier identity already has a different precommitted signing key: "
            f"{verifier_id}"
        )
    if not existing:
        rec["expectedPublicKeySha256"] = public_key_sha256
        rec["signingKeyCommittedAt"] = datetime.now(timezone.utc).isoformat()
        _write(path, state)
    return dict(rec)


def assert_precommitted_signing_key(root: Path, verifier_id: str, public_key_sha256: str) -> dict:
    rec = get(root, verifier_id)
    expected = rec.get("expectedPublicKeySha256")
    if not expected:
        raise DeployPackError(
            "verifier identity has no precommitted signing key; generate a new signed verifier with HARDEN-15+"
        )
    if expected != public_key_sha256:
        raise DeployPackError(
            "signed evidence public key does not match verifier key precommitment"
        )
    return rec

def get(root: Path, verifier_id: str) -> dict:
    state=_load(root/VERIFIER_STATE_FILE,{"schemaVersion":1,"verifiers":{}})
    rec=state.get("verifiers",{}).get(verifier_id)
    if not rec:
        raise DeployPackError(f"unknown verifier identity: {verifier_id}")
    return rec


def revoke(root: Path, verifier_id: str) -> dict:
    with repository_lock(root):
        return _revoke_locked(root, verifier_id)

def _revoke_locked(root: Path, verifier_id: str) -> dict:
    path=root/VERIFIER_STATE_FILE; state=_load(path,{"schemaVersion":1,"verifiers":{}})
    rec=state.get("verifiers",{}).get(verifier_id)
    if not rec:
        raise DeployPackError(f"unknown verifier identity: {verifier_id}")
    rec["status"]="revoked"; rec["revokedAt"]=datetime.now(timezone.utc).isoformat(); _write(path,state); return rec


def _dt(value: str) -> datetime:
    d=datetime.fromisoformat(value.replace("Z","+00:00")); return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def validate(root: Path, identity: dict, *, now: datetime|None=None) -> None:
    vid=identity.get("verifierId"); rec=get(root,vid)
    if rec.get("status") == "revoked": raise DeployPackError(f"verifier identity is revoked: {vid}")
    for k in ("nonce","issuedAt","expiresAt"):
        if identity.get(k) != rec.get(k): raise DeployPackError(f"verifier identity {k} does not match local issuance state")
    expected = rec.get("expectedPublicKeySha256")
    if expected:
        if identity.get("expectedPublicKeySha256") != expected:
            raise DeployPackError("verifier identity expectedPublicKeySha256 does not match local issuance state")
    at=now or datetime.now(timezone.utc)
    if at < _dt(rec["issuedAt"]): raise DeployPackError("verifier identity is not valid yet")
    if at > _dt(rec["expiresAt"]): raise DeployPackError(f"verifier identity expired at {rec['expiresAt']}")


def replay_key(evidence: dict) -> str:
    signed=evidence.get("signedRemoteEvidence") or {}; remote=evidence.get("remoteEvidence") or {}; ident=remote.get("verifierIdentity") or {}
    parts=[signed.get("sourceSha256"), signed.get("payloadSha256"), ident.get("verifierId"), ident.get("nonce")]
    if not all(parts): raise DeployPackError("signed evidence is missing replay-protection identity")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def assert_usable(root: Path, evidence: dict) -> str:
    from .keyring import assert_signer_usable
    assert_signer_usable(root, evidence)
    remote=evidence.get("remoteEvidence") or {}; ident=remote.get("verifierIdentity")
    if not isinstance(ident,dict): raise DeployPackError("signed remote evidence lacks verifier identity")
    validate(root,ident)
    verified=remote.get("verifiedAt")
    if verified:
        vd=_dt(verified)
        if vd < _dt(ident["issuedAt"]) or vd > _dt(ident["expiresAt"]): raise DeployPackError("remote verification timestamp is outside verifier validity window")
    key=replay_key(evidence)
    state=_load(root/REPLAY_STATE_FILE,{"schemaVersion":1,"consumed":{}})
    if key in state.get("consumed",{}): raise DeployPackError("signed remote evidence has already been consumed")
    return key


def consume(root: Path, key: str, *, evidence_path: Path, marked_ref: str, marked_commit: str) -> None:
    with repository_lock(root):
        return _consume_locked(root, key, evidence_path=evidence_path, marked_ref=marked_ref, marked_commit=marked_commit)

def _consume_locked(root: Path, key: str, *, evidence_path: Path, marked_ref: str, marked_commit: str) -> None:
    path=root/REPLAY_STATE_FILE; state=_load(path,{"schemaVersion":1,"consumed":{}}); consumed=state.setdefault("consumed",{})
    if key in consumed: raise DeployPackError("signed remote evidence has already been consumed")
    consumed[key]={"consumedAt":datetime.now(timezone.utc).isoformat(),"evidencePath":str(evidence_path),"markedRef":marked_ref,"markedCommit":marked_commit}; _write(path,state)
