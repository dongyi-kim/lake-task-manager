"""Control must work even when data workers and the SQLite cache are occupied."""

import asyncio
import os
import threading

import anyio
import httpx

import app.main as main


def test_liveness_and_window_controls_survive_saturated_workers_and_cache(monkeypatch):
    held, release = threading.Event(), threading.Event()
    actions = []

    def hold_cache():
        with main._cache._lock:
            held.set()
            release.wait(10)

    def forbidden(*args, **kwargs):
        raise AssertionError("liveness must not inspect Jira, auth, or disk")

    monkeypatch.setattr(main._client, "needs_login", forbidden)
    monkeypatch.setattr(main._client, "upstream_state", forbidden)
    monkeypatch.setattr(main, "pinned_rev", forbidden)
    monkeypatch.setattr(main, "request_focus_or_open", lambda: actions.append("open") or {"action": "open"})
    monkeypatch.setattr(main, "request_quit", lambda: actions.append("quit") or {"action": "quit"})
    monkeypatch.setenv("LAKE_SUPERVISED_WORKER", "test-worker-generation")
    monkeypatch.setattr(main, "_runtime_health_hook", lambda: True)
    holder = threading.Thread(target=hold_cache, daemon=True)
    holder.start()
    assert held.wait(2)

    async def scenario():
        limiter = anyio.to_thread.current_default_thread_limiter()
        borrowers = [object() for _ in range(limiter.total_tokens)]
        for borrower in borrowers:
            limiter.acquire_on_behalf_of_nowait(borrower)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                         base_url="http://127.0.0.1:4457") as client:
                with anyio.fail_after(1):
                    response = await client.get("/api/health/live")
                    assert response.json() == {"status": "ok", "pid": os.getpid(),
                                               "instance": "test-worker-generation"}
                    assert (await client.post("/api/app/open")).json() == {"action": "open"}
                    assert (await client.post("/api/app/quit")).json() == {"action": "quit"}
        finally:
            for borrower in borrowers:
                limiter.release_on_behalf_of(borrower)

    try:
        asyncio.run(scenario())
        assert actions == ["open", "quit"]
    finally:
        release.set()
        holder.join(2)


def test_healthy_server_reports_stalled_native_owner_to_watchdog(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main, "_runtime_health_hook", lambda: False)
    response = TestClient(main.app).get("/api/health/live")
    assert response.status_code == 503
    assert response.json()["status"] == "unresponsive"
