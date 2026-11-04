"""WBS batches Jira requests without confusing person scope with Epic membership."""

import time

import pytest

from app.auth.base import SessionExpired, UpstreamUnavailable
from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


KEYS = ("DL-101", "DL-102", "DL-103")
PLAN = {"wbs": [{"epics": [{"key": key} for key in KEYS]}]}


def _client():
    return JiraClient(get_settings(), Cache(":memory:"))


def _observe(client, monkeypatch, transform=None):
    original = client.provider.get_json
    calls = []

    def get(path, params=None, **kwargs):
        calls.append((path, dict(params or {})))
        if transform:
            transformed = transform(path, params or {}, original, kwargs)
            if transformed is not None:
                return transformed
        return original(path, params=params, **kwargs)

    monkeypatch.setattr(client.provider, "get_json", get)
    return calls


def _membership_requests(calls):
    return [params for path, params in calls
            if path == "/rest/api/2/search" and '"Epic Link"' in params.get("jql", "")]


def test_batched_wbs_matches_individual_rollups_and_reuses_membership(monkeypatch):
    expected_client = _client()
    expected = {key: expected_client.epic_progress_one(key) for key in KEYS}
    client = _client()
    calls = _observe(client, monkeypatch)

    assert client.epic_progress_map(PLAN) == expected
    assert len(_membership_requests(calls)) == 1
    before = len(calls)
    for key in KEYS:
        assert client.direct_child_keys(key, parent_type="Epic")
        assert client.epic_issues(key)
    assert client.epic_progress_map(PLAN) == expected
    assert len(calls) == before


def test_fresh_relationship_cache_does_not_trigger_speculative_wbs_search(monkeypatch):
    client = _client()
    expected = client.epic_progress_map(PLAN)
    client.cache.invalidate("jqlleaf:")
    calls = _observe(client, monkeypatch)

    assert client.epic_progress_map(PLAN) == expected
    assert not _membership_requests(calls)


def test_wbs_empty_partition_keeps_agile_fallback(monkeypatch):
    expected_client = _client()
    expected = {key: expected_client.epic_progress_one(key) for key in KEYS}
    client = _client()

    def empty_first_epic(path, params, original, kwargs):
        if path == "/rest/api/2/search" and '"Epic Link"' in params.get("jql", ""):
            data = original(path, params=params, **kwargs)
            rows = [row for row in data["issues"]
                    if row["fields"].get(client.s.epic_link_field_id) != KEYS[0]]
            assert data["total"] < 100, "fixture must fit in one page"
            return dict(data, issues=rows, total=len(rows))

    calls = _observe(client, monkeypatch, empty_first_epic)
    assert client.epic_progress_map(PLAN) == expected
    assert len(_membership_requests(calls)) == 1
    assert any(path == f"/rest/agile/1.0/epic/{KEYS[0]}/issue" for path, _ in calls)


def test_failed_wbs_batch_does_not_retry_every_epic_or_cache_empty(monkeypatch):
    client = _client()

    def expired(path, params, original, kwargs):
        if path == "/rest/api/2/search" and '"Epic Link"' in params.get("jql", ""):
            raise SessionExpired("injected group authentication expiry")

    calls = _observe(client, monkeypatch, expired)
    with pytest.raises(SessionExpired):
        client.epic_progress_map(PLAN)
    assert len(_membership_requests(calls)) == 1
    for key in KEYS:
        assert client.cache.get(client._direct_child_cache_key(key)) is None
        assert client.cache.get(f"epic_issues:{client.env}:{key}") is None


def test_expired_but_alive_wbs_rows_keep_existing_network_failure_fallback(monkeypatch):
    client = _client()
    expected = client.epic_progress_map(PLAN)
    with client.cache._lock:
        for key in KEYS:
            for cache_key in (f"epic_issues:{client.env}:{key}", client._direct_child_cache_key(key)):
                client.cache._conn.execute("UPDATE cache SET fetched_at=? WHERE key=?",
                    (time.time() - client.s.cache_ttl_seconds - 1, cache_key))
        client.cache._conn.commit()
    attempts = []

    def offline(query, **kwargs):
        attempts.append(query)
        raise UpstreamUnavailable("offline fixture")

    monkeypatch.setattr(client, "_search", offline)
    assert client.epic_progress_map(PLAN) == expected
    assert all(" OR " not in query for query in attempts)
