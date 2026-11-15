"""Official Jira 8.20 search evidence, including source coverage and actor identity."""
from copy import deepcopy
from datetime import date

import pytest
from fastapi.testclient import TestClient
from jira820 import make_app
from jira820.config import Config

from app.mock.permissions import PermissionStore
from app.mock.query_compat import install_query_compat


def make_http(**options):
    store = PermissionStore(Config(project_key="DEMO", latency_ms=0))
    template = deepcopy(next(iter(store.issues.values())))
    store.users.update({uid: {"name": uid, "displayName": uid} for uid in ("creator", "reporter", "alice", "bob")})
    store.issues = {"DEMO-1": {**template, "key": "DEMO-1", "creator": "creator", "reporter": "reporter",
        "assignee": "bob", "created": date(2026, 10, 1), "updated": date(2026, 11, 16),
        "comments": [{"id": "c1", "author": "alice", "updateAuthor": "bob", "body": "Half complete",
                      "created": date(2026, 11, 10), "updated": date(2026, 11, 16)}],
        "changelog": [{"author": "bob", "date": date(2026, 11, 9), "time": "12:00", "items": [
            {"field": "assignee", "from": "alice", "fromString": "Alice", "to": "bob", "toString": "Bob"}]},
            {"author": "alice", "date": date(2026, 11, 10), "time": "13:00", "items": [
            {"field": "status", "from": "1", "fromString": "Open", "to": "3", "toString": "In Progress"}]}]}}
    store.reindex()
    app = make_app(store=store)
    install_query_compat(app, **options)
    return TestClient(app)


@pytest.mark.parametrize("method", ["get", "post"])
def test_search_evidence_projection_identity_and_complete_coverage(method):
    with make_http() as http:
        query = {"jql": 'key = "DEMO-1"', "fields": "creator,reporter,comment", "expand": "changelog"}
        response = http.get("/rest/api/2/search", params=query) if method == "get" else http.post(
            "/rest/api/2/search", json={**query, "fields": query["fields"].split(","), "expand": ["changelog"]})
        assert response.status_code == 200
        issue = response.json()["issues"][0]
        assert set(issue["fields"]) == {"creator", "reporter", "comment"}
        assert issue["fields"]["creator"]["name"] == "creator"
        assert issue["fields"]["reporter"]["name"] == "reporter"
        comments = issue["fields"]["comment"]
        assert (comments["startAt"], comments["total"], comments["maxResults"]) == (0, 1, 1)
        assert comments["comments"][0]["author"]["name"] == "alice"
        assert comments["comments"][0]["updateAuthor"]["name"] == "bob"
        assert (issue["changelog"]["total"], len(issue["changelog"]["histories"])) == (2, 2)
        detail = http.get("/rest/api/2/issue/DEMO-1", params=query).json()
        assert detail["fields"] == issue["fields"]
        assert detail["changelog"] == issue["changelog"]
        assert http.get("/rest/api/2/issue/DEMO-1/comment").json()["comments"] == comments["comments"]


def test_truncated_search_advertises_source_total_and_detail_can_complete():
    with make_http(comment_limit=0, changelog_limit=1) as http:
        issue = http.get("/rest/api/2/search", params={"fields": "comment", "expand": "changelog"}).json()["issues"][0]
        assert issue["fields"]["comment"] == {"startAt": 0, "maxResults": 0, "total": 1, "comments": []}
        assert issue["changelog"]["total"] == 2
        assert len(issue["changelog"]["histories"]) == 1
        assert len(http.get("/rest/api/2/issue/DEMO-1", params={"expand": "changelog"}).json()["changelog"]["histories"]) == 2


@pytest.mark.parametrize(("jql", "count"), [
    ('issue in updatedBy("alice", "2026-11-10", "2026-11-10")', 1),
    ('issue in updatedBy("alice", "2026-11-11", "2026-11-16")', 0),
    ('issue in updatedBy("bob", "2026-11-16", "2026-11-16")', 1),
    ('assignee WAS "alice" DURING ("2026-11-08", "2026-11-08")', 1),
    ('assignee WAS "alice" AFTER "2026-11-10"', 0),
    ('assignee CHANGED FROM "alice" TO "bob" BY "bob" DURING ("2026-11-09", "2026-11-09")', 1),
    ('status CHANGED TO "In Progress" BY "bob"', 0),
    ('status CHANGED TO "3" BY "alice"', 1),
    ('status WAS "Open" BEFORE "2026-11-09"', 1),
])
def test_historical_jql_matches_dated_actor_and_assignment_not_current_updated(jql, count):
    with make_http() as http:
        response = http.get("/rest/api/2/search", params={"jql": jql})
        assert response.status_code == 200
        assert response.json()["total"] == count


def test_unsupported_historical_clause_is_not_silently_ignored():
    with make_http() as http:
        response = http.get("/rest/api/2/search", params={"jql": 'assignee CHANGED NONSENSE "alice"'})
        assert response.status_code == 400


def test_light_search_does_not_render_unrequested_comment_bodies(monkeypatch):
    with make_http() as http:
        def unexpected_comment(*args):
            raise AssertionError("Unrequested comment serialization")
        monkeypatch.setattr(http.app.state.store.serializer, "comment_obj", unexpected_comment)
        response = http.get("/rest/api/2/search", params={"fields": "summary"})
        assert response.status_code == 200
        assert set(response.json()["issues"][0]["fields"]) == {"summary"}


def test_status_was_accepts_current_status_id_without_a_prior_status_change():
    with make_http() as http:
        store = http.app.state.store
        store.issues["DEMO-1"]["statusName"] = "Open"
        store.issues["DEMO-1"]["changelog"] = []
        result = http.get("/rest/api/2/search", params={"jql": 'status WAS "1"'}).json()
        assert result["total"] == 1


def test_unsupported_search_expansion_is_detectable_and_detail_sources_still_work():
    with make_http(supports_comment=False, supports_changelog=False) as http:
        result = http.get("/rest/api/2/search", params={"fields": "comment", "expand": "changelog"}).json()["issues"][0]
        assert "comment" not in result["fields"] and "changelog" not in result
        assert http.get("/rest/api/2/issue/DEMO-1", params={"fields": "comment", "expand": "changelog"}).json()["changelog"]["total"] == 2
