"""SDK work, admission and disconnects must not block the UI's event loop."""

import asyncio
import threading

import httpx2
from fastapi import FastAPI
import pytest

from app.local_mcp.transport_host import TransportHost


def test_blocked_sdk_catalog_does_not_block_ui_or_queue_cancelled_requests(build_env):
    env = build_env()
    entered, release = threading.Event(), threading.Event()
    calls = []
    original = env.runtime.server.list_tools

    async def blocked_catalog():
        calls.append("catalog")
        entered.set()
        assert release.wait(5), "UI could not release the MCP catalog"
        return await original()

    env.runtime.server.list_tools = blocked_catalog
    env.runtime.transport_host._permits = threading.BoundedSemaphore(2)

    @env.app.get("/health/live")
    async def health():
        return {"status": "ok"}

    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app),
                    base_url="http://127.0.0.1:4457", headers={
                    "Authorization": "Bearer " + env.runtime.credentials.token(),
                    "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}) as http:
                def request(index):
                    return http.post("/mcp", json={"jsonrpc": "2.0", "id": index, "method": "tools/list"})
                first = asyncio.create_task(request(1))
                pending = None
                try:
                    assert await asyncio.to_thread(entered.wait, 2)
                    for _ in range(3):
                        response = await asyncio.wait_for(http.get("/health/live"), .5)
                        assert response.json() == {"status": "ok"}
                    pending = asyncio.create_task(request(2))
                    await asyncio.sleep(0)
                    overflow = await asyncio.wait_for(request(3), .5)
                    assert overflow.status_code == 429 and overflow.json()["executed"] is False
                    pending.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await pending
                finally:
                    release.set()
                    await first
                await asyncio.sleep(.02)
                assert calls == ["catalog"]  # Disconnected queued work never runs later.
                assert (await request(4)).status_code == 200
                assert calls == ["catalog", "catalog"]
        assert env.runtime.transport_host._loop is None

    asyncio.run(scenario())


def test_transport_releases_admission_after_handler_error_and_lifespan_reentry():
    child, seen = FastAPI(), []
    host = TransportHost([child], capacity=1)

    async def broken(scope, receive, send):
        seen.append(threading.current_thread().name)
        raise ValueError("handler failed")

    async def scenario():
        for _ in range(2):
            async with host.lifespan():
                for _ in range(2):
                    with pytest.raises(ValueError, match="handler failed"):
                        await host.wrap(broken)({"type": "http"}, None, None)
            assert host._loop is None
    asyncio.run(scenario())
    assert seen == ["ltm-mcp-transport"] * 4
