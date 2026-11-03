import base64
import time
from datetime import date

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.auth.base import PermissionDenied, SessionExpired, UpstreamUnavailable


def _documents(env):
    env.runtime.settings.search_confluence_spaces = ["DL"]
    env.runtime.settings.confluence_base = "http://confluence.test"
    env.store.confluence = {"u01": [
        {"title": f"MCP guide {number}", "space": "DL", "body": f"body {number} " + "x" * 50, "date": date.today()}
        for number in range(3)
    ] + [{"title": "MCP secret", "space": "OTHER", "body": "private body", "date": date.today()}]}
    return env.store.confluence_pages()


def test_real_sdk_workspace_search_docs_full_body_and_shared_ui_cache(build_env):
    env = build_env()
    documents = _documents(env)
    allowed = next(row for row in documents if row["space"] == "DL")
    forbidden = next(row for row in documents if row["space"] == "OTHER")
    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app),
                                           base_url="http://127.0.0.1:4457",
                                           headers={"Authorization": "Bearer " + env.runtime.credentials.token()}) as http:
                async with Client(streamable_http_client(env.runtime.url, http_client=http)) as mcp:
                    async def call(name, arguments):
                        result = await mcp.call_tool(name, arguments)
                        assert not result.is_error, result.structured_content
                        return result.structured_content["data"]
                    ui = await http.get("/api/search", params={"q": "MCP", "only": "confluence", "limit": 20})
                    before = len(env.calls)
                    workspace = await call("search_workspace", {"query": "MCP", "sources": ["confluence"]})
                    assert workspace["confluence"] == ui.json()["confluence"]
                    assert len(env.calls) == before  # Same UI query cache, no separate client or MCP cache.
                    first = await call("search_confluence_documents", {"query": "MCP", "max_results": 2})
                    second = await call("search_confluence_documents", {
                        "query": "MCP", "max_results": 2, "start_at": first["nextStartAt"]})
                    ids = [row["id"] for row in first["documents"] + second["documents"]]
                    assert len(ids) == len(set(ids)) == 3 and not second["hasMore"]
                    assert str(forbidden["id"]) not in ids
                    # Existing internal document consumer primes the exact body projection used by MCP.
                    env.client.confluence_page(str(allowed["id"]), expand="body.storage,space,version")
                    before = len(env.calls)
                    parts, offset = [], 0
                    while True:
                        chunk = await call("read_confluence_document", {
                            "page": str(allowed["id"]), "offset": offset, "max_chars": 17})
                        parts.append(chunk["content"])
                        if not chunk["hasMore"]:
                            break
                        offset = chunk["nextOffset"]
                    assert "".join(parts) == allowed["body"]
                    assert len(env.calls) == before
                    for identifier in (str(forbidden["id"]), "http://evil.test/pages/123/x"):
                        denied = await mcp.call_tool("read_confluence_document", {"page": identifier})
                        assert denied.is_error and "private body" not in str(denied.structured_content)
                    env.runtime.settings.search_confluence_spaces = []
                    before = len(env.calls)
                    denied = await mcp.call_tool("search_confluence_documents", {"query": "MCP"})
                    assert denied.is_error and len(env.calls) == before
    anyio.run(scenario)


def test_document_auth_and_incomplete_response_are_errors_and_not_cached(build_env, monkeypatch):
    env = build_env()
    page = _documents(env)[0]
    original = env.client._conf_get_json
    env.runtime.classify_error = lambda _exc: pytest.fail("Confluence failure must not mark the Jira session dead")
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            def fail(*_args, **_kwargs):
                raise SessionExpired("Confluence login required")
            monkeypatch.setattr(env.client, "_conf_get_json", fail)
            for tool, arguments in (("read_confluence_document", {"page": str(page["id"])}),
                                    ("search_confluence_documents", {"query": "MCP"})):
                result = await mcp.call_tool(tool, arguments)
                assert result.is_error and result.structured_content["error"]["code"] == "authentication_required"
                assert result.structured_content["error"]["service"] == "confluence"
            monkeypatch.setattr(env.client, "_conf_get_json", lambda *_args, **_kwargs: {"id": str(page["id"])})
            result = await mcp.call_tool("read_confluence_document", {"page": str(page["id"])})
            assert result.is_error and result.structured_content["error"]["code"] == "upstream_unavailable"
            assert not env.cache.entries_by_prefix("confpage:") and not env.cache.entries_by_prefix("confsearch:")
            monkeypatch.setattr(env.client, "_conf_get_json", original)
            result = await mcp.call_tool("read_confluence_document", {"page": str(page["id"])})
            assert not result.is_error and result.structured_content["data"]["content"] == page["body"]
    anyio.run(scenario)


def test_description_attachment_roundtrip_and_dashboard_access(build_env):
    env = build_env()
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            async def call(name, arguments):
                result = await mcp.call_tool(name, arguments)
                assert not result.is_error, result.structured_content
                return result.structured_content["data"]
            task = await call("create_task", {"body": {"type": "Task", "summary": "body editor",
                "descriptionHtml": "<p>Initial description</p>", "clientMutationId": "workspace:create:0001"}})
            key = task["key"]
            env.client.ticket_view(key)
            await call("update_description", {"key": key, "html": "<p>Changed description</p>",
                                               "client_mutation_id": "workspace:description:0001"})
            assert "Changed description" in env.client.ticket_view(key)["descriptionHtml"]
            attachment = await call("add_attachment", {"key": key, "filename": "bytes.txt",
                "base64_data": base64.b64encode(b"actual bytes").decode(), "client_mutation_id": "workspace:upload:0001"})
            parts, offset = [], 0
            while True:
                chunk = await call("read_attachment", {"key": key, "attachment_id": attachment["id"],
                                                       "offset": offset, "max_bytes": 4})
                parts.append(base64.b64decode(chunk["base64"]))
                if not chunk["hasMore"]:
                    break
                offset = chunk["nextOffset"]
            assert b"".join(parts) == b"actual bytes"
            await call("get_field_history", {"key": key})
            await call("get_ticket_context", {"key": key})
            before = len(env.calls)
            for tool, arguments in (("get_dashboard", {"kind": "wbs"}),
                                    ("get_dashboard", {"kind": "workload", "module": "private"}),
                                    ("get_activity", {"user_id": "u02"})):
                assert (await mcp.call_tool(tool, arguments)).is_error
            assert len(env.calls) == before
    anyio.run(scenario)


def test_confluence_cache_partitions_users_and_removes_revoked_content(build_env, monkeypatch):
    env = build_env()
    document = _documents(env)[0]
    identifier = str(document["id"])
    first = env.client.confluence_page(identifier, strict=True)
    before = len(env.calls)
    assert env.client.confluence_page(identifier, strict=True) == first and len(env.calls) == before
    monkeypatch.setattr(env.client, "current_user", lambda: {"id": "another-user"})
    assert env.client.confluence_page(identifier, strict=True) == first and len(env.calls) > before
    with env.cache._lock:
        env.cache._conn.execute("UPDATE cache SET fetched_at=? WHERE key LIKE 'confpage:%'", (time.time() - 700,))
        env.cache._conn.commit()
    def denied(*_args, **_kwargs):
        raise PermissionDenied("Confluence access revoked")
    monkeypatch.setattr(env.client, "_conf_get_json", denied)
    with pytest.raises(PermissionDenied):
        env.client.confluence_page(identifier, strict=True)
    with pytest.raises(PermissionDenied):
        env.client.confluence_page(identifier, strict=True)


@pytest.mark.parametrize("payload", [
    {"results": [], "partial": True}, {"results": [], "errors": ["failed page"]},
    {"results": [], "error": "failed page"}, {"results": [], "complete": False},
    {"results": [{"content": {"id": "1", "title": "space expansion absent"}}]},
    {"results": [{}]},
])
def test_incomplete_confluence_search_requires_space_expansion_and_is_not_cached(build_env, monkeypatch, payload):
    env = build_env()
    requested = []
    def response(_url, params=None):
        requested.append(params)
        return payload
    monkeypatch.setattr(env.client, "_conf_get_json", response)
    with pytest.raises(UpstreamUnavailable):
        env.client.confluence_search('space = "DL"')
    assert requested[0]["expand"] == "content.space,content.version"
    assert not env.cache.entries_by_prefix("confsearch:")
    monkeypatch.setattr(env.client, "_conf_get_json", lambda *_args, **_kwargs: {
        "results": [{"content": {"id": "1", "space": {"key": "DL"}}}]})
    assert env.client.confluence_search('space = "DL"')["results"]


def test_late_confluence_permission_failure_cannot_erase_newer_cache(build_env, monkeypatch):
    env = build_env()
    identifier = str(_documents(env)[0]["id"])
    original = env.client.confluence_page(identifier, strict=True)
    cache_key = next(iter(env.cache.entries_by_prefix("confpage:")))
    with env.cache._lock:
        env.cache._conn.execute("UPDATE cache SET fetched_at=? WHERE key=?", (time.time() - 700, cache_key))
        env.cache._conn.commit()
    replacement = {**original, "title": "newer authorized response"}
    def late_denial(*_args, **_kwargs):
        env.cache.invalidate(cache_key)  # Another generation has superseded the started request.
        env.cache.set(cache_key, replacement, 600)
        raise PermissionDenied("old request denied")
    monkeypatch.setattr(env.client, "_conf_get_json", late_denial)
    with pytest.raises(PermissionDenied):
        env.client.confluence_page(identifier, strict=True)
    assert env.cache.get(cache_key) == replacement
