"""Runs when the separately deployable display/freshness policy is integrated."""
import time

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

policy = pytest.importorskip("app.infra.cache_policy")


def test_actual_policy_ui_mcp_share_age_stale_refresh_and_mutation(build_env, monkeypatch):
    env = build_env()
    env.cache.configure(policy.CachePolicy(), revision=12)
    env.app.add_middleware(policy.CacheHeadersMiddleware, cache=env.cache)
    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app),
                                           base_url="http://127.0.0.1:4457",
                                           headers={"Authorization": "Bearer " + env.runtime.credentials.token()}) as http:
                async with Client(streamable_http_client(env.runtime.url, http_client=http)) as mcp:
                    created = await mcp.call_tool("create_task", {"body": {"type": "Task", "summary": "cache source",
                        "clientMutationId": "source:create:0001"}})
                    assert not created.is_error, created.structured_content
                    key = created.structured_content["data"]["key"]
                    cache_key = f"issueview:{env.client.env}:{key}"
                    ui = await http.get(f"/api/ticket/{key}")
                    assert ui.status_code == 200 and ui.headers["x-ltm-cache-state"] == "fresh"
                    source = env.cache.metadata(cache_key)
                    before = len(env.calls)
                    warm = await mcp.call_tool("get_ticket", {"key": key})
                    observed = warm.structured_content["cache"]
                    assert observed["available"] and observed["state"] == "fresh"
                    assert observed["policyRevision"] == 12
                    assert observed["fetchedAt"] == source["fetchedAt"]
                    assert len(env.calls) == before

                    fetched = time.time() - 700
                    with env.cache._lock:
                        env.cache._conn.execute("UPDATE cache SET fetched_at=? WHERE key=?", (fetched, cache_key))
                        env.cache._conn.commit()
                    monkeypatch.setattr(env.cache, "skip_producer", lambda: True)
                    stale = await mcp.call_tool("get_ticket", {"key": key})
                    assert stale.structured_content["data"]["summary"] == "cache source"
                    observed = stale.structured_content["cache"]
                    assert observed["state"] == "stale" and observed["fetchedAt"] == fetched
                    assert observed["freshUntil"] == fetched + 600 and observed["displayUntil"] == fetched + 86400
                    assert len(env.calls) == before

                    monkeypatch.setattr(env.cache, "skip_producer", lambda: False)
                    refreshed = await mcp.call_tool("get_ticket", {"key": key, "fresh": True})
                    assert not refreshed.is_error, refreshed.structured_content
                    assert refreshed.structured_content["cache"]["state"] == "fresh"
                    assert refreshed.structured_content["cache"]["fetchedAt"] > fetched
                    written = await mcp.call_tool("update_description", {"key": key,
                        "html": "<p>fresh after MCP write</p>", "client_mutation_id": "source:write:0001"})
                    assert not written.is_error, written.structured_content
                    ui_after = await http.get(f"/api/ticket/{key}")
                    assert "fresh after MCP write" in ui_after.json()["descriptionHtml"]
                    assert ui_after.headers["x-ltm-cache-state"] == "fresh"
    anyio.run(scenario)


def test_parallel_unified_search_keeps_real_confluence_cache_provenance(build_env):
    from datetime import date
    env = build_env()
    env.cache.configure(policy.CachePolicy(), revision=13)
    env.runtime.settings.search_confluence_spaces = ["DL"]
    env.store.confluence = {"u01": [{"title": "Source guide", "space": "DL", "body": "source body", "date": date.today()}]}
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("search_workspace", {"query": "Source", "sources": ["confluence"]})
            assert not result.is_error, result.structured_content
            assert result.structured_content["data"]["confluence"]["items"]
            assert result.structured_content["cache"]["available"]
            assert result.structured_content["cache"]["state"] == "fresh"
            before = len(env.calls)
            repeat = await mcp.call_tool("search_workspace", {"query": "Source", "sources": ["confluence"]})
            assert repeat.structured_content["cache"]["fetchedAt"] == result.structured_content["cache"]["fetchedAt"]
            assert len(env.calls) == before
    anyio.run(scenario)


@pytest.mark.parametrize("surface", ["mcp", "http"])
@pytest.mark.parametrize("upstream_state", ["done", "offline"])
def test_stale_display_status_cannot_authorize_field_write(build_env, monkeypatch, surface, upstream_state):
    from app.auth.base import UpstreamUnavailable
    env = build_env()
    env.cache.configure(policy.CachePolicy(), revision=14)
    created = env.client.create_child(None, "Task", "stale authorization")
    key = created["key"]
    initial = env.client.get_issue_light(key)
    assert initial["fields"]["status"]["statusCategory"]["key"] != "done"
    cache_key = f"issueL:{env.client.env}:{key}"
    with env.cache._lock:
        env.cache._conn.execute("UPDATE cache SET fetched_at=? WHERE key=?", (time.time() - 700, cache_key))
        env.cache._conn.commit()
    # The old Open ticket is still available for display, but cannot authorize a PUT.
    assert env.cache.get_alive(cache_key) is not None
    original = env.provider.get_json
    def changed_upstream(path, *args, **kwargs):
        if path == f"/rest/api/2/issue/{key}":
            if upstream_state == "offline":
                raise UpstreamUnavailable("fixture offline while checking status")
            result = original(path, *args, **kwargs)
            result["fields"]["status"] = {"id": "6", "name": "Done", "statusCategory": {"key": "done"}}
            return result
        return original(path, *args, **kwargs)
    monkeypatch.setattr(env.provider, "get_json", changed_upstream)
    before = sum(method == "put" for method, _, _ in env.calls)
    async def scenario():
        if surface == "mcp":
            async with Client(env.runtime.server) as mcp:
                result = await mcp.call_tool("update_fields", {"key": key, "body": {
                    "summary": "must not write", "clientMutationId": "strict:status:0001"}})
                assert result.is_error, result.structured_content
                if upstream_state == "done":
                    assert result.structured_content["error"]["status"] == 409
        else:
            from app.services.ticket_models import FieldsBody
            from app.routes.ticket_commands import update_fields_response
            if upstream_state == "offline":
                with pytest.raises(UpstreamUnavailable):
                    update_fields_response(env.client, env.runtime.settings, key, FieldsBody(summary="must not write"))
            else:
                response = update_fields_response(env.client, env.runtime.settings, key, FieldsBody(summary="must not write"))
                assert response.status_code == 409
    anyio.run(scenario)
    assert sum(method == "put" for method, _, _ in env.calls) == before
    assert env.store.issues[key]["summary"] == "stale authorization"


def test_write_authorization_reuses_fresh_issue_cache(build_env, monkeypatch):
    from app.services.ticket_models import FieldsBody
    env = build_env()
    env.cache.configure(policy.CachePolicy(), revision=14)
    key = env.client.create_child(None, "Task", "fresh authorization")["key"]
    env.client.get_issue_light(key)
    original = env.provider.get_json
    def fresh_cache(path, *args, **kwargs):
        assert path != f"/rest/api/2/issue/{key}", "fresh authorization should not force an issue refetch"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(env.provider, "get_json", fresh_cache)
    result = env.runtime.commands.update_fields(key, FieldsBody(summary="fresh allowed"))
    assert result["ok"] and env.store.issues[key]["summary"] == "fresh allowed"
