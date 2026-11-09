"""A lost HTTP response cannot kill Desktop's stdio process or replay a write."""
import socket
import sys
import threading
import time
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError
import pytest
import uvicorn


def test_lost_write_response_keeps_same_stdio_session_and_does_not_replay(build_env):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    env = build_env(listener.getsockname()[1])
    before = set(env.store.issues)
    original_tool = env.runtime.server.call_tool
    drop = threading.Event()
    reading, release_read = threading.Event(), threading.Event()
    async def observed(name, arguments, context=None):
        if name == "get_content_guide" and not release_read.is_set():
            reading.set()
            while not release_read.is_set():
                await anyio.sleep(.01)
        result = await original_tool(name, arguments, context)
        if name == "create_task" and not result.is_error:
            drop.set()
        return result
    env.runtime.server.call_tool = observed
    async def application(scope, receive, send):
        async def failing_send(message):
            if message["type"] == "http.response.body" and drop.is_set():
                drop.clear()
                raise ConnectionResetError("synthetic response loss after commit")
            await send(message)
        await env.app(scope, receive, failing_send)
    server = uvicorn.Server(uvicorn.Config(application, log_level="critical", proxy_headers=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    entry = Path(__file__).resolve().parents[2] / "app/local_mcp/stdio.py"
    parameters = StdioServerParameters(command=sys.executable, args=[str(entry)], env={
        "LTM_MCP_URL": env.runtime.url, "LTM_MCP_TOKEN": env.runtime.credentials.token(),
        "PYTHONDONTWRITEBYTECODE": "1"})
    async def scenario():
        with anyio.fail_after(20):
            async with stdio_client(parameters) as (reads, writes):
                async with ClientSession(reads, writes, read_timeout_seconds=5) as session:
                    await session.initialize()
                    await session.list_tools()
                    pending = []
                    async def read_guide():
                        pending.append(await session.call_tool("get_content_guide", {}))
                    async with anyio.create_task_group() as group:
                        group.start_soon(read_guide)
                        while not reading.is_set():
                            await anyio.sleep(.01)
                        try:
                            with pytest.raises(MCPError) as failed:
                                await session.call_tool("create_task", {"body": {"type": "Task",
                                    "summary": "single commit despite lost response", "clientMutationId": "stdio:lost:0001"}})
                        finally:
                            release_read.set()
                    assert len(pending) == 1 and not pending[0].is_error
                    assert failed.value.data == {
                        "code": "ltm_connection_unavailable", "retryable": False,
                        "retryReadOnly": True, "retryAfterSeconds": 1,
                        "executionUnknown": True, "requestReplayed": False, "repeatWrite": False}
                    assert "synthetic response loss" not in str(failed.value)
                    assert len(set(env.store.issues) - before) == 1
                    # No new initialize or stdio child: the client keeps this session.
                    assert "get_ticket" in {tool.name for tool in (await session.list_tools()).tools}
                    guide = await session.call_tool("get_content_guide", {})
                    assert not guide.is_error
                    assert len(set(env.store.issues) - before) == 1
    try:
        anyio.run(scenario)
    finally:
        release_read.set()
        server.should_exit = True
        thread.join(5)
        listener.close()
    assert not thread.is_alive()


@pytest.mark.parametrize("profile", ["", "/reporting"])
def test_same_stdio_session_recovers_after_ltm_stops_and_returns(build_env, profile):
    servers = []
    def start(port):
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        env = build_env(listener.getsockname()[1])
        server = uvicorn.Server(uvicorn.Config(env.app, log_level="critical", proxy_headers=False))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        servers.append((server, thread, listener))
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        return env
    def stop(server, thread, listener):
        server.should_exit = True
        thread.join(5)
        listener.close()
        assert not thread.is_alive()
    env = start(0)
    port = env.runtime.settings.app_port
    token = env.runtime.credentials.token()
    entry = Path(__file__).resolve().parents[2] / "app/local_mcp/stdio.py"
    parameters = StdioServerParameters(command=sys.executable, args=[str(entry)], env={
        "LTM_MCP_URL": env.runtime.url + profile, "LTM_MCP_TOKEN": token,
        "PYTHONDONTWRITEBYTECODE": "1"})
    async def scenario():
        with anyio.fail_after(25):
            async with stdio_client(parameters) as (reads, writes):
                async with ClientSession(reads, writes, read_timeout_seconds=5) as session:
                    await session.initialize()
                    initial = {tool.name for tool in (await session.list_tools()).tools}
                    stop(*servers[0])
                    for _ in range(2):
                        with pytest.raises(MCPError) as failed:
                            await session.list_tools()
                        assert failed.value.data["code"] == "ltm_connection_unavailable"
                    restarted = start(port)
                    assert restarted.runtime.credentials.token() == token
                    assert {tool.name for tool in (await session.list_tools()).tools} == initial
                    assert not (await session.call_tool("get_content_guide", {})).is_error
                    assert not env.calls and not restarted.calls  # Reconnect adds no Jira request.
    try:
        anyio.run(scenario)
    finally:
        for server in servers:
            stop(*server)
