"""Memory refusal precedes MCP authentication and writes; control tools still work."""
import json

import anyio
from mcp import Client
import pytest

from app.infra.resource_pressure import ResourceBusy, resource_pressure


@pytest.mark.parametrize("refusal_stage", ["entry", "dispatch"])
def test_memory_pressure_refuses_work_before_auth_and_keeps_control_tools_available(build_env, monkeypatch, refusal_stage):
    env = build_env()
    busy = False
    checks = []
    original_auth = env.runtime.authentication.check
    original_dispatch = env.runtime._work_lane.run
    refuse_dispatch = refusal_stage == "dispatch"

    async def dispatch(fn):
        nonlocal busy
        if refuse_dispatch:
            busy = True
        return await original_dispatch(fn)

    def pressure():
        if busy:
            raise ResourceBusy("simulated memory pressure")

    def authentication(*args, **kwargs):
        checks.append(args)
        return original_auth(*args, **kwargs)

    monkeypatch.setattr(resource_pressure, "check", pressure)
    monkeypatch.setattr(env.runtime.authentication, "check", authentication)
    monkeypatch.setattr(env.runtime._work_lane, "run", dispatch)
    classified = []
    env.runtime.classify_error = lambda exc: classified.append(type(exc).__name__)

    async def scenario():
        nonlocal busy, refuse_dispatch
        async with Client(env.runtime.server) as mcp:
            await mcp.list_tools()
            arguments = {"body": {"type": "Task", "summary": "Memory admission receipt",
                                  "clientMutationId": "memory:create:0001"}}
            for name, body, write in [
                ("create_task", arguments, True),
                ("search_issues", {"jql": "project = DL"}, False),
            ]:
                busy = refusal_stage == "entry"
                result = await mcp.call_tool(name, body)
                assert result.is_error
                payload = result.structured_content
                assert payload["ok"] is False
                assert payload["error"] == {
                    "code": "resource_pressure", "status": 503,
                    "message": "simulated memory pressure", "retryable": True,
                    "needLogin": False, "retryAfterSeconds": 1,
                    "executed": False, "retryWithSameMutationId": write,
                }
                assert json.loads(result.content[0].text) == payload
            assert checks == [] and env.calls == [] and classified == []
            for name in ("get_auth_status", "authenticate", "get_mcp_health", "get_content_guide"):
                assert not (await mcp.call_tool(name, {})).is_error
            busy = False
            refuse_dispatch = False
            created = await mcp.call_tool("create_task", arguments)
            repeated = await mcp.call_tool("create_task", arguments)
            assert not created.is_error
            assert repeated.structured_content == created.structured_content
            assert sum(method == "post" and path == "/rest/api/2/issue"
                       for method, path, _ in env.calls) == 1

    anyio.run(scenario)
