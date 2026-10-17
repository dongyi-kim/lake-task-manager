from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.agent.maxis import credentials, pilot_policy, profiles
from app.agent.maxis.destination_policy import canonical_maxis_api_base


class _Cipher:
    def protect(self, value):
        return value[::-1]

    def unprotect(self, value):
        return value[::-1]


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    from app.infra import settings

    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    for key in ("LAKE_MAXIS_PROFILE", *profiles.CONFIG_ENV_NAMES.values(),
                *profiles.WORKFLOW_VERSION_ENV_NAMES.values(),
                *profiles.ALLOWED_HOSTS_ENV_NAMES.values(), *credentials.ENV_NAMES.values()):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LAKE_MAXIS_INTERNAL_PILOT_ALLOWED_HOSTS", "pilot.maxis.example")
    credentials.set_backend_for_tests(_Cipher())
    yield
    credentials.set_backend_for_tests(None)


def _configured_pilot():
    profiles.save("internal-pilot", {
        "base_url": "https://pilot.maxis.example/v1",
        "workflow_bundle_version": "pilot-v1",
        "knowledge_version": "ltm-knowledge-2026.09",
        "knowledge_dataset_id": "11111111-1111-4111-8111-111111111111",
        "knowledge_sentinel_sha256": "b" * 64,
        "remote_log_policy_id": "SEC-PILOT",
        "remote_log_mode": "approved_bounded_content",
        "remote_log_retention_days": 1,
        "workflows": {key: {"version": identifier} for key, identifier in zip(
            profiles.WORKFLOW_KEYS,
            ["22222222-2222-4222-8222-222222222222",
             "33333333-3333-4333-8333-333333333333",
             "44444444-4444-4444-8444-444444444444"],
        )},
    })
    credentials.save_for("internal-pilot", {"agent_core": "pilot-secret"})
    return profiles.get("internal-pilot")


def _attested_pilot():
    candidate = _configured_pilot()
    policy = candidate["internal_pilot_policy"]
    policy["audience_scope_sha256"] = "a" * 64
    profiles.save("internal-pilot", {"internal_pilot_policy": policy})
    binding = profiles.knowledge_eval_attestation_binding("internal-pilot")
    now = datetime.now(timezone.utc)
    policy["controls_attestation"] = {
        "contract": pilot_policy.ATTESTATION_CONTRACT,
        "evidence_id": "PILOT-CONTROLS-1",
        "artifact_sha256": "d" * 64,
        "profile_material_sha256": binding["profile_material_sha256"],
        "verified_at": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
    }
    profiles.save("internal-pilot", {"internal_pilot_policy": policy})
    return policy


def test_pilot_is_separate_and_dev_policy_stays_synthetic():
    pilot = profiles.get("internal-pilot")
    assert pilot["stage"] == "internal-pilot"
    assert pilot["data_policy"] == "internal_read_only"
    assert pilot["internal_pilot_policy"]["content_retention_hours"] == 24
    assert pilot["internal_pilot_policy"]["metadata_retention_days"] == 7
    assert profiles.get("dev")["data_policy"] == "synthetic_only"
    for name, patch in (("dev", {"data_policy": "internal_read_only"}),
                        ("internal-pilot", {"data_policy": "synthetic_only"}),
                        ("prod", {"internal_pilot_policy": pilot_policy.default_policy()})):
        with pytest.raises(ValueError):
            profiles.save(name, patch)


def test_pilot_credentials_survive_other_profile_mutations():
    credentials.save_for("internal-pilot", {"agent_core": "pilot-secret"})
    credentials.save_for("dev", {"agent_core": "dev-secret"})
    credentials.delete_for("prod")
    assert credentials.get_for("internal-pilot", "agent_core") == "pilot-secret"
    assert "pilot-secret" not in json.dumps(profiles.status())


def test_process_key_is_not_shared_with_other_profiles(monkeypatch):
    monkeypatch.setenv("LAKE_MAXIS_PROFILE", "internal-pilot")
    monkeypatch.setenv(credentials.ENV_NAMES["agent_core"], "process-pilot-secret")
    assert credentials.get_for("internal-pilot", "agent_core") == "process-pilot-secret"
    for name in ("dev", "prod"):
        with pytest.raises(credentials.CredentialStoreUnavailable, match="requires LAKE_MAXIS_PROFILE"):
            credentials.get_for(name, "agent_core")
    assert "process-pilot-secret" not in json.dumps(profiles.status())


def test_pilot_requires_explicit_allowlist(monkeypatch):
    monkeypatch.delenv("LAKE_MAXIS_INTERNAL_PILOT_ALLOWED_HOSTS")
    with pytest.raises(ValueError, match="explicit destination allowlist"):
        profiles.save("internal-pilot", {"base_url": "https://dify.maxis.skax.co.kr/v1"})


@pytest.mark.parametrize("url", [
    "http://pilot.maxis.example/v1", "https://localhost/v1", "https://127.0.0.1/v1",
    "https://pilot.maxis.example:8443/v1", "https://dify.maxis.skax.co.kr/v1",
])
def test_pilot_transport_never_uses_dev_exceptions(url):
    with pytest.raises(ValueError):
        canonical_maxis_api_base(url, stage="internal-pilot", require_https=False)


@pytest.mark.parametrize("boundary", ["runtime", "probe"])
def test_pilot_callers_request_verified_https_before_transport(monkeypatch, boundary):
    from app.agent.maxis import backend, client
    from app.agent import routes

    class BeforeTransport(Exception):
        pass

    def inspect_config(**kwargs):
        assert kwargs["stage"] == "internal-pilot"
        assert kwargs["require_https"] is True
        assert kwargs["verify_tls"] is True
        raise BeforeTransport

    monkeypatch.setattr(client, "DifyClientConfig", inspect_config)
    profile = _configured_pilot()
    with pytest.raises(BeforeTransport):
        if boundary == "runtime":
            backend._client_config(profile, {})
        else:
            routes._run_maxis_probe(profile, {})


@pytest.mark.parametrize("sibling", ["dev", "prod"])
@pytest.mark.parametrize("asset", ["knowledge_dataset_id", "knowledge_sentinel_sha256", "workflow", "key"])
def test_pilot_assets_cannot_overlap_either_environment(sibling, asset):
    candidate = _configured_pilot()
    if asset == "workflow":
        profiles.save(sibling, {"workflows": candidate["workflows"]})
    elif asset == "key":
        credentials.save_for(sibling, {"editor_author": "pilot-secret"})
    else:
        profiles.save(sibling, {asset: candidate[asset]})
    for name in (sibling, "internal-pilot"):
        with pytest.raises(ValueError, match="independent"):
            profiles._require_cross_environment_asset_separation(profiles.get(name))


@pytest.mark.parametrize("field,value", [
    ("content_retention_hours", 25), ("metadata_retention_days", 8),
    ("content_retention_hours", True), ("metadata_retention_days", 1.0),
    ("audience_scope_sha256", "private audience"), ("controls_attestation", True),
])
def test_pilot_policy_rejects_invalid_or_unbounded_values(field, value):
    policy = {**pilot_policy.default_policy(), field: value}
    with pytest.raises(ValueError):
        profiles.save("internal-pilot", {"internal_pilot_policy": policy})


def test_legacy_content_retention_cannot_exceed_pilot_bound():
    with pytest.raises(ValueError, match="retention exceeds"):
        profiles.save("internal-pilot", {"remote_log_retention_days": 2})


def test_matching_metadata_never_proves_server_controls_or_enables_network():
    _attested_pilot()
    state = profiles.internal_pilot_controls_status()
    assert state["bindingValid"] is True
    assert state["valid"] is False
    assert state["reasons"] == list(pilot_policy.MISSING_VERIFIERS)
    for operation in (profiles.resolve, profiles.probe_candidate, profiles.start_probe, profiles.activate):
        with pytest.raises(ValueError, match="server controls are not verified"):
            operation("internal-pilot")
    assert profiles.probe_ok("internal-pilot") is False
    assert profiles.active_profile_name() == ""


def test_forged_persisted_activation_and_probe_cannot_enable_pilot():
    from app.infra import prefs

    _attested_pilot()
    prefs.save_strict({
        "maxisActiveProfile": "internal-pilot",
        "maxisActiveProfileFingerprint": profiles.profile_fingerprint(profiles.get("internal-pilot")),
        "maxisProbeOkByProfile": {"internal-pilot": "forged"},
        "maxisLastProbeByProfile": {"internal-pilot": {
            "ok": True, "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }},
    })
    row = next(row for row in profiles.status()["profiles"] if row["name"] == "internal-pilot")
    assert row["active"] is True
    assert row["enabled"] is False
    assert row["verified"] is False
    assert profiles.mark_probe("internal-pilot", True, {"workflows": {}}, start_binding="f" * 64) is False
    for mode in ("shadow", "maxis"):
        with pytest.raises(ValueError, match="server controls are not verified"):
            profiles.set_routes({"agent_core": mode})


@pytest.mark.parametrize("patch", [
    {"knowledge_sentinel_sha256": "c" * 64},
    {"workflows": {"agent_core": {"version": "55555555-5555-4555-8555-555555555555"}}},
    {"knowledge_dataset_id": "66666666-6666-4666-8666-666666666666"},
])
def test_release_change_invalidates_controls_binding(patch):
    _attested_pilot()
    profiles.save("internal-pilot", patch)
    assert profiles.internal_pilot_controls_status()["bindingValid"] is False


def test_policy_change_invalidates_controls_binding_and_fingerprint():
    policy = _attested_pilot()
    previous = profiles.profile_fingerprint(profiles.get("internal-pilot"))
    policy["metadata_retention_days"] = 6
    profiles.save("internal-pilot", {"internal_pilot_policy": policy})
    assert profiles.internal_pilot_controls_status()["bindingValid"] is False
    assert profiles.profile_fingerprint(profiles.get("internal-pilot")) != previous


def test_expired_controls_evidence_stays_blocked():
    policy = _attested_pilot()
    policy["controls_attestation"]["verified_at"] = "2020-01-01T00:00:00Z"
    policy["controls_attestation"]["expires_at"] = "2020-01-02T00:00:00Z"
    profiles.save("internal-pilot", {"internal_pilot_policy": policy})
    state = profiles.internal_pilot_controls_status()
    assert state["bindingValid"] is False
    assert "Pilot controls evidence has expired." in state["reasons"]


def test_raw_invalid_pilot_metadata_is_not_echoed_in_status():
    from app.infra import prefs

    row = profiles.get("internal-pilot")
    row["internal_pilot_policy"]["audience_scope_sha256"] = "app-secret-private"
    prefs.save_strict({"maxisProfiles": [row]})
    result = profiles.status()
    assert "app-secret-private" not in json.dumps(result)
    assert result["profiles"][-1]["internalPilotControls"]["valid"] is False


def test_pilot_environment_policy_is_profile_bound(monkeypatch):
    _configured_pilot()
    monkeypatch.setenv("LAKE_MAXIS_PROFILE", "internal-pilot")
    policy = {**pilot_policy.default_policy(), "metadata_retention_days": 3}
    monkeypatch.setenv("LAKE_MAXIS_INTERNAL_PILOT_POLICY", json.dumps(policy))
    assert profiles._effective(profiles.get("internal-pilot"))["internal_pilot_policy"] == policy
    assert "internal_pilot_policy" not in profiles._effective(profiles.get("dev"))
    monkeypatch.setenv("LAKE_MAXIS_REMOTE_LOG_RETENTION_DAYS", "2")
    with pytest.raises(ValueError, match="retention exceeds"):
        profiles._effective(profiles.get("internal-pilot"))
