"""Internal-pilot policy metadata, never a substitute for server enforcement.

This release can bind an operator's evidence reference to exact deployment material.
It cannot verify server retention, per-user ACLs or immutable nested releases yet.
Consequently even perfectly matching metadata cannot enable an internal pilot.
"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone

from app.agent.maxis.public_metadata import require_safe_public_text


POLICY_CONTRACT = "ltm-maxis-internal-pilot-policy.v1"
ATTESTATION_CONTRACT = "ltm-maxis-pilot-controls-attestation.v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_ATTESTATION_FIELDS = frozenset({
    "contract", "evidence_id", "artifact_sha256", "profile_material_sha256",
    "verified_at", "expires_at",
})
MISSING_VERIFIERS = (
    "Server retention enforcement evidence verification is not implemented.",
    "Pre-LLM user ACL and revocation enforcement evidence verification is not implemented.",
    "Immutable nested Workflow and Knowledge release evidence verification is not implemented.",
)


def default_policy() -> dict:
    return {
        "contract": POLICY_CONTRACT,
        "content_retention_hours": 24,
        "metadata_retention_days": 7,
        "audience_scope_sha256": "",
        "controls_attestation": {},
    }


def _instant(value: object) -> datetime:
    if not isinstance(value, str) or not _INSTANT.fullmatch(value):
        raise ValueError("Pilot control evidence timestamps must be canonical UTC instants.")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("Pilot control evidence timestamp is invalid.") from exc


def clean_policy(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != set(default_policy()):
        raise ValueError("Internal-pilot policy fields are incomplete or unsupported.")
    if value.get("contract") != POLICY_CONTRACT:
        raise ValueError("Unsupported internal-pilot policy contract.")
    for field, maximum in (("content_retention_hours", 24), ("metadata_retention_days", 7)):
        number = value.get(field)
        if type(number) is not int or not 0 <= number <= maximum:
            raise ValueError(f"Pilot {field} must be an integer from 0 to {maximum}.")
    audience = value.get("audience_scope_sha256")
    if not isinstance(audience, str) or (audience and not _DIGEST.fullmatch(audience)):
        raise ValueError("Pilot audience scope must be a lowercase SHA-256 digest.")
    attestation = value.get("controls_attestation")
    if not isinstance(attestation, dict):
        raise ValueError("Pilot controls attestation must be an object.")
    if attestation:
        if set(attestation) != _ATTESTATION_FIELDS:
            raise ValueError("Pilot controls attestation fields are incomplete or unsupported.")
        if attestation.get("contract") != ATTESTATION_CONTRACT:
            raise ValueError("Unsupported pilot controls attestation contract.")
        evidence_id = require_safe_public_text(attestation.get("evidence_id"), "pilot evidence id")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", evidence_id):
            raise ValueError("Invalid pilot controls evidence id.")
        for field in ("artifact_sha256", "profile_material_sha256"):
            if not isinstance(attestation.get(field), str) or not _DIGEST.fullmatch(attestation[field]):
                raise ValueError("Pilot control evidence bindings must be lowercase SHA-256 digests.")
        if _instant(attestation["expires_at"]) <= _instant(attestation["verified_at"]):
            raise ValueError("Pilot control evidence expiry must follow verification.")
    clean = deepcopy(value)
    if attestation:
        clean["controls_attestation"]["evidence_id"] = evidence_id
    return clean


def binding_policy(value: object) -> dict:
    """Policy material excludes its own attestation to avoid a circular binding."""
    clean = clean_policy(value)
    return {key: item for key, item in clean.items() if key != "controls_attestation"}


def controls_status(value: object, *, profile_material_sha256: str = "") -> dict:
    """Return closed, non-secret diagnostics even for manually corrupted preferences."""
    reasons = []
    try:
        policy = clean_policy(value)
    except ValueError:
        return {
            "configured": False, "bindingValid": False, "valid": False,
            "verification": "server_control_evidence_not_verified",
            "policy": {}, "attestation": {}, "expectedMaterialSha256": "",
            "reasons": ["Internal-pilot policy metadata is invalid.", *MISSING_VERIFIERS],
        }
    evidence = policy["controls_attestation"]
    if not policy["audience_scope_sha256"]:
        reasons.append("An explicit pilot audience scope binding is required.")
    if not evidence:
        reasons.append("Pilot controls evidence is not configured.")
    else:
        if not profile_material_sha256 or evidence["profile_material_sha256"] != profile_material_sha256:
            reasons.append("Pilot controls evidence is not bound to the current profile material.")
        now = datetime.now(timezone.utc)
        if _instant(evidence["verified_at"]) > now:
            reasons.append("Pilot control verification cannot be in the future.")
        if _instant(evidence["expires_at"]) <= now:
            reasons.append("Pilot controls evidence has expired.")
    return {
        "configured": bool(evidence), "bindingValid": bool(evidence) and not reasons,
        "valid": False,  # No config value or operator checkbox can enable this.
        "verification": "server_control_evidence_not_verified",
        "policy": binding_policy(policy), "attestation": evidence,
        "expectedMaterialSha256": (
            profile_material_sha256 if _DIGEST.fullmatch(profile_material_sha256) else ""
        ),
        "reasons": [*reasons, *MISSING_VERIFIERS],
    }
