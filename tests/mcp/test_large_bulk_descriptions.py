"""Raw bulk bodies are physical pages, never a whole-result snapshot or truncation."""
import hashlib
from types import SimpleNamespace

import anyio
from mcp import Client
import pytest

from app.auth.base import QueryStateChanged
from app.infra.cache_policy import read_scope
from app.jira.body_search import BODY_PAGE_SIZE
from app.jira.jira_client import JiraClient


def install_source(env, *, total, body="complete body", server_cap=None):
    calls = []

    def get(path, *, params=None, **kwargs):
        assert path == "/rest/api/2/search", "single-ticket GET fan-out is forbidden"
        params = dict(params or {})
        calls.append(params)
        start = params.get("startAt", 0)
        size = min(params["maxResults"], server_cap or params["maxResults"])
        fields = params["fields"].split(",")
        return {"startAt": start, "maxResults": size, "total": total, "issues": [
            {"key": f"DL-{index + 1}", "fields": {"summary": f"Issue {index + 1}",
                **({"description": body} if "description" in fields or "*all" in fields else {})}}
            for index in range(start, min(start + size, total))]}

    env.provider.get_json = get
    return calls


def test_one_requested_description_does_not_fetch_or_snapshot_ten_thousand_candidates(build_env, monkeypatch):
    env = build_env()
    calls = install_source(env, total=10_000)
    def forbidden(*args, **kwargs):
        pytest.fail("body projection must not build or read normalized snapshots")
    monkeypatch.setattr(env.client, "_jql_snapshot", forbidden)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            args = {"jql": "project = DL ORDER BY key ASC", "include_description": True, "max_results": 1}
            result = await mcp.call_tool("search_issues", args)
            assert not result.is_error, result.structured_content
            page = result.structured_content["data"]
            assert page["total"] == 10_000 and page["returned"] == 1
            assert page["nextStartAt"] == 1 and page["hasMore"]
            assert page["snapshotId"] is None and page["snapshotFrozen"] is False
            assert len(calls) == 1 and calls[0]["maxResults"] == 1
            again = await mcp.call_tool("search_issues", args)
            assert not again.is_error and len(calls) == 1
            previous_cursor = await mcp.call_tool("search_issues", {**args, "snapshot_id": "old-snapshot"})
            assert previous_cursor.is_error and len(calls) == 1
            assert "first page" in previous_cursor.structured_content["error"]["message"]
            with env.cache._lock:
                keys = [row[0] for row in env.cache._conn.execute("SELECT key FROM cache")]
            assert not any(key.startswith(("jqlrow:", "jqlsnap:", "jqlleaf:")) for key in keys)
    anyio.run(scenario)


def test_large_description_pages_reject_oversized_page_then_recover_every_full_body(build_env):
    env = build_env()
    body = "source-start:" + "x" * (2 * 1024 * 1024) + ":source-end"
    expected = hashlib.sha256(body.encode()).digest()
    calls = install_source(env, total=11, body=body)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            args = {"jql": "project = DL ORDER BY key ASC", "include_description": True}
            too_large = await mcp.call_tool("search_issues", args)
            assert too_large.is_error and too_large.structured_content.get("data") is None
            assert "16 MiB" in too_large.structured_content["error"]["message"]
            assert len(calls) == 1 and calls[0]["maxResults"] == BODY_PAGE_SIZE
            seen, start = set(), 0
            while True:
                result = await mcp.call_tool("search_issues", {**args, "max_results": 5, "start_at": start})
                assert not result.is_error, result.structured_content
                page = result.structured_content["data"]
                assert page["returned"] <= 5 and page["descriptionMissingKeys"] == []
                for issue in page["issues"]:
                    assert hashlib.sha256(issue["fields"]["description"].encode()).digest() == expected
                    assert issue["key"] not in seen
                    seen.add(issue["key"])
                if not page["hasMore"]:
                    break
                assert page["nextStartAt"] == start + page["returned"]
                start = page["nextStartAt"]
            assert seen == {f"DL-{index}" for index in range(1, 12)}
            assert [call["startAt"] for call in calls] == [0, 0, 5, 10]
    anyio.run(scenario)


def test_known_key_batch_stops_at_aggregate_budget_without_false_missing_or_partial_success(build_env):
    env = build_env()
    body = "full-description:" + "y" * (2 * 1024 * 1024)
    calls = install_source(env, total=11, body=body, server_cap=5)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("get_tickets", {
                "keys": [f"DL-{index}" for index in range(1, 12)], "include_description": True})
            assert result.is_error and result.structured_content["ok"] is False
            assert result.structured_content.get("data") is None
            message = result.structured_content["error"]["message"]
            assert "batch exceeds 16 MiB" in message and "max_results=1" in message
            assert [call["startAt"] for call in calls] == [0, 5]
    anyio.run(scenario)


def test_small_body_batch_is_one_rest_search_and_reuses_shared_page_cache(build_env):
    env = build_env()
    calls = install_source(env, total=3)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            args = {"keys": ["DL-3", "DL-1", "DL-2"], "include_description": True}
            for _ in range(2):
                result = await mcp.call_tool("get_tickets", args)
                assert not result.is_error, result.structured_content
                data = result.structured_content["data"]
                assert data["complete"] and data["returned"] == 3 and data["missingKeys"] == []
                assert [row["key"] for row in data["issues"]] == args["keys"]
                assert all(row["fields"]["description"] == "complete body" for row in data["issues"])
            assert len(calls) == 1
    anyio.run(scenario)


def test_body_page_cache_obeys_read_modes_mutation_generation_and_source_age(build_env):
    env = build_env()
    calls = install_source(env, total=1)
    query, fields = "project = DL", "summary,description"
    with read_scope(cache_mode="hot") as cold_sources:
        first = env.client.search_issues_page(query, fields=fields, light=False)
    assert len(calls) == 1 and cold_sources
    with read_scope(cache_mode="hot") as warm_sources:
        warm = env.client.search_issues_page(query, fields=fields, light=False)
    assert warm["issues"] == first["issues"]
    assert first["execution"]["physicalQueryCount"] == 1
    assert warm["execution"]["physicalQueryCount"] == 0
    assert len(calls) == 1
    assert cold_sources[-1]["fetchedAt"] == warm_sources[-1]["fetchedAt"]
    with read_scope(cache_mode="live") as live_sources:
        env.client.search_issues_page(query, fields=fields, light=False)
    assert len(calls) == 2 and live_sources[-1]["upstreamValidated"]
    env.client.advance_jql_generation()
    env.client.search_issues_page(query, fields=fields, light=False)
    assert len(calls) == 3


def test_body_page_cache_separates_context_and_projection_and_preserves_explicit_order(build_env, monkeypatch):
    env = build_env()
    calls = install_source(env, total=1)
    env.client.search_issues_page("project = DL", fields="summary,description", light=False)
    assert calls[-1]["jql"].endswith("ORDER BY key ASC")
    env.client.search_issues_page("project = DL ORDER BY updated DESC", fields="summary,description", light=False)
    assert calls[-1]["jql"] == "project = DL ORDER BY updated DESC"
    env.client.search_issues_page("project = DL", fields="description", light=False)
    monkeypatch.setattr(env.client, "_jql_user_context", lambda: "different-scope")
    env.client.search_issues_page("project = DL", fields="summary,description", light=False)
    assert len(calls) == 4


def test_one_oversized_description_is_an_explicit_error_not_cached_or_truncated(build_env):
    env = build_env()
    calls = install_source(env, total=1, body="z" * (16 * 1024 * 1024))
    with pytest.raises(ValueError, match="get_ticket"):
        env.client.search_issues_page("project = DL", max_results=1, fields="description", light=False)
    assert len(calls) == 1 and calls[0]["maxResults"] == 1
    with env.cache._lock:
        assert not env.cache._conn.execute("SELECT key FROM cache WHERE key LIKE 'search:body:%'").fetchall()


def test_body_page_refuses_mutation_during_physical_fetch_and_never_publishes_stale_rows(build_env):
    env = build_env()
    calls = install_source(env, total=1)
    original = env.provider.get_json
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        env.client.advance_jql_generation()
        return result
    env.provider.get_json = changed
    with pytest.raises(QueryStateChanged):
        env.client.search_issues_page("project = DL", fields="summary,description", light=False)
    assert len(calls) == 1
    with env.cache._lock:
        assert not env.cache._conn.execute("SELECT key FROM cache WHERE key LIKE 'search:body:%'").fetchall()


def actor_provider(client, actor):
    calls = []
    class Provider:
        supports_parallel = False
        def get_json(self, path, *, params=None, **kwargs):
            assert path == "/rest/api/2/search"
            calls.append(actor[0])
            return {"startAt": 0, "maxResults": params["maxResults"], "total": 1,
                    "issues": [{"key": "DL-1", "fields": {"summary": "Actor scope",
                                                          "description": "body-for-" + actor[0]}}]}
        def close(self):
            pass
    provider = Provider()
    client._provider, client._provider_built = provider, True
    client._make_provider = lambda: provider
    client.current_user = lambda: {"id": actor[0]}
    return calls


def body_for(client, query="assignee = currentUser()"):
    return client.search_issues_page(query, fields="summary,description", light=False)["issues"][0]["fields"]["description"]


def test_body_cache_cannot_cross_actor_change_and_provider_reset(build_env):
    env, actor = build_env(), ["a"]
    calls = actor_provider(env.client, actor)
    assert body_for(env.client) == "body-for-a"
    assert body_for(env.client) == "body-for-a" and calls == ["a"]
    actor[0] = "b"
    env.client.reset_provider()
    assert body_for(env.client) == "body-for-b"
    assert calls == ["a", "b"]


def test_current_user_identity_changes_partition_body_pages_without_provider_replacement(build_env):
    env, actor = build_env(), ["a"]
    calls = actor_provider(env.client, actor)
    assert body_for(env.client) == "body-for-a"
    actor[0] = "b"
    assert body_for(env.client) == "body-for-b"
    assert calls == ["a", "b"]


def test_new_client_does_not_reuse_previous_permission_session_body_cache(build_env):
    env = build_env()
    actor_provider(env.client, ["a"])
    assert body_for(env.client, "project = DL") == "body-for-a"
    restarted = JiraClient(env.client.s, env.cache)
    calls = actor_provider(restarted, ["b"])
    assert body_for(restarted, "project = DL") == "body-for-b"
    assert calls == ["b"]


def test_inflight_provider_generation_change_refuses_and_does_not_cache_old_actor_body(build_env):
    env = build_env()
    actor_provider(env.client, ["a"])
    original = env.client.provider.get_json
    def replaced(*args, **kwargs):
        result = original(*args, **kwargs)
        with env.client._provider_lock:
            env.client._provider_generation += 1
        return result
    env.client.provider.get_json = replaced
    with pytest.raises(QueryStateChanged):
        body_for(env.client, "project = DL")
    with env.cache._lock:
        assert not env.cache._conn.execute("SELECT key FROM cache WHERE key LIKE 'search:body:%'").fetchall()


def test_silent_sso_revision_change_partitions_body_cache_without_identity_probe(build_env, monkeypatch):
    env, actor, revision = build_env(), ["a"], [1]
    calls = actor_provider(env.client, actor)
    env.client.env = "prod"
    monkeypatch.setattr(env.client, "sso_store", lambda: SimpleNamespace(revision=lambda service: (revision[0], None)))
    def forbidden():
        pytest.fail("ordinary JQL must not fetch /myself")
    monkeypatch.setattr(env.client, "current_user", forbidden)
    assert body_for(env.client, "project = DL") == "body-for-a"
    actor[0], revision[0] = "b", 2
    assert body_for(env.client, "project = DL") == "body-for-b"
    assert calls == ["a", "b"]
