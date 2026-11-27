"""Overload and cancellation through the real SDK; only event-controlled local work."""

import asyncio
import threading

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
import pytest


def test_sdk_overload_keeps_ui_and_auth_available_and_recovers(build_env):
    env = build_env()
    release = threading.Event()
    entered = []
    results = []

    @env.app.get("/capacity-health")
    def health():
        # Like ordinary UI routes, this intentionally needs the default AnyIO pool.
        return {"status": "ok"}

    async def scenario():
        saturated = anyio.Event()
        caller_loop = asyncio.get_running_loop()

        def record_start(index):
            entered.append(index)
            if len(entered) == 8:
                caller_loop.call_soon_threadsafe(saturated.set)

        def held_read(index: int) -> dict:
            anyio.from_thread.run_sync(record_start, index)
            assert release.wait(15), "test did not release its held workers"
            return {"index": index}

        env.runtime.register(held_read, read_only=True, output_type=dict)
        async with env.app.router.lifespan_context(env.app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app),
                    base_url=env.runtime.url.removesuffix("/mcp"),
                    headers={"Authorization": "Bearer " + env.runtime.credentials.token()}) as http:
                async with Client(streamable_http_client(env.runtime.url, http_client=http)) as mcp:
                    # Discover the catalog before measuring admission/control-lane latency.
                    # Otherwise the SDK's first successful call fetches every output schema
                    # inside the one-second overload assertion (failed calls skip that fetch).
                    await mcp.list_tools()
                    async def call(index):
                        results.append(await mcp.call_tool("held_read", {"index": index}))

                    async with anyio.create_task_group() as group:
                        try:
                            for index in range(8):
                                group.start_soon(call, index)
                            # Establish saturation before probing overload. This deadline only
                            # catches deadlocks; the UI/control-lane latency assertion stays below.
                            with anyio.fail_after(10):
                                await saturated.wait()
                                # Exercise the tool-lane error envelope below the
                                # separate transport parser cap. Concurrent HTTP
                                # overflow is covered by transport_responsiveness.
                                for index in range(8, 24):
                                    await call(index)
                            assert len(entered) == 8
                            assert len(results) == 16
                            for result in results:
                                error = result.structured_content["error"]
                                assert result.is_error and error["code"] == "rate_limited"
                                assert error["executed"] is False and error["retryable"] is True
                            with anyio.fail_after(1):
                                assert (await http.get("/capacity-health")).json() == {"status": "ok"}
                                auth = await mcp.call_tool("get_auth_status", {})
                                guide = await mcp.call_tool("get_content_guide", {})
                            assert not auth.is_error and not guide.is_error
                            arguments = {"body": {"type": "Task", "summary": "Capacity receipt",
                                                  "clientMutationId": "capacity:create:0001"}}
                            rejected = await mcp.call_tool("create_task", arguments)
                            assert rejected.is_error
                            assert rejected.structured_content["error"]["executed"] is False
                            assert rejected.structured_content["error"]["retryWithSameMutationId"] is True
                            assert not any(method == "post" for method, _, _ in env.calls)
                        finally:
                            release.set()
                    assert sum(not result.is_error for result in results) == 8
                    recovered = await mcp.call_tool("held_read", {"index": 40})
                    assert not recovered.is_error
                    created = await mcp.call_tool("create_task", arguments)
                    repeated = await mcp.call_tool("create_task", arguments)
                    assert not created.is_error and repeated.structured_content == created.structured_content
                    assert sum(method == "post" and path == "/rest/api/2/issue"
                               for method, path, _ in env.calls) == 1

    anyio.run(scenario)


def test_cancelled_admitted_write_never_starts_after_worker_queue_releases():
    from app.local_mcp.capacity import ToolLane

    async def scenario():
        lane = ToolLane(1)
        calls = []
        # Hold the worker limiter to deterministically cancel before dispatch.
        async with lane.limiter:
            task = asyncio.create_task(lane.run(lambda: calls.append("written")))
            await anyio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await anyio.sleep(0)
        assert calls == []
        assert await lane.run(lambda: "recovered") == "recovered"

    anyio.run(scenario)


@pytest.mark.parametrize("kind,code", [("busy", "rate_limited"), ("query_changed", "query_state_changed")])
def test_provider_busy_is_not_classified_as_transport_or_login_failure(build_env, kind, code):
    from app.auth.base import QueryStateChanged, UpstreamBusy

    env = build_env()
    classified = []
    env.runtime.classify_error = lambda exc: classified.append(exc)

    def busy_read() -> dict:
        raise (QueryStateChanged if kind == "query_changed" else UpstreamBusy)("Local read cancelled")

    env.runtime.register(busy_read, read_only=True, output_type=dict)

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("busy_read", {})
            error = result.structured_content["error"]
            assert result.is_error and error["code"] == code
            assert error["retryable"] is True and error["needLogin"] is False
            assert "executed" not in error and classified == []

    anyio.run(scenario)


def test_cancelled_running_write_keeps_capacity_until_real_completion():
    from app.local_mcp.capacity import ToolCapacityExceeded, ToolLane

    release, entered, finished = threading.Event(), threading.Event(), threading.Event()
    writes = []

    def write():
        entered.set()
        try:
            assert release.wait(10), "test did not release its held worker"
            writes.append("written")
        finally:
            finished.set()

    async def scenario():
        lane = ToolLane(1)
        task = asyncio.create_task(lane.run(write))
        try:
            with anyio.fail_after(2):
                while not entered.is_set():
                    await anyio.sleep(0.001)
            # Raw asyncio cancellation bypasses AnyIO's cancellation shield, as can
            # transport teardown. The worker must still own its admission permit.
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(ToolCapacityExceeded):
                await lane.run(lambda: writes.append("duplicate"))
        finally:
            release.set()
        with anyio.fail_after(2):
            while not finished.is_set():
                await anyio.sleep(0.001)
            # finished is set inside the callable, just before the worker releases
            # admission. Observe that release rather than assuming thread timing.
            while True:
                try:
                    assert await lane.run(lambda: "recovered") == "recovered"
                    break
                except ToolCapacityExceeded:
                    await anyio.sleep(0.001)
        assert writes == ["written"]

    anyio.run(scenario)


def test_http_disconnect_before_worker_dispatch_prevents_late_write():
    from app.local_mcp.capacity import ToolLane, ToolRequestCancelled, request_disconnected
    async def scenario():
        lane, disconnected, writes = ToolLane(1), threading.Event(), []
        token = request_disconnected.set(disconnected)
        try:
            async with lane.limiter:
                call = asyncio.create_task(lane.run(lambda: writes.append("written")))
                await asyncio.sleep(0)
                disconnected.set()
            with pytest.raises(ToolRequestCancelled):
                await call
            assert writes == []
        finally:
            request_disconnected.reset(token)
        assert await lane.run(lambda: "ready") == "ready"
    anyio.run(scenario)


def test_large_output_preparation_keeps_worker_admission_and_transport_responsive(build_env, monkeypatch):
    import json

    env = build_env()
    release = threading.Event()
    prepared = []
    lock = threading.Lock()
    original_dumps = json.dumps

    def dumps(value, *args, **kwargs):
        data = value.get("data") if isinstance(value, dict) else None
        if isinstance(data, dict) and data.get("serialization_probe"):
            # A delay in encoding must hold the business permit without holding the SDK loop.
            assert threading.get_ident() != env.runtime.transport_host._worker.current.thread.ident
            with lock:
                prepared.append(threading.get_ident())
            assert release.wait(10), "test did not release output preparation"
        return original_dumps(value, *args, **kwargs)

    monkeypatch.setattr(json, "dumps", dumps)

    def large_read() -> dict:
        return {"serialization_probe": True, "description": "한글 본문 " * 32768,
                "comments": [{"body": "discussion " * 1024} for _ in range(8)]}

    env.runtime.register(large_read, read_only=True, output_type=dict)
    results = []

    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app),
                    base_url=env.runtime.url.removesuffix("/mcp"),
                    headers={"Authorization": "Bearer " + env.runtime.credentials.token()}) as http:
                async with Client(streamable_http_client(env.runtime.url, http_client=http)) as mcp:
                    await mcp.list_tools()
                    async def call():
                        results.append(await mcp.call_tool("large_read", {}))
                    async with anyio.create_task_group() as group:
                        try:
                            for _ in range(8):
                                group.start_soon(call)
                            with anyio.fail_after(5):
                                while len(prepared) != 8:
                                    await anyio.sleep(.001)
                            assert not env.runtime._work_lane.quiescent
                            with anyio.fail_after(2):
                                health = await mcp.call_tool("get_mcp_health", {})
                                refused = await mcp.call_tool("large_read", {})
                            assert not health.is_error
                            assert refused.structured_content["error"]["code"] == "rate_limited"
                            assert len(prepared) == 8
                        finally:
                            release.set()
                    assert len(results) == 8 and all(not result.is_error for result in results)
                    assert env.runtime._work_lane.quiescent
    anyio.run(scenario)


@pytest.mark.parametrize("comments", [False, True])
def test_ticket_size_refusal_never_returns_truncated_success(build_env, monkeypatch, comments):
    from app.local_mcp import read_tools
    env = build_env()
    monkeypatch.setattr(read_tools, "MAX_BODY_RESULT_BYTES", 1024)
    monkeypatch.setattr(env.client, "ticket_view", lambda *_args, **_kwargs: {
        "key": "DL-1", "descriptionHtml": "body" * 1024, "summary": "Large ticket"})

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("get_ticket", {"key": "DL-1", "detail": "read",
                                                      "include_comments": comments})
            payload = result.structured_content
            assert result.is_error and not payload["ok"]
            assert payload["error"]["code"] == "response_too_large"
            assert payload["error"]["status"] == 413
            assert "data" not in payload
            assert "Jira" in payload["error"]["message"]
    anyio.run(scenario)


def test_read_response_budget_precedes_schema_and_duplicate_text_encoding(build_env, monkeypatch):
    from app.local_mcp import response_budget
    env = build_env()
    monkeypatch.setattr(response_budget, "MAX_TOOL_RESPONSE_BYTES", 1024)
    def large_read() -> dict:
        return {"history": [{"body": "content" * 1024}]}
    env.runtime.register(large_read, read_only=True, output_type=dict)

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("large_read", {})
            assert result.is_error
            error = result.structured_content["error"]
            assert error["code"] == "response_too_large" and error["limitBytes"] == 1024
            assert "data" not in result.structured_content
            assert len(result.content[0].text) < 1024
    anyio.run(scenario)
