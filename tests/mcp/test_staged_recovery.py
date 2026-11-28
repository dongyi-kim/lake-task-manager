"""Transient SDK delays pause new work without disconnecting accepted writes or responses."""
import asyncio
import json
import threading
from types import SimpleNamespace

from fastapi import FastAPI
import httpx2
import pytest

from app.local_mcp import worker_lifecycle
from app.local_mcp.capacity import ToolLane
from app.local_mcp.transport_host import TransportHost
from app.local_mcp.worker_lifecycle import Generation, RequestLease, WorkerLifecycle


@pytest.fixture
def sampled_worker(monkeypatch, tmp_path):
    clock = [100.0]
    monkeypatch.setattr(worker_lifecycle, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    worker = WorkerLifecycle([], diagnostics_directory=tmp_path)
    generation = Generation(1)
    generation.thread = SimpleNamespace(is_alive=lambda: True)
    generation.accepting, generation.last_heartbeat = True, clock[0]
    worker.current, worker.state, worker._closing = generation, "healthy", False
    return worker, generation, clock


async def suspect(worker, clock):
    for at in (103, 104, 105):
        clock[0] = at
        await worker._monitor_once()


def test_transient_gap_preserves_leases_and_real_pulse_resumes_same_generation(sampled_worker):
    worker, generation, clock = sampled_worker
    lease = RequestLease(generation)
    generation.leases.add(lease)

    async def scenario():
        await suspect(worker, clock)
        assert not generation.accepting and not worker.can_dispatch(generation)
        assert not generation.stop.is_set() and not lease.disconnected.is_set()
        assert worker.snapshot()["admissionPaused"] and worker.snapshot()["recoveryGraceRemainingSeconds"] == 60
        clock[0] = 150
        await worker._monitor_once()
        assert worker.current is generation and worker.restart_count == 0
        generation.last_heartbeat = clock[0]
        assert await worker._probe(generation)  # Paused admission must not hide real progress.
        await worker._monitor_once()
        assert worker.state == "healthy" and generation.accepting and worker.current is generation
        assert worker.failures == 0 and worker._suspect_since is None and worker.restart_count == 0
        assert not lease.disconnected.is_set() and not generation.stop.is_set()
        clock[0] = 153
        await worker._monitor_once()
        clock[0] = 154
        await worker._monitor_once()
        clock[0] = 155
        await worker._monitor_once()
        assert worker.snapshot()["recoveryGraceRemainingSeconds"] == 60

    asyncio.run(scenario())


def test_idle_hang_waits_full_grace_but_known_exit_does_not(sampled_worker, monkeypatch):
    worker, generation, clock = sampled_worker
    recoveries = []

    async def recover(reason):
        recoveries.append(reason)

    monkeypatch.setattr(worker, "_recover", recover)

    async def scenario():
        await suspect(worker, clock)
        clock[0] = 164.9
        await worker._monitor_once()
        assert recoveries == [] and not generation.stop.is_set()
        clock[0] = 165
        await worker._monitor_once()
        assert recoveries == ["heartbeat_timeout"]
        recoveries.clear()
        generation.finished.set_result(None)
        generation.thread = SimpleNamespace(is_alive=lambda: False)
        await worker._monitor_once()
        assert recoveries == ["worker_exited"]

    asyncio.run(scenario())


def test_drain_timeout_reports_waiting_without_cancelling_then_pulse_can_recover(sampled_worker):
    worker, generation, clock = sampled_worker
    worker.quiescent = lambda: False
    worker.drain_timeout, worker.monitor_interval = 10, .01
    lease = RequestLease(generation)
    generation.leases.add(lease)

    async def scenario():
        # Establish the production grace using deterministic monitor samples.
        worker.monitor_interval = 1
        await suspect(worker, clock)
        worker.monitor_interval = .01
        clock[0] = 165
        recovery = asyncio.create_task(worker._monitor_once())
        await asyncio.sleep(0)
        assert worker.state == "recovering" and not recovery.done()
        assert not generation.stop.is_set() and not lease.disconnected.is_set()
        clock[0] = 176
        await asyncio.wait_for(recovery, 1)
        assert worker.state == "recovery_failed" and worker.last_reason == "drain_timeout"
        assert worker.current is generation and worker.restart_count == 0
        assert not generation.stop.is_set() and not lease.disconnected.is_set()
        generation.last_heartbeat = clock[0]
        await worker._monitor_once()
        assert generation.accepting and worker.state == "healthy" and worker.restart_count == 0

    asyncio.run(scenario())


async def until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(.005)
    await asyncio.wait_for(poll(), 3)


@pytest.mark.parametrize("trigger", ["heartbeat", "manual"])
def test_real_transport_preserves_write_and_response_across_staged_recovery(tmp_path, trigger):
    lane = ToolLane(1)
    host = TransportHost([FastAPI()], factory=lambda: [FastAPI()], quiescent=lambda: lane.quiescent)
    host.monitor_interval, host.probe_timeout = .01, .05
    worker = host._worker
    worker.recovery_grace, worker.drain_timeout = 1, 2
    worker.diagnostics_directory = tmp_path
    entered, release_write, blocked, release_loop = (threading.Event() for _ in range(4))
    response_pending, release_response = threading.Event(), threading.Event()
    calls = []

    def write():
        entered.set()
        assert release_write.wait(5)
        calls.append("committed")

    def block_loop():
        blocked.set()
        assert release_loop.wait(5)

    async def response(send, value):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": json.dumps(value).encode()})

    async def handler(scope, receive, send):
        if scope["path"] == "/mcp/restart":
            return await response(send, worker.request_restart(worker.current.number))
        if scope["path"] != "/mcp/write":
            calls.append("unexpected")
            return await response(send, {})
        await lane.run(write)
        response_pending.set()
        while not release_response.is_set():
            await asyncio.sleep(.005)
        await response(send, {"committed": True})

    endpoint = host.wrap(handler)

    async def app(scope, receive, send):
        if scope["path"] == "/ui":
            return await response(send, {"alive": True})
        return await endpoint(scope, receive, send)

    async def scenario():
        pending = None
        async with host.lifespan():
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://local") as http:
                generation = worker.current
                pending = asyncio.create_task(http.get("/mcp/write"))
                try:
                    await until(entered.is_set)
                    if trigger == "heartbeat":
                        generation.loop.call_soon_threadsafe(block_loop)
                        await until(blocked.is_set)
                        await until(lambda: not generation.accepting)
                    else:
                        acknowledged = await http.get("/mcp/restart")
                        assert acknowledged.json()["accepted"]
                        await until(lambda: worker.state == "recovering")
                    assert not pending.done() and worker.restart_count == 0
                    assert not generation.stop.is_set() and all(not lease.disconnected.is_set() for lease in generation.leases)
                    refused = await http.get("/mcp/next")
                    assert refused.status_code == 503 and refused.json()["executed"] is False
                    assert (await http.get("/ui")).json() == {"alive": True}
                    if trigger == "heartbeat":
                        release_loop.set()
                        await until(lambda: worker.state == "healthy")
                        assert worker.current is generation and worker.restart_count == 0
                    release_write.set()
                    await until(response_pending.is_set)
                    assert lane.quiescent and not generation.stop.is_set() and worker.restart_count == 0
                    assert all(not lease.disconnected.is_set() for lease in generation.leases)
                    release_response.set()
                    assert (await pending).json() == {"committed": True}
                    if trigger == "manual":
                        await until(lambda: worker.current is not generation and worker.state == "healthy")
                        assert worker.restart_count == 1
                    else:
                        assert worker.current is generation and worker.restart_count == 0
                    assert calls == ["committed"]
                finally:
                    release_loop.set(); release_write.set(); release_response.set()
                    if pending is not None:
                        await pending

    asyncio.run(scenario())
