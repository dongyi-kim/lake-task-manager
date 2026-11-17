"""The normal development world has a sizable, current Tracking workload."""
from collections import Counter
from datetime import date, timedelta

import pytest

from app.infra.settings import load_people, load_plan
from app.mock.world import World


def sample_rows(world):
    return [row for row in world.issues.values() if "mock-rich-tracking" in row["labels"]]


@pytest.mark.parametrize("today", [date(2026, 11, 17), date(2027, 1, 3)])
def test_normal_world_has_current_mapped_tasks_subtasks_and_monthly_voc(today):
    world = World(load_plan(), load_people(), today)
    rows = sample_rows(world)
    assert len(rows) == 210
    voc = [row for row in rows if row["component"] == "사용자 VoC"]
    tasks = [row for row in rows if row["type"] == "Task" and row not in voc]
    subs = [row for row in rows if row["type"] == "Sub-Task"]
    assert (len(tasks), len(subs), len(voc)) == (30, 60, 120)
    configured = {epic["key"] for task in world.plan["wbs"] if task["module"] == "ETL"
                  for epic in task["epics"]}
    assert {row["epicKey"] for row in tasks} == configured
    assert {row["assignee"] for row in rows} == set(world.people["ETL"])
    assert all(today - timedelta(days=30) <= row["created"] < today for row in voc)
    assert sum(row["created"] >= today - timedelta(days=6) for row in voc) >= 30
    for task in tasks:
        assert len(task["subtasks"]) == 2
        assert task["key"] in world.epic_children[task["epicKey"]]
        assert all(world.issues[key]["parentKey"] == task["key"] for key in task["subtasks"])


def test_rich_world_history_is_consistent_and_exercises_tracking_edge_cases():
    today = date(2026, 11, 17)
    world = World(load_plan(), load_people(), today)
    rows = sample_rows(world)
    assert rows, "rich workload must be enabled in the ordinary mock/local world"
    names = {"1": "Open", "3": "In Progress", "4": "Reopened", "5": "Resolved", "6": "Closed"}
    transitions = Counter()
    same_day = False
    inactive = False
    long_wait = False
    for row in rows:
        previous = "1"
        stamps = []
        for history in row["changelog"]:
            stamps.append((history["date"], history["time"]))
            assert row["created"] <= history["date"] <= row["updated"] < today
            for item in history["items"]:
                if item["field"] == "status":
                    assert item["from"] == previous
                    assert item["fromString"] == names[item["from"]]
                    assert item["toString"] == names[item["to"]]
                    transitions[item["from"], item["to"]] += 1
                    previous = item["to"]
        assert stamps == sorted(stamps)
        assert row["statusName"] == names[previous]
        assert all(row["created"] <= c["created"] <= row["updated"] < today for c in row["comments"])
        assert (row["resolved"] is not None) == (previous in {"5", "6"})
        same_day |= any(count >= 4 for count in Counter(day for day, _ in stamps).values())
        inactive |= row["updated"] < today - timedelta(days=7)
        long_wait |= any(item["field"] == "status" and item["to"] == "3"
                         and history["date"] >= today - timedelta(days=6)
                         and (history["date"] - row["created"]).days >= 60
                         for history in row["changelog"] for item in history["items"])
    assert transitions["5", "6"] and transitions["5", "4"] and transitions["4", "3"]
    assert same_day and inactive and long_wait
    assert max(len(row["comments"]) for row in rows) > 50
    assert max(len(row["changelog"]) for row in rows) > 50
    assert any("||" in row["description"] and "{code" in row["description"] for row in rows)


def test_rich_workload_is_deterministic_without_mutating_plan_or_existing_fixtures():
    plan, people = load_plan(), load_people()
    today = date(2026, 11, 17)
    first = World(plan, people, today)
    second = World(plan, people, today)
    assert sample_rows(first), "rich workload must be enabled in the ordinary mock/local world"
    assert sample_rows(first) == sample_rows(second)
    assert first.plan == load_plan() and first.people == load_people()
    assert len(first.issues["DL-9100"]["subtasks"]) == 14
    assert first.issues["DL-9201"]["epicKey"] == "DL-9200"


def test_normal_jira_bridge_serves_current_voc_and_complete_heavy_evidence(monkeypatch):
    from fastapi.testclient import TestClient
    from app.mock import fakebridge

    world = World(load_plan(), load_people(), date(2026, 11, 17))
    monkeypatch.setattr(fakebridge, "get_world", lambda: world)
    with TestClient(fakebridge.build_injected_app()) as http:
        response = http.get("/rest/api/2/search", params={
            "jql": 'project = DL AND labels = "mock-rich-tracking" AND component = "사용자 VoC" '
                   'AND created >= "2026-11-11"',
            "fields": "summary,status,assignee,created", "maxResults": 100,
        })
        assert response.status_code == 200
        assert response.json()["total"] == 30
        assert len(response.json()["issues"]) == 30
        heavy = http.get("/rest/api/2/search", params={
            "jql": 'key IN (DL-10000, DL-10003)', "fields": "summary,comment,subtasks",
            "expand": "changelog", "maxResults": 10,
        }).json()
        records = {row["key"]: row for row in heavy["issues"]}
        assert records["DL-10000"]["fields"]["comment"]["total"] == 75
        assert records["DL-10003"]["changelog"]["total"] == 65
        assert len(records["DL-10000"]["fields"]["subtasks"]) == 2
