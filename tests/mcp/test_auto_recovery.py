"""Background MCP recovery preserves a live app and never replays business calls."""
import asyncio
import time
import threading

import httpx2
from jsonschema import Draft202012Validator
import pytest


def http_client(env):
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app),
        base_url="http://127.0.0.1:4457", headers={
            "Authorization": "Bearer " + env.runtime.credentials.token(),
            "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"})


async def rpc(http, method, params=None, path="/mcp"):
    response = await asyncio.wait_for(http.post(path, json={"jsonrpc": "2.0", "id": 1,
        "method": method, "params": params or {}}), 5)
    assert response.status_code == 200, response.text
    value = response.json()
    assert "error" not in value, value
    return value["result"]


async def wait_for_replacement(host, old_loop):
    deadline = time.monotonic() + host._worker.startup_timeout + 1
    while time.monotonic() < deadline:
        if host._loop is not None and host._loop is not old_loop:
            return
        await asyncio.sleep(.01)
    pytest.fail(f"App stayed alive but its stopped MCP worker was never replaced: {host.health_snapshot()}, "
                f"failure={host._worker.current.failure_type}, frames={host._worker.current.failure_frames}")


@pytest.mark.parametrize("observed_misses", [0, 1])
def test_one_late_heartbeat_does_not_reject_a_responsive_worker(build_env, observed_misses):
    env = build_env()
    host = env.runtime.transport_host
    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            worker, generation = host._worker, host._worker.current
            generation.last_heartbeat = time.monotonic() - 2.1
            worker.state, worker.failures = ("unresponsive" if observed_misses else "healthy"), observed_misses
            assert worker.snapshot()["state"] == "unresponsive"
            async with http_client(env) as http:
                assert (await rpc(http, "tools/list"))["tools"]
            assert worker.current is generation and worker.restart_count == 0
    asyncio.run(scenario())


def test_live_app_automatically_rebuilds_stopped_sdk_and_all_profiles(build_env, monkeypatch):
    env = build_env()
    host = env.runtime.transport_host
    host.monitor_interval = .01
    host.probe_timeout = .5
    monkeypatch.setattr(env.runtime, "get_client", lambda: pytest.fail("recovery must not initialize Jira"))
    original_client, original_cache = env.client, env.cache
    token = env.runtime.credentials.token()

    @env.app.get("/test-live")
    async def live():
        return {"status": "ok"}

    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with http_client(env) as http:
                catalog = await rpc(http, "tools/list")
                old_server, old_loop = env.runtime.server, host._loop
                old_loop.call_soon_threadsafe(host._stop.set)
                assert (await http.get("/test-live")).json() == {"status": "ok"}
                await wait_for_replacement(host, old_loop)
                assert env.runtime.server is not old_server
                assert {row["name"] for row in (await rpc(http, "tools/list"))["tools"]} == {
                    row["name"] for row in catalog["tools"]}
                for profile in env.runtime.profiles:
                    path = "/mcp" + ("/" + profile if profile != "all" else "")
                    result = await rpc(http, "tools/call", {"name": "get_content_guide",
                        "arguments": {"skill": "ltm-work-research"}}, path)
                    assert not result.get("isError")
                    assert result["structuredContent"]["data"]["text"].startswith("---")
                    resource = await rpc(http, "resources/read", {"uri": "skill://ltm-work-research/SKILL.md"}, path)
                    assert resource["contents"][0]["text"] == result["structuredContent"]["data"]["text"]
                assert env.runtime.credentials.token() == token
                assert env.client is original_client and env.cache is original_cache
                assert env.calls == []
        assert host._loop is None
    asyncio.run(scenario())


def test_health_and_restart_ack_use_no_jira_and_deduplicate_generation(build_env, monkeypatch):
    env = build_env()
    host = env.runtime.transport_host
    host.monitor_interval = .01
    host.probe_timeout = .5
    monkeypatch.setattr(env.runtime, "get_client", lambda: pytest.fail("health tools must not initialize Jira"))

    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with http_client(env) as http:
                tools = {row["name"]: row for row in (await rpc(http, "tools/list"))["tools"]}
                assert {"get_mcp_health", "request_mcp_restart"} <= tools.keys()
                async def call(name, arguments=None):
                    result = await rpc(http, "tools/call", {"name": name, "arguments": arguments or {}})
                    assert not result.get("isError"), result
                    Draft202012Validator(tools[name]["outputSchema"]).validate(result["structuredContent"])
                    return result["structuredContent"]["data"]
                health = await call("get_mcp_health")
                assert health["state"] == "healthy"
                generation, old_loop = health["generation"], host._loop
                first = await call("request_mcp_restart", {"expected_generation": generation})
                assert first["accepted"] and first["status"] == "queued"
                second = await asyncio.wait_for(http.post("/mcp", json={"jsonrpc": "2.0", "id": 2,
                    "method": "tools/call", "params": {"name": "request_mcp_restart",
                        "arguments": {"expected_generation": generation}}}), 5)
                if second.status_code == 503:
                    assert second.json()["executed"] is False
                else:
                    assert second.status_code == 200
                    assert second.json()["result"]["structuredContent"]["data"]["status"] in {
                        "already_queued", "generation_changed"}
                await wait_for_replacement(host, old_loop)
                health = await call("get_mcp_health")
                assert health["state"] == "healthy" and health["generation"] == generation + 1
                stale = await call("request_mcp_restart", {"expected_generation": generation})
                assert not stale["accepted"] and stale["status"] == "generation_changed"
                assert env.calls == []
    asyncio.run(scenario())


def test_slow_business_work_does_not_restart_a_responsive_mcp_loop(build_env):
    env = build_env()
    host = env.runtime.transport_host
    host.monitor_interval, host.probe_timeout = .02, .2
    entered, release = threading.Event(), threading.Event()
    def slow_business() -> dict:
        entered.set()
        assert release.wait(3)
        return {"finished": True}
    env.runtime.register(slow_business, read_only=True, output_type=dict)
    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with http_client(env) as http:
                loop, generation = host._loop, host.health_snapshot()["generation"]
                pending = asyncio.create_task(rpc(http, "tools/call", {"name": "slow_business"}))
                try:
                    deadline = time.monotonic() + 2
                    while not entered.is_set() and time.monotonic() < deadline:
                        await asyncio.sleep(.02)
                    assert entered.is_set()
                    await asyncio.sleep(.8)
                    health = await rpc(http, "tools/call", {"name": "get_mcp_health"})
                    assert health["structuredContent"]["data"]["state"] == "healthy"
                    assert host._loop is loop and host.health_snapshot()["generation"] == generation
                    assert host.health_snapshot()["restartCount"] == 0
                finally:
                    release.set()
                    result = await pending
                assert result["structuredContent"]["data"] == {"finished": True}
    asyncio.run(scenario())


def test_blocked_mcp_loop_keeps_app_responsive_and_never_starts_over_old_worker(build_env):
    env = build_env()
    host = env.runtime.transport_host
    host.monitor_interval, host.probe_timeout = .01, .05
    host._worker.shutdown_timeout = .1
    entered, release = threading.Event(), threading.Event()
    def block_loop():
        entered.set()
        release.wait(3)
    @env.app.get("/test-live")
    async def live():
        return {"status": "ok"}
    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            generation = host._worker.current
            generation.loop.call_soon_threadsafe(block_loop)
            try:
                deadline = time.monotonic() + 2
                while host.health_snapshot()["state"] != "recovery_failed":
                    assert time.monotonic() < deadline, host.health_snapshot()
                    await asyncio.sleep(.02)
                assert entered.is_set() and generation.thread.is_alive()
                assert host._worker.current is generation
                assert host.health_snapshot()["lastRecoveryReason"] == "shutdown_timeout"
                async with http_client(env) as http:
                    assert (await http.get("/test-live")).json() == {"status": "ok"}
                    refused = await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                        "method": "tools/list"})
                    assert refused.status_code == 503 and refused.json()["executed"] is False
                assert not env.calls
            finally:
                release.set()
        assert not generation.thread.is_alive()
    asyncio.run(scenario())
