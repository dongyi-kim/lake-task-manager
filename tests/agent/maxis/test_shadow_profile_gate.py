from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.agent.maxis import backend, credentials, profiles, shadow_eval


DATASET_ID = "11111111-1111-4111-8111-111111111111"
WORKFLOW_ID = "22222222-2222-4222-8222-222222222222"
SENTINEL_SHA = "a" * 64
KNOWLEDGE_VERSION = "ltm-knowledge-2026.09"


class _TestCipher:
    def protect(self, value: bytes) -> bytes:
        return bytes(byte ^ 0xA5 for byte in value)

    def unprotect(self, value: bytes) -> bytes:
        return bytes(byte ^ 0xA5 for byte in value)


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    import app.infra.settings as settings

    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    for env in (
        "LAKE_MAXIS_PROFILE", *profiles.CONFIG_ENV_NAMES.values(),
        *profiles.WORKFLOW_VERSION_ENV_NAMES.values(), *credentials.ENV_NAMES.values(),
    ):
        monkeypatch.delenv(env, raising=False)
    credentials.set_backend_for_tests(_TestCipher())
    yield
    shadow_eval.reset_for_tests()
    credentials.set_backend_for_tests(None)


def _eval_attestation() -> dict:
    return {
        "contract": profiles.KNOWLEDGE_EVAL_ATTESTATION_CONTRACT,
        "evidence_id": "EVAL-2026-09-05",
        "outcome": "passed",
        "case_count": 25,
        "artifact_sha256": "b" * 64,
        "passed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **profiles.knowledge_eval_attestation_binding("prod"),
    }


def _configure_verified_prod(*, with_eval_attestation: bool = True) -> dict:
    profiles.save("prod", {
        "base_url": "https://maxis.example/v1",
        "workflow_bundle_version": "ltm-prod-1",
        "knowledge_version": KNOWLEDGE_VERSION,
        "knowledge_dataset_id": DATASET_ID,
        "knowledge_sentinel_sha256": SENTINEL_SHA,
        "remote_log_policy_id": "SEC-TEST-PROD",
        "remote_log_mode": "redacted",
        "remote_log_retention_days": 0,
        "workflows": {
            "agent_core": {"binding": "agent_core", "version": WORKFLOW_ID},
            "editor_author": {"binding": "editor_author", "version": "editor-1"},
            "pmo_brief": {"binding": "pmo_brief", "version": "pmo-1"},
        },
    })
    if with_eval_attestation:
        profiles.save("prod", {"knowledge_eval_attestation": _eval_attestation()})
    credentials.save_for("prod", {
        "agent_core": "prod-secret",
        "editor_author": "editor-secret",
        "pmo_brief": "pmo-secret",
    })
    profiles.credential_changed("prod")
    manifest = profiles.resolve_knowledge_manifest("prod", required=True)
    assert manifest is not None
    assert profiles.mark_probe("prod", True, {"workflows": {
        key: {
            "ok": True,
            "executionContractVerified": True,
            "versionBindingVerified": True,
            "citationContractVerified": True,
            "knowledgeRetrievalVerified": True,
            "knowledgeManifestSha256": manifest.sha256,
        }
        for key in profiles.WORKFLOW_KEYS
    }})
    return profiles.get("prod")


def _passing_sample(profile: dict, role_id: str) -> None:
    shadow_eval.record_success(
        profiles._shadow_dimensions(profile, "agent_core", role_id),
        comparison={
            "structureScore": 1.0,
            "numericComparable": True,
            "numericParity": True,
            "lexicalComparable": True,
            "lexicalScore": 0.8,
            "nonEmptyParity": True,
        },
        contract_valid=True,
        citation_valid=True,
    )


def test_prod_candidate_can_shadow_but_cannot_promote_before_quality_gate():
    profile = _configure_verified_prod()
    assert profiles.activate("prod")["enabled"] is True
    assert profiles.set_routes({"agent_core": "shadow"})["agent_core"] == "shadow"

    with pytest.raises(ValueError, match="shadow samples 0/5"):
        profiles.set_routes({"agent_core": "maxis"})

    roles = profiles._promotion_roles("agent_core")
    assert "work_architect" in roles and "knowledge_curator" in roles
    assert "research_analyst" not in roles and "people_advisor" not in roles

    for role_id in roles:
        for _ in range(shadow_eval.MIN_PROMOTION_SAMPLES):
            _passing_sample(profile, role_id)

    assert profiles.set_routes({"agent_core": "maxis"})["agent_core"] == "maxis"
    public = next(row for row in profiles.status()["profiles"] if row["name"] == "prod")
    assert public["promotion"]["agent_core"]["ready"] is True
    assert public["promotion"]["agent_core"]["privacy"] == "aggregate_metadata_only"
    assert set(public["promotion"]["agent_core"]["roles"]) == set(roles)
    assert public["knowledgeEvalAttestation"]["valid"] is True
    assert public["knowledgeEvalAttestation"]["attestation"]["outcome"] == "passed"
    assert public["knowledgeEvalAttestation"]["attestation"]["caseCount"] == 25
    assert public["knowledgeEvalAttestation"]["verification"] == (
        "metadata_binding_only_external_artifact_not_read"
    )


def test_one_agent_core_role_cannot_promote_the_whole_workflow():
    profile = _configure_verified_prod()
    profiles.activate("prod")
    profiles.set_routes({"agent_core": "shadow"})
    for _ in range(shadow_eval.MIN_PROMOTION_SAMPLES):
        _passing_sample(profile, "knowledge_curator")

    with pytest.raises(ValueError, match="work_architect: shadow samples 0/5"):
        profiles.set_routes({"agent_core": "maxis"})


def test_prod_activation_with_existing_maxis_route_requires_exact_new_profile_gate():
    profile = _configure_verified_prod()
    profiles.activate("prod")
    profiles.set_routes({"agent_core": "shadow"})
    for role_id in profiles._promotion_roles("agent_core"):
        for _ in range(shadow_eval.MIN_PROMOTION_SAMPLES):
            _passing_sample(profile, role_id)
    profiles.set_routes({"agent_core": "maxis"})

    profiles.save("prod", {"knowledge_dataset_id": "33333333-3333-4333-8333-333333333333"})
    credentials.save_for("prod", {
        "agent_core": "prod-secret",
        "editor_author": "editor-secret",
        "pmo_brief": "pmo-secret",
    })
    profiles.credential_changed("prod")
    manifest = profiles.resolve_knowledge_manifest("prod", required=True)
    assert manifest is not None
    profiles.mark_probe("prod", True, {"workflows": {
        key: {
            "ok": True,
            "executionContractVerified": True,
            "versionBindingVerified": True,
            "citationContractVerified": True,
            "knowledgeRetrievalVerified": True,
            "knowledgeManifestSha256": manifest.sha256,
        }
        for key in profiles.WORKFLOW_KEYS
    }})

    with pytest.raises(ValueError, match="Knowledge eval dataset_id"):
        profiles.activate("prod")

    profiles.save("prod", {"knowledge_eval_attestation": _eval_attestation()})
    _mark_prod_probe_after_profile_change()
    with pytest.raises(ValueError, match="shadow samples 0/5"):
        profiles.activate("prod")


def _mark_prod_probe_after_profile_change() -> None:
    manifest = profiles.resolve_knowledge_manifest("prod", required=True)
    assert manifest is not None
    assert profiles.mark_probe("prod", True, {"workflows": {
        key: {
            "ok": True,
            "executionContractVerified": True,
            "versionBindingVerified": True,
            "citationContractVerified": True,
            "knowledgeRetrievalVerified": True,
            "knowledgeManifestSha256": manifest.sha256,
        }
        for key in profiles.WORKFLOW_KEYS
    }})


def test_prod_shadow_activation_allowed_but_authority_requires_eval_attestation():
    _configure_verified_prod(with_eval_attestation=False)
    assert profiles.activate("prod")["enabled"] is True
    assert profiles.set_routes({"agent_core": "shadow"})["agent_core"] == "shadow"

    with pytest.raises(ValueError, match="25-case Knowledge eval attestation"):
        profiles.set_routes({"agent_core": "maxis"})


@pytest.mark.parametrize("patch", [
    {"outcome": "failed"},
    {"case_count": 24},
])
def test_eval_attestation_requires_an_explicit_25_case_pass_result(patch):
    _configure_verified_prod(with_eval_attestation=False)
    attestation = {**_eval_attestation(), **patch}

    with pytest.raises(ValueError, match="outcome=passed and case_count=25"):
        profiles.save("prod", {"knowledge_eval_attestation": attestation})


def test_removing_attestation_disables_existing_authoritative_runtime():
    profile = _configure_verified_prod()
    profiles.activate("prod")
    profiles.set_routes({"agent_core": "shadow"})
    for role_id in profiles._promotion_roles("agent_core"):
        for _ in range(shadow_eval.MIN_PROMOTION_SAMPLES):
            _passing_sample(profile, role_id)
    profiles.set_routes({"agent_core": "maxis"})
    assert profiles.active()["enabled"] is True
    pinned = backend.thread_runtime_pin()

    profiles.save("prod", {"knowledge_eval_attestation": {}})

    assert profiles.route_for("agent_core") == "maxis"
    assert profiles.active()["enabled"] is False
    with pytest.raises(backend.MaxisRuntimeError, match="검증·활성화"):
        backend.verify_thread_runtime(pinned)
    _mark_prod_probe_after_profile_change()
    with pytest.raises(ValueError, match="not configured"):
        profiles.activate("prod")


def test_environment_attestation_change_invalidates_existing_authority(monkeypatch):
    profile = _configure_verified_prod()
    profiles.activate("prod")
    profiles.set_routes({"agent_core": "shadow"})
    for role_id in profiles._promotion_roles("agent_core"):
        for _ in range(shadow_eval.MIN_PROMOTION_SAMPLES):
            _passing_sample(profile, role_id)
    profiles.set_routes({"agent_core": "maxis"})
    before = profiles.active_fingerprint()

    changed = _eval_attestation()
    changed["evidence_id"] = "EVAL-ROTATED"
    changed["artifact_sha256"] = "c" * 64
    monkeypatch.setenv(
        "LAKE_MAXIS_KNOWLEDGE_EVAL_ATTESTATION",
        json.dumps(changed, separators=(",", ":")),
    )

    assert profiles.active_fingerprint() != before
    assert profiles.active()["enabled"] is False
