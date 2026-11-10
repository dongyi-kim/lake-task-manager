"""Explicit freshness travels through the real SDK and stays request scoped."""
import time

import anyio
from mcp import Client
import pytest

from app.infra.cache import CacheReadPolicyError
from app.infra.cache_policy import CachePolicy, current_read_policy, note_read
from app.local_mcp.read_metadata import read_result


def test_sdk_catalog_exposes_cache_mode_only_for_business_reads(build_env):
    env = build_env()
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            catalog = {tool.name: tool for tool in (await mcp.list_tools()).tools}
            for name in ("get_ticket", "search_issues", "get_evidence_page", "get_descendants",
                         "search_users", "get_hierarchy_options", "read_confluence_document",
                         "read_confluence_attachment", "get_confluence_restrictions"):
                schema = catalog[name].input_schema
                mode = schema["properties"]["cache_mode"]
                assert mode["default"] is None and "cache_mode" not in schema.get("required", [])
                assert mode["anyOf"][0]["enum"] == ["live", "hot", "warm", "cool", "cold"]
                assert schema["additionalProperties"] is False
            for name in ("get_auth_status", "authenticate", "get_mcp_health", "get_workspace_config",
                         "get_llm_settings", "get_content_guide", "create_task", "update_description"):
                assert "cache_mode" not in catalog[name].input_schema["properties"]
            before = len(env.calls)
            for mode in ("unknown", 42, True, ["hot"]):
                result = await mcp.call_tool("get_ticket", {"key": "DL-1", "cache_mode": mode})
                assert result.is_error
            assert len(env.calls) == before
            write = await mcp.call_tool("create_task", {"cache_mode": "cold", "body": {
                "type": "Task", "summary": "must not write", "clientMutationId": "cache:reject:0001"}})
            assert write.is_error and len(env.calls) == before
            conflict = await mcp.call_tool("get_ticket", {"key": "DL-1", "fresh": True, "cache_mode": "warm"})
            assert conflict.is_error and conflict.structured_content["error"]["code"] == "invalid_request"
            assert len(env.calls) == before
    anyio.run(scenario)


def test_parallel_confluence_search_propagates_explicit_policy_and_keeps_hit_cache_only(build_env):
    from datetime import date
    env = build_env()
    env.cache.configure(CachePolicy(), revision=23)
    env.runtime.settings.search_confluence_spaces = ["DL"]
    env.store.confluence = {"u01": [{"title": "Source guide", "space": "DL", "body": "source body", "date": date.today()}]}
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            arguments = {"query": "Source", "sources": ["confluence"], "cache_mode": "hot"}
            initial = await mcp.call_tool("search_workspace", arguments)
            assert not initial.is_error, initial.structured_content
            before = len(env.calls)
            hit = await mcp.call_tool("search_workspace", arguments)
            assert not hit.is_error, hit.structured_content
            assert hit.structured_content["cache"]["compliant"]
            assert hit.structured_content["cache"]["requestedCacheMode"] == "hot"
            assert len(env.calls) == before
            live = await mcp.call_tool("search_workspace", {**arguments, "cache_mode": "live"})
            assert not live.is_error, live.structured_content
            assert live.structured_content["cache"]["upstreamValidated"]
            assert len(env.calls) > before
    anyio.run(scenario)


@pytest.mark.parametrize("mode,max_age", [("hot", 600), ("warm", 86400), ("cool", 604800), ("cold", 2592000)])
def test_valid_explicit_cache_hit_does_not_call_upstream_but_live_validates(build_env, mode, max_age):
    env = build_env()
    env.cache.configure(CachePolicy(), revision=22)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            initial = await mcp.call_tool("get_ticket", {"key": "DL-1"})
            assert not initial.is_error, initial.structured_content
            before = len(env.calls)
            hit = await mcp.call_tool("get_ticket", {"key": "DL-1", "cache_mode": mode})
            assert not hit.is_error, hit.structured_content
            meta = hit.structured_content["cache"]
            assert meta["requestedCacheMode"] == meta["effectiveCacheMode"] == mode
            assert meta["maxAgeSeconds"] == max_age and meta["compliant"]
            assert meta["upstreamValidated"] is False
            assert len(env.calls) == before
            live = await mcp.call_tool("get_ticket", {"key": "DL-1", "cache_mode": "live"})
            assert not live.is_error, live.structured_content
            assert live.structured_content["cache"]["upstreamValidated"]
            assert live.structured_content["cache"]["maxAgeSeconds"] == 0
            assert len(env.calls) > before
    anyio.run(scenario)


def test_worker_policy_is_isolated_and_preserves_oldest_decorative_source(build_env):
    env = build_env()
    from app.local_mcp.schemas.common import OpenModel
    class Probe(OpenModel):
        mode: str | None
    def policy_probe() -> dict:
        mode = current_read_policy().mode
        now = time.time()
        for fetched, kind in ((now - 20, "issue"), (now - 30, "epicmeta")):
            note_read({"state": "fresh", "fetchedAt": fetched, "freshUntil": now + 500,
                       "displayUntil": now + 1000, "kind": kind, "compliant": True,
                       "upstreamValidated": False})
        return {"mode": mode.value if mode else None}
    env.runtime.register(policy_probe, read_only=True, output_type=Probe)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            results = []
            async def call(mode):
                args = {} if mode is None else {"cache_mode": mode}
                results.append((mode, await mcp.call_tool("policy_probe", args)))
            async with anyio.create_task_group() as group:
                for mode in ("hot", "cold", None):
                    group.start_soon(call, mode)
            for mode, result in results:
                assert not result.is_error, result.structured_content
                assert result.structured_content["data"]["mode"] == mode
                meta = result.structured_content["cache"]
                if mode:
                    assert meta["fetchedAt"] == meta["oldestComponentFetchedAt"]
                else:
                    assert "requestedCacheMode" not in meta
    anyio.run(scenario)
    assert current_read_policy().mode is None


def test_policy_failure_is_retryable_503_and_unknown_sources_stay_unknown(build_env, monkeypatch):
    env = build_env()
    monkeypatch.setattr(env.client, "ticket_view", lambda *args, **kwargs: (_ for _ in ()).throw(CacheReadPolicyError("incomplete")))
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("get_ticket", {"key": "DL-1", "cache_mode": "live"})
            assert result.is_error
            assert result.structured_content["error"]["status"] == 503
            assert result.structured_content["error"]["retryable"] is True
    anyio.run(scenario)
    result = read_result(lambda: {}, {"cache_mode": "warm"}, None)
    assert result["cache"]["available"] is False and result["cache"]["state"] == "unknown"
    assert result["cache"]["compliant"] is False


def test_partial_explicit_read_cannot_claim_policy_compliance():
    def partial():
        note_read({"state": "fresh", "fetchedAt": 10, "freshUntil": 20, "displayUntil": 30,
                   "compliant": True, "upstreamValidated": True})
        return {"complete": False}
    result = read_result(partial, {"cache_mode": "live"}, None)
    assert result["partial"] and result["cache"]["compliant"] is False


def test_missing_requested_body_cannot_claim_explicit_policy_compliance():
    def missing_body():
        note_read({"state": "fresh", "fetchedAt": 10, "freshUntil": 20, "displayUntil": 30,
                   "compliant": True, "upstreamValidated": True})
        return {"complete": True, "descriptionRequested": True, "descriptionMissingKeys": ["DL-1"]}
    result = read_result(missing_body, {"cache_mode": "live"}, None)
    assert result["partial"] and result["cache"]["compliant"] is False
    # Existing omitted envelope defaults remain compatible.
    assert read_result(missing_body, {}, None)["partial"] is False


def test_authentication_partial_search_cannot_claim_policy_compliance(build_env, monkeypatch):
    from app.domain import search
    env = build_env()
    def check(service, **kwargs):
        return {"service": service, "authenticated": service == "jira", "needLogin": service != "jira",
                "pending": False, "message": "fixture auth"}
    def jira(*args, **kwargs):
        now = time.time()
        note_read({"state": "fresh", "fetchedAt": now, "freshUntil": now + 600,
                   "displayUntil": now + 86400, "compliant": True, "upstreamValidated": True})
        return {"items": []}
    monkeypatch.setattr(env.runtime.authentication, "check", check)
    monkeypatch.setattr(search, "_search_jira", jira)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("search_workspace", {"query": "test", "cache_mode": "hot"})
            assert not result.is_error, result.structured_content
            assert result.structured_content["partial"]
            assert result.structured_content["cache"]["compliant"] is False
    anyio.run(scenario)


def test_evidence_continuation_preserves_explicit_mode_inside_byte_budget(build_env):
    import json
    env = build_env()
    key = env.client.create_child(None, "Task", "policy continuation")["key"]
    env.provider.post_json(f"/rest/api/2/issue/{key}/comment", json_body={"body": "한글🙂 근거 " * 1500})
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            first = await mcp.call_tool("get_evidence_page", {"key": key, "cache_mode": "warm", "max_results": 5})
            assert not first.is_error, first.structured_content
            data = first.structured_content["data"]
            assert len(json.dumps(data, ensure_ascii=False).encode("utf-8")) <= 8192
            arguments = data["nextCall"]["arguments"]
            assert arguments["cache_mode"] == "warm"
            assert arguments["max_results"] == 5 and arguments["record_offset"] > 0
            following = await mcp.call_tool("get_evidence_page", arguments)
            assert not following.is_error, following.structured_content
            assert following.structured_content["cache"]["requestedCacheMode"] == "warm"
            assert len(json.dumps(following.structured_content["data"], ensure_ascii=False).encode("utf-8")) <= 8192
    anyio.run(scenario)
