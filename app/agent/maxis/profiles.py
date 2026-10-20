"""Runtime profiles and feature routing for MAXIS/Dify.

This module is deliberately separate from ``app.agent.profiles``.  A Dify app is a
versioned remote workflow, not an OpenAI-compatible model provider.  Candidate
profiles can be edited and probed without changing the active runtime profile.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from app.infra import prefs
from app.agent.maxis import credentials, pilot_policy
from app.agent.maxis.destination_policy import (
    ALLOWED_HOSTS_ENV_NAMES,
    DestinationPolicyError,
    canonical_maxis_api_base,
)
from app.agent.maxis.knowledge_manifest import (
    KnowledgeManifest,
    KnowledgeManifestError,
    build_manifest,
    canonical_dataset_id,
)
from app.agent.maxis.identifiers import canonical_published_workflow_id
from app.agent.maxis.knowledge_config import clean_path as clean_knowledge_path, resolve_path as knowledge_path
from app.agent.maxis.public_metadata import (
    credential_shaped_public_text,
    require_safe_public_text,
)


# Legacy IDs are retained only to preserve encrypted keys and deployment bindings.
PROFILE_NAMES = ("dev", "prod", "internal-pilot")
WORKFLOW_KEYS = credentials.WORKFLOW_KEYS
ROUTE_MODES = ("local", "shadow", "maxis")
DATA_POLICIES = ("synthetic_only", "internal_read_only")
EXECUTION_POLICIES = ("connected", "qualified")
REMOTE_LOG_MODES = ("redacted", "approved_bounded_content")
DEV_REMOTE_LOG_POLICY_ID = "synthetic-only"
PROBE_TTL_SECONDS = 24 * 60 * 60
PROBE_CONTRACT_VERSION = "ltm-maxis-probe.v2"
KNOWLEDGE_EVAL_ATTESTATION_CONTRACT = "ltm-maxis-knowledge-eval-attestation.v1"
KNOWLEDGE_EVAL_MATERIAL_CONTRACT = "ltm-maxis-knowledge-eval-material.v1"
# Release capabilities are explicit so configuration cannot claim a control that the
# implementation does not provide.  The validated 11-node Dify 1.11.1 bundle builds
# one delimiter-wrapped Role key, applies its metadata filter before retrieval reaches
# the LLM, and gates that same retrieval result through Knowledge Guard.
# This flag is for the legacy metadata-only attestation API, which never grants
# authority. PROD now uses qualification.py's independently enrolled Ed25519 verifier
# and complete, source-bound MAXIS-only evaluation instead.
SIGNED_EXTERNAL_EVAL_VERIFICATION_IMPLEMENTED = False
DETERMINISTIC_PRE_LLM_ROLE_ACL_IMPLEMENTED = True
_UTC_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_PUBLIC_METADATA_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_PUBLIC_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_PUBLIC_WORKFLOW_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_PUBLIC_PROBE_PARAMETERS = frozenset({
    "context_pack", "role_id", "correlation_id", "output_contract",
    "workflow_version", "knowledge_version",
})
_PUBLIC_PROBE_CODES = frozenset({
    "",
    "authentication",
    "bad_request",
    "binding_not_configured",
    "citation_contract_failed",
    "circuit_open",
    "configuration",
    "conflict",
    "contract_probe_failed",
    "credential_not_configured",
    "forbidden",
    "http",
    "invalid_response",
    "knowledge_retrieval_failed",
    "knowledge_version_mismatch",
    "missing_start_variables",
    "network",
    "not_found",
    "policy",
    "probe_failed",
    "rate_limited",
    "remote_status_not_ok",
    "response_too_large",
    "timeout",
    "unexpected_app_mode",
    "unexpected_start_variables",
    "upstream",
    "workflow_version_mismatch",
})
_PUBLIC_PROBE_ERRORS = frozenset({
    "",
    "Probe result binding is stale or missing.",
    "No configured workflow to probe.",
    "One or more workflow probes failed.",
})
_KNOWLEDGE_EVAL_ATTESTATION_FIELDS = frozenset({
    "contract", "evidence_id", "outcome", "case_count", "artifact_sha256",
    "passed_at", "suite_sha256",
    "dataset_id", "knowledge_manifest_sha256", "workflow_versions",
    "profile_material_sha256",
})
CONFIG_ENV_NAMES = {
    "base_url": "LAKE_MAXIS_BASE_URL",
    "workflow_bundle_version": "LAKE_MAXIS_WORKFLOW_BUNDLE_VERSION",
    "knowledge_version": "LAKE_MAXIS_KNOWLEDGE_VERSION",
    "knowledge_dataset_id": "LAKE_MAXIS_KNOWLEDGE_DATASET_ID",
    "knowledge_sentinel_sha256": "LAKE_MAXIS_KNOWLEDGE_SENTINEL_SHA256",
    "remote_log_policy_id": "LAKE_MAXIS_REMOTE_LOG_POLICY_ID",
    "remote_log_mode": "LAKE_MAXIS_REMOTE_LOG_MODE",
    "remote_log_retention_days": "LAKE_MAXIS_REMOTE_LOG_RETENTION_DAYS",
    "knowledge_eval_attestation": "LAKE_MAXIS_KNOWLEDGE_EVAL_ATTESTATION",
    "internal_pilot_policy": "LAKE_MAXIS_INTERNAL_PILOT_POLICY",
}
WORKFLOW_VERSION_ENV_NAMES = {
    "agent_core": "LAKE_MAXIS_AGENT_CORE_VERSION",
    "editor_author": "LAKE_MAXIS_EDITOR_AUTHOR_VERSION",
    "pmo_brief": "LAKE_MAXIS_PMO_BRIEF_VERSION",
}

# Existing canonical semantic roles are routed through one of the three Dify apps.
_ROLE_WORKFLOW = {
    "editor_author": "editor_author",
    "compose": "editor_author",
    "portfolio_analyst": "pmo_brief",
    "pmo_brief": "pmo_brief",
    "request_architect": "agent_core",
    "query_specialist": "agent_core",
    "research_analyst": "agent_core",
    "knowledge_curator": "agent_core",
    "work_architect": "agent_core",
    "people_advisor": "agent_core",
    "auditor": "agent_core",
    "result_integrator": "agent_core",
    "agent_core": "agent_core",
}
_DEFAULT_BINDINGS = {
    "agent_core": "agent_core",
    "editor_author": "editor_author",
    "pmo_brief": "pmo_brief",
}
_LOCK = threading.RLock()


class _CrossEnvironmentSeparationError(ValueError):
    """A fixed-vocabulary error safe to project onto the status API."""

    def __init__(self, categories: list[str]):
        self.categories = tuple(categories)
        super().__init__(
            "Dev/prod/internal-pilot MAXIS assets must be independent; overlapping asset "
            "categories: " + ", ".join(self.categories) + "."
        )


def _default_profile(name: str) -> dict:
    return {
        "name": name,
        "display_name": (
            f"MAXIS 연결 {PROFILE_NAMES.index(name) + 1}" if name in PROFILE_NAMES else "MAXIS 연결"
        ),
        "stage": name if name in PROFILE_NAMES else "prod",
        "execution_policy": "qualified" if name in {"prod", "internal-pilot"} else "connected",
        "base_url": "",
        "data_policy": "synthetic_only" if name == "dev" else "internal_read_only",
        "workflow_bundle_version": "",
        "knowledge_version": "",
        "knowledge_dataset_id": "",
        "knowledge_source_manifest": "",
        "knowledge_environment_binding": "",
        "knowledge_sentinel_sha256": "",
        # This is a manager attestation about the approved remote service policy,
        # not technical proof of Dify's workspace settings.  Production activation
        # therefore also requires a successful post-configuration probe.
        "remote_log_policy_id": DEV_REMOTE_LOG_POLICY_ID if name == "dev" else "",
        "remote_log_mode": "redacted" if name == "dev" else "",
        "remote_log_retention_days": 0 if name == "dev" else None,
        # Human/CI attestation pointing at an externally retained remote 25-case
        # result artifact. LTM verifies only metadata bindings, never artifact content.
        "knowledge_eval_attestation": {},
        **({"internal_pilot_policy": pilot_policy.default_policy()}
           if name == "internal-pilot" else {}),
        "workflows": {
            key: {"binding": binding, "version": ""}
            for key, binding in _DEFAULT_BINDINGS.items()
        },
    }


def _stored_rows(state: dict | None = None) -> dict[str, dict]:
    raw = (prefs.load() if state is None else state).get("maxisProfiles") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    rows = {}
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            name = credentials.normal_profile_name(row.get("name"))
        except ValueError:
            continue
        if row.get("name") == name:
            rows[name] = deepcopy(row)
    return rows


def profile_names() -> tuple[str, ...]:
    return tuple(dict.fromkeys((*PROFILE_NAMES, *_stored_rows())))


def _clean_display_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("MAXIS display name must be text.")
    label = require_safe_public_text(value, "MAXIS display name")
    if not label or len(label) > 80 or any(ord(char) < 32 for char in label):
        raise ValueError("MAXIS display name must contain 1–80 visible characters.")
    return label


def _merged_profile(name: str, *, stored_rows: dict[str, dict] | None = None) -> dict:
    credentials.normal_profile_name(name)
    out = _default_profile(name)
    saved = (_stored_rows() if stored_rows is None else stored_rows).get(name) or {}
    for key in (
        "display_name", "stage", "execution_policy",
        "base_url", "data_policy", "workflow_bundle_version", "knowledge_version",
        "knowledge_dataset_id", "knowledge_source_manifest", "knowledge_environment_binding",
        "knowledge_sentinel_sha256", "remote_log_policy_id", "remote_log_mode",
    ):
        if key in saved:
            out[key] = str(saved.get(key) or "").strip()
    if "remote_log_retention_days" in saved:
        out["remote_log_retention_days"] = saved.get("remote_log_retention_days")
    if "knowledge_eval_attestation" in saved:
        value = saved.get("knowledge_eval_attestation")
        out["knowledge_eval_attestation"] = deepcopy(value) if isinstance(value, dict) else value
    if "internal_pilot_policy" in saved:
        out["internal_pilot_policy"] = deepcopy(saved["internal_pilot_policy"])
    saved_workflows = saved.get("workflows") if isinstance(saved.get("workflows"), dict) else {}
    for workflow_key in WORKFLOW_KEYS:
        row = saved_workflows.get(workflow_key)
        if isinstance(row, dict):
            out["workflows"][workflow_key] = {
                "binding": str(row.get("binding") or "").strip(),
                "version": str(row.get("version") or "").strip(),
            }
    return out


def _normal_base_url(raw: str, stage: str) -> str:
    value = str(raw or "").strip().rstrip("/")
    if not value:
        return ""
    return canonical_maxis_api_base(
        value, stage=stage, require_https=stage != "dev",
    )


def _clean_version(value: object, label: str) -> str:
    result = require_safe_public_text(value, label)
    # Keep activation-time validation aligned with the Dify ``workflow_id`` and
    # audit wire contracts. This prevents a candidate from probing successfully and
    # then failing only on its first real request, and avoids storing pasted secrets
    # or prose in metadata-only measurements.
    if result and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", result):
        raise ValueError(f"Invalid {label}.")
    return result


def _clean_dataset_id(value: object) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    try:
        return canonical_dataset_id(candidate)
    except KnowledgeManifestError as exc:
        raise ValueError(str(exc)) from exc


def _clean_digest(value: object, label: str) -> str:
    candidate = str(value or "").strip()
    if candidate and not re.fullmatch(r"[0-9a-f]{64}", candidate):
        raise ValueError(f"{label} must be one lowercase SHA-256 digest.")
    return candidate


def _clean_retention_days(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Remote log retention days must be an integer from 0 to 90.")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"(?:0|[1-9][0-9]*)", value.strip()):
        result = int(value.strip())
    else:
        raise ValueError("Remote log retention days must be an integer from 0 to 90.")
    if not 0 <= result <= 90:
        raise ValueError("Remote log retention days must be between 0 and 90.")
    return result


def _clean_utc_instant(value: object, label: str) -> str:
    raw = str(value or "").strip()
    if not _UTC_INSTANT.fullmatch(raw):
        raise ValueError(f"{label} must be a canonical UTC ISO timestamp ending in Z.")
    try:
        datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"Invalid {label}.") from exc
    return raw


def _clean_knowledge_eval_attestation(value: object) -> dict:
    """Validate untrusted attestation metadata without treating it as proof.

    The referenced artifact stays outside LTM.  These fields only let the runtime bind
    an operator/CI assertion to one exact, non-secret profile material snapshot.
    """

    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("Knowledge eval attestation must be an object.")
    if not value:
        return {}
    if set(value) != _KNOWLEDGE_EVAL_ATTESTATION_FIELDS:
        raise ValueError("Knowledge eval attestation fields are incomplete.")
    if value.get("contract") != KNOWLEDGE_EVAL_ATTESTATION_CONTRACT:
        raise ValueError("Unsupported Knowledge eval attestation contract.")
    if value.get("outcome") != "passed" or value.get("case_count") != 25:
        raise ValueError(
            "Knowledge eval attestation must record outcome=passed and case_count=25."
        )
    evidence_id = _clean_version(value.get("evidence_id"), "Knowledge eval evidence id")
    if not evidence_id:
        raise ValueError("Knowledge eval evidence id is required.")
    workflow_versions = value.get("workflow_versions")
    if not isinstance(workflow_versions, dict) or set(workflow_versions) != set(WORKFLOW_KEYS):
        raise ValueError("Knowledge eval workflow_versions must cover every workflow.")
    clean_versions = {
        key: _clean_version(workflow_versions.get(key), f"{key} eval workflow version")
        for key in WORKFLOW_KEYS
    }
    if any(not version for version in clean_versions.values()):
        raise ValueError(
            "Knowledge eval workflow_versions must identify every evaluated workflow."
        )
    dataset_id = _clean_dataset_id(value.get("dataset_id"))
    if not dataset_id:
        raise ValueError("Knowledge eval dataset_id is required.")
    digests = {
        "artifact_sha256": _clean_digest(
            value.get("artifact_sha256"), "Knowledge eval artifact"
        ),
        "suite_sha256": _clean_digest(
            value.get("suite_sha256"), "Knowledge eval suite"
        ),
        "knowledge_manifest_sha256": _clean_digest(
            value.get("knowledge_manifest_sha256"), "Knowledge eval manifest"
        ),
        "profile_material_sha256": _clean_digest(
            value.get("profile_material_sha256"), "Knowledge eval profile material"
        ),
    }
    if any(not digest for digest in digests.values()):
        raise ValueError("Every Knowledge eval SHA-256 binding is required.")
    return {
        "contract": KNOWLEDGE_EVAL_ATTESTATION_CONTRACT,
        "evidence_id": evidence_id,
        "outcome": "passed",
        "case_count": 25,
        "artifact_sha256": digests["artifact_sha256"],
        "passed_at": _clean_utc_instant(
            value.get("passed_at"), "Knowledge eval passed_at"
        ),
        "suite_sha256": digests["suite_sha256"],
        "dataset_id": dataset_id,
        "knowledge_manifest_sha256": digests["knowledge_manifest_sha256"],
        "workflow_versions": clean_versions,
        "profile_material_sha256": digests["profile_material_sha256"],
    }


def _validate_remote_log_attestation(profile: dict, *, required: bool) -> None:
    """Validate a manager attestation; this does not inspect Dify workspace settings."""
    policy_id = str(profile.get("remote_log_policy_id") or "").strip()
    mode = str(profile.get("remote_log_mode") or "").strip().lower()
    retention = profile.get("remote_log_retention_days")
    if profile.get("data_policy") == "synthetic_only":
        if (policy_id != DEV_REMOTE_LOG_POLICY_ID or mode != "redacted"
                or retention != 0):
            raise ValueError(
                "A synthetic-only profile requires the fixed redacted/0-day "
                "remote log policy."
            )
        return
    if mode and mode not in REMOTE_LOG_MODES:
        raise ValueError(
            "Remote log mode must be redacted or approved_bounded_content."
        )
    if required and (not policy_id or not mode or retention is None):
        raise ValueError(
            "Internal MAXIS requires a manager-attested remote log policy id, mode, and "
            "retention days before probe/activation. This attestation does not "
            "technically verify the Dify workspace settings."
        )


def _build_profile_manifest(
    profile: dict, *, required: bool,
) -> KnowledgeManifest | None:
    version = str(profile.get("knowledge_version") or "").strip()
    dataset_id = str(profile.get("knowledge_dataset_id") or "").strip()
    if not version or not dataset_id:
        if required:
            raise ValueError(
                "Knowledge version and Dify Knowledge dataset UUID are required."
            )
        return None
    try:
        from app.agent.maxis.knowledge_config import profile_binding_sha256
        profile_binding_sha256(profile)
        return build_manifest(
            knowledge_version=version,
            dataset_id=dataset_id,
            config_path=knowledge_path(profile.get("knowledge_source_manifest")),
        )
    except (KnowledgeManifestError, ValueError) as exc:
        raise ValueError(f"Invalid MAXIS knowledge manifest: {exc}") from exc


def knowledge_eval_attestation_binding(profile: dict | str = "") -> dict:
    """Return the exact non-secret material an external eval must attest.

    This digest intentionally excludes API keys and the attestation itself.  It is a
    binding target, not verification that LTM opened, signed, or replayed the external
    result artifact.
    """

    if isinstance(profile, dict):
        candidate = profile
    else:
        name = str(profile or active_profile_name()).strip().lower()
        if not name:
            raise ValueError("No MAXIS profile for Knowledge eval attestation.")
        candidate = _effective(get(name))
    manifest = _build_profile_manifest(candidate, required=True)
    assert manifest is not None
    workflows = candidate.get("workflows") or {}
    workflow_versions = {
        key: str((workflows.get(key) or {}).get("version") or "").strip()
        for key in WORKFLOW_KEYS
    }
    material = {
        "contract": KNOWLEDGE_EVAL_MATERIAL_CONTRACT,
        "profile": {
            key: candidate.get(key)
            for key in (
                "name", "stage", "execution_policy", "base_url", "data_policy",
                "workflow_bundle_version", "knowledge_version",
                "knowledge_dataset_id", "knowledge_sentinel_sha256",
                "remote_log_policy_id", "remote_log_mode",
                "remote_log_retention_days",
            )
        },
        "knowledge_manifest_sha256": manifest.sha256,
        "workflows": {
            key: {
                "binding": str((workflows.get(key) or {}).get("binding") or ""),
                "version": workflow_versions[key],
            }
            for key in WORKFLOW_KEYS
        },
    }
    from app.agent.maxis.knowledge_config import profile_binding_sha256
    private_binding = profile_binding_sha256(candidate)
    if private_binding:
        material["knowledge_environment_sha256"] = private_binding
    if candidate.get("stage") == "internal-pilot":
        material["internal_pilot_policy"] = pilot_policy.binding_policy(
            candidate.get("internal_pilot_policy")
        )
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        "suite_sha256": str(manifest.governance["eval_suite_sha256"]),
        "dataset_id": str(candidate.get("knowledge_dataset_id") or ""),
        "knowledge_manifest_sha256": manifest.sha256,
        "workflow_versions": workflow_versions,
        "profile_material_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def knowledge_eval_attestation_status(profile: dict | str = "") -> dict:
    """Expose binding diagnostics without treating self-attested metadata as proof."""

    verification = "metadata_binding_only_signed_artifact_not_verified"
    implementation_reasons = []
    if not SIGNED_EXTERNAL_EVAL_VERIFICATION_IMPLEMENTED:
        implementation_reasons.append(
            "Signed external Knowledge eval artifact verification is not implemented."
        )
    if not DETERMINISTIC_PRE_LLM_ROLE_ACL_IMPLEMENTED:
        implementation_reasons.append(
            "Deterministic pre-LLM Role ACL enforcement is not implemented."
        )

    if isinstance(profile, dict):
        candidate = profile
    else:
        name = str(profile or active_profile_name()).strip().lower()
        if not name:
            raise ValueError("No MAXIS profile for Knowledge eval attestation.")
        candidate = _effective(get(name))
    raw = candidate.get("knowledge_eval_attestation")
    try:
        attestation = _clean_knowledge_eval_attestation(raw)
    except ValueError:
        return {
            "configured": bool(raw),
            "bindingValid": False,
            "valid": False,
            "verification": verification,
            "reasons": [
                "Knowledge eval attestation metadata is invalid.",
                *implementation_reasons,
            ],
            "attestation": {},
            "expectedBinding": {},
        }
    if str(candidate.get("stage") or "") not in {"prod", "internal-pilot"}:
        return {
            "configured": bool(attestation),
            "bindingValid": False,
            "valid": False,
            "verification": verification,
            "reasons": ["Knowledge eval attestation is required only for internal authority."],
            "attestation": {},
            "expectedBinding": {},
        }
    try:
        expected = knowledge_eval_attestation_binding(candidate)
    except ValueError:
        return {
            "configured": bool(attestation),
            "bindingValid": False,
            "valid": False,
            "verification": verification,
            "reasons": [
                "Knowledge eval profile binding is invalid.",
                *implementation_reasons,
            ],
            "attestation": {},
            "expectedBinding": {},
        }
    reasons: list[str] = []
    if not attestation:
        reasons.append("A remote 25-case Knowledge eval attestation is not configured.")
    else:
        for field in (
            "suite_sha256", "dataset_id", "knowledge_manifest_sha256",
            "workflow_versions", "profile_material_sha256",
        ):
            if attestation.get(field) != expected[field]:
                reasons.append(f"Knowledge eval {field} is not bound to the current profile.")
        passed = datetime.fromisoformat(attestation["passed_at"][:-1] + "+00:00")
        if passed > datetime.now(timezone.utc):
            reasons.append("Knowledge eval passed_at cannot be in the future.")
    public_attestation = ({
        "contract": attestation["contract"],
        "evidenceId": attestation["evidence_id"],
        "outcome": attestation["outcome"],
        "caseCount": attestation["case_count"],
        "artifactSha256": attestation["artifact_sha256"],
        "passedAt": attestation["passed_at"],
        "suiteSha256": attestation["suite_sha256"],
        "datasetId": attestation["dataset_id"],
        "knowledgeManifestSha256": attestation["knowledge_manifest_sha256"],
        "workflowVersions": dict(attestation["workflow_versions"]),
        "profileMaterialSha256": attestation["profile_material_sha256"],
    } if attestation else {})
    workflow_versions_sha256 = hashlib.sha256(json.dumps(
        expected["workflow_versions"], ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    binding_valid = bool(attestation) and not reasons
    return {
        "configured": bool(attestation),
        "bindingValid": binding_valid,
        # ``valid`` is an authority signal.  A matching metadata object can make
        # ``bindingValid`` true, but cannot make this true while any required
        # implementation capability above remains unavailable.
        "valid": binding_valid and not implementation_reasons,
        "verification": verification,
        "reasons": [*reasons, *implementation_reasons],
        "attestation": public_attestation,
        "expectedBinding": {
            "suiteSha256": expected["suite_sha256"],
            "datasetId": expected["dataset_id"],
            "knowledgeManifestSha256": expected["knowledge_manifest_sha256"],
            "workflowVersionsSha256": workflow_versions_sha256,
            "profileMaterialSha256": expected["profile_material_sha256"],
        },
    }


def _require_current_knowledge_eval_attestation(profile: dict) -> None:
    if str(profile.get("stage") or "") not in {"prod", "internal-pilot"}:
        return
    status = knowledge_eval_attestation_status(profile)
    if not status["valid"]:
        raise ValueError(
            "Prod MAXIS authority requires deterministic pre-LLM Role ACL enforcement "
            "and signed external Knowledge eval verification: "
            + "; ".join(status["reasons"])
        )


def internal_pilot_controls_status(profile: dict | str = "internal-pilot") -> dict:
    """Expose deployment blockers without treating metadata binding as enforcement."""
    candidate = profile if isinstance(profile, dict) else _effective(get(profile))
    material_sha = ""
    try:
        material_sha = knowledge_eval_attestation_binding(candidate)["profile_material_sha256"]
    except ValueError:
        pass
    return pilot_policy.controls_status(
        candidate.get("internal_pilot_policy"), profile_material_sha256=material_sha,
    )


def _require_internal_pilot_controls(profile: dict) -> None:
    if profile.get("stage") != "internal-pilot":
        return
    state = internal_pilot_controls_status(profile)
    if not state["valid"]:
        raise ValueError("Internal-pilot server controls are not verified: " + "; ".join(state["reasons"]))


def _validate(profile: dict, *, activation: bool = False,
              activation_workflows: list[str] | None = None) -> dict:
    name = str(profile.get("name") or "").strip().lower()
    credentials.normal_profile_name(name)
    stage = str(profile.get("stage") or "")
    if stage not in PROFILE_NAMES:
        raise ValueError("Invalid MAXIS transport policy.")
    if name in PROFILE_NAMES and stage != name:
        raise ValueError("Legacy MAXIS transport policy is immutable.")
    policy = str(profile.get("data_policy") or "").strip()
    if policy not in DATA_POLICIES:
        raise ValueError("Unsupported MAXIS dataPolicy.")
    if profile.get("execution_policy") not in EXECUTION_POLICIES:
        raise ValueError("Unsupported MAXIS execution policy.")
    result = deepcopy(profile)
    result["display_name"] = _clean_display_name(profile.get("display_name"))
    result["base_url"] = _normal_base_url(profile.get("base_url", ""), stage)
    if result["base_url"] and policy == "internal_read_only":
        result["base_url"] = canonical_maxis_api_base(
            result["base_url"], stage=stage, require_https=True, allow_dev_stub=False,
        )
    result["workflow_bundle_version"] = _clean_version(
        profile.get("workflow_bundle_version"), "workflow bundle version"
    )
    result["knowledge_version"] = _clean_version(
        profile.get("knowledge_version"), "knowledge version"
    )
    result["knowledge_dataset_id"] = _clean_dataset_id(
        profile.get("knowledge_dataset_id")
    )
    for field in ("knowledge_source_manifest", "knowledge_environment_binding"):
        result[field] = clean_knowledge_path(profile.get(field))
    if bool(result["knowledge_source_manifest"]) != bool(result["knowledge_environment_binding"]):
        raise ValueError("Private Knowledge requires both source manifest and environment binding.")
    result["knowledge_sentinel_sha256"] = _clean_digest(
        profile.get("knowledge_sentinel_sha256"), "Knowledge release sentinel"
    )
    result["remote_log_policy_id"] = _clean_version(
        profile.get("remote_log_policy_id"), "remote log policy id"
    )
    result["remote_log_mode"] = str(
        profile.get("remote_log_mode") or ""
    ).strip().lower()
    result["remote_log_retention_days"] = _clean_retention_days(
        profile.get("remote_log_retention_days")
    )
    result["knowledge_eval_attestation"] = _clean_knowledge_eval_attestation(
        profile.get("knowledge_eval_attestation")
    )
    if stage == "internal-pilot":
        result["internal_pilot_policy"] = pilot_policy.clean_policy(profile.get("internal_pilot_policy"))
        retention = result["remote_log_retention_days"]
        if retention is not None and retention * 24 > result["internal_pilot_policy"]["content_retention_hours"]:
            raise ValueError("Pilot remote content log retention exceeds the pilot policy bound.")
    elif "internal_pilot_policy" in profile:
        raise ValueError("Internal-pilot policy cannot be applied to another profile.")
    _validate_remote_log_attestation(result, required=activation and policy != "synthetic_only")
    workflows = profile.get("workflows")
    if not isinstance(workflows, dict):
        raise ValueError("workflows must be an object.")
    clean_workflows = {}
    for key in WORKFLOW_KEYS:
        row = workflows.get(key) or {}
        if not isinstance(row, dict):
            raise ValueError("Each workflow binding must be an object.")
        binding = _clean_version(row.get("binding"), f"{key} binding")
        version = canonical_published_workflow_id(
            row.get("version"), allow_empty=True,
        )
        if binding != key:
            raise ValueError(f"{key} must keep its registry binding '{key}'.")
        clean_workflows[key] = {"binding": binding, "version": version}
    if set(workflows) - set(WORKFLOW_KEYS):
        raise ValueError("Unsupported MAXIS workflow binding.")
    result["workflows"] = clean_workflows
    if activation:
        _require_internal_pilot_controls(result)
        missing = []
        if not result["base_url"]:
            missing.append("Base URL")
        if not result["workflow_bundle_version"]:
            missing.append("Workflow bundle version")
        if not result["knowledge_version"]:
            missing.append("Knowledge version")
        if not result["knowledge_dataset_id"]:
            missing.append("Dify Knowledge dataset UUID")
        if not result["knowledge_sentinel_sha256"]:
            missing.append("Knowledge release sentinel SHA-256")
        required_workflows = {"agent_core"}
        required_workflows.update(
            activation_workflows if activation_workflows is not None else
            [key for key, mode in routes().items() if mode in ("shadow", "maxis")]
        )
        if required_workflows - set(WORKFLOW_KEYS):
            raise ValueError("Unsupported MAXIS activation workflow.")
        for workflow_key in sorted(required_workflows):
            workflow = result["workflows"][workflow_key]
            if not workflow["binding"] or not workflow["version"]:
                missing.append(f"{workflow_key} binding/version")
            try:
                has_credential = bool(credentials.get_for(name, workflow_key))
            except credentials.CredentialStoreUnavailable as exc:
                raise ValueError(str(exc)) from exc
            if not has_credential:
                missing.append(f"{workflow_key} API key")
        if missing:
            raise ValueError("Cannot activate MAXIS profile; missing " + ", ".join(missing) + ".")
        _build_profile_manifest(result, required=True)
    return result


def get(profile_name: str) -> dict:
    name = str(profile_name or "").strip().lower()
    return _validate(_merged_profile(name))


def list_all() -> list[dict]:
    return [get(name) for name in profile_names()]


def create(display_name: str) -> dict:
    """Create an ordinary connection without changing active runtime or credentials."""
    return save(f"maxis-{uuid4()}", {"display_name": _clean_display_name(display_name)})


def save(profile_name: str, patch: dict | None = None) -> dict:
    name = str(profile_name or "").strip().lower()
    patch = patch or {}
    allowed = {"display_name", "execution_policy", "base_url", "data_policy", "workflow_bundle_version",
               "knowledge_version", "knowledge_dataset_id",
               "knowledge_source_manifest", "knowledge_environment_binding",
               "knowledge_sentinel_sha256", "remote_log_policy_id",
               "remote_log_mode", "remote_log_retention_days",
               "knowledge_eval_attestation", "internal_pilot_policy", "workflows"}
    if set(patch) - allowed:
        raise ValueError("Unsupported MAXIS profile field.")
    with _LOCK:
        state = prefs.load()
        stored_rows = _stored_rows(state)
        current = _merged_profile(name, stored_rows=stored_rows)
        for key in allowed - {"workflows"}:
            if key in patch:
                current[key] = patch[key]
        if "workflows" in patch:
            incoming = patch.get("workflows")
            if not isinstance(incoming, dict):
                raise ValueError("workflows must be an object.")
            if set(incoming) - set(WORKFLOW_KEYS):
                raise ValueError("Unsupported MAXIS workflow binding.")
            for workflow_key, row in incoming.items():
                if not isinstance(row, dict):
                    raise ValueError("Each workflow binding must be an object.")
                current["workflows"][workflow_key] = {
                    **current["workflows"][workflow_key],
                    **{k: row[k] for k in ("binding", "version") if k in row},
                }
        clean = _validate(current)
        # Preserve stored siblings exactly, including invalid/rotated candidates.
        # Only the requested row is normalized; absent profiles remain absent.
        raw = state.get("maxisProfiles") or []
        if isinstance(raw, dict):
            raw = list(raw.values())
        rows = [
            clean if isinstance(row, dict) and row.get("name") == name else row
            for row in (raw if isinstance(raw, list) else [])
        ]
        if name not in stored_rows:
            rows.append(clean)
        verified = dict(state.get("maxisProbeOkByProfile") or {})
        last_probe = dict(state.get("maxisLastProbeByProfile") or {})
        # Labels are presentation only; renaming an existing connection must not
        # interrupt its pinned runtime or erase a successful contract check.
        if set(patch) - {"display_name"}:
            verified.pop(name, None)
            last_probe.pop(name, None)
        prefs.save_strict({"maxisProfiles": rows,
                           "maxisProbeOkByProfile": verified,
                           "maxisLastProbeByProfile": last_probe})
    return get(name)


def active_profile_name() -> str:
    env = str(os.getenv("LAKE_MAXIS_PROFILE") or "").strip().lower()
    if env:
        return env if env in profile_names() else ""
    value = str(prefs.load().get("maxisActiveProfile") or "").strip().lower()
    return value if value in profile_names() else ""


def _effective(profile: dict) -> dict:
    result = deepcopy(profile)
    name = str(result.get("name") or "").strip().lower()
    selected = str(os.getenv("LAKE_MAXIS_PROFILE") or "").strip().lower()
    # Shared deployment variables do not encode a profile.  Bind them only when the
    # same process explicitly selects that profile; otherwise an inactive-candidate
    # probe could send its stored key to the other environment's URL/workflow.
    if selected != name:
        _validate_remote_log_attestation(result, required=False)
        return result
    env_base = str(os.getenv(CONFIG_ENV_NAMES["base_url"]) or "").strip()
    if env_base:
        result["base_url"] = _normal_base_url(env_base, result["stage"])
    for key in ("workflow_bundle_version", "knowledge_version"):
        value = str(os.getenv(CONFIG_ENV_NAMES[key]) or "").strip()
        if value:
            result[key] = _clean_version(value, key.replace("_", " "))
    dataset_id = str(os.getenv(CONFIG_ENV_NAMES["knowledge_dataset_id"]) or "").strip()
    if dataset_id:
        result["knowledge_dataset_id"] = _clean_dataset_id(dataset_id)
    sentinel_sha256 = str(
        os.getenv(CONFIG_ENV_NAMES["knowledge_sentinel_sha256"]) or ""
    ).strip()
    if sentinel_sha256:
        result["knowledge_sentinel_sha256"] = _clean_digest(
            sentinel_sha256, "Knowledge release sentinel"
        )
    # A fixed-fixture validation candidate keeps its synthetic/redacted contract.
    # Ordinary connections use the selected profile's explicit logging policy.
    if result.get("data_policy") != "synthetic_only":
        remote_policy_id = str(
            os.getenv(CONFIG_ENV_NAMES["remote_log_policy_id"]) or ""
        ).strip()
        if remote_policy_id:
            result["remote_log_policy_id"] = _clean_version(
                remote_policy_id, "remote log policy id"
            )
        remote_mode = str(
            os.getenv(CONFIG_ENV_NAMES["remote_log_mode"]) or ""
        ).strip()
        if remote_mode:
            result["remote_log_mode"] = remote_mode.lower()
        remote_retention = str(
            os.getenv(CONFIG_ENV_NAMES["remote_log_retention_days"]) or ""
        ).strip()
        if remote_retention:
            result["remote_log_retention_days"] = _clean_retention_days(remote_retention)
        raw_eval_attestation = str(
            os.getenv(CONFIG_ENV_NAMES["knowledge_eval_attestation"]) or ""
        ).strip()
        if raw_eval_attestation:
            if len(raw_eval_attestation) > 8192:
                raise ValueError("Knowledge eval attestation environment JSON is too large.")
            try:
                decoded_eval_attestation = json.loads(raw_eval_attestation)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "Knowledge eval attestation environment value must be valid JSON."
                ) from exc
            result["knowledge_eval_attestation"] = _clean_knowledge_eval_attestation(
                decoded_eval_attestation
            )
    if result.get("stage") == "internal-pilot":
        raw_policy = str(os.getenv(CONFIG_ENV_NAMES["internal_pilot_policy"]) or "").strip()
        if raw_policy:
            if len(raw_policy) > 8192:
                raise ValueError("Internal-pilot policy environment JSON is too large.")
            try:
                result["internal_pilot_policy"] = pilot_policy.clean_policy(json.loads(raw_policy))
            except (TypeError, ValueError) as exc:
                raise ValueError("Internal-pilot policy environment JSON is invalid.") from exc
    for workflow_key, env_name in WORKFLOW_VERSION_ENV_NAMES.items():
        value = str(os.getenv(env_name) or "").strip()
        if value:
            result["workflows"][workflow_key]["version"] = (
                canonical_published_workflow_id(value)
            )
    _validate_remote_log_attestation(result, required=False)
    return _validate(result)


def profile_fingerprint(profile: dict) -> str:
    """Stable public fingerprint containing no API key or credential digest."""
    manifest = _build_profile_manifest(profile, required=False)
    clean = {key: profile[key] for key in (
        "name", "stage", "execution_policy", "base_url", "data_policy", "workflow_bundle_version",
        "knowledge_version", "knowledge_dataset_id", "workflows",
        "knowledge_sentinel_sha256", "remote_log_policy_id", "remote_log_mode",
        "remote_log_retention_days", "knowledge_eval_attestation",
    )}
    clean["knowledge_manifest_sha256"] = manifest.sha256 if manifest else ""
    from app.agent.maxis.knowledge_config import profile_binding_sha256
    private_binding = profile_binding_sha256(profile)
    if private_binding:
        clean["knowledge_environment_sha256"] = private_binding
    if profile.get("stage") == "internal-pilot":
        clean["internal_pilot_policy"] = pilot_policy.clean_policy(profile.get("internal_pilot_policy"))
    encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _separation_asset_profile(profile_name: str) -> dict:
    """Read sibling assets without coupling profiles to a sibling's URL policy."""

    name = str(profile_name or "").strip().lower()
    candidate = deepcopy(_merged_profile(name))
    if str(os.getenv("LAKE_MAXIS_PROFILE") or "").strip().lower() != name:
        return candidate

    dataset_id = str(os.getenv(CONFIG_ENV_NAMES["knowledge_dataset_id"]) or "").strip()
    if dataset_id:
        candidate["knowledge_dataset_id"] = dataset_id
    sentinel = str(
        os.getenv(CONFIG_ENV_NAMES["knowledge_sentinel_sha256"]) or ""
    ).strip()
    if sentinel:
        candidate["knowledge_sentinel_sha256"] = sentinel
    workflows = candidate.get("workflows")
    if not isinstance(workflows, dict):
        workflows = {}
        candidate["workflows"] = workflows
    for workflow_key, env_name in WORKFLOW_VERSION_ENV_NAMES.items():
        version = str(os.getenv(env_name) or "").strip()
        if version:
            row = workflows.get(workflow_key)
            if not isinstance(row, dict):
                row = {}
                workflows[workflow_key] = row
            row["version"] = version
    return candidate


def _require_cross_environment_asset_separation(profile: dict) -> None:
    """Fail closed before any environment contacts assets assigned to another.

    MAXIS may expose environments through the same API host, and their profiles may
    carry the same human-readable release labels.  The actual Dify dataset, release
    sentinel, published workflow UUIDs, and credentials must nevertheless be
    independent.  Saving remains intentionally permissive so the recovery UI can
    hold a partially migrated profile while an operator replaces each asset.
    """

    name = credentials.normal_profile_name(profile.get("name"))
    for sibling_name in profile_names():
        if sibling_name != name:
            _require_asset_pair_separation(profile, sibling_name)


def _require_asset_pair_separation(profile: dict, sibling_name: str) -> None:
    name = str(profile["name"])

    # Do not validate or compare the sibling's host and release labels here. A stale
    # destination policy must remain independently repairable and cannot redefine
    # which concrete assets the current environment is allowed to use.
    sibling = _separation_asset_profile(sibling_name)
    # Ordinary saved connections may point at the same service and assets. The
    # stricter isolation contract remains for independently qualified deployments.
    if (profile.get("execution_policy") == "connected"
            and sibling.get("execution_policy") == "connected"
            and str(profile.get("base_url") or "").rstrip("/")
            == str(sibling.get("base_url") or "").rstrip("/")):
        return
    overlaps: list[str] = []

    sibling_dataset = str(
        sibling.get("knowledge_dataset_id") or ""
    ).strip().lower()
    profile_dataset = str(
        profile.get("knowledge_dataset_id") or ""
    ).strip().lower()
    if sibling_dataset and profile_dataset and sibling_dataset == profile_dataset:
        overlaps.append("Knowledge dataset")

    sibling_sentinel = str(
        sibling.get("knowledge_sentinel_sha256") or ""
    ).strip().lower()
    profile_sentinel = str(
        profile.get("knowledge_sentinel_sha256") or ""
    ).strip().lower()
    if (
        sibling_sentinel
        and profile_sentinel
        and sibling_sentinel == profile_sentinel
    ):
        overlaps.append("Knowledge release sentinel")

    def workflow_versions(candidate: dict) -> set[str]:
        workflows = candidate.get("workflows") or {}
        return {
            str((workflows.get(key) or {}).get("version") or "").strip().lower()
            for key in WORKFLOW_KEYS
            if str((workflows.get(key) or {}).get("version") or "").strip()
        }

    if workflow_versions(sibling) & workflow_versions(profile):
        overlaps.append("published workflow")

    try:
        shared_credential = credentials.profiles_share_any_credential(name, sibling_name)
    except credentials.CredentialStoreUnavailable as exc:
        raise ValueError(
            "Cannot verify MAXIS cross-environment credential separation because the secure "
            "credential store is unavailable."
        ) from exc
    if shared_credential:
        overlaps.append("API credential")

    if overlaps:
        raise _CrossEnvironmentSeparationError(overlaps)


def _probe_snapshot_signature(profile: dict, keys: dict[str, str]) -> str:
    """Bind one probe to the exact non-secret profile and credential snapshot used.

    The returned digest is persistence-only metadata.  API keys are included only in
    the one-way private material and must never be returned by an HTTP/status surface.
    """

    if not isinstance(keys, dict) or set(keys) != set(WORKFLOW_KEYS):
        raise ValueError("Probe credential snapshot must cover every workflow.")
    normalized = {
        key: str(keys.get(key) or "").strip() for key in WORKFLOW_KEYS
    }
    private_material = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    private = hashlib.sha256(private_material).hexdigest()
    material = f"{PROBE_CONTRACT_VERSION}|{profile_fingerprint(profile)}|{private}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _probe_signature(profile_name: str) -> str:
    profile = _effective(get(profile_name))
    _require_internal_pilot_controls(profile)
    _require_cross_environment_asset_separation(profile)
    return _probe_snapshot_signature(
        profile, credentials.resolved_for(profile_name),
    )


def _probe_timestamp_fresh(value: object) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    try:
        instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if instant.tzinfo is None or instant.utcoffset() is None:
        return False
    age = datetime.now(timezone.utc) - instant.astimezone(timezone.utc)
    return timedelta(0) <= age <= timedelta(seconds=PROBE_TTL_SECONDS)


def probe_ok(profile_name: str) -> bool:
    try:
        expected = _probe_signature(profile_name)
    except Exception:
        return False
    state = prefs.load()
    stored = (state.get("maxisProbeOkByProfile") or {}).get(profile_name)
    last_probe = (state.get("maxisLastProbeByProfile") or {}).get(profile_name)
    return bool(
        stored and stored == expected and isinstance(last_probe, dict)
        and last_probe.get("ok") is True
        and _probe_timestamp_fresh(last_probe.get("at"))
    )


def probe_candidate(profile_name: str) -> dict:
    """Return a probeable profile after enforcing its internal-data policy gates."""
    profile = resolve(profile_name)
    _require_cross_environment_asset_separation(profile)
    _validate_remote_log_attestation(
        profile, required=profile.get("data_policy") != "synthetic_only",
    )
    return profile


def start_probe(profile_name: str) -> tuple[dict, dict[str, str], str]:
    """Freeze the exact profile/key material that one network probe will exercise.

    Callers retain the keys only for the duration of the network operation and pass
    the opaque binding back to :func:`mark_probe`.  Taking the profile snapshot under
    the control-plane lock prevents a concurrent profile save from being interleaved
    with the persisted start binding; completion performs the matching check again.
    """

    name = str(profile_name or "").strip().lower()
    with _LOCK:
        profile = probe_candidate(name)
        keys = credentials.resolved_for(name)
        binding = _probe_snapshot_signature(profile, keys)
    return profile, keys, binding


def probe_workflow_keys(profile: dict) -> set[str]:
    """Candidate versions select probe scope, not another active profile's routes.

    An omitted optional version is intentionally disabled. Its stored key may remain
    for later reuse, but cannot make that feature executable or promotion-eligible.
    Profile/version and credential fingerprints still invalidate prior verification.
    """
    return {"agent_core"} | {
        key for key, workflow in profile["workflows"].items() if workflow.get("version")
    }


def _probe_contract_verified(profile_name: str, details: dict | None) -> bool:
    """Require proof for every configured/remote app before persisting verification."""
    if not isinstance(details, dict):
        return False
    workflow_results = details.get("workflows")
    if not isinstance(workflow_results, dict):
        return False
    profile = _effective(get(profile_name))
    try:
        _validate_remote_log_attestation(
            profile, required=profile.get("data_policy") != "synthetic_only",
        )
        _require_internal_pilot_controls(profile)
    except ValueError:
        return False
    try:
        manifest = _build_profile_manifest(profile, required=True)
    except ValueError:
        return False
    assert manifest is not None
    required = probe_workflow_keys(profile)
    for workflow_key, workflow in profile["workflows"].items():
        if workflow_key not in required:
            continue
        row = workflow_results.get(workflow_key)
        if not isinstance(row, dict) or any(
            row.get(field) is not True
            for field in (
                "ok",
                "appModeVerified",
                "executionContractVerified",
                "versionBindingVerified",
                "citationContractVerified",
                "knowledgeRetrievalVerified",
            )
        ):
            return False
        if row.get("knowledgeManifestSha256") != manifest.sha256:
            return False
    return bool(workflow_results)


def mark_probe(
    profile_name: str,
    ok: bool,
    details: dict | None = None,
    *,
    start_binding: str = "",
) -> bool:
    name = str(profile_name or "").strip().lower()
    safe_details = details if isinstance(details, dict) else {}
    supplied_binding = str(start_binding or "").strip().lower()
    # Successful legacy callers without a start binding are deliberately rejected.
    # Failure results may still clear verification, which is the fail-closed behavior.
    binding_shape_ok = bool(re.fullmatch(r"[0-9a-f]{64}", supplied_binding))
    with _LOCK:
        get(name)  # validate name and stored configuration
        state = prefs.load()
        verified = dict(state.get("maxisProbeOkByProfile") or {})
        last_probe = dict(state.get("maxisLastProbeByProfile") or {})
        try:
            current_binding = _probe_signature(name)
        except Exception:
            current_binding = ""
        binding_current = bool(
            binding_shape_ok
            and current_binding
            and hmac.compare_digest(supplied_binding, current_binding)
        )
        verified_ok = bool(
            ok and binding_current and _probe_contract_verified(name, details)
        )
        # Re-read credential/environment material immediately before persistence.
        # Profile mutations share this RLock; credential mutation invalidates the
        # persisted proof after its own secure-store write.
        if verified_ok:
            final_binding = _probe_signature(name)
            verified_ok = hmac.compare_digest(supplied_binding, final_binding)
            current_binding = final_binding
        if verified_ok:
            verified[name] = current_binding
        else:
            verified.pop(name, None)
        stale_completion = bool(ok and not binding_current)
        last_probe[name] = {
            "ok": verified_ok,
            "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "workflows": (
                {} if stale_completion
                else _safe_public_probe_workflows(
                    safe_details.get("workflows")
                )
            ),
            "error": (
                "Probe result binding is stale or missing."
                if stale_completion
                else _safe_public_probe_error(safe_details.get("error"))
            ),
        }
        prefs.save_strict({"maxisProbeOkByProfile": verified,
                           "maxisLastProbeByProfile": last_probe})
    return verified_ok


def credential_changed(profile_name: str) -> None:
    name = str(profile_name or "").strip().lower()
    state = prefs.load()
    verified = dict(state.get("maxisProbeOkByProfile") or {})
    last_probe = dict(state.get("maxisLastProbeByProfile") or {})
    verified.pop(name, None)
    last_probe.pop(name, None)
    prefs.save_strict({"maxisProbeOkByProfile": verified,
                       "maxisLastProbeByProfile": last_probe})


def activate(profile_name: str) -> dict:
    name = str(profile_name or "").strip().lower()
    env_name = str(os.getenv("LAKE_MAXIS_PROFILE") or "").strip().lower()
    if env_name and env_name != name:
        raise ValueError(
            f"LAKE_MAXIS_PROFILE selects {env_name}; {name} cannot be activated in this process."
        )
    profile = _validate(_effective(get(name)), activation=True)
    _require_cross_environment_asset_separation(profile)
    if not probe_ok(name):
        raise ValueError("Save and successfully probe this MAXIS profile before activation.")
    if profile.get("data_policy") == "synthetic_only" and any(mode == "maxis" for mode in routes().values()):
        raise ValueError(
            "A synthetic-only profile cannot be activated while an "
            "authoritative MAXIS route exists; move it to local or shadow first."
        )
    if profile.get("data_policy") != "synthetic_only":
        # Activation is allowed as a shadow candidate.  However, if global routing is
        # already authoritative MAXIS (for example after switching from dev to prod),
        # every affected prod deployment must have its own exact-version shadow proof.
        _require_prod_promotion(
            profile,
            [key for key, mode in routes().items() if mode == "maxis"],
        )
    fingerprint = profile_fingerprint(profile)
    prefs.save_strict({"maxisActiveProfile": name,
                       "maxisActiveProfileFingerprint": fingerprint})
    return resolve(name)


def apply_production(workflows: list[str]) -> dict:
    """Compatibility entry point; named-profile application owns the transaction."""
    return apply_profile("prod", workflows)


def prepare_use(profile_name: str) -> dict:
    """Explicitly prepare real guarded requests; selection/routes are not applied.

    A legacy validation-only connection keeps its ID, encrypted keys, destination,
    assets and remote-log metadata. A changed data policy invalidates verification
    and requires a fresh contract probe before :func:`apply_profile` may succeed.
    """
    with _LOCK:
        profile = get(profile_name)
        env_name = str(os.getenv("LAKE_MAXIS_PROFILE") or "").strip().lower()
        if env_name and env_name != profile["name"]:
            raise ValueError("The process selects a different MAXIS profile.")
        if kill_switch_enabled():
            raise ValueError("Release the MAXIS kill switch before applying.")
        if profile["data_policy"] == "synthetic_only":
            profile = save(profile["name"], {"data_policy": "internal_read_only"})
        return profile


def apply_profile(profile_name: str, workflows: list[str]) -> dict:
    """Apply the verified connection and selected features in one persistence write."""
    from app.agent.maxis.qualification import clean_workflows
    selected = clean_workflows(workflows)
    name = credentials.normal_profile_name(profile_name)
    with _LOCK:
        env_name = str(os.getenv("LAKE_MAXIS_PROFILE") or "").strip().lower()
        if env_name and env_name != name:
            raise ValueError("The process selects a different MAXIS profile.")
        if kill_switch_enabled():
            raise ValueError("Release the MAXIS kill switch before applying.")
        profile = _validate(_effective(get(name)), activation=True, activation_workflows=selected)
        if profile["data_policy"] != "internal_read_only":
            raise ValueError("Prepare this connection for guarded requests and verify it before applying.")
        _require_cross_environment_asset_separation(profile)
        if not probe_ok(name):
            raise ValueError("Successfully probe this MAXIS connection before applying.")
        _require_prod_promotion(profile, selected)
        selected_routes = {key: "maxis" if key in selected else "local" for key in WORKFLOW_KEYS}
        prefs.save_strict({"maxisActiveProfile": name,
                          "maxisActiveProfileFingerprint": profile_fingerprint(profile),
                          "maxisRoutes": selected_routes})
    return {"ok": True, "activeProfile": name, "routes": selected_routes}


def active_fingerprint() -> str:
    name = active_profile_name()
    if not name:
        return ""
    try:
        profile = _effective(get(name))
        _require_cross_environment_asset_separation(profile)
        return profile_fingerprint(profile)
    except Exception:
        return ""


def active() -> dict | None:
    name = active_profile_name()
    return resolve(name) if name else None


def resolve(profile_name: str = "") -> dict:
    name = str(profile_name or active_profile_name()).strip().lower()
    if not name:
        raise ValueError("No active MAXIS profile.")
    profile = _effective(get(name))
    _require_cross_environment_asset_separation(profile)
    _require_internal_pilot_controls(profile)
    manifest = _build_profile_manifest(profile, required=False)
    fingerprint = profile_fingerprint(profile)
    stored_fingerprint = str(prefs.load().get("maxisActiveProfileFingerprint") or "")
    selected = name == active_profile_name()
    enabled = bool(selected and stored_fingerprint == fingerprint and probe_ok(name)
                   and not kill_switch_enabled())
    return {
        **profile,
        "knowledge_manifest_sha256": manifest.sha256 if manifest else "",
        "knowledge_document_count": manifest.document_count if manifest else 0,
        "fingerprint": fingerprint,
        "active": selected,
        "verified": probe_ok(name),
        "enabled": enabled,
    }


def resolve_knowledge_manifest(
    profile: dict | str = "", *, required: bool = True,
) -> KnowledgeManifest | None:
    """Resolve the exact tracked content + Dify dataset deployment for a profile."""

    if isinstance(profile, dict):
        candidate = profile
    else:
        name = str(profile or active_profile_name()).strip().lower()
        if not name:
            if required:
                raise ValueError("No active MAXIS profile.")
            return None
        candidate = _effective(get(name))
    return _build_profile_manifest(candidate, required=required)


def profile_metadata(profile_name: str = "") -> dict:
    profile = resolve(profile_name)
    return {key: profile[key] for key in (
        "name", "display_name", "stage", "execution_policy", "enabled", "base_url", "data_policy",
        "workflow_bundle_version", "knowledge_version", "knowledge_dataset_id",
        "knowledge_sentinel_sha256", "remote_log_policy_id", "remote_log_mode",
        "remote_log_retention_days",
        "knowledge_manifest_sha256", "knowledge_document_count", "fingerprint",
    )}


def _workflow_key(use_case: str) -> str:
    value = str(use_case or "").strip().lower().replace("-", "_")
    if value not in _ROLE_WORKFLOW:
        raise ValueError("Unsupported MAXIS use case or role id.")
    return _ROLE_WORKFLOW[value]


def routes() -> dict[str, str]:
    saved = prefs.load().get("maxisRoutes") or {}
    return {
        key: str(saved.get(key) or "local") if str(saved.get(key) or "local") in ROUTE_MODES
        else "local"
        for key in WORKFLOW_KEYS
    }


def route_for(use_case: str) -> str:
    return routes()[_workflow_key(use_case)]


def _shadow_dimensions(profile: dict, workflow_key: str, role_id: str):
    from app.agent.maxis.shadow_eval import ShadowDimensions

    workflow = (profile.get("workflows") or {}).get(workflow_key) or {}
    return ShadowDimensions(
        profile=str(profile.get("name") or ""),
        profile_fingerprint=profile_fingerprint(profile),
        workflow_key=workflow_key,
        role_id=role_id,
        workflow_version=str(workflow.get("version") or ""),
        knowledge_version=str(profile.get("knowledge_version") or ""),
    )


def _promotion_roles(workflow_key: str) -> tuple[str, ...]:
    """Return every currently remote-capable Role behind a workflow route."""
    from app.agent.maxis.registry import load_registry
    from app.agent.workflow.role_manifest import remote_execution_allowed

    binding = load_registry().resolve(workflow_key)
    return tuple(
        role_id for role_id in binding.roles if remote_execution_allowed(role_id)
    )


def _workflow_promotion(profile: dict, workflow_key: str) -> dict:
    """Require evidence for every heterogeneous Role, while showing one UI summary."""
    from app.agent.maxis import shadow_eval

    roles = _promotion_roles(workflow_key)
    role_rows = {
        role_id: shadow_eval.promotion_status(
            _shadow_dimensions(profile, workflow_key, role_id)
        )
        for role_id in roles
    }
    if roles:
        aggregate = shadow_eval.summary(
            _shadow_dimensions(profile, workflow_key, roles[0]), all_roles=True,
        )
    else:
        aggregate = {
            "contract": "maxis-shadow-quality.v1",
            "privacy": "aggregate_metadata_only",
            "submitted": 0, "completed": 0, "succeeded": 0, "failed": 0,
            "queueDropped": 0, "numericComparable": 0,
            "successRatePct": None, "contractValidRatePct": None,
            "citationValidRatePct": None, "structureScorePct": None,
            "numericParityRatePct": None, "lexicalAgreementPct": None,
            "nonEmptyParityRatePct": None, "averageLatencyMs": None,
        }
    reasons = [
        f"{role_id}: {reason}"
        for role_id, row in role_rows.items()
        for reason in row.get("reasons") or ()
    ]
    return {
        **aggregate,
        "ready": bool(roles) and not reasons,
        "minimumSamples": shadow_eval.MIN_PROMOTION_SAMPLES * len(roles),
        "requiredRoles": list(roles),
        "roles": role_rows,
        "reasons": reasons,
    }


def _require_prod_promotion(profile: dict, workflow_keys) -> None:
    if profile.get("execution_policy", "qualified") == "connected":
        return
    required_workflows = sorted(set(workflow_keys))
    if not required_workflows:
        return
    if profile.get("stage") != "internal-pilot":
        from app.agent.maxis.qualification import status as qualification_status
        qualified = qualification_status(profile)
        if qualified["valid"] and set(required_workflows).issubset(qualified["workflows"]):
            return
        # Metadata-only legacy attestations never grant authority. The signed,
        # independently reviewed MAXIS-only run replaces Shadow's local baseline.
        raise ValueError("Signed external MAXIS qualification is required for the current deployment and features.")
    _require_current_knowledge_eval_attestation(profile)
    for workflow_key in required_workflows:
        status = _workflow_promotion(profile, workflow_key)
        if not status["ready"]:
            raise ValueError(
                "MAXIS qualification requires a passing shadow quality gate: "
                + "; ".join(status["reasons"])
            )


def require_authoritative(profile_name: str, workflow_keys) -> None:
    """Enforce the single authority gate used by every ``maxis`` route boundary.

    Real requests require explicit internal-data policy, current contract verification
    and activation. The optional qualified execution policy additionally requires a
    signed external evaluation artifact; a connected profile never claims that quality
    qualification. Role ACL, guarded evidence and local write approvals are unchanged.
    """

    name = str(profile_name or "").strip().lower()
    credentials.normal_profile_name(name)
    required_workflows = sorted(set(str(key or "").strip() for key in workflow_keys))
    if not required_workflows or set(required_workflows) - set(WORKFLOW_KEYS):
        raise ValueError("Authoritative MAXIS requires known workflow bindings.")
    profile = resolve(name)
    if not profile.get("enabled") or kill_switch_enabled():
        raise ValueError(
            "Activate a verified MAXIS profile and release the kill switch first."
        )
    if profile.get("data_policy") == "synthetic_only":
        raise ValueError(
            "A synthetic-only profile cannot become authoritative; "
            "prepare guarded requests, verify and apply the connection."
        )
    _require_prod_promotion(profile, required_workflows)


def set_routes(patch: dict | None) -> dict[str, str]:
    patch = patch or {}
    if set(patch) - set(WORKFLOW_KEYS):
        raise ValueError("Unsupported MAXIS route.")
    updated = routes()
    for key, raw in patch.items():
        mode = str(raw or "").strip().lower()
        if mode not in ROUTE_MODES:
            raise ValueError("MAXIS route must be local, shadow, or maxis.")
        if mode in ("shadow", "maxis"):
            profile = active()
            if not profile or not profile.get("enabled"):
                raise ValueError("Activate a verified MAXIS profile and release the kill switch first.")
            if mode == "maxis" and profile.get("data_policy") == "synthetic_only":
                raise ValueError(
                    "A synthetic-only profile cannot become authoritative; "
                    "prepare guarded requests, verify and apply the connection."
                )
            workflow = profile["workflows"].get(key) or {}
            if not workflow.get("binding") or not workflow.get("version"):
                raise ValueError(f"Configure the {key} workflow binding/version first.")
            if not credentials.get_for(profile["name"], key):
                raise ValueError(f"Configure the {key} API key first.")
            if mode == "maxis":
                require_authoritative(str(profile.get("name") or ""), [key])
        updated[key] = mode
    saved = prefs.save_strict({"maxisRoutes": updated})
    return {
        key: str((saved.get("maxisRoutes") or {}).get(key) or "local")
        for key in WORKFLOW_KEYS
    }


def kill_switch_enabled() -> bool:
    return bool(prefs.load().get("maxisKillSwitch", False))


def set_kill_switch(enabled: bool) -> bool:
    saved = prefs.save_strict({"maxisKillSwitch": bool(enabled)})
    return bool(saved.get("maxisKillSwitch"))


def _safe_public_text(
    value: object,
    *,
    pattern: re.Pattern[str],
    max_length: int,
) -> str:
    """Project one persisted scalar without coercing containers or reflecting keys."""

    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > max_length
        or any(ord(character) < 32 or ord(character) == 127 for character in candidate)
        or credential_shaped_public_text(candidate)
        or not pattern.fullmatch(candidate)
    ):
        return ""
    return candidate


def _safe_public_dataset_id(value: object) -> str:
    if not isinstance(value, str) or credential_shaped_public_text(value):
        return ""
    try:
        return canonical_dataset_id(value)
    except KnowledgeManifestError:
        return ""


def _safe_public_digest(value: object) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if credential_shaped_public_text(candidate):
        return ""
    return candidate if _PUBLIC_DIGEST.fullmatch(candidate) else ""


def _safe_public_workflow_version(value: object) -> str:
    if not isinstance(value, str) or credential_shaped_public_text(value):
        return ""
    candidate = value.strip().lower()
    return candidate if _PUBLIC_WORKFLOW_UUID.fullmatch(candidate) else ""


def _safe_public_retention_days(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 90 else None


def _safe_public_workflows(value: object) -> dict[str, dict[str, str]]:
    source = value if isinstance(value, dict) else {}
    result: dict[str, dict[str, str]] = {}
    for key in WORKFLOW_KEYS:
        row = source.get(key)
        row = row if isinstance(row, dict) else {}
        result[key] = {
            "binding": key if row.get("binding") == key else "",
            "version": _safe_public_workflow_version(row.get("version")),
        }
    return result


def _safe_public_profile_values(name: str, value: object) -> dict:
    """Return the exact browser-visible profile metadata shape from untrusted prefs."""

    source = value if isinstance(value, dict) else {}
    return {
        "base_url": _safe_public_base_url(source.get("base_url")),
        "data_policy": source.get("data_policy") if source.get("data_policy") in DATA_POLICIES else "",
        "workflow_bundle_version": _safe_public_text(
            source.get("workflow_bundle_version"),
            pattern=_PUBLIC_METADATA_ID,
            max_length=120,
        ),
        "knowledge_version": _safe_public_text(
            source.get("knowledge_version"),
            pattern=_PUBLIC_METADATA_ID,
            max_length=120,
        ),
        "knowledge_dataset_id": _safe_public_dataset_id(
            source.get("knowledge_dataset_id")
        ),
        "knowledge_sentinel_sha256": _safe_public_digest(
            source.get("knowledge_sentinel_sha256")
        ),
        "remote_log_policy_id": _safe_public_text(
            source.get("remote_log_policy_id"),
            pattern=_PUBLIC_METADATA_ID,
            max_length=120,
        ),
        "remote_log_mode": (
            source.get("remote_log_mode")
            if source.get("remote_log_mode") in REMOTE_LOG_MODES else ""
        ),
        "remote_log_retention_days": _safe_public_retention_days(
            source.get("remote_log_retention_days")
        ),
        "workflows": _safe_public_workflows(source.get("workflows")),
    }


def _safe_public_probe_code(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    candidate = value.strip()
    return candidate if candidate in _PUBLIC_PROBE_CODES else "probe_failed"


def _safe_public_utc_instant(value: object, label: str) -> str:
    if not isinstance(value, str) or credential_shaped_public_text(value):
        return ""
    try:
        return _clean_utc_instant(value, label)
    except ValueError:
        return ""


def _safe_public_probe_workflows(value: object) -> dict[str, dict]:
    source = value if isinstance(value, dict) else {}
    result: dict[str, dict] = {}
    for key in WORKFLOW_KEYS:
        raw = source.get(key)
        if not isinstance(raw, dict):
            continue
        status = raw.get("statusCode")
        elapsed = raw.get("ms")
        missing = raw.get("missingParameters")
        role_id = raw.get("roleId")
        role_id = role_id.strip() if isinstance(role_id, str) else ""
        result[key] = {
            "ok": raw.get("ok") is True,
            "statusCode": (
                status
                if isinstance(status, int) and not isinstance(status, bool)
                and 100 <= status <= 599 else None
            ),
            "ms": (
                elapsed
                if isinstance(elapsed, int) and not isinstance(elapsed, bool)
                and 0 <= elapsed <= 86_400_000 else None
            ),
            "errorCode": _safe_public_probe_code(raw.get("errorCode")),
            "missingParameters": sorted({
                item for item in missing
                if isinstance(item, str) and item in _PUBLIC_PROBE_PARAMETERS
            }) if isinstance(missing, list) else [],
            "appModeVerified": raw.get("appModeVerified") is True,
            "executionContractVerified": (
                raw.get("executionContractVerified") is True
            ),
            "versionBindingVerified": raw.get("versionBindingVerified") is True,
            "citationContractVerified": raw.get("citationContractVerified") is True,
            "knowledgeRetrievalVerified": (
                raw.get("knowledgeRetrievalVerified") is True
            ),
            "knowledgeManifestSha256": _safe_public_digest(
                raw.get("knowledgeManifestSha256")
            ),
            "roleId": role_id if role_id in _ROLE_WORKFLOW else "",
        }
    return result


def _safe_public_probe_error(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    candidate = value.strip()
    return candidate if candidate in _PUBLIC_PROBE_ERRORS else "MAXIS probe failed."


def _safe_public_last_probe(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    return {
        "ok": value.get("ok") is True,
        "at": _safe_public_utc_instant(value.get("at"), "Probe timestamp"),
        "workflows": _safe_public_probe_workflows(value.get("workflows")),
        "error": _safe_public_probe_error(value.get("error")),
    }


def _safe_public_credentials(profile_name: str) -> dict[str, dict[str, object]]:
    try:
        source = credentials.status_for(profile_name)
    except Exception:
        source = {}
    source = source if isinstance(source, dict) else {}
    result: dict[str, dict[str, object]] = {}
    for key in WORKFLOW_KEYS:
        raw = source.get(key)
        raw = raw if isinstance(raw, dict) else {}
        status_source = raw.get("source")
        if status_source not in {"environment", "blocked", "dpapi", "none"}:
            status_source = "none"
        environment_name = raw.get("environmentVariable")
        if environment_name != credentials.ENV_NAMES[key]:
            environment_name = ""
        store_error = ""
        if raw.get("storeError"):
            store_error = (
                f"{credentials.ENV_NAMES[key]} is not bound to this profile."
                if status_source == "blocked"
                else "The secure MAXIS credential store is unavailable."
            )
        result[key] = {
            "configured": raw.get("configured") is True,
            "source": status_source,
            "environmentVariable": environment_name,
            "storeError": store_error,
        }
    return result


def _safe_public_knowledge_eval_status(value: object) -> dict:
    """Re-project generated attestation diagnostics onto one closed public shape."""

    source = value if isinstance(value, dict) else {}
    raw_reasons = source.get("reasons")
    reasons: list[str] = []
    if isinstance(raw_reasons, list):
        for reason in raw_reasons[:16]:
            if (
                isinstance(reason, str)
                and reason
                and len(reason) <= 240
                and not any(
                    ord(character) < 32 or ord(character) == 127
                    for character in reason
                )
                and not credential_shaped_public_text(reason)
            ):
                reasons.append(reason)
            else:
                reasons.append("Knowledge eval attestation status is unavailable.")

    raw_attestation = source.get("attestation")
    attestation = {}
    if isinstance(raw_attestation, dict) and raw_attestation:
        raw_versions = raw_attestation.get("workflowVersions")
        attestation = {
            "contract": (
                KNOWLEDGE_EVAL_ATTESTATION_CONTRACT
                if raw_attestation.get("contract")
                == KNOWLEDGE_EVAL_ATTESTATION_CONTRACT else ""
            ),
            "evidenceId": _safe_public_text(
                raw_attestation.get("evidenceId"),
                pattern=_PUBLIC_METADATA_ID,
                max_length=120,
            ),
            "outcome": (
                "passed" if raw_attestation.get("outcome") == "passed" else ""
            ),
            "caseCount": (
                25 if raw_attestation.get("caseCount") == 25 else 0
            ),
            "artifactSha256": _safe_public_digest(
                raw_attestation.get("artifactSha256")
            ),
            "passedAt": _safe_public_utc_instant(
                raw_attestation.get("passedAt"), "Knowledge eval passed_at"
            ),
            "suiteSha256": _safe_public_digest(
                raw_attestation.get("suiteSha256")
            ),
            "datasetId": _safe_public_dataset_id(
                raw_attestation.get("datasetId")
            ),
            "knowledgeManifestSha256": _safe_public_digest(
                raw_attestation.get("knowledgeManifestSha256")
            ),
            "workflowVersions": {
                key: _safe_public_workflow_version(
                    raw_versions.get(key) if isinstance(raw_versions, dict) else ""
                )
                for key in WORKFLOW_KEYS
            },
            "profileMaterialSha256": _safe_public_digest(
                raw_attestation.get("profileMaterialSha256")
            ),
        }

    raw_expected = source.get("expectedBinding")
    expected = {}
    if isinstance(raw_expected, dict) and raw_expected:
        expected = {
            "suiteSha256": _safe_public_digest(raw_expected.get("suiteSha256")),
            "datasetId": _safe_public_dataset_id(raw_expected.get("datasetId")),
            "knowledgeManifestSha256": _safe_public_digest(
                raw_expected.get("knowledgeManifestSha256")
            ),
            "workflowVersionsSha256": _safe_public_digest(
                raw_expected.get("workflowVersionsSha256")
            ),
            "profileMaterialSha256": _safe_public_digest(
                raw_expected.get("profileMaterialSha256")
            ),
        }

    binding_valid = source.get("bindingValid") is True and bool(attestation)
    return {
        "configured": source.get("configured") is True,
        "bindingValid": binding_valid,
        "valid": source.get("valid") is True and binding_valid,
        "verification": "metadata_binding_only_signed_artifact_not_verified",
        "reasons": reasons,
        "attestation": attestation,
        "expectedBinding": expected,
    }


def _safe_public_config_error(error: Exception) -> str:
    if isinstance(error, _CrossEnvironmentSeparationError):
        return str(error)
    if isinstance(error, DestinationPolicyError):
        return (
            "MAXIS destination allowlist, URL, or credentials configuration is invalid."
        )
    if isinstance(error, credentials.CredentialStoreUnavailable):
        return "The secure MAXIS credential store is unavailable."
    return "MAXIS profile configuration is invalid."


def _safe_public_base_url(value: object) -> str:
    """Return a recoverable URL value without ever reflecting URL credentials.

    A manually edited or legacy preference may fail the current destination policy.
    Status still exposes a structurally safe, now-disallowed host so an operator can
    repair it, but malformed/userinfo-bearing values are blanked rather than echoed.
    """

    if not isinstance(value, str):
        return ""
    raw = value.strip()
    decoded = raw
    for _ in range(2):
        decoded = unquote(decoded)
    if (
        not raw
        or len(raw) > 2048
        or "\\" in raw
        or "\\" in decoded
        or any(
            ord(character) <= 32 or ord(character) == 127
            for character in raw + decoded
        )
        or credential_shaped_public_text(raw)
        or credential_shaped_public_text(decoded)
    ):
        return ""
    try:
        parsed = urlsplit(raw)
        decoded_parsed = urlsplit(decoded)
        _ = parsed.port
        _ = decoded_parsed.port
    except (TypeError, ValueError):
        return ""
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or decoded_parsed.scheme.lower() not in {"http", "https"}
        or not decoded_parsed.hostname
        or decoded_parsed.username is not None
        or decoded_parsed.password is not None
        or decoded_parsed.query
        or decoded_parsed.fragment
    ):
        return ""
    return raw


def _public_profile(name: str) -> dict:
    # Status is also the recovery UI.  Read the raw merged row before validation so
    # an allowlist rotation can disable an old URL without turning this endpoint into
    # a 500 that prevents the manager from editing it.
    raw_stored = _merged_profile(name)
    manifest = None
    config_valid = False
    try:
        stored = _validate(raw_stored)
        effective = _effective(stored)
        _require_cross_environment_asset_separation(effective)
        manifest = _build_profile_manifest(effective, required=False)
        config_error = ""
        fingerprint = profile_fingerprint(effective)
        config_valid = True
    except Exception as exc:
        stored = raw_stored
        effective = raw_stored
        config_error = _safe_public_config_error(exc)
        fingerprint = ""
    public_stored = _safe_public_profile_values(name, stored)
    public_effective = _safe_public_profile_values(name, effective)
    active_name = active_profile_name()
    active_sig = str(prefs.load().get("maxisActiveProfileFingerprint") or "")
    verified = probe_ok(name)
    enabled = bool(name == active_name and fingerprint and active_sig == fingerprint
                   and verified and not kill_switch_enabled())
    public_stored_base = public_stored["base_url"]
    public_effective_base = public_effective["base_url"]
    host = ""
    if public_effective_base:
        try:
            host = urlsplit(public_effective_base).netloc
        except Exception:
            host = ""
    promotion = {}
    if config_valid and effective.get("execution_policy") == "qualified":
        for workflow_key in WORKFLOW_KEYS:
            workflow = (effective.get("workflows") or {}).get(workflow_key) or {}
            if workflow.get("version"):
                promotion[workflow_key] = _workflow_promotion(
                    effective, workflow_key,
                )
    if config_valid:
        try:
            eval_attestation = knowledge_eval_attestation_status(effective)
        except Exception:
            eval_attestation = {
                "configured": False,
                "bindingValid": False,
                "valid": False,
                "verification": (
                    "metadata_binding_only_signed_artifact_not_verified"
                ),
                "reasons": ["Knowledge eval attestation status is unavailable."],
                "attestation": {},
                "expectedBinding": {},
            }
    else:
        eval_attestation = {
            "configured": bool(raw_stored.get("knowledge_eval_attestation")),
            "bindingValid": False,
            "valid": False,
            "verification": "metadata_binding_only_signed_artifact_not_verified",
            "reasons": [
                "Knowledge eval attestation is unavailable while profile "
                "configuration is invalid."
            ],
            "attestation": {},
            "expectedBinding": {},
        }
    eval_attestation = _safe_public_knowledge_eval_status(eval_attestation)
    state = prefs.load()
    try:
        display_name = _clean_display_name(effective.get("display_name"))
    except ValueError:
        display_name = _default_profile(name)["display_name"]
    return {
        "name": name,
        "isLegacy": name in PROFILE_NAMES,
        "stage": effective.get("stage") if effective.get("stage") in PROFILE_NAMES else "",
        "displayName": display_name,
        "executionPolicy": (
            effective.get("execution_policy")
            if effective.get("execution_policy") in EXECUTION_POLICIES else ""
        ),
        "baseUrl": public_stored_base,
        "effectiveBaseUrl": public_effective_base,
        "effectiveHost": host,
        "dataPolicy": public_stored["data_policy"],
        "workflowBundleVersion": public_stored["workflow_bundle_version"],
        "knowledgeVersion": public_stored["knowledge_version"],
        "knowledgeDatasetId": public_stored["knowledge_dataset_id"],
        "effectiveKnowledgeDatasetId": public_effective["knowledge_dataset_id"],
        "knowledgeSentinelSha256": public_stored["knowledge_sentinel_sha256"],
        "effectiveKnowledgeSentinelSha256": public_effective[
            "knowledge_sentinel_sha256"
        ],
        "knowledgeManifestSha256": manifest.sha256 if manifest else "",
        "knowledgeDocumentCount": manifest.document_count if manifest else 0,
        "remoteLogPolicyId": public_stored["remote_log_policy_id"],
        "effectiveRemoteLogPolicyId": public_effective["remote_log_policy_id"],
        "remoteLogMode": public_stored["remote_log_mode"],
        "effectiveRemoteLogMode": public_effective["remote_log_mode"],
        "remoteLogRetentionDays": public_stored["remote_log_retention_days"],
        "effectiveRemoteLogRetentionDays": public_effective[
            "remote_log_retention_days"
        ],
        "knowledgeEvalAttestation": eval_attestation,
        **({"qualification": _qualification_status(effective)}
           if config_valid else {}),
        **({"internalPilotControls": internal_pilot_controls_status(effective)}
           if name == "internal-pilot" else {}),
        "workflows": public_stored["workflows"],
        "credentials": _safe_public_credentials(name),
        "fingerprint": fingerprint,
        "active": name == active_name,
        "verified": verified,
        "enabled": enabled,
        "configError": config_error,
        "lastProbe": _safe_public_last_probe(
            (state.get("maxisLastProbeByProfile") or {}).get(name)
        ),
        "promotion": promotion,
    }


def _qualification_status(profile: dict) -> dict:
    from app.agent.maxis.qualification import status as qualification_status
    return qualification_status(profile)


def status() -> dict:
    from app.agent.maxis.audit import read_last_remote_observations

    env_profile = str(os.getenv("LAKE_MAXIS_PROFILE") or "").strip()
    config_overrides = {
        key: env_name if str(os.getenv(env_name) or "").strip() else ""
        for key, env_name in CONFIG_ENV_NAMES.items()
    }
    workflow_overrides = {
        key: env_name
        for key, env_name in WORKFLOW_VERSION_ENV_NAMES.items()
        if str(os.getenv(env_name) or "").strip()
    }
    credential_overrides = credentials.environment_overrides()
    unbound_shared_overrides = bool(
        not env_profile
        and (any(config_overrides.values()) or workflow_overrides or credential_overrides)
    )
    return {
        "profiles": [_public_profile(name) for name in profile_names()],
        "lastRemoteObservations": read_last_remote_observations(),
        "activeProfile": active_profile_name(),
        "activeFingerprint": active_fingerprint(),
        "routes": routes(),
        "killSwitch": kill_switch_enabled(),
        "environmentOverrides": {
            "profile": "LAKE_MAXIS_PROFILE" if env_profile else "",
            "baseUrl": config_overrides["base_url"],
            "workflowBundleVersion": config_overrides["workflow_bundle_version"],
            "knowledgeVersion": config_overrides["knowledge_version"],
            "knowledgeDatasetId": config_overrides["knowledge_dataset_id"],
            "knowledgeSentinelSha256": config_overrides[
                "knowledge_sentinel_sha256"
            ],
            "remoteLogPolicyId": config_overrides["remote_log_policy_id"],
            "remoteLogMode": config_overrides["remote_log_mode"],
            "remoteLogRetentionDays": config_overrides[
                "remote_log_retention_days"
            ],
            "knowledgeEvalAttestation": config_overrides[
                "knowledge_eval_attestation"
            ],
            "internalPilotPolicy": config_overrides["internal_pilot_policy"],
            "workflowVersions": workflow_overrides,
            "credentials": credential_overrides,
            "allowedHosts": {
                stage: env_name
                for stage, env_name in ALLOWED_HOSTS_ENV_NAMES.items()
                if str(os.getenv(env_name) or "").strip()
            },
        },
        "environmentError": (
            "LAKE_MAXIS_PROFILE must select an existing MAXIS profile ID."
            if env_profile and env_profile.lower() not in profile_names() else ""
        ) or (
            "Shared MAXIS environment overrides require LAKE_MAXIS_PROFILE to select a connection."
            if unbound_shared_overrides else ""
        ),
    }
