import json

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


def test_sdk_http_catalog_cache_reuse_and_pagination(build_env):
    env = build_env()
    # Prime the same issue/snapshot through the ordinary client used by the UI.
    query = 'project in ("DL") AND (project = DL) ORDER BY key ASC'
    direct = env.client.search_issues_page(query, max_results=2)
    before = len(env.calls)
    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app),
                                           base_url=f"http://127.0.0.1:{env.runtime.port}") as http:
                copied = await http.post("/api/mcp/credentials", json={"format": "http"}, headers={"X-LTM-Local": "1"})
                assert copied.status_code == 200, copied.json()
                config = json.loads(copied.json()["text"])["mcpServers"]["lake-task-manager"]
                http.headers.update(config["headers"])
                async with Client(streamable_http_client(config["url"], http_client=http)) as mcp:
                    catalog = (await mcp.list_tools()).tools
                    names = {tool.name for tool in catalog}
                    assert {"search_issues", "create_task", "delete_ticket", "add_attachment"} <= names
                    assert len(names) == 27
                    assert next(t for t in catalog if t.name == "get_ticket").annotations.read_only_hint
                    assert not next(t for t in catalog if t.name == "create_task").annotations.read_only_hint
                    result = await mcp.call_tool("search_issues", {"jql": "project = DL ORDER BY key ASC", "max_results": 2})
                    assert not result.is_error, result
                    first = result.structured_content["data"]
                    assert [i["key"] for i in first["issues"]] == [i["key"] for i in direct["issues"]]
                    assert len(env.calls) == before
                    second = await mcp.call_tool("search_issues", {"jql": "project = DL ORDER BY key ASC",
                        "max_results": 2, "start_at": first["nextStartAt"], "snapshot_id": first["snapshotId"]})
                    keys = [i["key"] for i in first["issues"] + second.structured_content["data"]["issues"]]
                    assert len(set(keys)) == 4
                    denied = await mcp.call_tool("get_ticket", {"key": "OUTSIDE-1"})
                    assert denied.is_error
                    invalid = await mcp.call_tool("add_comment", {"key": "DL-1", "html": "x", "client_mutation_id": ""})
                    assert invalid.is_error
                    for name, arguments, expected in (
                        ("get_history", {"key": first["issues"][0]["key"]}, "timeline"),
                        ("get_children", {"key": first["issues"][0]["key"]}, "children"),
                        ("get_documents", {"key": first["issues"][0]["key"]}, "documents"),
                        ("get_user", {"user_id": "u01"}, "id"),
                        ("search_users", {"query": ""}, "users"),
                        ("get_workload", {"user_id": "u01"}, "tickets"),
                    ):
                        read = await mcp.call_tool(name, arguments)
                        assert not read.is_error, read.structured_content
                        assert expected in read.structured_content["data"]
                        assert "cache" in read.structured_content and "partial" in read.structured_content
    anyio.run(scenario)


def test_management_and_mcp_boundaries_rotation_disable_and_actual_peer(build_env):
    env = build_env()
    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app), base_url="http://127.0.0.1:4457") as http:
                status = (await http.get("/api/mcp/settings")).json()
                assert status["ready"] and status["url"] == env.runtime.url
                token = env.runtime.credentials.token()
                assert token not in json.dumps(status)
                assert (await http.post("/mcp", json={})).status_code == 401
                assert (await http.post("/api/mcp/credentials", json={"format": "token"})).status_code == 403
                headers = {"X-LTM-Local": "1"}
                export = await http.post("/api/mcp/credentials", json={"format": "http"}, headers=headers)
                assert export.headers["cache-control"] == "no-store"
                assert json.loads(export.json()["text"])["mcpServers"]["lake-task-manager"]["headers"]["Authorization"] == "Bearer " + token
                for other in ({"Host": "evil.example"}, {"Origin": "https://evil.example"},
                              {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}):
                    denied = await http.post("/api/mcp/credentials", json={"format": "token"}, headers={**headers, **other})
                    assert denied.status_code == 403
                await http.post("/api/mcp/rotate", json={}, headers=headers)
                assert (await http.post("/mcp", json={}, headers={"Authorization": "Bearer " + token})).status_code == 401
                await http.put("/api/mcp/settings", json={"enabled": False}, headers=headers)
                assert (await http.get("/api/mcp/settings")).json()["ready"] is False
                assert (await http.post("/mcp", json={}, headers={"Authorization": "Bearer " + env.runtime.credentials.token()})).status_code == 503
                # Rotation is available while disabled; no implicit re-enable.
                await http.post("/api/mcp/rotate", json={}, headers=headers)
                assert not env.runtime.credentials.enabled
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app, client=("10.0.0.2", 123)),
                                           base_url="http://127.0.0.1:4457") as remote:
                spoofed = {"X-Forwarded-For": "127.0.0.1", "X-LTM-Local": "1"}
                assert (await remote.post("/api/mcp/credentials", json={"format": "token"}, headers=spoofed)).status_code == 403
                assert (await remote.get("/mcp", headers={"Authorization": "Bearer " + env.runtime.credentials.token(), **spoofed})).status_code == 403
    anyio.run(scenario)


def test_missing_or_corrupt_token_never_grants_access(tmp_path):
    from app.local_mcp.security import Credentials
    credentials = Credentials(tmp_path / "secret.json")
    original = credentials.token()
    assert original not in repr(credentials)
    assert not credentials.accepts("") and not credentials.accepts("Bearer invalid")
    assert Credentials(credentials.path).token() == original
    credentials.path.write_text('{"token": "short", "enabled": true}', encoding="utf-8")
    with pytest.raises(ValueError, match="인증 파일"):
        Credentials(credentials.path).accepts("Bearer short")
