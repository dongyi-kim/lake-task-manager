"""Best-effort Epic/VIT trees must keep omissions visible, including repeated reads."""
import time

import anyio
import pytest
from mcp import Client

from app.auth.base import PermissionDenied


@pytest.mark.parametrize("tool,parent_type,child_type,projection", [
    ("get_epic", "Epic", "Task", "tree"),
    ("get_vit_detail", "Epic", "Task", "tree"),
    ("get_children", "Task", "Sub-task", "children"),
])
def test_real_tree_partial_reads_remain_retryable_and_recover(build_env, monkeypatch, tool, parent_type, child_type, projection):
    env = build_env()
    parent = env.client.create_child(None, parent_type, "partial root", labels=["PMO_VIT"])["key"]
    child = env.client.create_child(parent, child_type, "visible after recovery")["key"]
    original = env.client.get_issue_light
    blocked = True

    def read(key, *args, **kwargs):
        if key == child and blocked:
            raise PermissionDenied("child temporarily invisible")
        return original(key, *args, **kwargs)

    monkeypatch.setattr(env.client, "get_issue_light", read)

    async def scenario():
        nonlocal blocked
        async with Client(env.runtime.server) as mcp:
            for _ in range(2):
                result = await mcp.call_tool(tool, {"key": parent})
                assert not result.is_error, result.structured_content
                assert result.structured_content["partial"]
                assert result.structured_content["missing"] > 0
                assert result.structured_content["data"][projection] == []
            # Neither a cold incomplete tree nor its enclosing VIT aggregate is cached.
            for prefix in ("epic_tree:", "vit_tree:", "vit_build:", "children:"):
                assert not env.cache.entries_by_prefix(prefix)
            blocked = False
            recovered = await mcp.call_tool(tool, {"key": parent})
            assert not recovered.is_error, recovered.structured_content
            assert not recovered.structured_content["partial"]
            assert recovered.structured_content["missing"] == 0
            assert child in {node["key"] for node in recovered.structured_content["data"][projection]}
            # The next unrelated call in the same worker pool does not inherit miss_count.
            clean = await mcp.call_tool("get_user", {})
            assert not clean.structured_content["partial"]
    anyio.run(scenario)


def test_partial_refresh_keeps_complete_epic_cache_and_original_age(build_env, monkeypatch):
    env = build_env()
    parent = env.client.create_child(None, "Epic", "complete root")["key"]
    child = env.client.create_child(parent, "Task", "cached child")["key"]
    complete = env.client.epic_tree(parent)
    key = f"epic_tree:{env.client.env}:{parent}"
    fetched = time.time() - env.client.s.cache_ttl_seconds - 1
    with env.cache._lock:
        env.cache._conn.execute("UPDATE cache SET fetched_at=? WHERE key=?", (fetched, key))
        env.cache._conn.commit()
    original = env.client.get_issue_light

    def read(issue_key, *args, **kwargs):
        if issue_key == child:
            raise PermissionDenied("child temporarily invisible")
        return original(issue_key, *args, **kwargs)

    monkeypatch.setattr(env.client, "get_issue_light", read)
    env.client.miss_begin()
    assert env.client.epic_tree(parent) == complete
    assert env.client.miss_count() > 0
    assert env.cache.get_stale_at(key) == (complete, fetched)


def test_parallel_tree_workers_propagate_omissions_to_parent_read(build_env, monkeypatch):
    env = build_env()
    monkeypatch.setattr(env.provider, "supports_parallel", True)
    env.client.miss_begin()

    def build(number):
        env.client.miss_add(number)
        return number

    assert env.client._pmap([1, 2, 3], build) == [1, 2, 3]
    assert env.client.miss_count() == 6
