from __future__ import annotations

import os

import pytest

from app.agent.maxis import credentials, profiles


DATASET_A = "11111111-1111-4111-8111-111111111111"
DATASET_B = "22222222-2222-4222-8222-222222222222"
SENTINEL_A = "a" * 64
SENTINEL_B = "b" * 64
KNOWLEDGE_VERSION = "ltm-knowledge-2026.09"


class _TestCipher:
    """Deterministic non-plaintext backend used only by isolated tests."""

    def protect(self, value: bytes) -> bytes:
        return bytes(byte ^ 0xA5 for byte in value)

    def unprotect(self, value: bytes) -> bytes:
        return bytes(byte ^ 0xA5 for byte in value)


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    import app.infra.settings as settings

    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    for env in ("LAKE_MAXIS_PROFILE", *profiles.CONFIG_ENV_NAMES.values(),
                *profiles.WORKFLOW_VERSION_ENV_NAMES.values(),
                *credentials.ENV_NAMES.values()):
        monkeypatch.delenv(env, raising=False)
    credentials.set_backend_for_tests(_TestCipher())
    yield
    credentials.set_backend_for_tests(None)


def _configured_dev() -> None:
    profiles.save("dev", {
        "base_url": "https://dev.maxis.example/v1/",
        "workflow_bundle_version": "ltm-agent-1",
        "knowledge_version": KNOWLEDGE_VERSION,
        "knowledge_dataset_id": DATASET_A,
        "knowledge_sentinel_sha256": SENTINEL_A,
        "workflows": {"agent_core": {"binding": "agent_core", "version": "1.2.0"}},
    })
    credentials.save_for("dev", {"agent_core": "app-secret-1111"})
    profiles.credential_changed("dev")


def _mark_verified(*workflow_keys: str) -> None:
    manifest = profiles.resolve_knowledge_manifest("dev", required=True)
    assert manifest is not None
    profiles.mark_probe("dev", True, {"workflows": {
        key: {
            "ok": True,
            "executionContractVerified": True,
            "versionBindingVerified": True,
            "citationContractVerified": True,
            "knowledgeRetrievalVerified": True,
            "knowledgeManifestSha256": manifest.sha256,
        }
        for key in workflow_keys
    }})


def test_dev_and_prod_profiles_have_fixed_data_policies():
    rows = {row["name"]: row for row in profiles.list_all()}
    assert rows["dev"]["stage"] == "dev"
    assert rows["dev"]["data_policy"] == "synthetic_only"
    assert rows["prod"]["stage"] == "prod"
    assert rows["prod"]["data_policy"] == "internal_read_only"
    with pytest.raises(ValueError, match="dataPolicy"):
        profiles.save("dev", {"data_policy": "internal_read_only"})


def test_profile_url_policy_allows_only_loopback_http_in_dev():
    profiles.save("dev", {"base_url": "http://127.0.0.1:5001"})
    assert profiles.get("dev")["base_url"] == "http://127.0.0.1:5001/v1"
    with pytest.raises(ValueError, match="HTTPS"):
        profiles.save("dev", {"base_url": "http://dev.maxis.example/v1"})
    with pytest.raises(ValueError, match="prod"):
        profiles.save("prod", {"base_url": "https://localhost/v1"})


@pytest.mark.parametrize("value", ["release with spaces", "app-secret/value", "line\nbreak"])
def test_profile_rejects_non_wire_safe_version_values(value):
    with pytest.raises(ValueError, match="Invalid workflow bundle version"):
        profiles.save("dev", {"workflow_bundle_version": value})


def test_profile_requires_canonical_dataset_uuid_for_activation():
    with pytest.raises(ValueError, match="dataset UUID"):
        profiles.save("dev", {"knowledge_dataset_id": "not-a-uuid"})

    profiles.save("dev", {
        "base_url": "https://dev.maxis.example/v1",
        "workflow_bundle_version": "bundle-1",
        "knowledge_version": "knowledge-1",
        "workflows": {"agent_core": {"binding": "agent_core", "version": "wf-1"}},
    })
    credentials.save_for("dev", {"agent_core": "app-key"})
    with pytest.raises(ValueError, match="dataset UUID"):
        profiles.activate("dev")


def test_profile_requires_a_lowercase_knowledge_sentinel_digest():
    with pytest.raises(ValueError, match="sentinel"):
        profiles.save("dev", {"knowledge_sentinel_sha256": "NOT-A-DIGEST"})


def test_credential_file_is_encrypted_and_status_never_discloses_key(tmp_path):
    secret = "app-secret-plain-value"
    result = credentials.save_for("dev", {"agent_core": secret})
    stored = (tmp_path / "maxis_credentials.dpapi").read_bytes()
    assert secret.encode() not in stored
    assert result["agent_core"] == {
        "configured": True, "source": "dpapi", "environmentVariable": "", "storeError": ""
    }
    assert secret not in repr(result)
    assert credentials.get_for("dev", "agent_core") == secret


def test_non_windows_persistent_store_fails_closed(monkeypatch):
    credentials.set_backend_for_tests(None)
    def unavailable():
        raise credentials.CredentialStoreUnavailable(
            "MAXIS credential persistence requires Windows current-user DPAPI."
        )
    monkeypatch.setattr(credentials, "WindowsDpapiBackend", unavailable)
    with pytest.raises(credentials.CredentialStoreUnavailable, match="DPAPI"):
        credentials.save_for("dev", {"agent_core": "never-plaintext"})


def test_environment_overrides_profile_url_selection_and_keys(monkeypatch):
    _configured_dev()
    _mark_verified("agent_core")
    profiles.activate("dev")
    monkeypatch.setenv("LAKE_MAXIS_PROFILE", "dev")
    monkeypatch.setenv("LAKE_MAXIS_BASE_URL", "https://runtime.maxis.example/v1")
    monkeypatch.setenv("LAKE_MAXIS_AGENT_CORE_API_KEY", "env-secret")
    resolved = profiles.resolve()
    assert resolved["base_url"] == "https://runtime.maxis.example/v1"
    assert credentials.get_for("dev", "agent_core") == "env-secret"
    assert profiles.probe_ok("dev") is False
    status_text = repr(profiles.status())
    assert "env-secret" not in status_text and "app-secret-1111" not in status_text


def test_environment_selected_profile_cannot_be_overridden_by_ui(monkeypatch):
    _configured_dev()
    monkeypatch.setenv("LAKE_MAXIS_PROFILE", "dev")
    with pytest.raises(ValueError, match="selects dev"):
        profiles.activate("prod")


def test_deployment_version_overrides_are_effective_private_and_invalidate_probe(monkeypatch):
    _configured_dev()
    _mark_verified("agent_core")
    profiles.activate("dev")
    original_fingerprint = profiles.active_fingerprint()

    overrides = {
        "LAKE_MAXIS_WORKFLOW_BUNDLE_VERSION": "bundle-from-deployment",
        "LAKE_MAXIS_KNOWLEDGE_VERSION": KNOWLEDGE_VERSION,
        "LAKE_MAXIS_KNOWLEDGE_DATASET_ID": DATASET_B,
        "LAKE_MAXIS_KNOWLEDGE_SENTINEL_SHA256": SENTINEL_B,
        "LAKE_MAXIS_AGENT_CORE_VERSION": "core-from-deployment",
        "LAKE_MAXIS_EDITOR_AUTHOR_VERSION": "editor-from-deployment",
        "LAKE_MAXIS_PMO_BRIEF_VERSION": "pmo-from-deployment",
    }
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)

    effective = profiles.resolve("dev")
    assert effective["workflow_bundle_version"] == "bundle-from-deployment"
    assert effective["knowledge_version"] == KNOWLEDGE_VERSION
    assert effective["knowledge_dataset_id"] == DATASET_B
    assert effective["knowledge_sentinel_sha256"] == SENTINEL_B
    assert len(effective["knowledge_manifest_sha256"]) == 64
    assert effective["workflows"]["agent_core"]["version"] == "core-from-deployment"
    assert effective["workflows"]["editor_author"]["version"] == "editor-from-deployment"
    assert effective["workflows"]["pmo_brief"]["version"] == "pmo-from-deployment"
    assert profiles.active_fingerprint() != original_fingerprint
    assert profiles.probe_ok("dev") is False
    assert effective["enabled"] is False

    status = profiles.status()
    names = status["environmentOverrides"]
    assert names["workflowBundleVersion"] == "LAKE_MAXIS_WORKFLOW_BUNDLE_VERSION"
    assert names["knowledgeVersion"] == "LAKE_MAXIS_KNOWLEDGE_VERSION"
    assert names["knowledgeDatasetId"] == "LAKE_MAXIS_KNOWLEDGE_DATASET_ID"
    assert names["knowledgeSentinelSha256"] == (
        "LAKE_MAXIS_KNOWLEDGE_SENTINEL_SHA256"
    )
    assert names["workflowVersions"] == profiles.WORKFLOW_VERSION_ENV_NAMES
    rendered = repr(status)
    assert DATASET_B in rendered  # dataset identity and manifest digest are public metadata
    assert not any(
        value in rendered for key, value in overrides.items()
         if key not in {
            "LAKE_MAXIS_KNOWLEDGE_VERSION",
            "LAKE_MAXIS_KNOWLEDGE_DATASET_ID",
            "LAKE_MAXIS_KNOWLEDGE_SENTINEL_SHA256",
        }
    )


def test_probe_activation_routing_and_kill_switch_are_separate_gates():
    _configured_dev()
    assert profiles.resolve("dev")["enabled"] is False
    with pytest.raises(ValueError, match="probe"):
        profiles.activate("dev")

    _mark_verified("agent_core")
    activated = profiles.activate("dev")
    assert activated["enabled"] is True
    assert profiles.active_fingerprint() == activated["fingerprint"]
    assert "app-secret" not in profiles.active_fingerprint()

    with pytest.raises(ValueError, match="synthetic-only"):
        profiles.set_routes({"agent_core": "maxis"})
    assert profiles.set_routes({"agent_core": "shadow"})["agent_core"] == "shadow"
    assert profiles.route_for("work_architect") == "shadow"
    profiles.set_kill_switch(True)
    assert profiles.active()["enabled"] is False
    assert profiles.route_for("work_architect") == "shadow"  # no silent local fallback
    with pytest.raises(ValueError, match="kill switch"):
        profiles.set_routes({"editor_author": "shadow"})


def test_parameter_only_probe_result_cannot_mark_profile_verified():
    _configured_dev()
    profiles.mark_probe("dev", True, {
        "workflows": {"agent_core": {
            "ok": True,
            "statusCode": 200,
            "missingParameters": [],
        }},
    })

    assert profiles.probe_ok("dev") is False
    assert profiles.status()["profiles"][0]["lastProbe"]["ok"] is False
    with pytest.raises(ValueError, match="probe"):
        profiles.activate("dev")


def test_profile_or_key_change_invalidates_prior_probe_and_activation():
    _configured_dev()
    _mark_verified("agent_core")
    profiles.activate("dev")
    before = profiles.active_fingerprint()

    profiles.save("dev", {"knowledge_dataset_id": DATASET_B})
    assert profiles.active_fingerprint() != before
    assert profiles.probe_ok("dev") is False
    assert profiles.resolve("dev")["enabled"] is False

    before_dataset = profiles.profile_fingerprint(profiles.get("dev"))
    profiles.save("dev", {"knowledge_dataset_id": DATASET_A})
    after_dataset = profiles.profile_fingerprint(profiles.get("dev"))
    assert after_dataset != before_dataset

    before_sentinel = after_dataset
    profiles.save("dev", {"knowledge_sentinel_sha256": SENTINEL_B})
    assert profiles.profile_fingerprint(profiles.get("dev")) != before_sentinel

    _mark_verified("agent_core")
    profiles.activate("dev")
    public_before_key_rotation = profiles.active_fingerprint()
    credentials.save_for("dev", {"agent_core": "rotated-secret"})
    profiles.credential_changed("dev")
    assert profiles.active_fingerprint() == public_before_key_rotation
    assert profiles.resolve("dev")["enabled"] is False


def test_unreviewed_knowledge_version_environment_override_fails_closed(monkeypatch):
    _configured_dev()
    monkeypatch.setenv("LAKE_MAXIS_KNOWLEDGE_VERSION", "unreviewed-version")

    with pytest.raises(ValueError, match="exact knowledge version"):
        profiles.resolve("dev")


def test_activation_requires_credentials_for_existing_remote_routes():
    from app.infra import prefs

    _configured_dev()
    prefs.save({"maxisRoutes": {
        "agent_core": "local", "editor_author": "shadow", "pmo_brief": "local",
    }})
    _mark_verified("agent_core")
    with pytest.raises(ValueError, match="editor_author"):
        profiles.activate("dev")

    profiles.save("dev", {
        "workflows": {"editor_author": {
            "binding": "editor_author", "version": "1.0.0",
        }},
    })
    credentials.save_for("dev", {"editor_author": "editor-secret"})
    profiles.credential_changed("dev")
    _mark_verified("agent_core", "editor_author")
    assert profiles.activate("dev")["enabled"] is True


def test_dev_activation_rejects_an_existing_authoritative_route():
    from app.infra import prefs

    _configured_dev()
    _mark_verified("agent_core")
    prefs.save({"maxisRoutes": {
        "agent_core": "maxis", "editor_author": "local", "pmo_brief": "local",
    }})

    with pytest.raises(ValueError, match="authoritative MAXIS route"):
        profiles.activate("dev")
