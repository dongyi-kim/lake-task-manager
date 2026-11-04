"""Public output contracts travel over HTTP and validate the original wire response."""

import json

import anyio
import httpx2
from jsonschema import Draft202012Validator, ValidationError
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
import pytest
from pydantic import ValidationError as ModelValidationError

from app.auth.base import SessionExpired, UpstreamUnavailable
from app.local_mcp.output_schema import data_types
from app.local_mcp.schemas.common import OpenModel, ToolError


def test_http_catalog_has_specific_output_contract_for_every_tool(build_env):
    env = build_env()

    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app),
                    headers={"Authorization": "Bearer " + env.runtime.credentials.token()}) as http:
                async with Client(streamable_http_client(env.runtime.url, http_client=http)) as client:
                    catalog = {tool.name: tool for tool in (await client.list_tools()).tools}
                    assert set(catalog) == set(data_types()) | {"get_hierarchy_options", "change_ticket_hierarchy"}
                    assert len(catalog) == 65
                    for tool in catalog.values():
                        schema = tool.output_schema
                        assert schema and schema["type"] == "object"
                        Draft202012Validator.check_schema(schema)
                        validator = Draft202012Validator(schema)
                        validator.validate({"ok": False, "error": {"code": "not_found", "message": "missing"}})
                        with pytest.raises(ValidationError):
                            validator.validate({"ok": True})
                        with pytest.raises(ValidationError):
                            validator.validate({"ok": False, "error": {"message": "missing code"}})
                    body = catalog["create_task"].input_schema["$defs"]["CreateInput"]
                    assert "clientMutationId" in body["required"]
                    assert body["properties"]["clientMutationId"]["minLength"] == 8
                    for name, arguments in (
                        ("search_issues", {"jql": "project = DL", "max_results": 2}),
                        ("get_ticket", {"key": "DL-1"}),
                        ("get_comments", {"key": "DL-1"}),
                        ("get_ticket", {"key": "OUTSIDE-1"}),
                    ):
                        result = await client.call_tool(name, arguments)
                        payload = result.structured_content
                        Draft202012Validator(catalog[name].output_schema).validate(payload)
                        assert json.loads(result.content[0].text) == payload
                        assert result.is_error == (not payload["ok"])
                        assert payload.get("error", {}).get("code") != "output_schema_mismatch", payload

    anyio.run(scenario)


class WrittenTicket(OpenModel):
    key: str
    optional: str | None = None


def test_output_validation_preserves_response_and_never_repeats_completed_write(build_env):
    env = build_env()
    calls = []

    def malformed_write() -> dict:
        calls.append("written")
        return {"key": 42, "clientMutationId": "schema-completed-write"}

    def valid_read() -> dict:
        return {"key": "DL-1", "customExtension": {"retained": True}}

    env.runtime.register(malformed_write, read_only=False, output_type=WrittenTicket)
    env.runtime.register(valid_read, read_only=True, output_type=WrittenTicket)

    async def scenario():
        async with Client(env.runtime.server) as client:
            catalog = {tool.name: tool for tool in (await client.list_tools()).tools}
            result = await client.call_tool("malformed_write", {})
            assert result.is_error and calls == ["written"]
            payload = result.structured_content
            assert payload["error"]["code"] == "output_schema_mismatch"
            assert payload["error"]["repeatWrite"] is False
            assert payload["error"]["retryable"] is False
            assert payload["error"]["operationResult"] == {
                "ok": True, "data": {"key": 42, "clientMutationId": "schema-completed-write"}}
            Draft202012Validator(catalog["malformed_write"].output_schema).validate(payload)
            result = await client.call_tool("valid_read", {})
            assert not result.is_error
            assert result.structured_content["data"] == {"key": "DL-1", "customExtension": {"retained": True}}
            assert "optional" not in result.structured_content["data"]
            assert json.loads(result.content[0].text) == result.structured_content

    anyio.run(scenario)


def test_malformed_completed_create_keeps_persisted_key_and_replays_receipt_without_second_post(build_env, monkeypatch):
    env = build_env()
    original_create = env.runtime.commands.create
    original_keys = set(env.store.issues)
    arguments = {"body": {"type": "Task", "summary": "Persisted despite response schema failure",
                          "clientMutationId": "schema:real-completed-create:0001"}}

    def malformed_completed_create(body, parent=None):
        created = original_create(body, parent)
        # Damage only the response after the real Jira write/receipt has completed.
        return {**created, "ok": "completed"}

    monkeypatch.setattr(env.runtime.commands, "create", malformed_completed_create)

    def post_count():
        return sum(method == "post" and path == "/rest/api/2/issue" for method, path, _ in env.calls)

    async def scenario():
        async with Client(env.runtime.server) as client:
            catalog = {tool.name: tool for tool in (await client.list_tools()).tools}
            first = await client.call_tool("create_task", arguments)
            assert first.is_error and post_count() == 1
            error = first.structured_content["error"]
            assert error["code"] == "output_schema_mismatch"
            assert error["repeatWrite"] is False and error["retryable"] is False
            completed = error["operationResult"]
            key = completed["data"]["key"]
            assert completed == {"ok": True, "data": {"ok": "completed", "key": key}}
            assert set(env.store.issues) - original_keys == {key}
            assert env.store.issues[key]["summary"] == arguments["body"]["summary"]
            assert json.loads(first.content[0].text) == first.structured_content
            Draft202012Validator(catalog["create_task"].output_schema).validate(first.structured_content)

            replay = await client.call_tool("create_task", arguments)
            assert replay.is_error and replay.structured_content == first.structured_content
            assert post_count() == 1 and set(env.store.issues) - original_keys == {key}

            # Fixing the serializer exposes the original confirmed receipt, not a new issue.
            monkeypatch.setattr(env.runtime.commands, "create", original_create)
            restored = await client.call_tool("create_task", arguments)
            assert not restored.is_error and restored.structured_content["data"] == {"ok": True, "key": key}
            assert post_count() == 1 and set(env.store.issues) - original_keys == {key}

    anyio.run(scenario)


def test_new_internal_tools_must_explicitly_declare_their_output_type(build_env):
    env = build_env()

    def missing_contract() -> dict:
        return {}

    with pytest.raises(KeyError, match="missing_contract"):
        env.runtime.register(missing_contract, read_only=True)


@pytest.mark.parametrize("explanation", [
    {"message": "MCP error"}, {"error": "UI error"}, {"detail": "Session expired"},
    {"error": "Network unavailable", "detail": "Timed out"},
    {"message": None, "detail": "Known detail"},
])
def test_error_alternative_explanations_validate_without_invented_fields(explanation):
    payload = {"code": "upstream_unavailable", **explanation, "retryable": True}
    parsed = ToolError.model_validate(payload)
    assert parsed.model_dump(exclude_unset=True) == payload
    Draft202012Validator(ToolError.model_json_schema()).validate(payload)


@pytest.mark.parametrize("explanation", [{}, {"message": None}, {"detail": {}}, {"error": 401}])
def test_error_requires_a_real_explanation_in_both_runtime_and_public_schema(explanation):
    payload = {"code": "broken", **explanation}
    with pytest.raises(ModelValidationError):
        ToolError.model_validate(payload)
    with pytest.raises(ValidationError):
        Draft202012Validator(ToolError.model_json_schema()).validate(payload)


@pytest.mark.parametrize("exception,payload", [
    (SessionExpired("expired fixture session"), {
        "code": "authentication_required", "status": 401, "needLogin": True,
        "env": "prod", "detail": "expired fixture session"}),
    (SessionExpired("request rejected"), {
        "code": "upstream_unavailable", "status": 502,
        "error": "요청이 거절되었습니다(세션은 정상) — request rejected"}),
    (SessionExpired("network state unknown"), {
        "code": "upstream_unavailable", "status": 503,
        "error": "Jira 연결 상태를 확인하지 못했습니다. 잠시 후 자동 재시도합니다.",
        "retryable": True, "needLogin": False}),
    (UpstreamUnavailable("fixture connection timeout"), {
        "code": "upstream_unavailable", "status": 503,
        "error": "Jira 연결이 응답하지 않습니다. 앱은 계속 실행 중이며 잠시 후 자동 재시도합니다.",
        "retryable": True, "detail": "fixture connection timeout"}),
])
def test_production_classified_auth_and_network_errors_preserve_wire_shape(build_env, exception, payload):
    env = build_env()
    classified = []

    # The production callback adds code/status to the shared UI handler JSON.
    # Its error/detail variants intentionally have no MCP-specific message field.
    def classify_error(exc):
        assert exc is exception
        classified.append(exc)
        return dict(payload)

    def production_failure() -> dict:
        raise exception

    env.runtime.classify_error = classify_error
    env.runtime.register(production_failure, read_only=True, output_type=WrittenTicket)
    expected_error = dict(payload)
    if payload.get("needLogin"):
        expected_error["nextAction"] = {"tool": "authenticate", "arguments": {
            "service": "jira", "interactive": True}}

    async def scenario():
        async with Client(env.runtime.server) as client:
            tool = next(tool for tool in (await client.list_tools()).tools if tool.name == "production_failure")
            result = await client.call_tool("production_failure", {})
            assert result.is_error and classified == [exception]
            expected = {"ok": False, "error": expected_error}
            assert result.structured_content == expected
            assert json.loads(result.content[0].text) == expected
            Draft202012Validator(tool.output_schema).validate(expected)
            assert "message" not in result.structured_content["error"]

    anyio.run(scenario)
