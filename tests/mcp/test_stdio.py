import socket
import sys
import threading
import time
from pathlib import Path

import anyio
import pytest
import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.local_mcp.stdio import connection_settings


def test_packaged_stdio_mode_never_runs_app_startup(monkeypatch):
    import run
    import app.local_mcp.stdio as stdio
    called = []
    monkeypatch.setattr(sys, "argv", ["LakeTaskManager.exe", "--mcp-stdio"])
    monkeypatch.setattr(stdio, "main", lambda: called.append("proxy"))
    monkeypatch.setattr(run, "_ensure_deps", lambda: pytest.fail("must not install dependencies"))
    monkeypatch.setattr(run, "get_settings", lambda: pytest.fail("must not start app"))
    run.main()
    assert called == ["proxy"]


def test_stdio_proxy_uses_the_running_http_instance_and_shared_cache(build_env):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    env = build_env(listener.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(env.app, log_level="error", proxy_headers=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    executable = Path(__file__).resolve().parents[2] / "app" / "local_mcp" / "stdio.py"
    parameters = StdioServerParameters(command=sys.executable, args=[str(executable)], env={
        "LTM_MCP_URL": env.runtime.url, "LTM_MCP_TOKEN": env.runtime.credentials.token(),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    async def scenario():
        with anyio.fail_after(20):
            async with stdio_client(parameters) as (reads, writes):
                async with ClientSession(reads, writes, read_timeout_seconds=10) as session:
                    await session.initialize()
                    assert "get_ticket" in {tool.name for tool in (await session.list_tools()).tools}
                    result = await session.call_tool("create_task", {"body": {
                        "type": "Task", "summary": "stdio shared instance", "clientMutationId": "stdio:create:0001"}})
                    assert not result.is_error, result
                    key = result.structured_content["data"]["key"]
                    assert key in env.store.issues  # No standalone process-local mock world was created.
                    direct = env.client.ticket_view(key)
                    before = len(env.calls)
                    read = await session.call_tool("get_ticket", {"key": key})
                    assert read.structured_content["data"]["summary"] == direct["summary"]
                    assert len(env.calls) == before
    try:
        anyio.run(scenario)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
    assert not thread.is_alive()


@pytest.mark.parametrize("url", ["", "https://example.com/mcp", "http://127.0.0.1:4457/mcp?token=x",
                                 "http://user:pass@127.0.0.1:4457/mcp", "http://127.0.0.1:4457/api"])
def test_stdio_requires_explicit_local_connection_without_mock_fallback(monkeypatch, url):
    monkeypatch.setenv("LTM_MCP_URL", url)
    monkeypatch.setenv("LTM_MCP_TOKEN", "synthetic-token")
    monkeypatch.setenv("JIRA_ENV", "mock")
    with pytest.raises(ValueError):
        connection_settings()
