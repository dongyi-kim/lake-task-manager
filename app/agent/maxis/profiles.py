"""Runtime profiles and feature routing for MAXIS/Dify.

This module is deliberately separate from ``app.agent.profiles``.  A Dify app is a
versioned remote workflow, not an OpenAI-compatible model provider.  Candidate
profiles can be edited and probed without changing the active runtime profile.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from app.infra import prefs
from app.agent.maxis import credentials
from app.agent.maxis.knowledge_manifest import (
    KnowledgeManifest,
    KnowledgeManifestError,
    build_manifest,
    canonical_dataset_id,
)


PROFILE_NAMES = ("dev", "prod")
WORKFLOW_KEYS = credentials.WORKFLOW_KEYS
ROUTE_MODES = ("local", "shadow", "maxis")
DATA_POLICIES = ("synthetic_only", "internal_read_only")
REMOTE_LOG_MODES = ("redacted", "approved_bounded_content")
DEV_REMOTE_LOG_POLICY_ID = "synthetic-only"
PROBE_TTL_SECONDS = 24 * 60 * 60
KNOWLEDGE_EVAL_ATTESTATION_CONTRACT = "ltm-maxis-knowledge-eval-attestation.v1"
KNOWLEDGE_EVAL_MATERIAL_CONTRACT = "ltm-maxis-knowledge-eval-material.v1"
_UTC_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
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


def _default_profile(name: str) -> dict:
    return {
        "name": name,
        "stage": name,
        "base_url": "",
        "data_policy": "synthetic_only" if name == "dev" else "internal_read_only",
        "workflow_bundle_version": "",
        "knowledge_version": "",
        "knowledge_dataset_id": "",
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
        "workflows": {
            key: {"binding": binding, "version": ""}
            for key, binding in _DEFAULT_BINDINGS.items()
        },
    }


def _stored_rows() -> dict[str, dict]:
    raw = prefs.load().get("maxisProfiles") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    rows = {}
    for row in raw if isinstance(raw, list) else []:
        if isinstance(row, dict) and row.get("name") in PROFILE_NAMES:
            rows[str(row["name"])] = deepcopy(row)
    return rows


def _merged_profile(name: str) -> dict:
    if name not in PROFILE_NAMES:
        raise KeyError("MAXIS profile must be dev or prod.")
    out = _default_profile(name)
    saved = _stored_rows().get(name) or {}
    for key in (
        "base_url", "data_policy", "workflow_bundle_version", "knowledge_version",
        "knowledge_dataset_id",
        "knowledge_sentinel_sha256", "remote_log_policy_id", "remote_log_mode",
    ):
        if key in saved:
            out[key] = str(saved.get(key) or "").strip()
    if "remote_log_retention_days" in saved:
        out["remote_log_retention_days"] = saved.get("remote_log_retention_days")
    if "knowledge_eval_attestation" in saved:
        value = saved.get("knowledge_eval_attestation")
        out["knowledge_eval_attestation"] = deepcopy(value) if isinstance(value, dict) else value
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
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc or parts.username or parts.password:
        raise ValueError("MAXIS Base URL must be an absolute URL without credentials.")
    if parts.query or parts.fragment:
        raise ValueError("MAXIS Base URL cannot contain a query or fragment.")
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError("MAXIS Base URL must use HTTP or HTTPS.")
    if scheme == "http" and not (stage == "dev" and host in ("127.0.0.1", "localhost", "::1")):
        raise ValueError("HTTPS is required except for a loopback dev endpoint.")
    if stage == "prod" and host in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("The prod MAXIS profile cannot target loopback.")
    path = parts.path.rstrip("/")
    if not path:
        path = "/v1"
    elif not path.endswith("/v1"):
        raise ValueError("MAXIS Base URL must end with /v1.")
    return urlunsplit((scheme, parts.netloc, path, "", ""))


def _clean_version(value: object, label: str) -> str:
    result = str(value or "").strip()
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
    name = str(profile.get("name") or "").strip().lower()
    policy_id = str(profile.get("remote_log_policy_id") or "").strip()
    mode = str(profile.get("remote_log_mode") or "").strip().lower()
    retention = profile.get("remote_log_retention_days")
    if name == "dev":
        if (policy_id != DEV_REMOTE_LOG_POLICY_ID or mode != "redacted"
                or retention != 0):
            raise ValueError(
                "The dev synthetic-only profile requires the fixed redacted/0-day "
                "remote log policy."
            )
        return
    if mode and mode not in REMOTE_LOG_MODES:
        raise ValueError(
            "Remote log mode must be redacted or approved_bounded_content."
        )
    if required and (not policy_id or not mode or retention is None):
        raise ValueError(
            "Prod MAXIS requires a manager-attested remote log policy id, mode, and "
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
        return build_manifest(
            knowledge_version=version,
            dataset_id=dataset_id,
        )
    except KnowledgeManifestError as exc:
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
                "name", "stage", "base_url", "data_policy",
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
    """Expose a secret-free binding status; external artifact content is not read."""

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
    except ValueError as exc:
        return {
            "configured": bool(raw),
            "valid": False,
            "verification": "metadata_binding_only_external_artifact_not_read",
            "reasons": [str(exc)],
            "attestation": {},
            "expectedBinding": {},
        }
    if str(candidate.get("stage") or "") != "prod":
        return {
            "configured": bool(attestation),
            "valid": False,
            "verification": "metadata_binding_only_external_artifact_not_read",
            "reasons": ["Knowledge eval attestation is required only for prod authority."],
            "attestation": {},
            "expectedBinding": {},
        }
    try:
        expected = knowledge_eval_attestation_binding(candidate)
    except ValueError as exc:
        return {
            "configured": bool(attestation),
            "valid": False,
            "verification": "metadata_binding_only_external_artifact_not_read",
            "reasons": [str(exc)],
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
    return {
        "configured": bool(attestation),
        "valid": bool(attestation) and not reasons,
        "verification": "metadata_binding_only_external_artifact_not_read",
        "reasons": reasons,
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
    if str(profile.get("stage") or "") != "prod":
        return
    status = knowledge_eval_attestation_status(profile)
    if not status["valid"]:
        raise ValueError(
            "Prod MAXIS authority requires a current external 25-case Knowledge eval "
            "attestation: " + "; ".join(status["reasons"])
        )


def _validate(profile: dict, *, activation: bool = False) -> dict:
    name = str(profile.get("name") or "").strip().lower()
    if name not in PROFILE_NAMES or profile.get("stage") != name:
        raise ValueError("MAXIS profile stage is immutable and must be dev or prod.")
    policy = str(profile.get("data_policy") or "").strip()
    required_policy = "synthetic_only" if name == "dev" else "internal_read_only"
    if policy != required_policy:
        raise ValueError(f"The {name} profile requires dataPolicy={required_policy}.")
    result = deepcopy(profile)
    result["base_url"] = _normal_base_url(profile.get("base_url", ""), name)
    result["workflow_bundle_version"] = _clean_version(
        profile.get("workflow_bundle_version"), "workflow bundle version"
    )
    result["knowledge_version"] = _clean_version(
        profile.get("knowledge_version"), "knowledge version"
    )
    result["knowledge_dataset_id"] = _clean_dataset_id(
        profile.get("knowledge_dataset_id")
    )
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
    _validate_remote_log_attestation(result, required=activation and name == "prod")
    workflows = profile.get("workflows")
    if not isinstance(workflows, dict):
        raise ValueError("workflows must be an object.")
    clean_workflows = {}
    for key in WORKFLOW_KEYS:
        row = workflows.get(key) or {}
        if not isinstance(row, dict):
            raise ValueError("Each workflow binding must be an object.")
        binding = _clean_version(row.get("binding"), f"{key} binding")
        version = _clean_version(row.get("version"), f"{key} workflow version")
        if binding != key:
            raise ValueError(f"{key} must keep its registry binding '{key}'.")
        clean_workflows[key] = {"binding": binding, "version": version}
    if set(workflows) - set(WORKFLOW_KEYS):
        raise ValueError("Unsupported MAXIS workflow binding.")
    result["workflows"] = clean_workflows
    if activation:
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
            key for key, mode in routes().items() if mode in ("shadow", "maxis")
        )
        for workflow_key in sorted(required_workflows):
            workflow = result["workflows"][workflow_key]
            if not workflow["binding"] or not workflow["version"]:
                missing.append(f"{workflow_key} binding/version")
            if not credentials.get_for(name, workflow_key):
                missing.append(f"{workflow_key} API key")
        if missing:
            raise ValueError("Cannot activate MAXIS profile; missing " + ", ".join(missing) + ".")
        _build_profile_manifest(result, required=True)
    return result


def get(profile_name: str) -> dict:
    name = str(profile_name or "").strip().lower()
    return _validate(_merged_profile(name))


def list_all() -> list[dict]:
    return [get(name) for name in PROFILE_NAMES]


def save(profile_name: str, patch: dict | None = None) -> dict:
    name = str(profile_name or "").strip().lower()
    current = _merged_profile(name)
    patch = patch or {}
    allowed = {"base_url", "data_policy", "workflow_bundle_version",
               "knowledge_version", "knowledge_dataset_id",
               "knowledge_sentinel_sha256", "remote_log_policy_id",
               "remote_log_mode", "remote_log_retention_days",
               "knowledge_eval_attestation", "workflows"}
    if set(patch) - allowed:
        raise ValueError("Unsupported MAXIS profile field.")
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
    with _LOCK:
        rows = {row["name"]: row for row in list_all()}
        rows[name] = clean
        state = prefs.load()
        verified = dict(state.get("maxisProbeOkByProfile") or {})
        last_probe = dict(state.get("maxisLastProbeByProfile") or {})
        verified.pop(name, None)
        last_probe.pop(name, None)
        prefs.save_strict({"maxisProfiles": [rows[key] for key in PROFILE_NAMES],
                           "maxisProbeOkByProfile": verified,
                           "maxisLastProbeByProfile": last_probe})
    return get(name)


def active_profile_name() -> str:
    env = str(os.getenv("LAKE_MAXIS_PROFILE") or "").strip().lower()
    if env:
        return env if env in PROFILE_NAMES else ""
    value = str(prefs.load().get("maxisActiveProfile") or "").strip().lower()
    return value if value in PROFILE_NAMES else ""


def _effective(profile: dict) -> dict:
    result = deepcopy(profile)
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
    # The process-level attestation overrides describe production MAXIS.  Applying
    # them to the dev row would silently defeat dev's fixed synthetic/redacted/0-day
    # contract (and make the status endpoint fail whenever prod env config exists).
    if str(result.get("name") or "").strip().lower() == "prod":
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
    for workflow_key, env_name in WORKFLOW_VERSION_ENV_NAMES.items():
        value = str(os.getenv(env_name) or "").strip()
        if value:
            result["workflows"][workflow_key]["version"] = _clean_version(
                value, f"{workflow_key} workflow version"
            )
    _validate_remote_log_attestation(result, required=False)
    return result


def profile_fingerprint(profile: dict) -> str:
    """Stable public fingerprint containing no API key or credential digest."""
    manifest = _build_profile_manifest(profile, required=False)
    clean = {key: profile[key] for key in (
        "name", "stage", "base_url", "data_policy", "workflow_bundle_version",
        "knowledge_version", "knowledge_dataset_id", "workflows",
        "knowledge_sentinel_sha256", "remote_log_policy_id", "remote_log_mode",
        "remote_log_retention_days", "knowledge_eval_attestation",
    )}
    clean["knowledge_manifest_sha256"] = manifest.sha256 if manifest else ""
    encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _probe_signature(profile_name: str) -> str:
    profile = _effective(get(profile_name))
    private = credentials.verification_digest(profile_name)
    material = f"{profile_fingerprint(profile)}|{private}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


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
    """Return a probeable profile after enforcing prod policy attestation."""
    profile = resolve(profile_name)
    _validate_remote_log_attestation(
        profile, required=str(profile.get("stage") or "") == "prod",
    )
    return profile


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
            profile, required=str(profile.get("stage") or "") == "prod",
        )
    except ValueError:
        return False
    try:
        manifest = _build_profile_manifest(profile, required=True)
    except ValueError:
        return False
    assert manifest is not None
    required = {"agent_core"}
    required.update(
        key for key, mode in routes().items() if mode in ("shadow", "maxis")
    )
    for workflow_key, workflow in profile["workflows"].items():
        try:
            has_key = bool(credentials.get_for(profile_name, workflow_key))
        except Exception:
            return False
        configured = bool(
            workflow_key in required or workflow.get("version") or has_key
        )
        if not configured:
            continue
        row = workflow_results.get(workflow_key)
        if not isinstance(row, dict) or any(
            row.get(field) is not True
            for field in (
                "ok",
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


def mark_probe(profile_name: str, ok: bool, details: dict | None = None) -> bool:
    name = str(profile_name or "").strip().lower()
    get(name)  # validate name and stored configuration
    state = prefs.load()
    verified = dict(state.get("maxisProbeOkByProfile") or {})
    last_probe = dict(state.get("maxisLastProbeByProfile") or {})
    verified_ok = bool(ok) and _probe_contract_verified(name, details)
    if verified_ok:
        verified[name] = _probe_signature(name)
    else:
        verified.pop(name, None)
    safe_details = details if isinstance(details, dict) else {}
    last_probe[name] = {
        "ok": verified_ok,
        "at": datetime.now(timezone.utc).isoformat(),
        "workflows": deepcopy(safe_details.get("workflows") or {}),
        "error": str(safe_details.get("error") or "")[:240],
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
    if not probe_ok(name):
        raise ValueError("Save and successfully probe this MAXIS profile before activation.")
    if name == "dev" and any(mode == "maxis" for mode in routes().values()):
        raise ValueError(
            "The dev synthetic-only profile cannot be activated while an "
            "authoritative MAXIS route exists; move it to local or shadow first."
        )
    if name == "prod":
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


def active_fingerprint() -> str:
    name = active_profile_name()
    if not name:
        return ""
    try:
        return profile_fingerprint(_effective(get(name)))
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
        "name", "stage", "enabled", "base_url", "data_policy",
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
    if str(profile.get("stage") or "") != "prod":
        return
    required_workflows = sorted(set(workflow_keys))
    if not required_workflows:
        return
    _require_current_knowledge_eval_attestation(profile)
    for workflow_key in required_workflows:
        status = _workflow_promotion(profile, workflow_key)
        if not status["ready"]:
            raise ValueError(
                "Prod MAXIS promotion requires a passing shadow quality gate: "
                + "; ".join(status["reasons"])
            )


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
                    "The dev synthetic-only profile cannot become authoritative; "
                    "use its canary/shadow path or activate prod."
                )
            workflow = profile["workflows"].get(key) or {}
            if not workflow.get("binding") or not workflow.get("version"):
                raise ValueError(f"Configure the {key} workflow binding/version first.")
            if not credentials.get_for(profile["name"], key):
                raise ValueError(f"Configure the {key} API key first.")
            if mode == "maxis":
                _require_prod_promotion(profile, [key])
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


def _public_profile(name: str) -> dict:
    stored = get(name)
    manifest = None
    try:
        effective = _effective(stored)
        manifest = _build_profile_manifest(effective, required=False)
        config_error = ""
        fingerprint = profile_fingerprint(effective)
    except Exception as exc:
        effective = stored
        config_error = str(exc)
        fingerprint = ""
    active_name = active_profile_name()
    active_sig = str(prefs.load().get("maxisActiveProfileFingerprint") or "")
    verified = probe_ok(name)
    enabled = bool(name == active_name and fingerprint and active_sig == fingerprint
                   and verified and not kill_switch_enabled())
    host = ""
    if effective.get("base_url"):
        try:
            host = urlsplit(effective["base_url"]).netloc
        except Exception:
            host = ""
    promotion = {}
    if not config_error and str(effective.get("stage") or "") == "prod":
        for workflow_key in WORKFLOW_KEYS:
            workflow = (effective.get("workflows") or {}).get(workflow_key) or {}
            if workflow.get("version"):
                promotion[workflow_key] = _workflow_promotion(
                    effective, workflow_key,
                )
    eval_attestation = knowledge_eval_attestation_status(effective)
    return {
        "name": name,
        "stage": name,
        "baseUrl": stored["base_url"],
        "effectiveBaseUrl": effective.get("base_url", ""),
        "effectiveHost": host,
        "dataPolicy": stored["data_policy"],
        "workflowBundleVersion": stored["workflow_bundle_version"],
        "knowledgeVersion": stored["knowledge_version"],
        "knowledgeDatasetId": stored["knowledge_dataset_id"],
        "effectiveKnowledgeDatasetId": effective.get("knowledge_dataset_id", ""),
        "knowledgeSentinelSha256": stored["knowledge_sentinel_sha256"],
        "effectiveKnowledgeSentinelSha256": effective.get(
            "knowledge_sentinel_sha256", ""
        ),
        "knowledgeManifestSha256": manifest.sha256 if manifest else "",
        "knowledgeDocumentCount": manifest.document_count if manifest else 0,
        "remoteLogPolicyId": stored["remote_log_policy_id"],
        "effectiveRemoteLogPolicyId": effective.get("remote_log_policy_id", ""),
        "remoteLogMode": stored["remote_log_mode"],
        "effectiveRemoteLogMode": effective.get("remote_log_mode", ""),
        "remoteLogRetentionDays": stored["remote_log_retention_days"],
        "effectiveRemoteLogRetentionDays": effective.get(
            "remote_log_retention_days"
        ),
        "knowledgeEvalAttestation": eval_attestation,
        "workflows": deepcopy(stored["workflows"]),
        "credentials": credentials.status_for(name),
        "fingerprint": fingerprint,
        "active": name == active_name,
        "verified": verified,
        "enabled": enabled,
        "configError": config_error,
        "lastProbe": deepcopy((prefs.load().get("maxisLastProbeByProfile") or {}).get(name)),
        "promotion": promotion,
    }


def status() -> dict:
    env_profile = str(os.getenv("LAKE_MAXIS_PROFILE") or "").strip()
    config_overrides = {
        key: env_name if str(os.getenv(env_name) or "").strip() else ""
        for key, env_name in CONFIG_ENV_NAMES.items()
    }
    return {
        "profiles": [_public_profile(name) for name in PROFILE_NAMES],
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
            "workflowVersions": {
                key: env_name
                for key, env_name in WORKFLOW_VERSION_ENV_NAMES.items()
                if str(os.getenv(env_name) or "").strip()
            },
            "credentials": credentials.environment_overrides(),
        },
        "environmentError": (
            "LAKE_MAXIS_PROFILE must be dev or prod."
            if env_profile and env_profile.lower() not in PROFILE_NAMES else ""
        ),
    }
