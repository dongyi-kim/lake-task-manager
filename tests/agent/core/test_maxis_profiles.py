from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.agent.maxis import credentials, profiles


DATASET_A = "11111111-1111-4111-8111-111111111111"
DATASET_B = "22222222-2222-4222-8222-222222222222"
SENTINEL_A = "a" * 64
SENTINEL_B = "b" * 64
KNOWLEDGE_VERSION = "ltm-public-knowledge.v1"
AGENT_WORKFLOW_ID = "22222222-2222-4222-8222-222222222222"
EDITOR_WORKFLOW_ID = "33333333-3333-4333-8333-333333333333"
PMO_WORKFLOW_ID = "44444444-4444-4444-8444-444444444444"
MIXED_CASE_WORKFLOW_ID = "ABCDEF12-3456-4ABC-8DEF-ABCDEF123456"
DEPLOY_AGENT_WORKFLOW_ID = "55555555-5555-4555-8555-555555555555"
DEPLOY_EDITOR_WORKFLOW_ID = "66666666-6666-4666-8666-666666666666"
DEPLOY_PMO_WORKFLOW_ID = "77777777-7777-4777-8777-777777777777"


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
                *profiles.ALLOWED_HOSTS_ENV_NAMES.values(),
                *credentials.ENV_NAMES.values()):
        monkeypatch.delenv(env, raising=False)
    # Test-only FQDNs are authorized through the same deployment-owned environment
    # contract as production; the application contains no test-host bypass.
    monkeypatch.setenv(
        "LAKE_MAXIS_DEV_ALLOWED_HOSTS",
        "dev.maxis.example,runtime.maxis.example",
    )
    monkeypatch.setenv(
        "LAKE_MAXIS_PROD_ALLOWED_HOSTS",
        "prod.maxis.example,maxis.example",
    )
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
        "workflows": {"agent_core": {
            "binding": "agent_core", "version": AGENT_WORKFLOW_ID,
        }},
    })
    credentials.save_for("dev", {"agent_core": "app-secret-1111"})
    profiles.credential_changed("dev")


def _configured_prod(*, base_url: str = "https://prod.maxis.example/v1") -> None:
    profiles.save("prod", {
        "base_url": base_url,
        # Release labels may match dev; separation is enforced on concrete assets.
        "workflow_bundle_version": "ltm-agent-1",
        "knowledge_version": KNOWLEDGE_VERSION,
        "knowledge_dataset_id": DATASET_B,
        "knowledge_sentinel_sha256": SENTINEL_B,
        "remote_log_policy_id": "SEC-TEST-PROD",
        "remote_log_mode": "redacted",
        "remote_log_retention_days": 0,
        "workflows": {
            "agent_core": {
                "binding": "agent_core", "version": DEPLOY_AGENT_WORKFLOW_ID,
            },
            "editor_author": {
                "binding": "editor_author", "version": DEPLOY_EDITOR_WORKFLOW_ID,
            },
            "pmo_brief": {
                "binding": "pmo_brief", "version": DEPLOY_PMO_WORKFLOW_ID,
            },
        },
    })
    credentials.save_for("prod", {
        "agent_core": "app-prod-agent-secret",
        "editor_author": "app-prod-editor-secret",
        "pmo_brief": "app-prod-pmo-secret",
    })
    profiles.credential_changed("prod")


def _mark_verified(*workflow_keys: str) -> None:
    manifest = profiles.resolve_knowledge_manifest("dev", required=True)
    assert manifest is not None
    _, keys, start_binding = profiles.start_probe("dev")
    keys.clear()
    profiles.mark_probe("dev", True, {"workflows": {
        key: {
            "ok": True,
            "appModeVerified": True,
            "executionContractVerified": True,
            "versionBindingVerified": True,
            "citationContractVerified": True,
            "knowledgeRetrievalVerified": True,
            "knowledgeManifestSha256": manifest.sha256,
        }
        for key in workflow_keys
    }}, start_binding=start_binding)


def test_successful_probe_status_preserves_canonical_utc_timestamp():
    _configured_dev()
    _mark_verified("agent_core")

    timestamp = profiles.status()["profiles"][0]["lastProbe"]["at"]

    assert timestamp.endswith("Z")
    assert datetime.fromisoformat(timestamp[:-1] + "+00:00").tzinfo is not None


def test_dev_and_prod_profiles_have_fixed_data_policies():
    rows = {row["name"]: row for row in profiles.list_all()}
    assert rows["dev"]["stage"] == "dev"
    assert rows["dev"]["data_policy"] == "synthetic_only"
    assert rows["prod"]["stage"] == "prod"
    assert rows["prod"]["data_policy"] == "internal_read_only"
    with pytest.raises(ValueError, match="dataPolicy"):
        profiles.save("dev", {"data_policy": "internal_read_only"})


@pytest.mark.parametrize("storage_shape", ["list", "dict"])
@pytest.mark.parametrize("target_exists", [False, True])
def test_profile_save_preserves_unrelated_stored_rows_and_probe_records(
    storage_shape, target_exists, monkeypatch,
):
    from app.infra import prefs

    # A rotated/invalid sibling must remain repairable, including fields this
    # version does not understand and operator-entered whitespace.
    sibling = {
        "name": "prod", "base_url": " https://retired.maxis.example/v1/ ",
        "data_policy": "not-a-policy", "knowledge_version": " old-release ",
        "workflows": {"agent_core": {"binding": " old-binding ",
                                     "version": " old-version ",
                                     "extension": {"labels": [" keep "]}}},
        "operator_metadata": {"note": " retain whitespace ", "nested": [1, {"x": 2}]},
    }
    rows = [profiles.get("dev"), sibling] if target_exists else [sibling]
    stored = rows if storage_shape == "list" else {row["name"]: row for row in rows}
    verified = {name: {"fingerprint": " keep "} for name in profiles.PROFILE_NAMES}
    last_probe = {name: {"result": {"items": [" keep "]}} for name in profiles.PROFILE_NAMES}
    prefs.save_strict({"maxisProfiles": stored, "maxisProbeOkByProfile": verified,
                       "maxisLastProbeByProfile": last_probe})
    before = deepcopy(prefs.load())
    monkeypatch.setattr(credentials, "get_for", lambda *_args, **_kwargs: pytest.fail(
        "Saving profile metadata must not read credentials."))

    saved = profiles.save("dev", {"workflow_bundle_version": "updated-release"})

    after = prefs.load()
    assert [row["name"] for row in after["maxisProfiles"]] == (
        ["dev", "prod"] if target_exists else ["prod", "dev"]
    )
    assert next(row for row in after["maxisProfiles"] if row["name"] == "prod") == sibling
    assert saved["workflow_bundle_version"] == "updated-release"
    assert after["maxisProbeOkByProfile"] == {key: value for key, value in verified.items() if key != "dev"}
    assert after["maxisLastProbeByProfile"] == {key: value for key, value in last_probe.items() if key != "dev"}
    changed_keys = {"maxisProfiles", "maxisProbeOkByProfile", "maxisLastProbeByProfile"}
    assert {key: value for key, value in after.items() if key not in changed_keys} == {
        key: value for key, value in before.items() if key not in changed_keys
    }


def test_profile_save_creates_only_the_requested_profile():
    from app.infra import prefs

    profiles.save("dev", {"workflow_bundle_version": "first-release"})

    assert [row["name"] for row in prefs.load()["maxisProfiles"]] == ["dev"]
    assert profiles.get("prod")["name"] == "prod"
    assert profiles.get("internal-pilot")["name"] == "internal-pilot"


def test_profile_save_preserves_unrecognized_stored_rows():
    from app.infra import prefs

    siblings = [{"name": "future-profile", "extension": {"note": " keep "}},
                "legacy-row", None]
    prefs.save_strict({"maxisProfiles": siblings})

    profiles.save("dev", {"workflow_bundle_version": "first-release"})

    stored = prefs.load()["maxisProfiles"]
    assert stored[:-1] == siblings
    assert stored[-1]["name"] == "dev"


@pytest.mark.parametrize("stored", [7, True, "legacy-value"])
def test_profile_save_recovers_a_noncontainer_profile_store(stored):
    from app.infra import prefs

    prefs.save_strict({"maxisProfiles": stored})

    profiles.save("dev", {"workflow_bundle_version": "first-release"})

    assert [row["name"] for row in prefs.load()["maxisProfiles"]] == ["dev"]


def test_profile_save_propagates_persistence_failure_without_changing_state(monkeypatch):
    from app.infra import prefs

    prefs.save_strict({"maxisProfiles": [profiles.get("dev")],
                       "maxisProbeOkByProfile": {"dev": {"fingerprint": "keep"}}})
    before = deepcopy(prefs.load())
    failure = OSError("test persistence failure")

    def fail_save(_patch):
        raise failure

    monkeypatch.setattr(prefs, "save_strict", fail_save)
    with pytest.raises(OSError) as caught:
        profiles.save("dev", {"workflow_bundle_version": "updated-release"})

    assert caught.value is failure
    assert prefs.load() == before


def test_invalid_profile_save_preserves_stored_profiles_and_probe_records():
    from app.infra import prefs

    prefs.save_strict({"maxisProfiles": [profiles.get("dev"), profiles.get("prod")],
                       "maxisProbeOkByProfile": {"dev": {"fingerprint": "keep"}},
                       "maxisLastProbeByProfile": {"dev": {"result": "keep"}}})
    before = deepcopy(prefs.load())

    with pytest.raises(ValueError, match="dataPolicy"):
        profiles.save("dev", {"data_policy": "internal_read_only"})

    assert prefs.load() == before


def test_profile_save_merges_the_target_snapshot_after_acquiring_the_lock(monkeypatch):
    from app.infra import prefs

    prefs.save_strict({"maxisProfiles": [profiles.get("dev")]})

    @contextmanager
    def acquired_after_another_save():
        state = prefs.load()
        state["maxisProfiles"][0]["workflow_bundle_version"] = "concurrent-release"
        prefs.save_strict({"maxisProfiles": state["maxisProfiles"],
                           "maxisLastProbeByProfile": {"prod": {"result": "concurrent"}}})
        yield

    monkeypatch.setattr(profiles, "_LOCK", acquired_after_another_save())

    saved = profiles.save("dev", {"knowledge_version": KNOWLEDGE_VERSION})

    assert saved["workflow_bundle_version"] == "concurrent-release"
    assert saved["knowledge_version"] == KNOWLEDGE_VERSION
    assert prefs.load()["maxisLastProbeByProfile"] == {"prod": {"result": "concurrent"}}


def test_profile_url_policy_allows_only_loopback_http_in_dev():
    profiles.save("dev", {"base_url": "http://127.0.0.1:5001"})
    assert profiles.get("dev")["base_url"] == "http://127.0.0.1:5001/v1"
    with pytest.raises(ValueError, match="HTTPS"):
        profiles.save("dev", {"base_url": "http://dev.maxis.example/v1"})
    with pytest.raises(ValueError, match="prod"):
        profiles.save("prod", {"base_url": "https://localhost/v1"})


@pytest.mark.parametrize("url", [
    "https://evil.example/v1",
    "https://169.254.169.254/v1",
    "https://127.1/v1",
])
@pytest.mark.parametrize("stage", ["dev", "prod"])
def test_profile_save_rejects_non_allowlisted_or_ip_destinations(stage, url):
    with pytest.raises(ValueError):
        profiles.save(stage, {"base_url": url})


@pytest.mark.parametrize("stage", ["dev", "prod"])
def test_profile_save_rejects_nondefault_remote_https_port(stage):
    with pytest.raises(ValueError, match="port 443"):
        profiles.save(stage, {
            "base_url": "https://dify.maxis.skax.co.kr:8443/v1",
        })


def test_default_allowlist_accepts_current_maxis_api_host(monkeypatch):
    monkeypatch.delenv("LAKE_MAXIS_DEV_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("LAKE_MAXIS_PROD_ALLOWED_HOSTS", raising=False)

    profiles.save("dev", {"base_url": "https://dify.maxis.skax.co.kr/v1"})
    profiles.save("prod", {"base_url": "https://dify.maxis.skax.co.kr/v1"})

    assert profiles.get("dev")["base_url"] == "https://dify.maxis.skax.co.kr/v1"
    assert profiles.get("prod")["base_url"] == "https://dify.maxis.skax.co.kr/v1"


def test_profile_allowlist_environment_replaces_default_and_invalid_values_fail_closed(
    monkeypatch,
):
    monkeypatch.setenv("LAKE_MAXIS_DEV_ALLOWED_HOSTS", "new-dev.maxis.example")
    profiles.save("dev", {"base_url": "https://new-dev.maxis.example/v1"})
    with pytest.raises(ValueError, match="allowlist"):
        profiles.save("dev", {"base_url": "https://dify.maxis.skax.co.kr/v1"})

    monkeypatch.setenv("LAKE_MAXIS_DEV_ALLOWED_HOSTS", "*.maxis.example")
    with pytest.raises(ValueError, match="ASCII FQDN"):
        profiles.get("dev")


def test_invalid_dev_allowlist_disables_loopback_stub_too(monkeypatch):
    monkeypatch.setenv("LAKE_MAXIS_DEV_ALLOWED_HOSTS", "*.maxis.example")

    with pytest.raises(ValueError, match="ASCII FQDN"):
        profiles.save("dev", {"base_url": "http://127.0.0.1:5001/v1"})


@pytest.mark.parametrize("value", ["release with spaces", "app-secret/value", "line\nbreak"])
def test_profile_rejects_non_wire_safe_version_values(value):
    with pytest.raises(ValueError, match="Invalid workflow bundle version"):
        profiles.save("dev", {"workflow_bundle_version": value})


@pytest.mark.parametrize("value", [
    "app-0123456789abcdefghijklmnop",
    "Bearer abcdefghijklmnopqrstuvwxyz",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlMTIzNDU",
])
def test_profile_rejects_credential_shaped_public_metadata_without_echo(value):
    with pytest.raises(ValueError, match="credential-shaped") as caught:
        profiles.save("dev", {"workflow_bundle_version": value})

    assert value not in str(caught.value)


@pytest.mark.parametrize("value", ["1.2.0", "wf-1", "{" + AGENT_WORKFLOW_ID + "}"])
def test_profile_rejects_non_uuid_published_workflow_versions(value):
    with pytest.raises(ValueError, match="canonical UUID"):
        profiles.save("dev", {"workflows": {
            "agent_core": {"binding": "agent_core", "version": value},
        }})


def test_profile_canonicalizes_uppercase_published_workflow_uuid():
    saved = profiles.save("dev", {"workflows": {
        "agent_core": {
            "binding": "agent_core", "version": MIXED_CASE_WORKFLOW_ID,
        },
    }})

    assert saved["workflows"]["agent_core"]["version"] == (
        MIXED_CASE_WORKFLOW_ID.lower()
    )


def test_profile_requires_canonical_dataset_uuid_for_activation():
    with pytest.raises(ValueError, match="dataset UUID"):
        profiles.save("dev", {"knowledge_dataset_id": "not-a-uuid"})

    profiles.save("dev", {
        "base_url": "https://dev.maxis.example/v1",
        "workflow_bundle_version": "bundle-1",
        "knowledge_version": "knowledge-1",
        "workflows": {"agent_core": {
            "binding": "agent_core", "version": AGENT_WORKFLOW_ID,
        }},
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
    stored = (tmp_path / "maxis" / "credentials.dpapi").read_bytes()
    assert secret.encode() not in stored
    assert result["agent_core"] == {
        "configured": True, "source": "dpapi", "environmentVariable": "", "storeError": ""
    }
    assert secret not in repr(result)
    assert credentials.get_for("dev", "agent_core") == secret


def test_credential_store_reads_legacy_path_but_all_mutations_write_new_path(tmp_path):
    legacy = tmp_path / "maxis_credentials.dpapi"
    payload = {
        "dev": {"agent_core": "legacy-agent-secret"},
        "prod": {"pmo_brief": "legacy-pmo-secret"},
    }
    protected = _TestCipher().protect(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))
    legacy.write_bytes(credentials._MAGIC + protected)

    assert credentials.get_for("dev", "agent_core") == "legacy-agent-secret"
    assert not (tmp_path / "maxis" / "credentials.dpapi").exists()

    credentials.save_for("dev", {"editor_author": "new-editor-secret"})

    new_store = tmp_path / "maxis" / "credentials.dpapi"
    assert new_store.exists()
    assert legacy.exists()  # migration is intentionally non-destructive
    assert credentials.get_for("dev", "agent_core") == "legacy-agent-secret"
    assert credentials.get_for("dev", "editor_author") == "new-editor-secret"
    assert credentials.get_for("prod", "pmo_brief") == "legacy-pmo-secret"
    rendered = repr(credentials.status_for("dev"))
    assert "legacy-agent-secret" not in rendered
    assert "new-editor-secret" not in rendered


def test_clearing_migrated_credentials_does_not_resurrect_legacy_values(tmp_path):
    legacy = tmp_path / "maxis_credentials.dpapi"
    payload = {"dev": {"agent_core": "legacy-agent-secret"}}
    protected = _TestCipher().protect(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))
    legacy.write_bytes(credentials._MAGIC + protected)

    credentials.save_for("dev", clear=["agent_core"])

    assert legacy.exists()
    new_store = tmp_path / "maxis" / "credentials.dpapi"
    assert new_store.exists()
    assert credentials.get_for("dev", "agent_core") == ""

    # Deleting the authoritative new file is interpreted as empty/revoked.  The
    # non-secret migration tombstone prevents stale legacy material from reviving.
    new_store.unlink()
    assert (tmp_path / "maxis" / "legacy-store-disabled").exists()
    assert credentials.get_for("dev", "agent_core") == ""


def test_non_windows_persistent_store_fails_closed(monkeypatch):
    credentials.set_backend_for_tests(None)
    def unavailable():
        raise credentials.CredentialStoreUnavailable(
            "MAXIS credential persistence requires Windows current-user DPAPI."
        )
    monkeypatch.setattr(credentials, "WindowsDpapiBackend", unavailable)
    with pytest.raises(credentials.CredentialStoreUnavailable, match="DPAPI"):
        credentials.save_for("dev", {"agent_core": "never-plaintext"})


@pytest.mark.skipif(os.name != "nt", reason="Windows current-user DPAPI only")
def test_windows_dpapi_backend_round_trips_without_plaintext():
    import ctypes

    backend = credentials.WindowsDpapiBackend()
    plaintext = b"ltm-maxis-native-dpapi-round-trip"

    try:
        protected = backend.protect(plaintext)
    except credentials.CredentialStoreUnavailable:
        # Windows CI/service identities may have no loaded user master-key profile.
        # The desktop process runs under the interactive user; retain this as a native
        # smoke test wherever DPAPI is available without masking other native-call
        # defects (for example ERROR_INVALID_PARAMETER from a wrong ctypes ABI).
        if ctypes.get_last_error() in {2, 1312}:
            pytest.skip("Windows user DPAPI master-key profile is unavailable")
        raise

    assert protected != plaintext
    assert plaintext not in protected
    assert backend.unprotect(protected) == plaintext


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


def test_environment_api_key_is_bound_to_explicit_environment_profile(monkeypatch):
    credentials.save_for("dev", {"agent_core": "stored-dev-secret"})
    credentials.save_for("prod", {"agent_core": "stored-prod-secret"})
    monkeypatch.setenv("LAKE_MAXIS_AGENT_CORE_API_KEY", "environment-prod-secret")
    monkeypatch.setenv("LAKE_MAXIS_PROFILE", "prod")

    assert credentials.get_for("prod", "agent_core") == "environment-prod-secret"
    with pytest.raises(credentials.CredentialStoreUnavailable, match="PROFILE=dev"):
        credentials.get_for("dev", "agent_core")
    assert credentials.status_for("prod")["agent_core"]["source"] == "environment"
    assert credentials.status_for("dev")["agent_core"]["source"] == "blocked"
    assert credentials.status_for("dev")["agent_core"]["configured"] is False
    assert credentials.environment_overrides("dev") == {}
    assert credentials.environment_overrides("prod") == {
        "agent_core": "LAKE_MAXIS_AGENT_CORE_API_KEY",
    }


def test_unbound_environment_api_key_is_inventory_only_and_never_used(monkeypatch):
    credentials.save_for("dev", {"agent_core": "stale-stored-secret"})
    monkeypatch.setenv("LAKE_MAXIS_AGENT_CORE_API_KEY", "unbound-secret")

    assert credentials.environment_overrides() == {
        "agent_core": "LAKE_MAXIS_AGENT_CORE_API_KEY",
    }
    with pytest.raises(credentials.CredentialStoreUnavailable, match="PROFILE=dev"):
        credentials.get_for("dev", "agent_core")
    with pytest.raises(credentials.CredentialStoreUnavailable, match="PROFILE=prod"):
        credentials.get_for("prod", "agent_core")
    assert credentials.status_for("dev")["agent_core"]["configured"] is False
    assert credentials.status_for("dev")["agent_core"]["source"] == "blocked"
    assert "unbound-secret" not in repr(credentials.status_for("dev"))
    assert "stale-stored-secret" not in repr(credentials.status_for("dev"))
    assert "require LAKE_MAXIS_PROFILE" in profiles.status()["environmentError"]


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
    monkeypatch.setenv("LAKE_MAXIS_PROFILE", "dev")

    overrides = {
        "LAKE_MAXIS_WORKFLOW_BUNDLE_VERSION": "bundle-from-deployment",
        "LAKE_MAXIS_KNOWLEDGE_VERSION": KNOWLEDGE_VERSION,
        "LAKE_MAXIS_KNOWLEDGE_DATASET_ID": DATASET_B,
        "LAKE_MAXIS_KNOWLEDGE_SENTINEL_SHA256": SENTINEL_B,
        "LAKE_MAXIS_AGENT_CORE_VERSION": DEPLOY_AGENT_WORKFLOW_ID,
        "LAKE_MAXIS_EDITOR_AUTHOR_VERSION": DEPLOY_EDITOR_WORKFLOW_ID,
        "LAKE_MAXIS_PMO_BRIEF_VERSION": DEPLOY_PMO_WORKFLOW_ID,
    }
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)

    effective = profiles.resolve("dev")
    assert effective["workflow_bundle_version"] == "bundle-from-deployment"
    assert effective["knowledge_version"] == KNOWLEDGE_VERSION
    assert effective["knowledge_dataset_id"] == DATASET_B
    assert effective["knowledge_sentinel_sha256"] == SENTINEL_B
    assert len(effective["knowledge_manifest_sha256"]) == 64
    assert effective["workflows"]["agent_core"]["version"] == DEPLOY_AGENT_WORKFLOW_ID
    assert effective["workflows"]["editor_author"]["version"] == DEPLOY_EDITOR_WORKFLOW_ID
    assert effective["workflows"]["pmo_brief"]["version"] == DEPLOY_PMO_WORKFLOW_ID
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


def test_deployment_workflow_override_requires_published_workflow_uuid(monkeypatch):
    _configured_dev()
    monkeypatch.setenv("LAKE_MAXIS_PROFILE", "dev")
    monkeypatch.setenv("LAKE_MAXIS_AGENT_CORE_VERSION", "legacy-version-1")

    with pytest.raises(ValueError, match="canonical UUID"):
        profiles.resolve("dev")


def test_shared_deployment_overrides_apply_only_to_selected_profile(monkeypatch):
    _configured_dev()
    profiles.save("prod", {
        "base_url": "https://prod.maxis.example/v1",
        "workflow_bundle_version": "stored-prod-bundle",
    })
    monkeypatch.setenv("LAKE_MAXIS_PROFILE", "prod")
    monkeypatch.setenv("LAKE_MAXIS_BASE_URL", "https://maxis.example/v1")
    monkeypatch.setenv("LAKE_MAXIS_WORKFLOW_BUNDLE_VERSION", "selected-prod-bundle")

    dev = profiles.resolve("dev")
    prod = profiles.resolve("prod")

    assert dev["base_url"] == "https://dev.maxis.example/v1"
    assert dev["workflow_bundle_version"] == "ltm-agent-1"
    assert prod["base_url"] == "https://maxis.example/v1"
    assert prod["workflow_bundle_version"] == "selected-prod-bundle"


@pytest.mark.parametrize(
    ("category", "expected_error"),
    [
        ("dataset", "Knowledge dataset"),
        ("sentinel", "Knowledge release sentinel"),
        ("workflow", "published workflow"),
        ("credential", "API credential"),
    ],
)
def test_prod_overlapping_assets_can_be_saved_but_not_probed_activated_or_resolved(
    category,
    expected_error,
):
    _configured_dev()
    _configured_prod()

    if category == "dataset":
        profiles.save("prod", {"knowledge_dataset_id": DATASET_A})
    elif category == "sentinel":
        profiles.save("prod", {"knowledge_sentinel_sha256": SENTINEL_A})
    elif category == "workflow":
        # Workflow UUIDs are environment assets even when bound to different roles.
        profiles.save("prod", {"workflows": {
            "editor_author": {
                "binding": "editor_author", "version": AGENT_WORKFLOW_ID,
            },
        }})
    else:
        # Credentials likewise cannot be reused across different workflow bindings.
        credentials.save_for("prod", {"pmo_brief": "app-secret-1111"})
        profiles.credential_changed("prod")

    # Candidate persistence is deliberately recoverable while the operator repairs it.
    assert profiles.get("prod")["name"] == "prod"
    for action in (
        lambda: profiles.resolve("prod"),
        lambda: profiles.probe_candidate("prod"),
        lambda: profiles.activate("prod"),
    ):
        with pytest.raises(ValueError, match=expected_error):
            action()

    public = profiles.status()["profiles"][1]
    assert expected_error in public["configError"]
    assert public["verified"] is False
    assert public["enabled"] is False
    assert "app-secret-1111" not in repr(public)


def test_prod_environment_key_overlap_is_blocked_without_secret_or_digest_disclosure(
    monkeypatch,
):
    secret = "app-sensitive-cross-environment-secret"
    _configured_dev()
    credentials.save_for("dev", {"agent_core": secret})
    profiles.credential_changed("dev")
    _configured_prod()
    monkeypatch.setenv("LAKE_MAXIS_PROFILE", "prod")
    monkeypatch.setenv("LAKE_MAXIS_AGENT_CORE_API_KEY", secret)

    with pytest.raises(ValueError, match="API credential") as error:
        profiles.resolve("prod")

    rendered = str(error.value)
    assert secret not in rendered
    assert hashlib.sha256(secret.encode("utf-8")).hexdigest() not in rendered


@pytest.mark.parametrize(
    ("category", "expected_error"),
    [
        ("dataset", "Knowledge dataset"),
        ("sentinel", "Knowledge release sentinel"),
        ("workflow", "published workflow"),
        ("credential", "API credential"),
    ],
)
def test_dev_overlapping_assets_can_be_saved_but_fail_every_runtime_boundary(
    category,
    expected_error,
):
    _configured_prod()
    _configured_dev()

    if category == "dataset":
        profiles.save("dev", {"knowledge_dataset_id": DATASET_B})
    elif category == "sentinel":
        profiles.save("dev", {"knowledge_sentinel_sha256": SENTINEL_B})
    elif category == "workflow":
        profiles.save("dev", {"workflows": {
            "agent_core": {
                "binding": "agent_core", "version": DEPLOY_PMO_WORKFLOW_ID,
            },
        }})
    else:
        credentials.save_for("dev", {
            "agent_core": "app-prod-agent-secret",
        })
        profiles.credential_changed("dev")

    # Persistence remains a recovery surface; all runtime/network boundaries close.
    assert profiles.get("dev")["name"] == "dev"
    for action in (
        lambda: profiles.resolve("dev"),
        lambda: profiles.probe_candidate("dev"),
        lambda: profiles.start_probe("dev"),
        lambda: profiles.activate("dev"),
    ):
        with pytest.raises(ValueError, match=expected_error):
            action()

    public = profiles.status()["profiles"][0]
    assert expected_error in public["configError"]
    assert public["verified"] is False
    assert public["enabled"] is False
    assert "app-prod-agent-secret" not in repr(public)


def test_dev_resolution_ignores_invalid_prod_destination_during_repair(monkeypatch):
    _configured_dev()
    _configured_prod()
    monkeypatch.setenv(
        "LAKE_MAXIS_PROD_ALLOWED_HOSTS", "rotated-prod.maxis.example",
    )

    assert profiles.resolve("dev")["name"] == "dev"
    assert profiles.probe_candidate("dev")["name"] == "dev"


def test_dev_separation_does_not_recursively_validate_malformed_prod_sibling():
    from app.infra import prefs

    _configured_dev()
    dev = profiles.get("dev")
    prefs.save_strict({"maxisProfiles": [dev, {
        "name": "prod",
        "base_url": "not-a-url",
        "data_policy": "not-a-policy",
        "knowledge_dataset_id": "not-a-dataset",
        "knowledge_sentinel_sha256": "not-a-digest",
        "workflows": {
            "agent_core": {
                "binding": "wrong-binding", "version": "not-a-workflow-id",
            },
        },
    }]})

    assert profiles.resolve("dev")["name"] == "dev"
    assert profiles.probe_candidate("dev")["name"] == "dev"


def test_dev_environment_key_overlap_is_blocked_without_secret_disclosure(monkeypatch):
    secret = "app-sensitive-prod-secret-for-dev-overlap"
    _configured_prod()
    credentials.save_for("prod", {"agent_core": secret})
    profiles.credential_changed("prod")
    _configured_dev()
    monkeypatch.setenv("LAKE_MAXIS_PROFILE", "dev")
    monkeypatch.setenv("LAKE_MAXIS_AGENT_CORE_API_KEY", secret)

    with pytest.raises(ValueError, match="API credential") as error:
        profiles.resolve("dev")

    assert secret not in str(error.value)


@pytest.mark.parametrize("dev_patch", [None, {"knowledge_dataset_id": DATASET_A}])
def test_prod_separation_allows_unconfigured_or_partial_nonoverlapping_dev(dev_patch):
    if dev_patch:
        profiles.save("dev", dev_patch)
    _configured_prod()

    assert profiles.resolve("prod")["name"] == "prod"
    assert profiles.probe_candidate("prod")["name"] == "prod"


def test_prod_separation_does_not_compare_host_or_release_version(monkeypatch):
    monkeypatch.setenv(
        "LAKE_MAXIS_DEV_ALLOWED_HOSTS",
        "dev.maxis.example,shared.maxis.example",
    )
    monkeypatch.setenv(
        "LAKE_MAXIS_PROD_ALLOWED_HOSTS",
        "prod.maxis.example,shared.maxis.example",
    )
    _configured_dev()
    profiles.save("dev", {"base_url": "https://shared.maxis.example/v1"})
    _configured_prod(base_url="https://shared.maxis.example/v1")
    dev = profiles.get("dev")

    # A later dev allowlist rotation is unrelated to prod asset identity.
    monkeypatch.setenv("LAKE_MAXIS_DEV_ALLOWED_HOSTS", "rotated-dev.maxis.example")

    resolved = profiles.resolve("prod")
    assert resolved["base_url"] == "https://shared.maxis.example/v1"
    assert resolved["workflow_bundle_version"] == dev["workflow_bundle_version"]
    assert resolved["knowledge_version"] == dev["knowledge_version"]


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
    _, keys, start_binding = profiles.start_probe("dev")
    keys.clear()
    profiles.mark_probe("dev", True, {
        "workflows": {"agent_core": {
            "ok": True,
            "statusCode": 200,
            "missingParameters": [],
        }},
    }, start_binding=start_binding)

    assert profiles.probe_ok("dev") is False
    assert profiles.status()["profiles"][0]["lastProbe"]["ok"] is False
    with pytest.raises(ValueError, match="probe"):
        profiles.activate("dev")


def test_probe_without_exact_app_mode_attestation_cannot_mark_profile_verified():
    _configured_dev()
    manifest = profiles.resolve_knowledge_manifest("dev", required=True)
    assert manifest is not None
    _, keys, start_binding = profiles.start_probe("dev")
    keys.clear()

    profiles.mark_probe("dev", True, {"workflows": {"agent_core": {
        "ok": True,
        "executionContractVerified": True,
        "versionBindingVerified": True,
        "citationContractVerified": True,
        "knowledgeRetrievalVerified": True,
        "knowledgeManifestSha256": manifest.sha256,
    }}}, start_binding=start_binding)

    assert profiles.probe_ok("dev") is False
    assert profiles.status()["profiles"][0]["lastProbe"]["ok"] is False


def test_successful_probe_without_start_binding_fails_closed():
    _configured_dev()
    manifest = profiles.resolve_knowledge_manifest("dev", required=True)
    assert manifest is not None

    verified = profiles.mark_probe("dev", True, {"workflows": {"agent_core": {
        "ok": True,
        "appModeVerified": True,
        "executionContractVerified": True,
        "versionBindingVerified": True,
        "citationContractVerified": True,
        "knowledgeRetrievalVerified": True,
        "knowledgeManifestSha256": manifest.sha256,
    }}})

    assert verified is False
    assert profiles.probe_ok("dev") is False
    last_probe = profiles.status()["profiles"][0]["lastProbe"]
    assert last_probe["workflows"] == {}
    assert "stale or missing" in last_probe["error"]


@pytest.mark.parametrize("mutation", ["profile", "credential"])
def test_stale_probe_completion_cannot_verify_changed_material(mutation):
    _configured_dev()
    profile, keys, start_binding = profiles.start_probe("dev")
    keys.clear()
    manifest_sha256 = profile["knowledge_manifest_sha256"]
    if mutation == "profile":
        # This field does not change the Knowledge manifest, so the explicit start
        # binding is what prevents stale success from blessing the new profile.
        profiles.save("dev", {"workflow_bundle_version": "ltm-agent-2"})
    else:
        credentials.save_for("dev", {"agent_core": "rotated-secret"})
        profiles.credential_changed("dev")

    verified = profiles.mark_probe(
        "dev",
        True,
        {"workflows": {"agent_core": {
            "ok": True,
            "appModeVerified": True,
            "executionContractVerified": True,
            "versionBindingVerified": True,
            "citationContractVerified": True,
            "knowledgeRetrievalVerified": True,
            "knowledgeManifestSha256": manifest_sha256,
        }}},
        start_binding=start_binding,
    )

    assert verified is False
    assert profiles.probe_ok("dev") is False
    last_probe = profiles.status()["profiles"][0]["lastProbe"]
    assert last_probe["ok"] is False
    assert last_probe["workflows"] == {}
    assert "stale" in last_probe["error"]


def test_legacy_probe_signature_cannot_bypass_new_app_mode_gate():
    from app.infra import prefs

    _configured_dev()
    effective = profiles._effective(profiles.get("dev"))
    credential_digest = credentials.verification_digest("dev")
    legacy_material = (
        f"{profiles.profile_fingerprint(effective)}|{credential_digest}"
    )
    legacy_signature = hashlib.sha256(legacy_material.encode("utf-8")).hexdigest()
    prefs.save_strict({
        "maxisProbeOkByProfile": {"dev": legacy_signature},
        "maxisLastProbeByProfile": {"dev": {
            "ok": True,
            "at": datetime.now(timezone.utc).isoformat(),
        }},
    })

    assert profiles.probe_ok("dev") is False


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
    monkeypatch.setenv("LAKE_MAXIS_PROFILE", "dev")
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
            "binding": "editor_author", "version": EDITOR_WORKFLOW_ID,
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
