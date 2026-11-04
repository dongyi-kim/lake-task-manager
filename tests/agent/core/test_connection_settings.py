"""Shared UI/MCP connection verification uses exact inputs without locking network calls."""

import hashlib
import os

import pytest

from app.agent import config, connection_settings as connections
from app.infra import prefs, settings


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    for name in list(os.environ):
        if name.startswith(("LAKE_AGENT_", "AOAI_", "OPENAI_", "AZURE_OPENAI_", "LANGFUSE_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "available", lambda: (True, ""))
    monkeypatch.setattr(connections, "reset_runtime", lambda: None)
    return connections.create_config("검증 대상", "fake")["id"]


@pytest.mark.parametrize("changes", [
    {"chatModel": "another-model"}, {"secrets": {"compatApiKey": "changed-fixture-key"}},
    {"secrets": {"compatHeaders": '{"X-Auth":"changed-header"}'}},
    {"secrets": {"simpleBaseUrl": "http://127.0.0.1:9999/v1"}},
    {"secrets": {"simpleApiKey": "changed-simple-key"}},
    {"secrets": {"embeddingApiKey": "changed-embedding-key"}},
    {"embeddingApiVersion": "changed-embedding-version"},
])
def test_auth_only_recheck_cannot_reuse_prior_model_verification(cid, changes):
    assert connections.check_connection(cid, "models")["ok"]
    connections.update_config(cid, changes)
    assert connections.check_connection(cid, "auth")["ok"]
    row = connections.get_config(cid)
    assert row["authOk"] and not row["modelsOk"]
    assert not connections.activate_config(cid)["ok"]
    assert connections.read_settings()["activeConfigId"] == ""


@pytest.mark.parametrize("stage,newer_check", [("auth", False), ("auth", True), ("models", False)])
def test_late_probe_does_not_verify_changed_inputs_or_replace_newer_evidence(cid, monkeypatch, stage, newer_check):
    def change_during_auth(**_kw):
        connections.update_config(cid, {"secrets": {"compatApiKey": "new-fixture-key"}})
        if newer_check:
            config._record_verification(cid, config._auth_signature(cid))
        return {"chat": ["fake-chat"], "embed": ["fake-embed"], "total": 2}
    if stage == "auth":
        monkeypatch.setattr(config, "list_models", change_during_auth)
    else:
        original = config.get_embeddings
        def change_during_models(**kwargs):
            connections.update_config(cid, {"chatModel": "changed-fake-model"})
            return original(**kwargs)
        monkeypatch.setattr(config, "get_embeddings", change_during_models)
    result = connections.check_connection(cid, stage)
    assert not result["ok"] and result["configChanged"]
    assert config.auth_ok(cid) is newer_check
    assert not config.models_ok(cid) and not connections.activate_config(cid)["ok"]


def test_legacy_active_marker_requires_one_time_model_check_and_reactivation(cid):
    # Exact pre-hardening fake-profile model signature: credentials/headers were not included.
    old_parts = [cid, "fake", "", "", "", "fake-chat", "fake-chat", "fake-embed",
                 "", "fake", "", "", "", "", "", "", ""]
    legacy = hashlib.sha256("|".join(old_parts).encode()).hexdigest()[:32]
    prefs.save({"agentActiveConfigId": cid, "agentAuthOkByConfig": {cid: config._auth_signature(cid)},
                "agentModelOkByConfig": {cid: legacy}, "agentActiveSig": legacy})
    assert config.auth_ok(cid) and not config.models_ok(cid) and not config.verified()
    assert not connections.activate_config(cid)["ok"]
    assert connections.check_connection(cid, "models")["ok"]
    assert connections.activate_config(cid)["ok"] and config.verified()


@pytest.mark.parametrize("explicit_version", ["", "2025-03-01-preview"])
def test_mixed_provider_embedding_hashes_effective_api_version(cid, monkeypatch, explicit_version):
    target = connections.create_config("혼합 연결", "openai_compat")["id"]
    connections.update_config(target, {"embeddingProvider": "aoai", "apiVersion": "2024-10-21",
                                       "embeddingApiVersion": explicit_version})
    config.mark_verified(target)
    assert connections.activate_config(target)["ok"]
    before = config._auth_signature(target)
    connections.update_config(target, {"apiVersion": "2025-01-01-preview"})
    assert config.embedding_definition(target).api_version == (explicit_version or "2025-01-01-preview")
    assert (config._auth_signature(target) == before) is bool(explicit_version)
    assert config.verified() is bool(explicit_version)
    monkeypatch.setattr(config, "list_models", lambda **_kw: {"chat": ["chat"], "embed": ["embed"]})
    assert connections.check_connection(target, "auth")["ok"] and config.auth_ok(target)
    assert config.models_ok(target) is bool(explicit_version)
    assert connections.activate_config(target)["ok"] is bool(explicit_version)


def test_diagnostic_sanitizer_precedes_shortening_and_resets_on_error(cid):
    from app.agent import capabilities
    from app.agent.diagnostics import diagnostic_sanitizer, diagnostic_text
    secret = "fixture-private-" * 5
    with pytest.raises(RuntimeError), diagnostic_sanitizer(lambda text: text.replace(secret, "[redacted]")):
        assert secret[:15] not in config._brief(ValueError("x" * 280 + secret))
        assert secret[:15] not in capabilities._brief(ValueError("x" * 220 + secret))
        assert secret[:15] not in diagnostic_text("x" * 40 + secret, 60)
        capabilities.record("complex", "plain_chat", False, "x" * 220 + secret, config_id=cid)
        assert secret[:15] not in capabilities.get("complex", cid)["errors"]["plain_chat"]
        raise RuntimeError("force finally")
    assert config._brief(ValueError(secret)) == secret
