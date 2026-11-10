"""Live recent-comment previews remain honest when a thread exceeds the UI window."""
import anyio
import pytest
from mcp import Client

from app.auth.base import SessionExpired
from app.infra.cache_policy import CachePolicy


def test_live_sdk_comments_and_ticket_accept_long_thread_preview(build_env):
    env = build_env()
    env.client._reprime = lambda *_args, **_kwargs: None
    for index in range(25):
        env.client.add_comment("DL-1", f"<p>Discussion {index}</p>")
    env.cache.invalidate()
    env.cache.configure(CachePolicy())
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            preview = await mcp.call_tool("get_comments", {"key": "DL-1", "cache_mode": "live"})
            assert not preview.is_error, preview.structured_content
            data = preview.structured_content
            assert data["data"]["returnedCount"] == 20 and data["data"]["mayHaveMore"]
            assert data["cache"]["compliant"] and data["cache"]["upstreamValidated"]
            ticket = await mcp.call_tool("get_ticket", {"key": "DL-1", "cache_mode": "live"})
            assert not ticket.is_error and ticket.structured_content["cache"]["compliant"]
    anyio.run(scenario)


@pytest.mark.parametrize("failure", [SessionExpired("expired"), ConnectionError("offline")])
def test_live_sdk_comment_preview_keeps_auth_and_transport_errors(build_env, monkeypatch, failure):
    env = build_env()
    env.client._issue_comments("DL-1", 100)
    original = env.provider.get_json
    def fail(path, *args, **kwargs):
        if path.endswith("/comment"):
            raise failure
        return original(path, *args, **kwargs)
    monkeypatch.setattr(env.provider, "get_json", fail)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("get_comments", {"key": "DL-1", "cache_mode": "live"})
            assert result.is_error
            assert not result.structured_content.get("data")
    anyio.run(scenario)
