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

        def record_start(index):
            entered.append(index)
            if len(entered) == 8:
                saturated.set()

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
                                async with anyio.create_task_group() as rejected_calls:
                                    for index in range(8, 40):
                                        rejected_calls.start_soon(call, index)
                            assert len(entered) == 8
                            assert len(results) == 32
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
