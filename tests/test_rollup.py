"""rollup.build 다운스트림 조합 검증 (Jira 불필요)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.domain import rollup   # noqa: E402


PLAN = {
    "project_key": "LAKE",
    "modules": ["A"],
    "epics": {"E1": "e1", "E2": "e2"},
    "wbs": [{
        "id": "W1", "module": "A", "name": "w1",
        "start": "2026-01-01", "end": "2026-02-01",
        "epics": [{"key": "E1", "weight": 0.5}, {"key": "E2", "weight": 0.5}],
    }],
}


def test_weighted_wbs_and_pmo():
    epic_prog = {
        "E1": {"progressPct": 100.0, "doneSp": 10, "totalSp": 10, "mockSp": 0},
        "E2": {"progressPct": 0.0, "doneSp": 0, "totalSp": 10, "mockSp": 0},
    }
    data = rollup.build(PLAN, epic_prog, generated_at="fixed")
    w = data["wbs"][0]
    assert w["progressPct"] == 50.0           # 0.5*100 + 0.5*0
    assert data["rollup"]["pmo"]["progressPct"] == 50.0
    assert data["rollup"]["pmo"]["rawTotalSp"] == 20
    assert data["timeline"] == {"start": "2026-01-01", "end": "2026-02-01"}


def test_epic_occurrence_dates_within_wbs():
    data = rollup.build(PLAN, {}, generated_at="fixed")
    for w in data["wbs"]:
        for e in w["epics"]:
            assert w["start"] <= e["start"] < e["end"] <= w["end"]
