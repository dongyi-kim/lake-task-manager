from collections import Counter

from fastapi.testclient import TestClient


def test_opt_in_fixture_has_exact_current_scope_and_deterministic_independent_worlds():
    from app.mock.tracking_fixture import build_tracking_fixture
    fixture = build_tracking_fixture()
    second = build_tracking_fixture()
    assert fixture.as_of == "2026-11-16T12:00:00+09:00"
    assert Counter(i["type"] for i in fixture.issues.values()) == {"Epic": 10, "Task": 30, "Sub-Task": 60}
    assert all(i["statusCategory"] != "done" for i in fixture.issues.values())
    assert len(fixture.people["Tracking"]) == 15
    assert fixture.issues == second.issues and fixture.expected == second.expected
    fixture.issues["TRACK-1"]["comments"].clear()
    assert fixture.issues != second.issues


def test_fixture_serves_realistic_hierarchy_identity_and_known_events():
    from app.mock.tracking_fixture import build_tracking_fixture
    fixture = build_tracking_fixture(heavy=True)
    with TestClient(fixture.make_app(comment_limit=2, changelog_limit=2)) as http:
        page = http.get("/rest/api/2/search", params={"jql": 'project = "TRACK"', "fields":
            "summary,parent,subtasks,creator,reporter,comment", "expand": "changelog", "maxResults": 100}).json()
        assert page["total"] == 100
        assert any(row["fields"].get("parent") for row in page["issues"])
        assert any(row["fields"]["comment"]["total"] > 100 for row in page["issues"])
        assert any(row["changelog"]["total"] > 100 for row in page["issues"])
        assert all(row["fields"]["creator"] != row["fields"]["reporter"] for row in page["issues"])
        assert all("description" not in row["fields"] for row in page["issues"])
        assert {"created", "completed", "reopened", "comment", "commentEdited", "fieldChanged"} <= {
            e["kind"] for e in fixture.expected["events"]}


def test_fixture_event_oracle_matches_real_collector_interpretation():
    from datetime import datetime
    from app.mock.tracking_fixture import build_tracking_fixture
    from app.tracking.analysis import analyze_issue, period_bounds
    fixture = build_tracking_fixture()
    start, end = period_bounds("2026-01-01", "2026-11-16", datetime.fromisoformat(fixture.as_of))
    actual = []
    with TestClient(fixture.make_app()) as http:
        statuses = http.get("/rest/api/2/status").json()
        categories = {str(s[k]).casefold(): s["statusCategory"]["key"] for s in statuses for k in ("id", "name")}
        for raw in http.get("/rest/api/2/search", params={"maxResults": 100, "expand": "changelog"}).json()["issues"]:
            _issue, events, missing = analyze_issue(raw, raw["fields"]["comment"]["comments"], start, end,
                fixture.people["Tracking"], categories, fixture.epic_field, True, True)
            assert not missing
            actual.extend((e["issueKey"], e["kind"], e["actorId"], datetime.fromisoformat(e["at"])) for e in events)
    expected = [(e["issueKey"], e["kind"], e["actorId"], datetime.fromisoformat(e["at"]))
                for e in fixture.expected["events"] if start <= datetime.fromisoformat(e["at"]) < end]
    assert Counter(actual) == Counter(expected)


def test_archived_workload_preserves_active_scope_and_can_use_dated_index_candidates():
    from app.mock.tracking_fixture import build_tracking_fixture
    fixture = build_tracking_fixture(archived_count=9900)
    assert len(fixture.issues) == 10000
    assert Counter(i["type"] for i in fixture.issues.values() if i["statusCategory"] != "done") == {
        "Epic": 10, "Task": 30, "Sub-Task": 60}
    with TestClient(fixture.make_app()) as http:
        response = http.get("/rest/api/2/search", params={"jql": 'project = "TRACK" AND updated >= "2026-11-01"',
                                                        "fields": "summary", "maxResults": 100}).json()
        assert response["total"] == 100
