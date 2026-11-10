from contextlib import contextmanager
from types import SimpleNamespace

import anyio
import pytest
from mcp import Client

from app.local_mcp import read_metadata


@pytest.mark.parametrize("states,expected", [
    (["fresh", "dead", "stale"], "dead"), (["fresh", "unrecognized"], "unknown"),
    (["fresh", "fresh"], "fresh"),
])
def test_worst_source_state_is_never_upgraded_to_fresh(monkeypatch, states, expected):
    @contextmanager
    def scope(strict=False, cache_mode=None):
        yield [{"state": state, "fetchedAt": 10, "freshUntil": 20, "displayUntil": 30} for state in states]
    monkeypatch.setattr(read_metadata, "read_scope", scope)
    assert read_metadata.read_result(lambda: {}, {}, SimpleNamespace())["cache"]["state"] == expected


def test_unobserved_reads_are_unknown_and_keep_partial_details(monkeypatch):
    monkeypatch.setattr(read_metadata, "read_scope", None)
    data = {"tickets": {"complete": False, "error": "partial lookup"}}
    result = read_metadata.read_result(lambda: data, {}, SimpleNamespace())
    assert result["cache"] == {"available": False, "state": "unknown"}
    assert result["partial"] and result["data"] is data


def test_sdk_read_scope_tracks_oldest_source_inside_worker(build_env, monkeypatch):
    env = build_env()
    original_view = env.client.ticket_view("DL-1")
    active = []
    seen = []
    @contextmanager
    def scope(strict=False, cache_mode=None):
        active.append(True)
        seen.append((strict, cache_mode.mode.value))
        try:
            yield [{"state": "fresh", "fetchedAt": 20, "freshUntil": 40, "displayUntil": 80},
                   {"state": "stale", "fetchedAt": 10, "freshUntil": 30, "displayUntil": 70,
                    "invalidated": True}]
        finally:
            active.pop()
    def view(key, fresh=False):
        assert active and fresh
        return {**original_view, "key": key, "summary": "observed"}
    monkeypatch.setattr(read_metadata, "read_scope", scope)
    monkeypatch.setattr(env.client, "ticket_view", view)
    env.cache.policy_revision = 9
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("get_ticket", {"key": "DL-1", "fresh": True})
            assert not result.is_error
            assert result.structured_content["cache"] == {
                "available": True, "state": "stale", "invalidated": True, "sourceCount": 2,
                "fetchedAt": 10, "freshUntil": 30, "displayUntil": 70, "policyRevision": 9,
                "requestedCacheMode": "live", "effectiveCacheMode": "live", "maxAgeSeconds": 0,
                "compliant": False, "upstreamValidated": False, "oldestComponentFetchedAt": 10}
    anyio.run(scenario)
    assert seen == [(True, "live")] and not active
