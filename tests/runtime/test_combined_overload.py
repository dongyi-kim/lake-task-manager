"""Real HTTP/tool gates share simulated process-tree pressure; no network or OOM."""
import asyncio
import json
import threading

import anyio
import httpx
import pytest

from app.infra.request_admission import RequestAdmissionMiddleware
from app.infra.resource_pressure import ResourceBusy, ResourcePressure
from app.local_mcp.capacity import ToolCapacityExceeded, ToolLane


@pytest.mark.parametrize("channels", [("http",), ("mcp",), ("http", "mcp")], ids=["http", "mcp", "combined"])
def test_overload_cancellation_pressure_and_recovery_share_bounded_admission(channels):
    async def scenario():
        # Aggregate job memory models Python/Jira payloads plus Chromium without
        # allocating large buffers or relying on this machine's current load.
        counters = {"processBytes": 10, "availableBytes": 1000, "totalBytes": 4000,
                    "jobBytes": 100, "jobLimitBytes": 1000}
        pressure = ResourcePressure(sampler=lambda: dict(counters), interval=0, soft_limit=100, reserve=100)
        lane = ToolLane(1)
        entered = {channel: threading.Event() for channel in channels}
        release = {channel: threading.Event() for channel in channels}
        calls, observers = [], []
        active = {"http": 0, "mcp": 0}
        peak = dict(active)
        lock = threading.Lock()
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: observers.append(context))

        def held(channel):
            pressure.check()
            with lock:
                calls.append(channel)
                active[channel] += 1
                peak[channel] = max(peak[channel], active[channel])
            entered[channel].set()
            try:
                assert release[channel].wait(10), "test failed to release admitted work"
            finally:
                with lock:
                    active[channel] -= 1

        async def app(scope, receive, send):
            if scope["path"] == "/api/slow":
                await anyio.to_thread.run_sync(lambda: held("http"))
            elif scope["path"] != "/api/health/live":
                calls.append("http-recovered")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": json.dumps(pressure.snapshot()).encode()})

        gate = RequestAdmissionMiddleware(app, capacity=1, pressure_check=pressure.check)

        def checked_recovery():
            pressure.check()
            calls.append("mcp-recovered")
            return "ready"

        async def wait_until(predicate):
            with anyio.fail_after(3):
                while not predicate():
                    await anyio.sleep(.001)

        tasks = []
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://test") as client:
                if "http" in channels:
                    tasks.append(asyncio.create_task(client.get("/api/slow")))
                if "mcp" in channels:
                    tasks.append(asyncio.create_task(lane.run(lambda: held("mcp"))))
                await wait_until(lambda: all(event.is_set() for event in entered.values()))
                if "http" in channels:
                    responses = await asyncio.gather(*(client.get("/api/data") for _ in range(16)))
                    assert all(response.status_code == 429 and response.json()["executed"] is False
                               and response.json()["code"] == "request_capacity" for response in responses)
                if "mcp" in channels:
                    for _ in range(16):
                        with pytest.raises(ToolCapacityExceeded):
                            await lane.run(checked_recovery)
                assert sorted(calls) == sorted(channels)

                counters["jobBytes"] = 900
                with pytest.raises(ResourceBusy):
                    pressure.check()
                assert pressure.snapshot()["busy"] and pressure.snapshot()["processBytes"] == 10
                for task in tasks:
                    task.cancel()
                assert all(isinstance(result, asyncio.CancelledError)
                           for result in await asyncio.gather(*tasks, return_exceptions=True))
                assert gate._business == int("http" in channels)
                assert lane.quiescent is ("mcp" not in channels)
                with anyio.fail_after(1):
                    health = await client.get("/api/health/live")
                assert health.status_code == 200 and health.json()["busy"]
                # Caller cancellation must not admit replacement operations while
                # the original synchronous Jira/UI work is still completing.
                if "http" in channels:
                    assert (await client.get("/api/data")).json()["code"] == "request_capacity"
                    release["http"].set()
                    await wait_until(lambda: gate._business == 0 and not gate._requests)
                if "mcp" in channels:
                    with pytest.raises(ToolCapacityExceeded):
                        await lane.run(checked_recovery)
                    release["mcp"].set()
                    await wait_until(lambda: lane.quiescent)
                refused = await client.get("/api/data")
                assert refused.status_code == 429 and refused.json()["code"] == "resource_pressure"
                assert refused.json()["executed"] is False
                with pytest.raises(ResourceBusy):
                    await lane.run(checked_recovery)
                assert sorted(calls) == sorted(channels)

                counters["jobBytes"] = 600
                assert (await client.get("/api/data")).status_code == 200
                assert await lane.run(checked_recovery) == "ready"
                await wait_until(lambda: not gate._requests)
                assert sorted(calls) == sorted([*channels, "http-recovered", "mcp-recovered"])
                assert peak == {channel: int(channel in channels) for channel in ("http", "mcp")}
                assert active == {"http": 0, "mcp": 0} and gate._business == 0 and lane.quiescent
                await anyio.sleep(0)
                assert observers == []
        finally:
            for event in release.values():
                event.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            # RequestAdmission owns detached work after caller cancellation.
            await wait_until(lambda: not gate._requests and lane.quiescent)
            loop.set_exception_handler(previous)

    anyio.run(scenario)
