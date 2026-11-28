"""Control must work even when data workers and the SQLite cache are occupied."""

import asyncio
import os
import threading

import anyio
import httpx
import pytest

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
                                               "instance": "test-worker-generation",
                                               "processAlive": True, "degraded": False,
                                               "restartRecommended": False,
                                               "activeWork": {"http": 0, "mcp": 0, "sso": 0},
                                               "resources": main.resource_pressure.snapshot()}
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
    assert response.status_code == 200
    assert response.json()["status"] == "ok" and response.json()["processAlive"]
    assert response.json()["degraded"] and not response.json()["restartRecommended"]


def test_unrecoverable_mcp_worker_does_not_restart_usable_desktop(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "_runtime_health_hook", lambda: True)
    monkeypatch.setattr(main._local_mcp, "started", True)
    state = {"state": "recovery_failed", "generation": 4}
    monkeypatch.setattr(main._local_mcp.transport_host, "health_snapshot", lambda: dict(state))
    client = TestClient(main.app)
    response = client.get("/api/health/live")
    assert response.status_code == 200
    assert response.json()["components"]["mcp"]["state"] == "recovery_failed"
    assert response.json()["degraded"] and not response.json()["restartRecommended"]
    state["state"] = "recovering"
    assert client.get("/api/health/live").status_code == 200


@pytest.mark.parametrize("recommend", [False, True])
def test_process_liveness_keeps_identity_and_native_recommendation_separate(monkeypatch, recommend):
    from fastapi.testclient import TestClient
    state = {"healthy": False, "windowHealthy": False, "windowPhase": "failed",
             "restartRecommended": recommend}
    monkeypatch.setattr(main, "_runtime_health_hook", lambda: dict(state))
    monkeypatch.setattr(main._local_mcp, "started", False)
    monkeypatch.setenv("LAKE_SUPERVISED_WORKER", "native-grace-generation")
    result = TestClient(main.app).get("/api/health/live")
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "ok" and body["processAlive"]
    assert body["pid"] == os.getpid() and body["instance"] == "native-grace-generation"
    assert body["degraded"] and body["components"]["healthy"] is False
    assert body["restartRecommended"] is recommend


@pytest.mark.parametrize("hook_error", [False, True])
def test_health_hook_error_and_memory_pressure_cannot_recommend_process_restart(monkeypatch, hook_error):
    from fastapi.testclient import TestClient
    def broken():
        raise RuntimeError("observer unavailable")
    monkeypatch.setattr(main, "_runtime_health_hook", broken if hook_error else lambda: True)
    monkeypatch.setattr(main.resource_pressure, "snapshot", lambda: {"busy": True})
    monkeypatch.setattr(main._local_mcp, "started", False)
    result = TestClient(main.app).get("/api/health/live")
    assert result.status_code == 200
    body = result.json()
    assert body["processAlive"] and body["degraded"] and not body["restartRecommended"]
    if hook_error:
        assert body["components"]["reason"] == "health_hook_error"


def test_mcp_health_observer_error_does_not_hide_process_liveness(monkeypatch):
    from fastapi.testclient import TestClient
    def broken():
        raise RuntimeError("observer unavailable")
    monkeypatch.setattr(main, "_runtime_health_hook", lambda: True)
    monkeypatch.setattr(main._local_mcp, "started", True)
    monkeypatch.setattr(main._local_mcp.transport_host, "health_snapshot", broken)
    result = TestClient(main.app).get("/api/health/live")
    assert result.status_code == 200
    body = result.json()
    assert body["processAlive"] and body["degraded"] and not body["restartRecommended"]
    assert body["components"]["mcp"]["reason"] == "health_snapshot_error"


def test_liveness_exposes_only_counts_for_work_that_must_be_allowed_to_settle(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "_runtime_health_hook", lambda: True)
    monkeypatch.setitem(main._http_activity, "http", 3)
    monkeypatch.setattr(main._local_mcp._work_lane, "_owned", 2)
    monkeypatch.setattr(main._local_mcp._control_lane, "_owned", 1)
    monkeypatch.setattr(main, "active_sso_executions", lambda: 1)
    body = TestClient(main.app).get("/api/health/live").json()
    assert body["activeWork"] == {"http": 3, "mcp": 3, "sso": 1}
    assert body["processAlive"] and not body["restartRecommended"]
