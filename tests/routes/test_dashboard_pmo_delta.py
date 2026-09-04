from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import dashboards


class _SnapshotCache:
    def __init__(self):
        self.rows: dict[tuple[str, str], list[dict]] = {}
        self.clock = 1000.0

    def recent_snapshots(self, kind, key, limit=30):
        return list(reversed(self.rows.get((kind, key), [])))[:limit]

    def add_snapshot(self, kind, key, metric):
        self.clock += 1
        self.rows.setdefault((kind, key), []).append({
            "takenAt": self.clock,
            "metric": metric,
        })


class _Client:
    def miss_begin(self):
        return None

    def miss_count(self):
        return 0

    def epic_progress_map(self, _plan):
        return {}


def _rollup(progress: float) -> dict:
    return {
        "generatedAt": "2026-09-04T00:00:00Z",
        "rollup": {
            "pmo": {
                "progressPct": progress,
                "rawDoneSp": progress,
                "rawTotalSp": 100,
                "rawMockSp": 0,
            },
            "modules": [{
                "id": "lake",
                "progressPct": progress,
                "rawDoneSp": progress,
                "rawTotalSp": 100,
                "rawMockSp": 0,
            }],
        },
    }


def test_wbs_route_persists_project_scoped_delta_history(monkeypatch):
    cache = _SnapshotCache()
    state = {"project": "ALPHA", "progress": 20.0}
    monkeypatch.setattr(
        dashboards, "load_plan", lambda: {"project_key": state["project"]},
    )
    monkeypatch.setattr(
        dashboards.rollup, "build", lambda _plan, _progress: _rollup(state["progress"]),
    )
    app = FastAPI()
    app.include_router(dashboards.build_dashboard_router(
        client=_Client(),
        cache=cache,
        settings=SimpleNamespace(jira_base="https://jira.example"),
        scoped_people=lambda: {},
        require_manager=lambda: None,
        require_module_access=lambda _module: None,
        require_person_access=lambda _person: None,
    ))
    client = TestClient(app)

    first = client.get("/api/wbs").json()
    assert first["deltaBrief"]["status"] == "first_snapshot"

    state["progress"] = 35.0
    second = client.get("/api/wbs").json()
    assert second["deltaBrief"]["status"] == "improved"
    progress = next(
        row for row in second["deltaBrief"]["facts"]
        if row["metric"] == "progressPct"
    )
    assert progress == {
        "metric": "progressPct", "label": "전체 진척률",
        "before": 20.0, "current": 35.0, "delta": 15.0, "unit": "%p",
    }
    assert len(cache.rows[("pmo-detail", "ALPHA")]) == 2

    state.update(project="BETA", progress=80.0)
    isolated = client.get("/api/wbs").json()
    assert isolated["deltaBrief"]["status"] == "first_snapshot"
    assert len(cache.rows[("pmo-detail", "BETA")]) == 1
