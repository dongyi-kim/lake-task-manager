"""UI interactions become scoped MCP evidence; denied interactions remain absent."""

import anyio
import httpx2
from mcp import Client
from jsonschema import Draft202012Validator
from app.auth.base import SessionExpired


def test_ui_recording_local_boundary_and_expired_auth_are_noninteractive(build_env, monkeypatch):
    env = build_env()
    env.store.users.update({
        "sua.one": {"name": "sua.one", "displayName": "강수아 가람회사"},
        "sua.two": {"name": "sua.two", "displayName": "김수아 나래회사"},
    })
    async def scenario():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app),
                                     base_url="http://127.0.0.1:4457") as http:
            body = {"userId": "sua.one", "event": "mention", "actorHint": "u01"}
            rejected = {"userId": "sua.two", "event": "selection", "actorHint": "u01"}
            assert (await http.post("/api/people/recent", json=rejected)).status_code == 403
            headers = {"X-LTM-Local": "1"}
            response = await http.post("/api/people/recent", json=body, headers=headers)
            assert response.status_code == 200 and response.json() == {"recorded": True}
            assert (await http.post("/api/people/recent", json=rejected,
                    headers={**headers, "Origin": "https://untrusted.example"})).status_code == 403
            assert (await http.post("/api/people/recent", json={**rejected, "event": "lookup"},
                                   headers=headers)).status_code == 422
            async with Client(env.runtime.server) as mcp:
                tools = {tool.name: tool for tool in (await mcp.list_tools()).tools}
                response = await mcp.call_tool("search_users", {"query": "수아"})
                assert not response.is_error
                Draft202012Validator(tools["search_users"].output_schema).validate(response.structured_content)
                users = {user["id"]: user["matchContext"] for user in response.structured_content["data"]["users"]}
                assert users["sua.one"]["recentMentionAt"] > 0
                assert users["sua.one"]["recentLookupAt"] is None and users["sua.one"]["recentSelectionAt"] is None
                assert all(users["sua.two"][field] is None for field in (
                    "recentLookupAt", "recentSelectionAt", "recentMentionAt"))
            def expired():
                raise SessionExpired("expired")
            monkeypatch.setattr(env.provider, "get_json", lambda *_a, **_k: expired())
            env.client._provider_generation += 1
            response = await http.post("/api/people/recent", json=body, headers=headers)
            assert response.status_code == 200 and response.json() == {"recorded": True}
            missing_hint = {"userId": "sua.one", "event": "mention"}
            assert (await http.post("/api/people/recent", json=missing_hint, headers=headers)).status_code == 200
    anyio.run(scenario)
