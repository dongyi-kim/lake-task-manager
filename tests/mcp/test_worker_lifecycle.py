"""Recovery owns generations, acknowledgements and real synchronous work separately."""
import asyncio
import json
import threading
import time
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

from app.local_mcp.capacity import ToolLane
from app.local_mcp.worker_lifecycle import Generation, RequestLease, WorkerLifecycle, request_lease


async def until(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(.02)
    pytest.fail("Lifecycle did not reach the expected bounded state")


@pytest.mark.parametrize("failure", ["stale_pulse", "finished"])
def test_health_snapshot_does_not_call_a_non_admitting_worker_healthy(failure):
    worker = WorkerLifecycle([])
    worker.current, worker.state = Generation(1), "healthy"
    worker._closing = False
    generation = worker.current
    generation.thread = SimpleNamespace(is_alive=lambda: True)
    generation.accepting = True
    generation.last_heartbeat = time.monotonic() - (10 if failure == "stale_pulse" else 0)
    if failure == "finished":
        generation.finished.set_result(None)
    assert not worker.can_admit(generation)
    assert worker.snapshot()["state"] == "unresponsive"


@pytest.mark.parametrize("fence", ["closing", "stopping", "finished", "dead", "not_accepting", "old", "recovering", "fatal", "overdue"])
def test_suspect_admission_never_bypasses_generation_or_shutdown_fences(fence):
    worker = WorkerLifecycle([])
    generation = Generation(1)
    worker.current, worker.state, worker._closing = generation, "unresponsive", False
    generation.thread = SimpleNamespace(is_alive=lambda: True)
    generation.accepting, generation.last_heartbeat = True, time.monotonic() - 2.1
    assert worker.can_dispatch(generation) and not worker.can_admit(generation)
    assert worker.snapshot()["state"] == "unresponsive"
    if fence == "closing": worker._closing = True
    elif fence == "stopping": generation.stop.set()
    elif fence == "finished": generation.finished.set_result(None)
    elif fence == "dead": generation.thread = SimpleNamespace(is_alive=lambda: False)
    elif fence == "not_accepting": generation.accepting = False
    elif fence == "old": worker.current = Generation(2)
    elif fence == "recovering": worker.state = "recovering"
    elif fence == "fatal": worker.state = "recovery_failed"
    else: generation.last_heartbeat = time.monotonic() - 10
    assert not worker.can_dispatch(generation)


def test_shutdown_before_worker_thread_enters_preserves_its_stop_signal(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original_thread = threading.Thread
    def delayed_thread(*args, target, **kwargs):
        def delayed_owner():
            entered.set()
            assert release.wait(2)
            target()
        return original_thread(*args, target=delayed_owner, **kwargs)
    monkeypatch.setattr(threading, "Thread", delayed_thread)
    worker = WorkerLifecycle([])
    worker.monitor_interval, worker.shutdown_timeout = .01, .4
    async def scenario():
        async def running():
            async with worker.lifespan():
                pytest.fail("Startup was cancelled before its owner ran")
        task = asyncio.create_task(running())
        await until(entered.is_set)
        generation = worker.current
        try:
            task.cancel()
            await until(lambda: worker._closing)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not generation.thread.is_alive()
        finally:
            release.set()
            if generation.stop is not None:
                generation.stop.set()
            await until(lambda: not generation.thread.is_alive())
    asyncio.run(scenario())


def test_failed_ack_does_not_activate_restart_and_duplicate_acks_coalesce():
    worker = WorkerLifecycle([])
    worker.current, worker._closing, worker.state = Generation(1), False, "healthy"
    abandoned = RequestLease(worker.current)
    token = request_lease.set(abandoned)
    try:
        assert worker.request_restart(1)["status"] == "queued"
    finally:
        request_lease.reset(token)
    abandoned.response_complete = True
    abandoned.disconnected.set()
    worker.finish_request(abandoned)
    assert worker._restart_generation is None and not worker._pending
    first, second = RequestLease(worker.current), RequestLease(worker.current)
    for lease, expected in ((first, "queued"), (second, "already_queued")):
        token = request_lease.set(lease)
        try:
            assert worker.request_restart(1)["status"] == expected
        finally:
            request_lease.reset(token)
    assert worker._restart_generation is None
    for lease in (first, second):
        lease.response_complete = True
        worker.finish_request(lease)
    assert worker._restart_generation == 1 and not worker._pending


def test_stop_disconnects_pending_work_before_signalling_worker():
    worker = WorkerLifecycle([])
    generation = Generation(1)
    lease = RequestLease(generation)
    generation.leases.add(lease)
    generation.thread = SimpleNamespace(is_alive=lambda: False)
    def stopped():
        assert lease.disconnected.is_set()
        generation.finished.set_result(None)
    generation.stop = SimpleNamespace(set=stopped)
    assert asyncio.run(worker._stop(generation))


def test_replacement_waits_for_actual_sync_work_after_async_cancellation():
    lane = ToolLane(1)
    entered, release = threading.Event(), threading.Event()
    calls, factories = [], []
    def write_once():
        calls.append("write")
        entered.set()
        assert release.wait(3)
    def fresh_apps():
        assert lane.quiescent
        factories.append("new")
        return [FastAPI()]
    worker = WorkerLifecycle([], factory=fresh_apps, quiescent=lambda: lane.quiescent)
    worker.monitor_interval = .01
    generation = Generation(1)
    generation.finished.set_result(None)
    generation.thread = SimpleNamespace(is_alive=lambda: False)
    generation.stop = threading.Event()
    worker.current, worker._closing = generation, False
    async def scenario():
        task = asyncio.create_task(lane.run(write_once))
        try:
            await until(entered.is_set)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not lane.quiescent
            recovery = asyncio.create_task(worker._recover("worker_exited"))
            await asyncio.sleep(.04)
            assert not factories and not recovery.done()
            release.set()
            await recovery
            assert factories == ["new"] and calls == ["write"]
        finally:
            release.set()
            worker._closing = True
            await worker._stop(worker.current)
    asyncio.run(scenario())


def test_repeated_worker_failure_stops_at_recovery_limit_without_secret_payload():
    attempts = []
    def broken_apps():
        attempts.append(1)
        raise ValueError("synthetic-secret-not-for-log")
    worker = WorkerLifecycle([FastAPI()], factory=broken_apps)
    worker.monitor_interval, worker.probe_timeout, worker.restart_limit = .01, .5, 2
    async def scenario():
        async with worker.lifespan():
            worker.current.stop.set()
            try:
                await until(lambda: worker.state == "recovery_failed")
            except pytest.fail.Exception:
                pytest.fail(f"Recovery failed to reach its cap: {worker.snapshot()}, attempts={attempts}, "
                    f"failure={worker.current.failure_type}, events={worker.drain_events()}")
            assert attempts == [1, 1] and worker.restart_count == 2
            generation = worker.current
            await asyncio.sleep(.04)
            assert worker.current is generation
            assert worker.snapshot()["lastRecoveryReason"] == "recovery_limit"
            events = worker.drain_events()
            assert "synthetic-secret-not-for-log" not in json.dumps(events)
            assert any(event["event"] == "mcp_recovery_failed" for event in events)
    asyncio.run(scenario())


def test_app_shutdown_cancels_an_acknowledged_restart_without_resurrecting_worker():
    worker = WorkerLifecycle([FastAPI()], factory=lambda: [FastAPI()])
    worker.monitor_interval = .1
    async def scenario():
        async with worker.lifespan():
            generation = worker.current
            lease = RequestLease(generation)
            token = request_lease.set(lease)
            try:
                assert worker.request_restart(generation.number)["accepted"]
            finally:
                request_lease.reset(token)
            lease.response_complete = True
            worker.finish_request(lease)
        await asyncio.sleep(.15)
        assert worker.current is generation
        assert not generation.thread.is_alive()
        assert worker.snapshot()["state"] == "stopped"
    asyncio.run(scenario())
