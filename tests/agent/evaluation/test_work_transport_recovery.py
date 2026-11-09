"""Qualification uses the supervised product transport without losing its controls."""
import time

import anyio
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
import httpx2
import pytest
from support.evaluation_tmp import external_tmp_path as tmp_path

from tools.codex_mcp_eval.work_fixture import WorkFixture


@pytest.mark.parametrize("condition", ["baseline", "candidate"])
@pytest.mark.parametrize("transport", ["default", "fallback"])
def test_work_fixture_preserves_controls_and_observers_after_worker_replacement(tmp_path, condition, transport):
    with WorkFixture(tmp_path / "raw", port=0, today="2026-11-09",
                     skill_condition=condition, skill_transport=transport) as fixture:
        async def scenario():
            async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + fixture.token}, trust_env=False) as http:
                async with Client(streamable_http_client(fixture.url, http_client=http)) as client:
                    health = await client.call_tool("get_mcp_health", {})
                    assert health.structured_content["data"]["state"] == "healthy"
                    assert "request_mcp_restart" not in {t.name for t in (await client.list_tools()).tools}
                    old = fixture.runtime.transport_host._worker.current
                    old.stop.set()
                    deadline = time.monotonic() + 7
                    while (fixture.runtime.transport_host._worker.current is old
                           or fixture.runtime.transport_host.health_snapshot()["state"] != "healthy"):
                        assert time.monotonic() < deadline
                        await anyio.sleep(.02)
                    assert bool((await client.list_resources()).resources) == (transport == "default")
                    before = len(fixture.tool_calls)
                    guides = await client.call_tool("get_content_guide", {})
                    names = {r["frontmatter"]["name"] for r in guides.structured_content["data"]["skills"]}
                    assert ("ltm-work-research" in names) == (condition == "candidate")
                    assert len(fixture.tool_calls) == before + 1
                    assert fixture.tool_calls[-1]["name"] == "get_content_guide"
                    if transport == "default":
                        await client.read_resource("skill://ltm-task-authoring/SKILL.md")
                        assert fixture.skill_reads[-1]["kind"] == "resource"
                    assert not fixture.upstream_calls
        anyio.run(scenario)
