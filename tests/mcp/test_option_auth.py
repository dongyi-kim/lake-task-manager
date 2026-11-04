"""Actual option loaders must not turn expired sessions into empty/guessed metadata."""

import anyio
import pytest
from mcp import Client

from app.auth.base import SessionExpired, UpstreamUnavailable


@pytest.mark.parametrize("kind,path,cache_key", [
    ("components", "/rest/api/2/project/DL/components", "components:prod"),
    ("link_types", "/rest/api/2/issueLinkType", "linktypes:prod"),
    ("epics", "/rest/api/2/search", "epic_options:res:prod:fixture"),
])
@pytest.mark.parametrize("failure", ["expired", "offline"])
def test_option_failure_is_explicit_uncached_and_recoverable(build_env, monkeypatch, kind, path, cache_key, failure):
    env = build_env()
    monkeypatch.setattr(env.client, "env", "prod")
    monkeypatch.setattr(env.client, "needs_login", lambda: False)
    probes = []
    monkeypatch.setattr(env.client, "proactive_auth_probe", lambda **kwargs: probes.append(kwargs) or {"needLogin": True})
    original = env.provider.get_json
    failed = [True]
    def request(request_path, *args, **kwargs):
        if request_path == path and failed[0]:
            raise SessionExpired("fixture expired") if failure == "expired" else UpstreamUnavailable("fixture offline")
        return original(request_path, *args, **kwargs)
    monkeypatch.setattr(env.provider, "get_json", request)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("list_options", {"kind": kind, "query": "fixture"})
            assert result.is_error, result.structured_content
            error = result.structured_content["error"]
            assert error["needLogin"] is (failure == "expired")
            assert ("nextAction" in error) is (failure == "expired")
            assert len(probes) == (1 if failure == "expired" else 0)
            assert env.cache.get(cache_key) is None
            failed[0] = False
            recovered = await mcp.call_tool("list_options", {"kind": kind, "query": "fixture"})
            assert not recovered.is_error, recovered.structured_content
    anyio.run(scenario)
