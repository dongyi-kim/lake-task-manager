from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.agent.maxis import backend, credentials, profiles, shadow_eval


DATASET_ID = "11111111-1111-4111-8111-111111111111"
WORKFLOW_ID = "22222222-2222-4222-8222-222222222222"
EDITOR_WORKFLOW_ID = "33333333-3333-4333-8333-333333333333"
PMO_WORKFLOW_ID = "44444444-4444-4444-8444-444444444444"
SENTINEL_SHA = "a" * 64
KNOWLEDGE_VERSION = "ltm-public-knowledge.v1"


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
        *profiles.WORKFLOW_VERSION_ENV_NAMES.values(),
        *profiles.ALLOWED_HOSTS_ENV_NAMES.values(), *credentials.ENV_NAMES.values(),
    ):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("LAKE_MAXIS_PROD_ALLOWED_HOSTS", "maxis.example")
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
            "editor_author": {
                "binding": "editor_author", "version": EDITOR_WORKFLOW_ID,
            },
            "pmo_brief": {"binding": "pmo_brief", "version": PMO_WORKFLOW_ID},
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
    _, keys, start_binding = profiles.start_probe("prod")
    keys.clear()
    assert profiles.mark_probe("prod", True, {"workflows": {
        key: {
            "ok": True,
            "appModeVerified": True,
            "executionContractVerified": True,
            "versionBindingVerified": True,
            "citationContractVerified": True,
            "knowledgeRetrievalVerified": True,
            "queryPathAttempted": True,
            "queryPathVerified": True,
            "knowledgeManifestSha256": manifest.sha256,
        }
        for key in profiles.WORKFLOW_KEYS
    }}, start_binding=start_binding)
    return profiles.get("prod")


def test_disabled_pmo_does_not_inherit_previous_route_or_stored_key(monkeypatch):
    _configure_verified_prod(with_eval_attestation=False)
    profiles.prefs.save_strict({"maxisRoutes": {
        "agent_core": "local", "editor_author": "local", "pmo_brief": "maxis"}})
    profiles.save("prod", {"workflows": {
        "pmo_brief": {"binding": "pmo_brief", "version": ""}}})
    assert credentials.get_for("prod", "pmo_brief") == "pmo-secret"
    candidate, keys, signature = profiles.start_probe("prod")
    assert profiles.probe_workflow_keys(candidate) == {"agent_core", "editor_author"}
    manifest = profiles.resolve_knowledge_manifest("prod")
    assert profiles.mark_probe("prod", True, {"workflows": {
        key: {"ok": True, "appModeVerified": True, "executionContractVerified": True,
              "versionBindingVerified": True, "citationContractVerified": True,
              "knowledgeRetrievalVerified": True, "knowledgeManifestSha256": manifest.sha256,
              "queryPathAttempted": True, "queryPathVerified": True}
        for key in ("agent_core", "editor_author")
    }}, start_binding=signature)
    keys.clear()
    # A verified optional candidate still cannot execute the old PMO route.
    assert not profiles.resolve("prod")["enabled"]
    from app.agent.maxis import qualification
    monkeypatch.setattr(qualification, "status", lambda _: {
        "valid": True, "workflows": ["agent_core", "editor_author"]})
    result = profiles.apply_production(["agent_core", "editor_author"])
    assert result["routes"] == {"agent_core": "maxis", "editor_author": "maxis", "pmo_brief": "local"}
    assert profiles.resolve("prod")["enabled"]


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


def test_prod_candidate_can_shadow_but_metadata_never_unlocks_authority():
    profile = _configure_verified_prod()
    assert profiles.activate("prod")["enabled"] is True
    assert profiles.set_routes({"agent_core": "shadow"})["agent_core"] == "shadow"

    with pytest.raises(ValueError, match="Signed external|signed external"):
        profiles.set_routes({"agent_core": "maxis"})

    roles = profiles._promotion_roles("agent_core")
    assert "work_architect" in roles and "knowledge_curator" in roles
    assert "research_analyst" not in roles and "people_advisor" not in roles

    for role_id in roles:
        for _ in range(shadow_eval.MIN_PROMOTION_SAMPLES):
            _passing_sample(profile, role_id)

    with pytest.raises(ValueError, match="Signed external|signed external"):
        profiles.set_routes({"agent_core": "maxis"})
    assert profiles.route_for("agent_core") == "shadow"
    public = next(row for row in profiles.status()["profiles"] if row["name"] == "prod")
    assert public["promotion"]["agent_core"]["ready"] is True
    assert public["promotion"]["agent_core"]["privacy"] == "aggregate_metadata_only"
    assert set(public["promotion"]["agent_core"]["roles"]) == set(roles)
    assert public["knowledgeEvalAttestation"]["bindingValid"] is True
    assert public["knowledgeEvalAttestation"]["valid"] is False
    assert public["knowledgeEvalAttestation"]["attestation"]["outcome"] == "passed"
    assert public["knowledgeEvalAttestation"]["attestation"]["caseCount"] == 25
    assert public["knowledgeEvalAttestation"]["verification"] == (
        "metadata_binding_only_signed_artifact_not_verified"
    )
    assert any(
        "Signed external Knowledge eval" in reason
        for reason in public["knowledgeEvalAttestation"]["reasons"]
    )
    assert not any(
        "Role ACL enforcement is not implemented" in reason
        for reason in public["knowledgeEvalAttestation"]["reasons"]
    )


def test_one_agent_core_role_cannot_promote_the_whole_workflow():
    profile = _configure_verified_prod()
    profiles.activate("prod")
    profiles.set_routes({"agent_core": "shadow"})
    for _ in range(shadow_eval.MIN_PROMOTION_SAMPLES):
        _passing_sample(profile, "knowledge_curator")

    status = profiles._workflow_promotion(profiles.resolve("prod"), "agent_core")
    assert status["ready"] is False
    assert any("work_architect: shadow samples 0/5" in reason for reason in status["reasons"])


def test_prod_activation_with_legacy_maxis_route_is_hard_blocked():
    from app.infra import prefs

    profile = _configure_verified_prod()
    for role_id in profiles._promotion_roles("agent_core"):
        for _ in range(shadow_eval.MIN_PROMOTION_SAMPLES):
            _passing_sample(profile, role_id)
    prefs.save_strict({"maxisRoutes": {
        "agent_core": "maxis", "editor_author": "local", "pmo_brief": "local",
    }})

    with pytest.raises(ValueError, match="Signed external|signed external"):
        profiles.activate("prod")


def _mark_prod_probe_after_profile_change() -> None:
    manifest = profiles.resolve_knowledge_manifest("prod", required=True)
    assert manifest is not None
    _, keys, start_binding = profiles.start_probe("prod")
    keys.clear()
    assert profiles.mark_probe("prod", True, {"workflows": {
        key: {
            "ok": True,
            "appModeVerified": True,
            "executionContractVerified": True,
            "versionBindingVerified": True,
            "citationContractVerified": True,
            "knowledgeRetrievalVerified": True,
            "queryPathAttempted": True,
            "queryPathVerified": True,
            "knowledgeManifestSha256": manifest.sha256,
        }
        for key in profiles.WORKFLOW_KEYS
    }}, start_binding=start_binding)


def test_prod_shadow_activation_allowed_but_authority_requires_eval_attestation():
    _configure_verified_prod(with_eval_attestation=False)
    assert profiles.activate("prod")["enabled"] is True
    assert profiles.set_routes({"agent_core": "shadow"})["agent_core"] == "shadow"

    with pytest.raises(ValueError, match="qualification"):
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


def test_removing_attestation_invalidates_active_shadow_profile():
    _configure_verified_prod()
    profiles.activate("prod")
    profiles.set_routes({"agent_core": "shadow"})
    assert profiles.active()["enabled"] is True
    pinned = backend.thread_runtime_pin()

    profiles.save("prod", {"knowledge_eval_attestation": {}})

    assert profiles.route_for("agent_core") == "shadow"
    assert profiles.active()["enabled"] is False
    with pytest.raises(backend.MaxisRuntimeError, match="검증·활성화"):
        backend.verify_thread_runtime(pinned)
    _mark_prod_probe_after_profile_change()
    assert profiles.activate("prod")["enabled"] is True


def test_environment_attestation_change_invalidates_existing_authority(monkeypatch):
    _configure_verified_prod()
    profiles.activate("prod")
    profiles.set_routes({"agent_core": "shadow"})
    before = profiles.active_fingerprint()

    changed = _eval_attestation()
    changed["evidence_id"] = "EVAL-ROTATED"
    changed["artifact_sha256"] = "c" * 64
    monkeypatch.setenv("LAKE_MAXIS_PROFILE", "prod")
    monkeypatch.setenv(
        "LAKE_MAXIS_KNOWLEDGE_EVAL_ATTESTATION",
        json.dumps(changed, separators=(",", ":")),
    )

    assert profiles.active_fingerprint() != before
    assert profiles.active()["enabled"] is False


def test_explicit_engine_cannot_bypass_thread_pin_verify_or_role_boundary():
    _configure_verified_prod()
    profiles.activate("prod")

    with pytest.raises(backend.MaxisRuntimeError, match="release security gate"):
        backend.thread_runtime_pin(engine="maxis", profile="prod")

    pin = backend.thread_runtime_pin(engine="shadow", profile="prod")
    maxis_routes = {
        role_id: ("maxis" if mode == "shadow" else mode)
        for role_id, mode in pin["runtime_role_routes"].items()
    }
    metadata = backend._profile_snapshot("prod", required=True)
    forged = {
        **pin,
        "runtime_engine": "maxis",
        "runtime_role_routes": maxis_routes,
        "runtime_profile_fingerprint": backend._runtime_fingerprint(
            metadata,
            maxis_routes,
            registry_digest=pin["runtime_registry_digest"],
        ),
    }
    with pytest.raises(backend.MaxisRuntimeError, match="release security gate"):
        backend.verify_thread_runtime(forged)

    with pytest.raises(backend.MaxisRuntimeError, match="release security gate"):
        backend.resolve_role("request_architect", {
            "runtime_profile": "prod",
            "runtime_role_routes": {"request_architect": "maxis"},
        })
