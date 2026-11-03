"""Real localhost MCP transport over a private full mock world; never calls an LLM."""
import json
import socket

import anyio
import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from tools.codex_mcp_eval.fixture import EvaluationFixture


def test_full_world_sdk_reads_share_ui_cache_and_reject_every_write(tmp_path):
    with EvaluationFixture(tmp_path / "fixture", port=0, today="2026-11-04") as fixture:
        url, token = fixture.url, fixture.token
        known = {f"DL-{number}" for number in range(9041, 9048)} | {"DL-9062", "DL-9090"}
        assert known <= set(fixture.store.issues)
        truth = fixture.ground_truth()
        assert truth["today"] == "2026-11-04" and truth["personId"] == "skcc.i2011"
        assert len(truth["personOpenKeys"]) == 21
        assert truth["currentUserId"] == "test.ui01" and truth["currentUserOpenKeys"]
        conformance = fixture.assert_conformance()
        assert conformance["parentJql"]["normal"]["keys"] == ["DL-9093", "DL-9094", "DL-9095"]
        assert conformance["parentJql"]["denied"]["keys"] == ["DL-9093", "DL-9094"]
        assert not conformance["cacheWarmed"]
        assert not fixture.store.denied_issue_keys
        assert fixture.cache.get(f"issueL:{fixture.client.env}:DL-9090") is None
        assert fixture.upstream_calls == [], "ground truth must not warm the measured cache"
        with httpx.Client() as http:
            assert http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401
            ui = http.get(url.replace("/mcp", "/api/ticket/DL-9090"))
            assert ui.status_code == 200
        before = len(fixture.upstream_calls)
        async def scenario():
            async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + token}) as http:
                async with Client(streamable_http_client(url, http_client=http)) as mcp:
                    names = {tool.name for tool in (await mcp.list_tools()).tools}
                    assert {"get_ticket", "search_workspace", "search_issues"} <= names
                    assert not {"create_task", "update_fields", "get_workspace_config"} & names
                    result = await mcp.call_tool("get_ticket", {"key": "DL-9090"})
                    assert not result.is_error and result.structured_content["data"]["key"] == "DL-9090"
                    assert len(fixture.upstream_calls) == before
                    rejected = await mcp.call_tool("create_task", {"body": {
                        "type": "Task", "summary": "must never exist", "clientMutationId": "eval:denied:0001"}})
                    assert rejected.is_error
        anyio.run(scenario)
        assert fixture.summary()["worldUnchanged"] and fixture.summary()["providerStoreUnchanged"]
        assert fixture.summary()["toolErrors"] == 1
        assert token not in json.dumps(fixture.tool_calls, ensure_ascii=False)
        assert all("authorization" not in row for row in fixture.upstream_calls)
        assert fixture.tool_calls[0]["result"]["data"]["key"] == "DL-9090"
        assert len(fixture.catalog_sha256) == 64
        port = fixture.port
    assert not (tmp_path / "fixture" / "mcp-secret.json").exists()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))


def test_cases_use_fresh_world_store_and_cache_and_busy_port_is_not_changed(tmp_path):
    with EvaluationFixture(tmp_path / "first", port=0) as first:
        before = first.fingerprint()
        first.client.ticket_view("DL-9090")
        first.store.issues["DL-9090"]["summary"] = "intentional isolation canary"
        assert not first.summary()["providerStoreUnchanged"]
        busy_port = first.port
        # A live fixture cannot be replaced or create a second mutable world in this process.
        with pytest.raises(RuntimeError, match="sequential"):
            with EvaluationFixture(tmp_path / "overlap", port=0):
                pass
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", busy_port))
        listener.listen()
        with pytest.raises(OSError):
            with EvaluationFixture(tmp_path / "busy", port=busy_port):
                pass
    with EvaluationFixture(tmp_path / "second", port=0) as second:
        assert second.fingerprint() == before
        assert second.upstream_calls == []
        assert second.cache.get(f"issueview:{second.client.env}:DL-9090") is None
        assert second.store.issues["DL-9090"]["summary"] != "intentional isolation canary"


def test_partial_child_and_missing_ticket_are_visible_through_actual_builders(tmp_path):
    with EvaluationFixture(tmp_path / "partial", port=0, fault="partial") as fixture:
        fixture.assert_conformance()
        assert fixture.store.denied_issue_keys == {"DL-9095"}
        assert fixture.upstream_calls == fixture.tool_calls == []
        async def scenario():
            async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + fixture.token}) as http:
                async with Client(streamable_http_client(fixture.url, http_client=http)) as mcp:
                    for _ in range(2):
                        result = await mcp.call_tool("get_children", {"key": "DL-9090"})
                        assert not result.is_error, result.structured_content
                        assert result.structured_content["partial"] and result.structured_content["missing"] > 0
                    missing = await mcp.call_tool("get_ticket", {"key": "DL-999999"})
                    assert missing.is_error and not missing.structured_content["ok"]
                    denied = await mcp.call_tool("get_ticket", {"key": "DL-9095", "fresh": True})
                    assert denied.is_error, "full description reads must not bypass the child permission fault"
                    search = await mcp.call_tool("search_issues", {"jql": 'parent = "DL-9090" ORDER BY key ASC',
                                                                  "max_results": 20})
                    assert not search.is_error, search.structured_content
                    assert search.structured_content["data"]["total"] == 2
                    assert {row["key"] for row in search.structured_content["data"]["issues"]} == {"DL-9093", "DL-9094"}
        anyio.run(scenario)
        assert fixture.summary()["worldUnchanged"] and fixture.summary()["providerStoreUnchanged"]
