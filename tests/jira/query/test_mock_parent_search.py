"""Mock REST conformance, independent of the evaluated Agent and fixture ticket IDs."""
import copy

import pytest
from fastapi.testclient import TestClient
from jira820 import make_app
from jira820.config import Config

from app.mock.permissions import PermissionStore
from app.mock.query_compat import install_query_compat


@pytest.fixture
def mock_search():
    store = PermissionStore(Config(project_key="DEMO", latency_ms=0))
    template = copy.deepcopy(next(iter(store.issues.values())))
    store.issues = {}
    for key, parent in (("DEMO-1", ""), ("DEMO-2", ""), ("DEMO-3", "DEMO-1"),
                        ("DEMO-4", "DEMO-1"), ("DEMO-5", "DEMO-2"), ("DEMO-6", "")):
        store.issues[key] = {**copy.deepcopy(template), "key": key, "parentKey": parent,
                             "summary": "child" if parent else "independent",
                             "subtasks": [], "type": "Sub-Task" if parent else "Task"}
    store.issues["DEMO-1"]["subtasks"] = ["DEMO-3", "DEMO-4"]
    store.reindex()
    app = make_app(store=store)
    install_query_compat(app)
    with TestClient(app) as http:
        yield http, store


@pytest.mark.parametrize(("query", "expected"), [
    ('parent = "DEMO-1"', ["DEMO-3", "DEMO-4"]),
    ("PARENT = 'demo-2'", ["DEMO-5"]),
    ('"parent" IN ("DEMO-2", "DEMO-1")', ["DEMO-3", "DEMO-4", "DEMO-5"]),
    ('parent != "DEMO-1"', ["DEMO-5"]),
    ('parent NOT IN ("DEMO-1", "DEMO-2")', []),
    ('parent NOT IN ("DEMO-1", "DEMO-99")', ["DEMO-5"]),
    ('parent = "DEMO-99"', []),
    ('parent != "DEMO-99"', ["DEMO-3", "DEMO-4", "DEMO-5"]),
    ('(parent = "DEMO-1" OR parent = "DEMO-2") AND key != "DEMO-4"', ["DEMO-3", "DEMO-5"]),
    ('parent = "DEMO-1" OR (parent = "DEMO-2" AND key = "DEMO-99")', ["DEMO-3", "DEMO-4"]),
    ('parent = "DEMO-1" OR key = "DEMO-6"', ["DEMO-3", "DEMO-4", "DEMO-6"]),
    ('NOT (parent IN ("DEMO-1"))', ["DEMO-5"]),
    ('NOT (parent = "DEMO-1" OR parent = "DEMO-2")', []),
    ('parent = "DEMO-1" ORDER BY key DESC', ["DEMO-4", "DEMO-3"]),
    ('summary ~ "independent"', ["DEMO-1", "DEMO-2", "DEMO-6"]),
    ('summary ~ "parent" OR key = "DEMO-6"', ["DEMO-6"]),
])
def test_parent_operators_boolean_precedence_and_order(mock_search, query, expected):
    http, _store = mock_search
    result = http.get("/rest/api/2/search", params={"jql": query, "fields": "summary,parent"})
    assert result.status_code == 200
    assert [issue["key"] for issue in result.json()["issues"]] == expected
    assert result.json()["total"] == len(expected)
    assert all("summary" in issue["fields"] for issue in result.json()["issues"])


def test_visibility_precedes_pagination_and_parent_references_remain(mock_search):
    http, store = mock_search
    store.denied_issue_keys = {"DEMO-3"}
    for query, expected in [('parent = "DEMO-1"', ["DEMO-4"]),
                            ('key IN ("DEMO-3", "DEMO-4", "DEMO-5")', ["DEMO-4", "DEMO-5"])]:
        pages = [http.get("/rest/api/2/search", params={"jql": query + " ORDER BY key ASC",
                                                       "startAt": index, "maxResults": 1}).json()
                 for index in range(len(expected) + 1)]
        assert all(page["total"] == len(expected) for page in pages)
        assert [issue["key"] for page in pages for issue in page["issues"]] == expected
    for suffix in ("", "/comment", "/changelog", "/remotelink", "/editmeta"):
        assert http.get("/rest/api/2/issue/DEMO-3" + suffix).status_code == 403
    parent = http.get("/rest/api/2/issue/DEMO-1").json()
    assert {row["key"] for row in parent["fields"]["subtasks"]} == {"DEMO-3", "DEMO-4"}
    assert http.get("/rest/api/2/issue/DEMO-4").status_code == 200


@pytest.mark.parametrize("query", ['parent ~ "DEMO-1"', 'parent IN ()', 'parent IN ("DEMO-1",)'])
def test_unsupported_parent_does_not_silently_match_every_issue(mock_search, query):
    http, _store = mock_search
    result = http.get("/rest/api/2/search", params={"jql": query})
    assert result.status_code == 400 and result.json()["errorMessages"]
