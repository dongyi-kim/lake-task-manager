"""Creation metadata is discoverable, strict, bounded and never a silent empty fallback."""

import json

import anyio
from jsonschema import Draft202012Validator
from mcp import Client
import pytest

from app.auth.base import UpstreamUnavailable


@pytest.fixture
def env(build_env, monkeypatch):
    env = build_env()
    original = env.provider.get_json
    env.metadata = {"summary": {"required": True, "schema": {"type": "string"}},
                    "reporter": {"required": True, "schema": {"type": "user"}},
                    "parent": {"required": True, "schema": {"type": "issuelink"}},
                    "components": {"required": True, "schema": {"type": "array", "items": "component"},
                                   "allowedValues": [{"id": "1", "name": "ETL"}]},
                    "customfield_risk": {"required": True, "schema": {"type": "string"}}}
    env.requests, env.failure = [], None
    def request(path, *args, **kwargs):
        if path == "/rest/api/2/issue/createmeta":
            env.requests.append(kwargs)
            if env.failure:
                raise env.failure
            return {"projects": [{"key": "DL", "issuetypes": [{"name": "Task", "fields": env.metadata}]}]}
        return original(path, *args, **kwargs)
    monkeypatch.setattr(env.provider, "get_json", request)
    return env


def test_creation_discovery_has_required_paths_managed_fields_and_strict_failure_recovery(env):
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            catalog = {tool.name: tool for tool in (await mcp.list_tools()).tools}
            validator = Draft202012Validator(catalog["list_options"].output_schema)
            args = {"kind": "create_fields", "issue_type": "Task"}
            missing = await mcp.call_tool("list_options", {"kind": "create_fields"})
            assert missing.is_error and not env.requests
            # A previous UI fallback must not masquerade as verified create metadata.
            env.cache.set("createmeta:mock:DL:task", {}, 900)
            env.failure = UpstreamUnavailable("fixture offline")
            failed = await mcp.call_tool("list_options", args)
            assert failed.is_error and failed.structured_content["error"]["code"] == "upstream_unavailable"
            assert env.cache.get("createmeta:mock:DL:task:strict") is None
            env.failure = None
            result = await mcp.call_tool("list_options", args)
            assert not result.is_error, result.content
            validator.validate(result.structured_content)
            data = result.structured_content["data"]
            assert data["unsupportedRequired"] == ["customfield_risk"]
            assert data["options"]["summary"]["inputFields"] == ["body.summary"]
            assert data["options"]["parent"]["inputTools"] == ["create_subtask"]
            assert data["options"]["parent"]["schema"]["type"] == "issuelink"
            assert data["options"]["reporter"]["managed"] is True
            assert data["options"]["components"]["allowedValuesTotal"] == 1
            assert data["options"]["components"]["allowedValuesIncluded"] is False
            before = len(env.requests)
            detail = await mcp.call_tool("list_options", {**args, "field_id": "components"})
            assert detail.structured_content["data"]["options"]["components"]["allowedValues"] == [{"id": "1", "name": "ETL"}]
            assert len(env.requests) == before
            unknown_type = await mcp.call_tool("list_options", {**args, "issue_type": "Other"})
            assert unknown_type.is_error  # Never use the sole mismatching type's metadata.
            assert env.cache.get("createmeta:mock:DL:other:strict") is None
    anyio.run(scenario)


@pytest.mark.parametrize("strict", [False, True])
def test_malformed_creation_metadata_never_becomes_a_cached_empty_success(env, monkeypatch, strict):
    original = env.provider.get_json
    monkeypatch.setattr(env.provider, "get_json", lambda *_a, **_k: ["invalid metadata"])
    if strict:
        with pytest.raises(UpstreamUnavailable):
            env.client.create_fields("Task", strict=True)
    else:
        assert env.client.create_fields("Task") == {}
    assert env.cache.get("createmeta:mock:DL:task" + (":strict" if strict else "")) is None
    monkeypatch.setattr(env.provider, "get_json", original)
    assert env.client.create_fields("Task", strict=strict) == env.metadata


def test_creation_field_and_value_pages_preserve_all_required_entries_and_identifiers(env):
    env.metadata = {f"customfield_{i:03d}": {"name": "필수 항목 " + str(i), "required": True,
                     "schema": {"type": "string"}} for i in range(60)}
    expected_values = [{"id": f"exact-{i}", "name": "허용 선택지 " * 15 + str(i)} for i in range(200)]
    env.metadata["components"] = {"required": True, "allowedValues": expected_values,
                                  "defaultValue": [{"id": "exact-0"}], "hasDefaultValue": True}
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            async def pages(extra):
                start, combined, unsupported = 0, [], []
                while True:
                    result = await mcp.call_tool("list_options", {"kind": "create_fields", "issue_type": "Task",
                        "start_at": start, "max_results": 50, **extra})
                    assert not result.is_error, result.content
                    data = result.structured_content["data"]
                    assert len(json.dumps(data, ensure_ascii=False).encode()) <= 6144
                    combined.extend(data["options"]["components"]["allowedValues"] if extra else data["options"])
                    unsupported.extend(data["unsupportedRequired"])
                    if not data["hasMore"]:
                        return combined, unsupported, data
                    assert data["nextStartAt"] > start
                    start = data["nextStartAt"]
            fields, unsupported, _ = await pages({})
            assert fields == sorted(env.metadata)
            assert unsupported == sorted(key for key in env.metadata if key != "components")
            values, _, detail = await pages({"field_id": "components"})
            assert values == expected_values
            assert detail["paginationKind"] == "allowedValues"
            assert detail["options"]["components"]["defaultValue"] == [{"id": "exact-0"}]
            assert len(env.requests) == 1
    anyio.run(scenario)


@pytest.mark.parametrize("field", ["defaultValue", "allowedValues"])
def test_oversized_creation_metadata_errors_without_truncating_identifiers(env, field):
    item = {"id": "exact-" + "x" * 8000}
    env.metadata["components"][field] = [item]
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("list_options", {"kind": "create_fields", "issue_type": "Task",
                                                        "field_id": "components"})
            assert result.is_error and result.structured_content["error"]["status"] == 413
            assert "exact-" not in result.content[0].text
    anyio.run(scenario)
