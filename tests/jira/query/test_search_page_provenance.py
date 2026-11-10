"""Direct Jira pages carry their actual observation and never poison shared rows."""
from copy import deepcopy
import time

import pytest

from app.auth.base import QueryStateChanged
from app.infra.cache import Cache
from app.infra.cache_policy import read_scope, reduce_read_metadata
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


@pytest.fixture
def client():
    value = JiraClient(get_settings(), Cache(":memory:"))
    yield value
    value.cache._conn.close()


@pytest.mark.parametrize("rows", [[], [{"key": "DL-1", "fields": {"summary": "one"}}]])
def test_live_unsupported_page_reports_request_start_even_without_cache_rows(client, monkeypatch, rows):
    clock = [time.time()]
    started, calls = clock[0], []
    monkeypatch.setattr("app.jira.jira_client.time.time", lambda: clock[0])

    def fetch(path, params=None):
        calls.append((path, params))
        clock[0] += 9
        return {"issues": rows, "total": len(rows), "maxResults": 100}

    monkeypatch.setattr(client.provider, "get_json", fetch)
    with read_scope(cache_mode="live") as sources:
        result = client.search_issues_page(
            'status WAS "Resolved" DURING ("2026-10-01", "2026-11-01")', fields="summary")
        source = reduce_read_metadata(sources)
    assert result["issues"] == rows
    assert len(calls) == 1
    assert source["fetchedAt"] == started
    assert source["upstreamValidated"] and source["compliant"]
    assert client.cache.get(f"issueL:{client.env}:DL-1") is None


@pytest.mark.parametrize("incomplete", [False, True])
def test_page_writes_only_complete_canonical_rows_at_original_age(client, monkeypatch, incomplete):
    row = deepcopy(client.get_issue_light("DL-9012"))
    client.cache.invalidate()
    if incomplete:
        row["fields"].pop("status")
    clock = [time.time()]
    started = clock[0]
    monkeypatch.setattr("app.jira.jira_client.time.time", lambda: clock[0])

    def fetch(*args, **kwargs):
        clock[0] += 9
        return {"issues": [row], "total": 1}

    monkeypatch.setattr(client.provider, "get_json", fetch)
    with read_scope(cache_mode="live"):
        client._legacy_search_issues_page("project = DL")
    source = client.cache.metadata(f"issueL:{client.env}:DL-9012")
    if incomplete:
        assert source is None
    else:
        assert source["fetchedAt"] == started


def test_mutation_during_direct_page_cannot_publish_or_certify_stale_rows(client, monkeypatch):
    row = deepcopy(client.get_issue_light("DL-9012"))
    client.cache.invalidate()

    def fetch(*args, **kwargs):
        client.cache.invalidate()
        return {"issues": [row], "total": 1}

    monkeypatch.setattr(client.provider, "get_json", fetch)
    with read_scope(cache_mode="live") as sources:
        with pytest.raises(QueryStateChanged):
            client._legacy_search_issues_page("project = DL")
    assert not sources
    assert client.cache.metadata(f"issueL:{client.env}:DL-9012") is None


@pytest.mark.parametrize("data", [None, {"issues": None}, {"issues": {}},
    {"issues": [None]}, {"issues": [{"key": "DL-1"}]},
    {"issues": [], "errorMessages": ["denied"]}, {"issues": [], "maxResults": "bad"}])
def test_invalid_search_response_cannot_certify_success(client, monkeypatch, data):
    monkeypatch.setattr(client.provider, "get_json", lambda *args, **kwargs: data)
    with read_scope(cache_mode="live") as sources:
        with pytest.raises(ValueError):
            client._legacy_search_issues_page("project = DL")
    assert not sources


def test_transport_failure_leaves_no_successful_observation(client, monkeypatch):
    def fail(*args, **kwargs):
        raise ConnectionError("interrupted")
    monkeypatch.setattr(client.provider, "get_json", fail)
    with read_scope(cache_mode="live") as sources:
        with pytest.raises(ConnectionError):
            client._legacy_search_issues_page("project = DL")
    assert not sources


def test_slow_direct_page_uses_elapsed_age_for_hot_compliance(client, monkeypatch):
    clock = [time.time()]
    started = clock[0]
    monkeypatch.setattr("app.jira.jira_client.time.time", lambda: clock[0])
    def fetch(*args, **kwargs):
        clock[0] += 601
        return {"issues": [], "total": 0}
    monkeypatch.setattr(client.provider, "get_json", fetch)
    with read_scope(cache_mode="hot") as sources:
        client._legacy_search_issues_page("project = DL")
        source = reduce_read_metadata(sources)
    assert source["fetchedAt"] == started and source["state"] == "stale"
    assert not source["compliant"]


def test_abandoned_snapshot_sources_cannot_certify_or_age_direct_fallback(client, monkeypatch):
    from app.infra.cache_policy import note_read
    from app.jira.jql import JqlUnsupported
    started = time.time()
    def abandoned(*args, **kwargs):
        note_read({"kind": "jqlleaf", "fetchedAt": started - 10000,
                   "freshUntil": 0, "displayUntil": 0, "state": "stale", "compliant": False})
        raise JqlUnsupported("incomplete plan")
    monkeypatch.setattr(client, "_jql_snapshot", abandoned)
    monkeypatch.setattr(client.provider, "get_json", lambda *args, **kwargs: {"issues": [], "total": 0})
    with read_scope(cache_mode="live") as sources:
        client.search_issues_page("project = DL", fields="summary")
        source = reduce_read_metadata(sources)
    assert source["fetchedAt"] >= started and source["compliant"]
    assert len(sources) == 1


def test_direct_page_does_not_replace_newer_canonical_row(client, monkeypatch):
    old = deepcopy(client.get_issue_light("DL-9012"))
    newer = deepcopy(old)
    newer["fields"]["summary"] = "newer concurrent read"
    key = f"issueL:{client.env}:DL-9012"
    def fetch(*args, **kwargs):
        client.cache.set_if_fence(key, newer, 600, client.cache.fence(), fetched_at=time.time() + 1)
        return {"issues": [old], "total": 1}
    monkeypatch.setattr(client.provider, "get_json", fetch)
    assert client._legacy_search_issues_page("project = DL")["issues"] == [old]
    assert client.cache.get(key) == newer


@pytest.mark.parametrize("patch", [{"total": True}, {"total": -1}, {"total": 1.5},
    {"total": "1"}, {"startAt": 1}, {"startAt": False}, {"maxResults": -1},
    {"maxResults": True}, {"maxResults": 1.5},
    {"total": 0}, {"issues": [], "total": 1},
    {"issues": [{"key": "DL-1", "fields": {}}, {"key": "DL-1", "fields": {}}], "total": 2}])
def test_contradictory_pagination_never_becomes_fresh_prefix(client, monkeypatch, patch):
    data = {"issues": [{"key": "DL-1", "fields": {}}], "total": 1, "startAt": 0,
            "maxResults": 100, **patch}
    monkeypatch.setattr(client.provider, "get_json", lambda *args, **kwargs: data)
    with read_scope(cache_mode="live") as sources:
        with pytest.raises(ValueError):
            client._legacy_search_issues_page("project = DL", fields="summary")
    assert not sources


def test_empty_page_after_total_remains_valid(client, monkeypatch):
    monkeypatch.setattr(client.provider, "get_json", lambda *args, **kwargs:
        {"issues": [], "total": 2, "startAt": 5, "maxResults": 100})
    with read_scope(cache_mode="live") as sources:
        page = client._legacy_search_issues_page("project = DL", start_at=5)
        assert reduce_read_metadata(sources)["compliant"]
    assert page["returned"] == 0 and not page["hasMore"]
