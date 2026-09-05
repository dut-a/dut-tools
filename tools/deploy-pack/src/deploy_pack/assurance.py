from __future__ import annotations

from copy import deepcopy

ASSURANCE_SCHEMA_VERSION = 1

ASSURANCE_LEVELS = {
    "local": {
        "rank": 10,
        "supported": True,
        "authority": "operator-controlled-local-verification",
        "description": "Verification performed locally against an archive or extracted tree.",
        "claims": ["artifact-or-tree-integrity", "manifest-binding"],
        "doesNotClaim": ["remote-host-state", "independent-attestation", "host-compromise-resistance"],
    },
    "host-cooperative-remote": {
        "rank": 20,
        "supported": True,
        "authority": "target-host-self-verification",
        "description": "Verification code executes on the deployment host and reports its own state.",
        "claims": ["remote-deployed-tree-state", "manifest-binding"],
        "doesNotClaim": ["host-compromise-resistance", "independent-attestation", "hardware-rooted-attestation"],
    },
    "independent-observer": {
        "rank": 30,
        "supported": False,
        "authority": "independent-trust-domain",
        "description": "Reserved for verification by an observer outside the deployment host trust domain.",
        "claims": ["independently-observed-deployment-state"],
        "doesNotClaim": ["hardware-rooted-attestation"],
    },
    "platform-attested": {
        "rank": 40,
        "supported": False,
        "authority": "platform-or-hardware-attestation-root",
        "description": "Reserved for cryptographically verifiable platform or hardware-backed attestation.",
        "claims": ["attested-platform-state"],
        "doesNotClaim": [],
    },
}


def taxonomy() -> dict:
    return {
        "schemaVersion": ASSURANCE_SCHEMA_VERSION,
        "levels": deepcopy(ASSURANCE_LEVELS),
        "orderingNote": (
            "Ranks express increasing independence of the evidence authority, not a universal "
            "cryptographic-security score."
        ),
    }


def assurance_for(*, verification_scope: str | None, signed: bool = False, method: str | None = None) -> dict:
    level = "host-cooperative-remote" if verification_scope == "remote" else "local"
    base = deepcopy(ASSURANCE_LEVELS[level])
    result = {
        "schemaVersion": ASSURANCE_SCHEMA_VERSION,
        "level": level,
        "authority": base["authority"],
        "claims": list(base["claims"]),
        "doesNotClaim": list(base["doesNotClaim"]),
        "verificationMethod": method,
        "evidenceIntegrity": "ed25519-precommitted-verifier-key" if signed else "unsigned-record",
    }
    if signed and level == "host-cooperative-remote":
        result["claims"].append("evidence-integrity-after-host-signing")
        result["doesNotClaim"].append("host-honesty")
    return result


def infer_assurance(evidence: dict) -> dict:
    existing = evidence.get("assurance")
    if isinstance(existing, dict):
        validate_assurance(existing, evidence=evidence)
        return deepcopy(existing)
    signed = bool(evidence.get("signedRemoteEvidence"))
    return assurance_for(
        verification_scope=evidence.get("verificationScope"),
        signed=signed,
        method=evidence.get("verificationMethod"),
    )


def validate_assurance(value: dict, *, evidence: dict | None = None) -> None:
    if value.get("schemaVersion") != ASSURANCE_SCHEMA_VERSION:
        raise ValueError(f"unsupported assurance schema: {value.get('schemaVersion')}")
    level = value.get("level")
    if level not in ASSURANCE_LEVELS:
        raise ValueError(f"unsupported assurance level: {level}")
    if evidence is not None:
        scope = evidence.get("verificationScope")
        if scope == "remote" and level != "host-cooperative-remote":
            raise ValueError(
                "current deploy-pack remote verifier evidence may only claim "
                "host-cooperative-remote assurance"
            )
        if scope != "remote" and level != "local":
            raise ValueError("local deploy-pack evidence may only claim local assurance")
