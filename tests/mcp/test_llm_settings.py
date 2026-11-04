"""Named LLM settings over the real MCP SDK; all persistence is isolated and probes are fake."""

import json
from types import SimpleNamespace

import anyio
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from mcp import Client
import pytest

from app.agent import config, connection_settings, secrets
from app.local_mcp.llm_tools import LLM_TOOLS


@pytest.fixture
def env(build_env, monkeypatch, tmp_path):
    from app.infra import settings
    from app.agent.routes import router
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    for name in {name for names in secrets.ENV_NAMES.values() for name in names} | {"LAKE_AGENT_PROVIDER"}:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "available", lambda: (True, ""))
    monkeypatch.setattr(connection_settings, "reset_runtime", lambda: None)
    result = build_env()
    result.app.include_router(router)
    result.runtime.authentication.check = lambda *_a, **_k: pytest.fail("LLM settings must not check Jira auth")
    return result


def invoke(env, tool_name, _validate_output=True, **arguments):
    async def run():
        async with Client(env.runtime.server) as mcp:
            catalog = {tool.name: tool for tool in (await mcp.list_tools()).tools}
            result = await mcp.call_tool(tool_name, arguments)
            if _validate_output:
                Draft202012Validator(catalog[tool_name].output_schema).validate(result.structured_content)
            assert "output_schema_mismatch" not in result.content[0].text
            return result
    return anyio.run(run)


def data(env, tool_name, **arguments):
    result = invoke(env, tool_name, **arguments)
    assert not result.is_error, result.content
    return result.structured_content["data"]


def test_ui_and_mcp_share_candidates_secrets_and_explicit_activation(env, monkeypatch):
    resets = []
    monkeypatch.setattr(connection_settings, "reset_runtime", lambda: resets.append(True))
    row = data(env, "create_llm_config", name="로컬 시험", provider="fake")
    cid = row["id"]
    assert not row["active"] and not row["authOk"]
    assert invoke(env, "activate_llm_config", config_id=cid).is_error
    models = data(env, "list_llm_models", config_id=cid)
    assert "fake-chat" in models["models"]
    assert not data(env, "get_llm_settings")["configs"][0]["authOk"]
    for stage in ("auth", "models"):
        assert data(env, "check_llm_connection", config_id=cid, stage=stage)["passed"]
    assert data(env, "get_llm_settings")["activeConfigId"] == ""
    assert data(env, "activate_llm_config", config_id=cid)["active"]
    assert resets == [True]
    assert invoke(env, "delete_llm_config", config_id=cid).is_error

    with TestClient(env.app) as ui:
        other = ui.post("/api/agent/configs", json={"name": "후보", "provider": "openai"}).json()["config"]
        updated = data(env, "update_llm_config", config_id=other["id"], changes={
            "chatModel": "candidate-model", "secrets": {"openaiApiKey": "fixture-secret-12345"}})
        assert updated["secrets"]["openaiApiKey"] == "configured"
        assert secrets.get_for(other["id"], "openaiApiKey") == "fixture-secret-12345"
        state = ui.get("/api/agent/status").json()
        assert state["activeConfigId"] == cid
        assert next(x for x in state["configs"] if x["id"] == other["id"])["chatModel"] == "candidate-model"
        ui.put(f"/api/agent/configs/{other['id']}", json={"name": "UI에서 변경"})
        assert data(env, "get_llm_settings")["configs"][1]["name"] == "UI에서 변경"
    data(env, "update_llm_config", config_id=other["id"], changes={"secrets": {"openaiApiKey": ""}})
    assert secrets.get_for(other["id"], "openaiApiKey") == ""
    assert data(env, "delete_llm_config", config_id=other["id"])["deleted"]
    assert env.calls == []


def test_provider_errors_and_model_lists_never_echo_keys_or_headers(env, monkeypatch):
    cid = data(env, "create_llm_config", name="오류 검증", provider="openai_compat")["id"]
    key, header = "fixture-key-private-xyz", "fixture-header-private-abc"
    data(env, "update_llm_config", config_id=cid, changes={"secrets": {
        "compatBaseUrl": "http://127.0.0.1:9999/v1", "compatApiKey": key,
        "compatHeaders": json.dumps({"X-Auth": header})}})
    monkeypatch.setattr(config, "list_models", lambda **_kw: {
        "chat": [], "embed": [], "error": f"provider refused {key} {header}"})
    result = invoke(env, "list_llm_models", config_id=cid)
    assert key not in result.content[0].text and header not in result.content[0].text
    checked = invoke(env, "check_llm_connection", config_id=cid)
    assert checked.structured_content["data"]["passed"] is False
    assert key not in checked.content[0].text and header not in checked.content[0].text
    monkeypatch.setattr(config, "probe", lambda **_kw: {"ok": False, "error": {key: header}})
    nested = invoke(env, "check_llm_connection", config_id=cid, stage="models")
    assert key not in nested.content[0].text and header not in nested.content[0].text
    def failed_save(*_a):
        raise OSError(f"failure with {key}")
    monkeypatch.setattr(secrets, "save_for", failed_save)
    failed = invoke(env, "update_llm_config", config_id=cid, changes={"secrets": {"compatApiKey": key}})
    assert failed.is_error and key not in failed.content[0].text


@pytest.mark.parametrize("path", ["error", "capability", "sample"])
@pytest.mark.parametrize("field", ["compatApiKey", "compatHeaders"])
def test_real_probe_redacts_before_error_capability_and_sample_shortening(env, monkeypatch, path, field):
    from app.agent import capabilities
    cid = data(env, "create_llm_config", name="경계 진단", provider="fake")["id"]
    secret = "boundary-private-fixture-" * 3
    value = secret if field.endswith("Key") else json.dumps({"X-Auth": secret})
    data(env, "update_llm_config", config_id=cid, changes={"secrets": {field: value}})
    limit = {"error": 300, "capability": 240, "sample": 60}[path]
    raw = "x" * (limit - 20) + secret

    class Probe:
        def __init__(self, initial=False):
            self.initial = initial
        def invoke(self, _prompt):
            if path == "error" or path == "capability" and not self.initial:
                raise ValueError(raw)
            return SimpleNamespace(content=raw if path == "sample" else "pong")
        def with_structured_output(self, *_a, **_kw):
            return self

    monkeypatch.setattr(config, "get_llm", lambda **kw: Probe(kw.get("max_tokens") == 5))
    monkeypatch.setattr(config, "get_embeddings", lambda **_kw: SimpleNamespace(embed_query=lambda _text: [1., 0.]))
    monkeypatch.setattr(capabilities, "native_tools_allowed", lambda *_a, **_kw: False)
    result = invoke(env, "check_llm_connection", config_id=cid, stage="models")
    assert not result.is_error and secret[:20] not in result.content[0].text
    if path == "capability":
        assert secret[:20] not in json.dumps(capabilities.get("complex", cid)["errors"])
    assert config._brief(ValueError(secret)) == secret  # MCP context does not leak to later UI calls.


@pytest.mark.parametrize("values,hidden", [
    ({"compatHeaders": 'broken-json-fixture-secret'}, "broken-json-fixture-secret"),
    ({"compatBaseUrl": "https://user:fixture-password@example.test/v1"}, "fixture-password"),
    ({"compatBaseUrl": "https://example.test/v1?key=fixture-query-secret"}, "fixture-query-secret"),
    ({"compatApiKey": ["wrong-type-fixture-secret"]}, "wrong-type-fixture-secret"),
    ({"compatHeaders": {"X-Auth": "object-fixture-secret"}}, "object-fixture-secret"),
    ({"compatApiKey": "combined-fixture-secret", "unknown": "combined-fixture-secret"}, "combined-fixture-secret"),
])
def test_sdk_validation_errors_do_not_echo_sensitive_input(env, values, hidden):
    cid = data(env, "create_llm_config", name="입력 검증", provider="openai_compat")["id"]
    result = invoke(env, "update_llm_config", config_id=cid, changes={"secrets": values}, _validate_output=False)
    assert result.is_error and hidden not in result.content[0].text
    assert secrets.load_for(cid) == {}


def test_preexisting_credentialed_urls_are_not_exported(env, monkeypatch):
    cid = data(env, "create_llm_config", name="이전 UI 설정", provider="openai_compat")["id"]
    secrets.save_for(cid, {"compatBaseUrl": "https://user:old-secret@example.test/v1?key=query-secret"})
    result = invoke(env, "get_llm_settings")
    assert "old-secret" not in result.content[0].text and "query-secret" not in result.content[0].text
    assert result.structured_content["data"]["configs"][0]["secrets"]["compatBaseUrl"] == "https://example.test/v1"
    monkeypatch.setattr(config, "list_models", lambda **_kw: {
        "chat": [], "embed": [], "error": "URL failure old-secret query-secret"})
    checked = invoke(env, "check_llm_connection", config_id=cid)
    assert "old-secret" not in checked.content[0].text and "query-secret" not in checked.content[0].text


def test_environment_precedence_and_local_only_settings_are_explicit(env, monkeypatch):
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "fake")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-environment-secret")
    result = invoke(env, "get_llm_settings")
    assert result.structured_content["data"]["runtimeConfigSource"] == "environment"
    assert result.structured_content["data"]["envOverrides"]["openaiApiKey"] == "OPENAI_API_KEY"
    assert "fixture-environment-secret" not in result.content[0].text
    cid = data(env, "create_llm_config", name="범위 제한", provider="fake")["id"]
    for changes in ({"provider": "openai"}, {"secrets": {"file": "arbitrary"}}, {"userPrompt": "not a connection"}):
        async def rejected():
            async with Client(env.runtime.server) as mcp:
                assert (await mcp.call_tool("update_llm_config", {"config_id": cid, "changes": changes})).is_error
        anyio.run(rejected)
    assert env.calls == []


def test_catalog_describes_all_seven_tools_and_write_only_inputs(env):
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            catalog = {tool.name: tool for tool in (await mcp.list_tools()).tools}
            assert LLM_TOOLS <= catalog.keys()
            for name in LLM_TOOLS:
                assert catalog[name].description and catalog[name].output_schema
            assert catalog["get_llm_settings"].annotations.read_only_hint
            assert not catalog["check_llm_connection"].annotations.read_only_hint
            fields = catalog["update_llm_config"].input_schema["$defs"]["ConnectionSecrets"]["properties"]
            assert any(x.get("writeOnly") for x in fields["openaiApiKey"]["anyOf"])
    anyio.run(scenario)


def test_short_dummy_secrets_do_not_rewrite_configuration_identifiers(env):
    cid = data(env, "create_llm_config", name="candidate a1", provider="openai_compat")["id"]
    row = data(env, "update_llm_config", config_id=cid, changes={"chatModel": "model-a1", "secrets": {
        "compatApiKey": "a", "compatHeaders": '{"X-Test":"1"}'}})
    assert (row["id"], row["provider"], row["name"], row["chatModel"]) == (
        cid, "openai_compat", "candidate a1", "model-a1")
    assert data(env, "get_llm_settings")["configs"][0]["id"] == cid


@pytest.mark.parametrize("tool", ["list_llm_models", "check_llm_connection"])
def test_diagnostics_retain_old_secret_redaction_during_rotation(env, monkeypatch, tool):
    cid = data(env, "create_llm_config", name="교체 중 진단", provider="openai_compat")["id"]
    old = "old-fixture-credential"
    data(env, "update_llm_config", config_id=cid, changes={"secrets": {"compatApiKey": old}})
    def response(**_kw):
        connections = connection_settings
        connections.update_config(cid, {"secrets": {"compatApiKey": "new-fixture-credential"}})
        return {"ok": False, "chat": [], "embed": [], "warnings": [old],
                "capabilities": {"complex": {"errors": {old: old}}}}
    monkeypatch.setattr(config, "list_models" if tool == "list_llm_models" else "probe", response)
    arguments = {"config_id": cid, **({"stage": "models"} if tool == "check_llm_connection" else {})}
    result = invoke(env, tool, **arguments)
    assert old not in result.content[0].text
    assert "[redacted]" in result.content[0].text


def test_catalog_and_profile_pages_are_bounded_complete_and_keep_identifiers(env, monkeypatch):
    created = [connection_settings.create_config(f"profile-{i}", "fake")["id"] for i in range(13)]
    state = data(env, "get_llm_settings")
    assert state["returned"] == 3 and state["total"] == 13 and state["hasMore"]
    found = [row["id"] for row in state["configs"]]
    while state["hasMore"]:
        state = data(env, "get_llm_settings", start_at=state["nextStartAt"])
        assert len(json.dumps(state, ensure_ascii=False).encode()) <= 8192
        found.extend(row["id"] for row in state["configs"])
    assert found == created
    expected = [f"model-{i:04d}" for i in range(1000)]
    monkeypatch.setattr(config, "list_models", lambda **_kw: {"chat": expected, "embed": []})
    page = data(env, "list_llm_models", config_id=created[0])
    assert page["returned"] == 25 and page["total"] == 1000
    actual = page["models"]
    while page["hasMore"]:
        page = data(env, "list_llm_models", config_id=created[0], start_at=page["nextStartAt"], max_results=50)
        assert len(json.dumps(page, ensure_ascii=False).encode()) <= 8192
        actual.extend(page["models"])
    assert actual == expected
    long_ids = [f"model-{i}-" + "이름" * 100 for i in range(30)]
    monkeypatch.setattr(config, "list_models", lambda **_kw: {"chat": long_ids, "embed": []})
    limited = data(env, "list_llm_models", config_id=created[0], max_results=50)
    assert 0 < limited["returned"] < len(long_ids) and limited["hasMore"]
    assert limited["models"] == long_ids[:limited["returned"]]
    assert len(json.dumps(limited, ensure_ascii=False).encode()) <= 8192


def test_large_diagnostics_are_explicit_previews_and_identifiers_are_not_shortened(env, monkeypatch):
    cid = data(env, "create_llm_config", name="큰 응답", provider="fake")["id"]
    monkeypatch.setattr(config, "probe", lambda **_kw: {"ok": False, "error": "진단 " * 10000})
    result = data(env, "check_llm_connection", config_id=cid, stage="models")
    assert result["result"]["truncated"] and len(json.dumps(result, ensure_ascii=False).encode()) <= 8192
    monkeypatch.setattr(config, "list_models", lambda **_kw: {"chat": ["model-" + "x" * 9000], "embed": []})
    oversized = invoke(env, "list_llm_models", config_id=cid)
    assert oversized.is_error and oversized.structured_content["error"]["status"] == 413
    changed = invoke(env, "update_llm_config", config_id=cid, changes={"chatModel": "x" * 9000})
    assert changed.is_error and connection_settings.get_config(cid)["chatModel"] == "fake-chat"
    connection_settings.update_config(cid, {"chatModel": "legacy-ui-model-" + "x" * 9000})
    assert invoke(env, "get_llm_settings").structured_content["error"]["status"] == 413
    assert data(env, "update_llm_config", config_id=cid, changes={"chatModel": "fake-chat"})["id"] == cid
