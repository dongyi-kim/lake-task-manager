"""Month collection and MCP reads share real admission without losing evidence."""
import asyncio
from copy import deepcopy
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace

import anyio
from fastapi import FastAPI
import httpx2
import pytest

from app.auth.base import UpstreamBusy
from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient
from app.jira.jql_scheduling import BoundedQueryScheduler
from app.local_mcp.capacity import ToolCapacityExceeded, ToolLane
from app.tracking.manager import TrackingManager


class MonthProvider:
    mcp_jql = "project = P ORDER BY key ASC"

    def __init__(self, supports_parallel):
        self.supports_parallel = supports_parallel
        self.lock = threading.Lock()
        self.release, self.saturated, self.overlap = (threading.Event() for _ in range(3))
        self.active = self.maximum = self.mcp_active = 0
        self.busy = True
        self.comment_calls = []
        self.rows = {}
        for index in range(100):
            key, user = f"P-{index + 1:03}", {"name": f"staff{index % 15:02}"}
            kind = "Epic" if index < 10 else "Task" if index < 40 else "Sub-task"
            fields = {"summary": key, "issuetype": {"name": kind}, "creator": user,
                      "assignee": user, "created": "2026-10-01T09:00:00+0900",
                      "updated": "2026-10-30T18:00:00+0900", "components": [{"name": "M"}],
                      "status": {"id": "2", "statusCategory": {"key": "done"}}}
            if 10 <= index < 40:
                fields["epic"] = f"P-{(index - 10) // 3 + 1:03}"
            elif index >= 40:
                fields["parent"] = {"key": f"P-{(index - 40) // 2 + 11:03}"}
            self.rows[key] = {"key": key, "fields": fields, "changelog": {"startAt": 0,
                "total": 1, "histories": [{"id": key, "created": "2026-10-20T10:00:00+0900",
                "author": user, "items": [{"field": "status", "from": "1", "to": "2"}]}]}}

    def get_json(self, path, params=None):
        params = params or {}
        mcp = params.get("jql") == self.mcp_jql
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.mcp_active += int(mcp)
            if self.mcp_active == (2 if self.supports_parallel else 1):
                self.saturated.set()
            if not mcp and self.mcp_active:
                self.overlap.set()
        try:
            if mcp:
                assert self.release.wait(5), "MCP workers were not released"
            time.sleep(0.002)  # Local transport latency makes page overlap observable.
            if path.endswith("/status"):
                return [{"id": "1", "statusCategory": {"key": "new"}},
                        {"id": "2", "statusCategory": {"key": "done"}}]
            if path.endswith("/comment"):
                key = path.split("/")[-2]
                with self.lock:
                    self.comment_calls.append(key)
                    if key == "P-100" and self.busy:
                        self.busy = False
                        raise UpstreamBusy("local admission pressure")
                return {"startAt": 0, "total": 1, "comments": [{"id": key,
                    "created": "2026-10-30T18:00:00+0900",
                    "author": self.rows[key]["fields"]["assignee"], "body": "Month evidence"}]}
            assert path.endswith("/search"), path
            keys = re.findall(r'"(P-\d+)"', params["jql"]) or list(self.rows)
            start = params.get("startAt", 0)
            size = min(params.get("maxResults", 100), 25)
            return {"startAt": start, "maxResults": size, "total": len(keys),
                    "issues": deepcopy([self.rows[key] for key in keys[start:start + size]])}
        finally:
            with self.lock:
                self.active -= 1
                self.mcp_active -= int(mcp)


@pytest.mark.parametrize("supports_parallel", [False, True], ids=["serial-sso", "parallel"])
def test_month_tracking_survives_concurrent_mcp_overload_and_busy_resume(tmp_path, supports_parallel):
    provider, cache = MonthProvider(supports_parallel), Cache(":memory:")
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
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    async def until(predicate):
        with anyio.fail_after(5):
            while not predicate():
                await anyio.sleep(0.005)

    def heavy_read(entered=None):
        if entered is not None:
            entered.set()
        return [client._query_page(provider.mcp_jql, "summary", start_at=offset % 100,
                                   max_results=25)["issues"] for offset in range(0, 400, 25)]

    async def scenario():
        lane = ToolLane(2)
        entered = [threading.Event(), threading.Event()]
        jobs = [asyncio.create_task(lane.run(lambda event=event: heavy_read(event))) for event in entered]
        run_id = None
        try:
            await until(provider.saturated.is_set)
            await until(lambda: all(event.is_set() for event in entered))
            run = await anyio.to_thread.run_sync(lambda: manager.create({
                "startDate": "2026-10-01", "endDate": "2026-10-31",
                "people": [f"staff{i:02}" for i in range(15)]}))
            run_id = run["id"]
            if supports_parallel:
                await until(provider.overlap.is_set)
            else:
                assert manager.workers[run_id].is_alive() and not lane.quiescent
            for _ in range(12):
                with pytest.raises(ToolCapacityExceeded):
                    await lane.run(heavy_read)
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                          base_url="http://local") as http:
                # Sync health uses the ordinary AnyIO worker pool while MCP owns its lane.
                for _ in range(3):
                    with anyio.fail_after(1):
                        assert (await http.get("/health")).json() == {"status": "ok"}
                        assert (await anyio.to_thread.run_sync(lambda: manager.get(run_id)))["id"] == run_id
            if not supports_parallel:
                assert manager.workers[run_id].is_alive() and provider.maximum == 1
            provider.release.set()
            results = await asyncio.gather(*jobs)
            assert all(sum(map(len, pages)) == 400 for pages in results)
            assert lane.quiescent
            assert await lane.run(lambda: client._query_page(provider.mcp_jql, "summary"))
            await until(lambda: not manager.workers[run_id].is_alive())
            paused = manager.get(run_id)
            assert paused["status"] == "paused" and not paused["coverage"]["complete"]
            assert paused["coverage"]["missing"]
            assert paused["errors"][0]["reason"] == "UpstreamBusy"
            preserved = {event["id"] for event in paused["events"]}
            assert preserved
            complete = await anyio.to_thread.run_sync(lambda: manager.resume(run_id, background=False))
            assert complete["status"] == "complete"
            assert complete["coverage"]["complete"] and complete["coverage"]["missing"] == []
            assert complete["coverage"]["issuesTotal"] == complete["coverage"]["issuesComplete"] == 100
            assert {issue["key"] for issue in complete["issues"]} == set(provider.rows)
            assert {(event["issueKey"], event["kind"]) for event in complete["events"]} == {
                (key, kind) for key in provider.rows for kind in ("created", "completed", "comment")}
            assert len(complete["events"]) == 300
            assert preserved <= {event["id"] for event in complete["events"]}
            assert provider.comment_calls.count("P-100") == 2
            assert len(provider.comment_calls) == 101  # Completed sources survive the pause.
            assert 3 <= provider.maximum <= 4 if supports_parallel else provider.maximum == 1
        finally:
            provider.release.set()
            await asyncio.gather(*jobs, return_exceptions=True)
            if run_id:
                manager.controls[run_id].set()
                await anyio.to_thread.run_sync(lambda: manager.workers[run_id].join(5))

    try:
        anyio.run(scenario)
    finally:
        cache.close()
