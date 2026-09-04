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
from datetime import datetime, timezone
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
CONFIG_ENV_NAMES = {
    "base_url": "LAKE_MAXIS_BASE_URL",
    "workflow_bundle_version": "LAKE_MAXIS_WORKFLOW_BUNDLE_VERSION",
    "knowledge_version": "LAKE_MAXIS_KNOWLEDGE_VERSION",
    "knowledge_dataset_id": "LAKE_MAXIS_KNOWLEDGE_DATASET_ID",
    "knowledge_sentinel_sha256": "LAKE_MAXIS_KNOWLEDGE_SENTINEL_SHA256",
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
        "knowledge_sentinel_sha256",
    ):
        if key in saved:
            out[key] = str(saved.get(key) or "").strip()
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
               "knowledge_sentinel_sha256", "workflows"}
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
        prefs.save({"maxisProfiles": [rows[key] for key in PROFILE_NAMES],
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
    for workflow_key, env_name in WORKFLOW_VERSION_ENV_NAMES.items():
        value = str(os.getenv(env_name) or "").strip()
        if value:
            result["workflows"][workflow_key]["version"] = _clean_version(
                value, f"{workflow_key} workflow version"
            )
    return result


def profile_fingerprint(profile: dict) -> str:
    """Stable public fingerprint containing no API key or credential digest."""
    manifest = _build_profile_manifest(profile, required=False)
    clean = {key: profile[key] for key in (
        "name", "stage", "base_url", "data_policy", "workflow_bundle_version",
        "knowledge_version", "knowledge_dataset_id", "workflows",
        "knowledge_sentinel_sha256",
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


def probe_ok(profile_name: str) -> bool:
    try:
        expected = _probe_signature(profile_name)
    except Exception:
        return False
    stored = (prefs.load().get("maxisProbeOkByProfile") or {}).get(profile_name)
    return bool(stored) and stored == expected


def _probe_contract_verified(profile_name: str, details: dict | None) -> bool:
    """Require proof for every configured/remote app before persisting verification."""
    if not isinstance(details, dict):
        return False
    workflow_results = details.get("workflows")
    if not isinstance(workflow_results, dict):
        return False
    profile = _effective(get(profile_name))
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
    prefs.save({"maxisProbeOkByProfile": verified,
                "maxisLastProbeByProfile": last_probe})
    return verified_ok


def credential_changed(profile_name: str) -> None:
    name = str(profile_name or "").strip().lower()
    state = prefs.load()
    verified = dict(state.get("maxisProbeOkByProfile") or {})
    last_probe = dict(state.get("maxisLastProbeByProfile") or {})
    verified.pop(name, None)
    last_probe.pop(name, None)
    prefs.save({"maxisProbeOkByProfile": verified,
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
    prefs.save({"maxisActiveProfile": name,
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
        "knowledge_sentinel_sha256",
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
    for workflow_key in sorted(set(workflow_keys)):
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
    prefs.save({"maxisRoutes": updated})
    return updated


def kill_switch_enabled() -> bool:
    return bool(prefs.load().get("maxisKillSwitch", False))


def set_kill_switch(enabled: bool) -> bool:
    prefs.save({"maxisKillSwitch": bool(enabled)})
    return kill_switch_enabled()


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
