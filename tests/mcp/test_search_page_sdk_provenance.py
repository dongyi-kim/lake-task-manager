"""The public SDK exposes the direct Jira-page observation even for empty results."""
import anyio
from mcp import Client


def test_live_historical_search_page_has_real_source_without_cached_issue_rows(build_env, monkeypatch):
    env = build_env()
    calls = []
    original = env.provider.get_json
    def fetch(path, params=None, **kwargs):
        if path == "/rest/api/2/search":
            calls.append(params)
            return {"issues": [], "startAt": params["startAt"], "total": 0, "maxResults": 50}
        return original(path, params=params, **kwargs)
    monkeypatch.setattr(env.provider, "get_json", fetch)

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("search_issues", {
                "jql": 'project = DL AND status WAS "Resolved" DURING ("2026-10-01", "2026-11-01")',
                "detail": "summary", "cache_mode": "live"})
            assert not result.is_error, result.structured_content
            body = result.structured_content
            assert body["data"]["issues"] == [] and body["data"]["total"] == 0
            assert body["cache"]["available"] and body["cache"]["upstreamValidated"]
            assert body["cache"]["compliant"] and not body["cache"]["invalidated"]
            assert len(calls) == 1
    anyio.run(scenario)
