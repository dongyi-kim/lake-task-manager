"""The suite controls memory inputs without bypassing real HTTP admission policy."""
import asyncio

import httpx

from app.infra.request_admission import RequestAdmissionMiddleware
from app.infra.resource_pressure import MiB, resource_pressure


def test_deterministic_memory_sample_still_refuses_and_recovers_http_work(resource_pressure_sample):
    executed = []

    async def handler(scope, receive, send):
        executed.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def scenario():
        gate = RequestAdmissionMiddleware(handler, pressure_check=resource_pressure.check)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://test") as client:
            assert (await client.get("/api/work")).status_code == 200
            assert executed == ["/api/work"]

            resource_pressure_sample["processBytes"] = 2048 * MiB
            resource_pressure._next = 0  # Advance sampling without a wall-clock sleep.
            refused = await client.post("/api/write", json={"value": "must not execute"})
            assert refused.status_code == 429
            assert refused.json()["code"] == "resource_pressure"
            assert refused.json()["executed"] is False
            assert refused.headers["Retry-After"] == "1"
            assert executed == ["/api/work"]

            # The real 85% hysteresis must keep refusing below the soft limit.
            resource_pressure_sample["processBytes"] = 950 * MiB
            resource_pressure._next = 0
            assert (await client.get("/api/work")).status_code == 429
            assert executed == ["/api/work"]

            resource_pressure_sample["processBytes"] = 64 * MiB
            resource_pressure._next = 0
            assert (await client.get("/api/work")).status_code == 200
            assert executed == ["/api/work", "/api/work"]

    asyncio.run(scenario())
