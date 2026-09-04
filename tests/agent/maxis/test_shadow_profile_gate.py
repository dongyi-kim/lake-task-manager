from __future__ import annotations

import pytest

from app.agent.maxis import credentials, profiles, shadow_eval


DATASET_ID = "11111111-1111-4111-8111-111111111111"
WORKFLOW_ID = "22222222-2222-4222-8222-222222222222"
SENTINEL_SHA = "a" * 64


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


def _configure_verified_prod() -> dict:
    profiles.save("prod", {
        "base_url": "https://maxis.example/v1",
        "workflow_bundle_version": "ltm-prod-1",
        "knowledge_version": "ltm-knowledge-1",
        "knowledge_dataset_id": DATASET_ID,
        "knowledge_sentinel_sha256": SENTINEL_SHA,
        "workflows": {
            "agent_core": {"binding": "agent_core", "version": WORKFLOW_ID},
        },
    })
    credentials.save_for("prod", {"agent_core": "prod-secret"})
    profiles.credential_changed("prod")
    manifest = profiles.resolve_knowledge_manifest("prod", required=True)
    assert manifest is not None
    assert profiles.mark_probe("prod", True, {"workflows": {
        "agent_core": {
            "ok": True,
            "executionContractVerified": True,
            "versionBindingVerified": True,
            "citationContractVerified": True,
            "knowledgeRetrievalVerified": True,
            "knowledgeManifestSha256": manifest.sha256,
        },
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

    profiles.save("prod", {"knowledge_version": "ltm-knowledge-2"})
    credentials.save_for("prod", {"agent_core": "prod-secret"})
    profiles.credential_changed("prod")
    manifest = profiles.resolve_knowledge_manifest("prod", required=True)
    assert manifest is not None
    profiles.mark_probe("prod", True, {"workflows": {"agent_core": {
        "ok": True,
        "executionContractVerified": True,
        "versionBindingVerified": True,
        "citationContractVerified": True,
        "knowledgeRetrievalVerified": True,
        "knowledgeManifestSha256": manifest.sha256,
    }}})

    with pytest.raises(ValueError, match="shadow samples 0/5"):
        profiles.activate("prod")
