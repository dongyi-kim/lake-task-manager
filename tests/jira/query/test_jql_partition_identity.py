"""Ambiguous Jira user literals and incomplete rows cannot poison reusable caches."""

from copy import deepcopy

import pytest

from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient
from app.jira.jql import fields_with_order


def test_display_name_alias_stays_opaque_while_verified_reporter_is_cached(monkeypatch):
    client = JiraClient(get_settings(), Cache(":memory:"))
    query = 'assignee = "John Smith" OR reporter = bob'
    row = {"key": "DL-1", "fields": {
        "assignee": {"name": "jsmith", "displayName": "John Smith"},
        "reporter": {"name": "bob"}, "status": {"statusCategory": {"key": "new"}},
    }}
    calls = []

    def fetch(*args):
        calls.append(args[0])
        return [row]

    monkeypatch.setattr(client, "_fetch_exhaustive_jql", fetch)
    assert client.search_issues(query) == [row]
    alias = 'assignee = "John Smith"'
    assert client.cache.get(client._jql_leaf_key(alias)[2]) is None
    assert client.cache.get(client._jql_leaf_key("reporter = bob")[2]) == ["DL-1"]
    assert client.search_issues(alias) == [row]
    assert len(calls) == 2


def test_missing_selector_does_not_overwrite_complete_issue_cache(monkeypatch):
    client = JiraClient(get_settings(), Cache(":memory:"))
    original_row = client.get_issue_light("DL-9012")
    partial = deepcopy(original_row)
    partial["fields"].pop("reporter")
    get = client.provider.get_json

    def incomplete(path, params=None, **kwargs):
        if path == "/rest/api/2/search":
            return {"startAt": 0, "total": 1, "maxResults": 100, "issues": [partial]}
        return get(path, params=params, **kwargs)

    monkeypatch.setattr(client.provider, "get_json", incomplete)
    result = client.search_issues("assignee = test.ui01 OR reporter = test.ui02")
    assert result == [partial]
    assert client.get_issue_light("DL-9012") == original_row


@pytest.mark.parametrize("selector", ["reporter", "epic"])
def test_incomplete_group_rows_remain_query_scoped_and_preserve_shared_projection(monkeypatch, selector):
    client = JiraClient(get_settings(), Cache(":memory:"))
    original = client.search_issues("key = DL-9012")[0]
    field = "reporter" if selector == "reporter" else client.s.epic_link_field_id
    partial = deepcopy(original)
    partial["fields"].pop(field)
    query = ("assignee = test.ui01 OR reporter = test.ui02" if selector == "reporter"
             else '"Epic Link" = DL-9000 OR "Epic Link" = DL-99999')
    compiled = client._compile_jql(query)
    fields = fields_with_order(client._issue_fields(light=True), compiled.order)
    projection = client._projection_signature(fields, True)
    row_key = client._jql_row_key(client._jql_generation(), projection, original["key"])
    assert client.cache.get(row_key) == original
    get, calls = client.provider.get_json, []

    def incomplete(path, params=None, **kwargs):
        if path == "/rest/api/2/search":
            calls.append(params["jql"])
            if " OR " in params["jql"]:
                return {"startAt": 0, "total": 1, "maxResults": 100, "issues": [partial]}
        return get(path, params=params, **kwargs)

    monkeypatch.setattr(client.provider, "get_json", incomplete)
    first = client.search_issues_page(query)
    assert first["issues"] == [partial]
    assert client.search_issues_page(query, snapshot_id=first["snapshotId"])["issues"] == [partial]
    assert client.search_issues(query + " ORDER BY summary") == [partial]
    assert len(calls) == 1  # Exact opaque replay requires no immediate repair request.
    assert client.cache.get(row_key) == original
    assert client.cache.get(f"issueL:{client.env}:{original['key']}") == original
    # Another canonical query can reuse the prior complete row, never the private partial row.
    assert client.search_issues("key = DL-9012 ORDER BY summary") == [original]
    assert len(calls) == 1
    for leaf in compiled.leaves:
        assert client.cache.get(client._jql_leaf_key(leaf)[2]) is None
    opaque = client.cache.entries_by_prefix("jqlopaque:")
    assert len(opaque) == 1 and list(opaque.values()) == [[partial]]


def test_missing_field_without_existing_rows_never_marks_another_leaf_ready(monkeypatch):
    client = JiraClient(get_settings(), Cache(":memory:"))
    partial = {"key": "DL-9012", "fields": {"assignee": {"name": "test.ui01"},
                                                "status": {"statusCategory": {"key": "new"}}}}
    get = client.provider.get_json
    def incomplete(path, params=None, **kwargs):
        if path == "/rest/api/2/search" and " OR " in params["jql"]:
            return {"startAt": 0, "total": 1, "issues": [partial]}
        return get(path, params=params, **kwargs)
    monkeypatch.setattr(client.provider, "get_json", incomplete)
    query = "assignee = test.ui01 OR reporter = test.ui02"
    assert client.search_issues(query) == [partial]
    compiled = client._compile_jql(query)
    fields = fields_with_order(client._issue_fields(light=True), compiled.order)
    projection = client._projection_signature(fields, True)
    assert client.cache.get(client._jql_row_key(0, projection, "DL-9012")) is None
    assert client.cache.get("issueL:mock:DL-9012") is None
    # A known membership for a different query must still obtain a complete projection.
    leaf = "key = DL-9012"
    client.cache.set(client._jql_leaf_key(leaf)[2], ["DL-9012"], 600)
    assert not client._jql_leaf_cache_ready(leaf, fields, True, 0, projection)
    assert "reporter" in client.search_issues(leaf)[0]["fields"]


def test_null_requested_selectors_remain_valid_shared_rows(monkeypatch):
    client = JiraClient(get_settings(), Cache(":memory:"))
    row = {"key": "DL-1", "fields": {"assignee": None, "reporter": None, "status": None}}
    monkeypatch.setattr(client, "_fetch_exhaustive_jql", lambda *_args: [row])
    assert client.search_issues("key = DL-1") == [row]
    assert not client.cache.entries_by_prefix("jqlopaque:")
