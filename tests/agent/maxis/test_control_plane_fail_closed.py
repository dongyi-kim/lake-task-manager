from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app.agent.maxis import credentials, profiles
from app.infra import prefs


DATASET_ID = "11111111-1111-4111-8111-111111111111"
SENTINEL_SHA = hashlib.sha256(b"release-sentinel").hexdigest()
KNOWLEDGE_VERSION = "ltm-public-knowledge.v1"
WORKFLOW_ID = "22222222-2222-4222-8222-222222222222"


class _TestCipher:
    def protect(self, value: bytes) -> bytes:
        return b"cipher:" + value[::-1]

    def unprotect(self, value: bytes) -> bytes:
        assert value.startswith(b"cipher:")
        return value[len(b"cipher:"):][::-1]


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    import app.infra.settings as settings

    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    for name in (
        "LAKE_MAXIS_PROFILE",
        *profiles.CONFIG_ENV_NAMES.values(),
        *profiles.WORKFLOW_VERSION_ENV_NAMES.values(),
        *profiles.ALLOWED_HOSTS_ENV_NAMES.values(),
        *credentials.ENV_NAMES.values(),
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LAKE_MAXIS_PROD_ALLOWED_HOSTS", "prod.maxis.example")
    credentials.set_backend_for_tests(_TestCipher())
    yield
    credentials.set_backend_for_tests(None)


def _save_prod(*, with_log_attestation: bool) -> None:
    patch = {
        "base_url": "https://prod.maxis.example/v1",
        "workflow_bundle_version": "ltm-agent-1",
        "knowledge_version": KNOWLEDGE_VERSION,
        "knowledge_dataset_id": DATASET_ID,
        "knowledge_sentinel_sha256": SENTINEL_SHA,
        "workflows": {
            "agent_core": {"binding": "agent_core", "version": WORKFLOW_ID},
        },
    }
    if with_log_attestation:
        patch.update({
            "remote_log_policy_id": "SEC-2026-091",
            "remote_log_mode": "redacted",
            "remote_log_retention_days": 30,
        })
    profiles.save("prod", patch)
    credentials.save_for("prod", {"agent_core": "app-prod-secret"})
    profiles.credential_changed("prod")


def _mark_prod_verified() -> None:
    manifest = profiles.resolve_knowledge_manifest("prod", required=True)
    assert manifest is not None
    _, keys, start_binding = profiles.start_probe("prod")
    keys.clear()
    assert profiles.mark_probe("prod", True, {"workflows": {
        "agent_core": {
            "ok": True,
            "appModeVerified": True,
            "executionContractVerified": True,
            "versionBindingVerified": True,
            "citationContractVerified": True,
            "knowledgeRetrievalVerified": True,
            "knowledgeManifestSha256": manifest.sha256,
        },
    }}, start_binding=start_binding)


def test_strict_save_raises_and_preserves_previous_control_value(monkeypatch):
    prefs.save_strict({"maxisKillSwitch": False})

    def fail_replace(_source, _target):
        raise OSError("disk unavailable")

    monkeypatch.setattr(prefs.os, "replace", fail_replace)
    with pytest.raises(prefs.PrefsPersistenceError, match="persisted safely"):
        profiles.set_kill_switch(True)

    assert prefs.load()["maxisKillSwitch"] is False


def test_prod_probe_requires_explicit_remote_log_attestation():
    _save_prod(with_log_attestation=False)

    with pytest.raises(ValueError, match="manager-attested remote log policy"):
        profiles.probe_candidate("prod")
    assert profiles.mark_probe("prod", True, {"workflows": {}}) is False


def test_remote_log_attestation_is_public_fingerprinted_metadata():
    _save_prod(with_log_attestation=True)
    row = profiles.resolve("prod")
    public = profiles.status()["profiles"][1]

    assert row["remote_log_policy_id"] == "SEC-2026-091"
    assert row["remote_log_mode"] == "redacted"
    assert row["remote_log_retention_days"] == 30
    assert public["remoteLogPolicyId"] == "SEC-2026-091"
    assert public["remoteLogMode"] == "redacted"
    assert public["remoteLogRetentionDays"] == 30

    before = profiles.profile_fingerprint(row)
    profiles.save("prod", {"remote_log_retention_days": 7})
    assert profiles.profile_fingerprint(profiles.resolve("prod")) != before


def test_remote_log_environment_override_invalidates_probe_and_active_fingerprint(monkeypatch):
    _save_prod(with_log_attestation=True)
    _mark_prod_verified()
    before = profiles.activate("prod")["fingerprint"]

    monkeypatch.setenv("LAKE_MAXIS_PROFILE", "prod")
    monkeypatch.setenv("LAKE_MAXIS_REMOTE_LOG_POLICY_ID", "SEC-2026-ROTATED")
    resolved = profiles.resolve("prod")
    public = profiles.status()["profiles"][1]

    assert resolved["fingerprint"] != before
    assert resolved["verified"] is False
    assert resolved["enabled"] is False
    assert public["effectiveRemoteLogPolicyId"] == "SEC-2026-ROTATED"
    assert profiles.status()["environmentOverrides"]["remoteLogPolicyId"] == (
        "LAKE_MAXIS_REMOTE_LOG_POLICY_ID"
    )


@pytest.mark.parametrize("timestamp", [
    (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
    (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    "not-a-timestamp",
    "",
])
def test_probe_evidence_expiry_and_invalid_time_disable_active_prod(timestamp):
    _save_prod(with_log_attestation=True)
    _mark_prod_verified()
    assert profiles.activate("prod")["enabled"] is True

    state = prefs.load()
    probes = dict(state["maxisLastProbeByProfile"])
    probes["prod"] = {**probes["prod"], "at": timestamp}
    prefs.save_strict({"maxisLastProbeByProfile": probes})

    assert profiles.probe_ok("prod") is False
    assert profiles.resolve("prod")["enabled"] is False


def test_dev_remote_log_policy_is_fixed_to_synthetic_redacted_zero_day():
    row = profiles.get("dev")
    assert row["data_policy"] == "synthetic_only"
    assert row["remote_log_policy_id"] == profiles.DEV_REMOTE_LOG_POLICY_ID
    assert row["remote_log_mode"] == "redacted"
    assert row["remote_log_retention_days"] == 0

    with pytest.raises(ValueError, match="fixed redacted/0-day"):
        profiles.save("dev", {"remote_log_retention_days": 1})


def test_prod_remote_log_environment_does_not_override_fixed_dev_policy(monkeypatch):
    monkeypatch.setenv("LAKE_MAXIS_REMOTE_LOG_POLICY_ID", "SEC-PROD")
    monkeypatch.setenv("LAKE_MAXIS_REMOTE_LOG_MODE", "approved_bounded_content")
    monkeypatch.setenv("LAKE_MAXIS_REMOTE_LOG_RETENTION_DAYS", "30")

    row = profiles.resolve("dev")

    assert row["remote_log_policy_id"] == profiles.DEV_REMOTE_LOG_POLICY_ID
    assert row["remote_log_mode"] == "redacted"
    assert row["remote_log_retention_days"] == 0


@pytest.mark.parametrize("value", [-1, 91, True, "1.5"])
def test_remote_log_retention_rejects_unbounded_or_ambiguous_values(value):
    with pytest.raises(ValueError, match="retention days"):
        profiles.save("prod", {"remote_log_retention_days": value})
