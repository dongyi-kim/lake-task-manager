"""SDK work, admission and disconnects must not block the UI's event loop."""

import asyncio
import json
import threading

import httpx2
from fastapi import FastAPI
import pytest

from app.local_mcp.transport_host import TransportHost


async def session_tasks(runtime):
    async def inspect():
        return len(runtime.server.session_manager._task_group._tasks)
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(inspect(), runtime.transport_host._loop))


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


@pytest.mark.parametrize("read_only", [True, False])
def test_cancelled_http_requests_finish_work_once_and_release_sdk_sessions(build_env, read_only):
    env = build_env()
    entered, release = threading.Event(), threading.Event()
    completed = []

    def held_operation(index: int) -> dict:
        entered.set()
        assert release.wait(5)
        completed.append(index)
        return {"index": index}

    env.runtime.register(held_operation, read_only=read_only, output_type=dict)

    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app),
                    base_url="http://127.0.0.1:4457", headers={
                    "Authorization": "Bearer " + env.runtime.credentials.token(),
                    "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}) as http:
                assert await session_tasks(env.runtime) == 0
                for index in range(4):
                    entered.clear(); release.clear()
                    call = asyncio.create_task(http.post("/mcp", json={"jsonrpc": "2.0", "id": index,
                        "method": "tools/call", "params": {"name": "held_operation", "arguments": {"index": index}}}))
                    try:
                        assert await asyncio.to_thread(entered.wait, 2)
                        call.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await call
                    finally:
                        release.set()
                    for _ in range(100):
                        if await session_tasks(env.runtime) == 0:
                            break
                        await asyncio.sleep(.01)
                    assert await session_tasks(env.runtime) == 0
                    assert completed == list(range(index + 1))
    asyncio.run(scenario())


def test_closed_socket_during_response_does_not_leak_sdk_session(build_env):
    env = build_env()
    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
            scope = {"type": "http", "method": "POST", "path": "/mcp", "root_path": "", "query_string": b"",
                     "scheme": "http", "server": ("127.0.0.1", 4457), "client": ("127.0.0.1", 1234),
                     "headers": [(b"host", b"127.0.0.1:4457"), (b"content-type", b"application/json"),
                                 (b"accept", b"application/json, text/event-stream"),
                                 (b"authorization", ("Bearer " + env.runtime.credentials.token()).encode()),
                                 (b"mcp-protocol-version", b"2025-06-18")]}
            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}
            async def send(message):
                raise BrokenPipeError("client left")
            await env.app(scope, receive, send)
            for _ in range(100):
                if await session_tasks(env.runtime) == 0:
                    break
                await asyncio.sleep(.01)
            assert await session_tasks(env.runtime) == 0
    asyncio.run(scenario())
