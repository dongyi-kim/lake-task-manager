"""Actual hierarchy transport and cached parent provenance obey the request's age policy."""
import anyio
import pytest
from mcp import Client

from app.infra.cache_policy import CachePolicy, read_scope
from app.jira.hierarchy_service import _descendant_children, get_descendants


def _hierarchy(env):
    env.client._reprime = lambda *_args, **_kwargs: None
    root = env.client.create_child(None, "Task", "Freshness root")["key"]
    for index in range(14):
        env.client.create_child(root, "Sub-task", f"Child {index}")
    env.cache.invalidate()
    return root


@pytest.mark.parametrize("mode", ["live", "hot", "warm", "cool"])
def test_sdk_descendants_accepts_validated_upstream_membership(build_env, mode):
    env = build_env()
    root = _hierarchy(env)
    env.cache.configure(CachePolicy())
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("get_descendants", {"root_key": root, "cache_mode": mode})
            assert not result.is_error
            data = result.structured_content
            assert data["data"]["complete"] and data["data"]["total"] == 15
            assert data["cache"]["compliant"]
    anyio.run(scenario)


def test_live_child_membership_does_not_bless_an_unvalidated_cached_parent(build_env, monkeypatch):
    env = build_env()
    root = _hierarchy(env)
    old = env.client.get_issue_light(root)
    old["fields"]["subtasks"] = []
    env.cache.invalidate()
    env.cache.configure(CachePolicy())
    env.cache.set(f"issueL:{env.client.env}:{root}", old, 600)
    before = len(env.calls)
    with read_scope(cache_mode="live") as sources:
        children, refreshed = _descendant_children(env.client, root, old, {"DL"})
    assert len(env.calls) > before
    assert len(children) == 14 and refreshed["fields"]["subtasks"]
    assert sources[-1]["upstreamValidated"] and sources[-1]["compliant"]


def test_cool_child_membership_preserves_age_of_policy_valid_parent(build_env, monkeypatch):
    env = build_env()
    root = _hierarchy(env)
    raw = env.client.get_issue_light(root)
    now = [100000.0]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now[0])
    env.cache.invalidate()
    env.cache.configure(CachePolicy())
    env.cache.set(f"issueL:{env.client.env}:{root}", raw, 600)
    now[0] += 2 * 86400
    before = len(env.calls)
    with read_scope(cache_mode="cool") as sources:
        children, _ = _descendant_children(env.client, root, raw, {"DL"})
    assert children and len(env.calls) == before
    assert sources[-1]["fetchedAt"] == 100000
    assert sources[-1]["compliant"] and not sources[-1]["upstreamValidated"]


def test_live_child_membership_cannot_publish_a_parent_changed_during_read(build_env, monkeypatch):
    from app.infra.cache import CacheReadPolicyError
    env = build_env()
    root = _hierarchy(env)
    raw = env.client.get_issue_light(root)
    env.cache.invalidate()
    env.cache.configure(CachePolicy())
    original = env.provider.get_json
    reads = []
    def racing(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == f"/rest/api/2/issue/{root}":
            reads.append(1)
            env.cache.invalidate(f"issueL:{env.client.env}:{root}")
        return value
    monkeypatch.setattr(env.provider, "get_json", racing)
    with read_scope(cache_mode="live"), pytest.raises(CacheReadPolicyError):
        _descendant_children(env.client, root, raw, {"DL"})
    assert reads == [1, 1]
    assert env.cache.get(f"issueL:{env.client.env}:{root}") is None
    assert env.cache.get(env.client._direct_child_cache_key(root)) is None
