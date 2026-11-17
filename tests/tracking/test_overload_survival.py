"""API, MCP and month collection survive one bounded combined overload burst."""
import asyncio
import hashlib
from pathlib import Path
import threading
from types import SimpleNamespace

import anyio
from fastapi import FastAPI
import httpx
import pytest

from app.auth.base import UpstreamBusy
from app.infra.cache import Cache
from app.infra.request_admission import RequestAdmissionMiddleware
from app.infra.resource_pressure import resource_pressure
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient
from app.jira.jql_scheduling import BoundedQueryScheduler
from app.local_mcp.capacity import ToolCapacityExceeded, ToolLane
from app.tracking.manager import TrackingManager
from tracking.test_concurrent_load import MonthProvider


def test_month_collection_preserves_evidence_during_combined_api_and_mcp_overload(tmp_path):
    # Two distinct 2 MiB sources exercise body staging and durable rich evidence
    # without a memory-pressure/OOM test or an enormous synthetic dataset.
    editor_body = "editor-start:" + "E" * (2 * 1024 * 1024) + ":editor-end"
    comment_body = "comment-start:" + "C" * (2 * 1024 * 1024) + ":comment-end"
    editor_hash = hashlib.sha256(editor_body.encode()).hexdigest()
    comment_hash = hashlib.sha256(comment_body.encode()).hexdigest()

    class Provider(MonthProvider):
        def __init__(self):
            super().__init__(supports_parallel=True)
            self.collecting, self.collector_release = threading.Event(), threading.Event()

        def get_json(self, path, params=None):
            if path.endswith("/P-100/comment"):
                self.collecting.set()
                assert self.collector_release.wait(10), "collector was not released"
            result = super().get_json(path, params)
            if path.endswith("/P-099/comment"):
                result["comments"][0]["body"] = comment_body
            return result

    provider, cache = Provider(), Cache(":memory:")
    settings = SimpleNamespace(**vars(get_settings()))
    settings.jira_env, settings.project_key = "test", "P"
    settings.search_jira_projects, settings.epic_link_field_id = ["P"], "epic"
    client = JiraClient(settings, cache)
    client._provider, client._provider_built = provider, True
    client._query_scheduler = BoundedQueryScheduler(capacity=4)
    client.current_user_for_authorization = lambda: {"id": "owner"}
    manager = TrackingManager(client, Path(tmp_path) / "tracking.sqlite3",
        people_loader=lambda: {"M": [f"staff{i:02}" for i in range(15)]},
        plan_loader=lambda: {"wbs": [{"id": "month", "name": "Month", "module": "M",
                                    "epics": [{"key": f"P-{i:03}"} for i in range(1, 11)]}]})
    request = {"startDate": "2026-10-01", "endDate": "2026-10-31",
               "people": [f"staff{i:02}" for i in range(15)]}
    api_release, mcp_release = threading.Event(), threading.Event()
    api_entered = [threading.Event() for _ in range(8)]
    mcp_entered = [threading.Event() for _ in range(8)]
    app = FastAPI()

    @app.post("/api/hold")
    def api_work(body: dict):
        index = body["id"]
        api_entered[index].set()
        assert api_release.wait(10), "API handlers were not released"
        return {"id": index, "sha256": hashlib.sha256(body.get("html", "").encode()).hexdigest()}

    @app.get("/api/health/live")
    async def live():
        return {"status": "ok"}

    app.add_middleware(RequestAdmissionMiddleware, pressure_check=resource_pressure.check)

    async def until(predicate):
        with anyio.fail_after(10):
            while not predicate():
                await anyio.sleep(.005)

    def mcp_work(index):
        mcp_entered[index].set()
        # Two MCP reads occupy physical REST slots. The remaining accepted tools
        # retain their lane ownership without taking every collector page slot.
        if index >= 2:
            assert mcp_release.wait(10), "MCP tools were not released"
        return client._query_page(provider.mcp_jql, "summary", start_at=0, max_results=25)

    def stored_run_sizes():
        with manager.store.connect() as db:
            return db.execute("SELECT COUNT(*),SUM(LENGTH(snapshot)),SUM(LENGTH(checkpoint)) FROM runs").fetchone()

    def raw_comment_hash(run_id):
        _, _, _, checkpoint = manager.store.read(run_id, checkpoint_fields=("sources",))
        body = checkpoint["sources"]["P-099"]["comments"][0]["body"]
        return hashlib.sha256(body.encode()).hexdigest()

    async def scenario():
        lane, run_id = ToolLane(8), None
        api_jobs, mcp_jobs = [], []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as http:
            try:
                api_jobs = [asyncio.create_task(http.post("/api/hold", json={
                    "id": index, "html": editor_body if index == 0 else "small body"})) for index in range(8)]
                await until(lambda: all(event.is_set() for event in api_entered))
                mcp_jobs = [asyncio.create_task(lane.run(lambda index=index: mcp_work(index))) for index in range(8)]
                await until(lambda: all(event.is_set() for event in mcp_entered) and provider.saturated.is_set())
                run = await anyio.to_thread.run_sync(lambda: manager.create(request))
                run_id = run["id"]
                await until(provider.collecting.is_set)
                assert provider.overlap.is_set()
                assert len(manager.workers) == len(manager.controls) == 1
                assert all(not job.done() for job in [*api_jobs, *mcp_jobs])

                with anyio.fail_after(2):
                    refused = await asyncio.gather(*(http.post("/api/hold", json={"id": 999}) for _ in range(100)))
                    assert all(result.status_code == 429 for result in refused)
                    assert all(result.json()["executed"] is False and result.headers["Retry-After"] == "1"
                               for result in refused)
                    for _ in range(10):
                        with pytest.raises(ToolCapacityExceeded):
                            await lane.run(lambda: pytest.fail("excess MCP work executed"))
                    for _ in range(3):
                        assert (await http.get("/api/health/live")).json() == {"status": "ok"}
                before = stored_run_sizes()
                for _ in range(25):
                    with pytest.raises(UpstreamBusy):
                        await anyio.to_thread.run_sync(lambda: manager.create(request))
                assert before[0] == 1 and stored_run_sizes() == before
                assert len(manager.workers) == len(manager.controls) == 1

                api_release.set()
                mcp_release.set()
                provider.release.set()
                provider.collector_release.set()
                api_results, mcp_results = await asyncio.gather(asyncio.gather(*api_jobs), asyncio.gather(*mcp_jobs))
                assert all(result.status_code == 200 for result in api_results)
                assert api_results[0].json()["sha256"] == editor_hash
                assert all(len(result["issues"]) == 25 for result in mcp_results)
                assert lane.quiescent
                await until(lambda: run_id not in manager.workers)
                paused = await anyio.to_thread.run_sync(lambda: manager.get(run_id))
                assert paused["status"] == "paused" and paused["coverage"]["missing"]
                assert paused["errors"][0]["reason"] == "UpstreamBusy"
                preserved = {event["id"] for event in paused["events"]}
                assert raw_comment_hash(run_id) == comment_hash
                complete = await anyio.to_thread.run_sync(lambda: manager.resume(run_id, background=False))
                assert complete["status"] == "complete" and complete["coverage"]["complete"]
                assert complete["coverage"]["missing"] == []
                assert complete["coverage"]["issuesTotal"] == complete["coverage"]["issuesComplete"] == 100
                assert {issue["key"] for issue in complete["issues"]} == set(provider.rows)
                assert len(complete["events"]) == 300
                assert {(event["issueKey"], event["kind"]) for event in complete["events"]} == {
                    (key, kind) for key in provider.rows for kind in ("created", "completed", "comment")}
                assert preserved <= {event["id"] for event in complete["events"]}
                assert raw_comment_hash(run_id) == comment_hash
                assert provider.comment_calls.count("P-100") == 2 and len(provider.comment_calls) == 101
                assert provider.maximum <= 4
                assert manager.workers == manager.controls == {}
                assert (await http.post("/api/hold", json={"id": 0})).status_code == 200
                assert await lane.run(lambda: "recovered") == "recovered"
            finally:
                api_release.set()
                mcp_release.set()
                provider.release.set()
                provider.collector_release.set()
                await asyncio.gather(*api_jobs, *mcp_jobs, return_exceptions=True)
                if run_id:
                    with manager.lock:
                        signal, worker = manager.controls.get(run_id), manager.workers.get(run_id)
                        if signal is not None:
                            signal.set()
                    if worker is not None:
                        await anyio.to_thread.run_sync(lambda: worker.join(10))
                        assert not worker.is_alive()

    try:
        anyio.run(scenario)
    finally:
        cache.close()
