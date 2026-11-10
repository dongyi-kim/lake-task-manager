"""Streaming compatibility aggregates retain the query evidence they actually consumed."""
from copy import deepcopy
from datetime import datetime, timezone
import time

import pytest

from app.domain import mytasks
from app.infra.cache import Cache
from app.infra.cache_policy import CachePolicy, read_scope
from app.infra.settings import get_settings
from app.jira import jql as jql_compiler
from app.jira.jira_client import JiraClient


@pytest.fixture
def stream_client(monkeypatch):
    cache = Cache(":memory:")
    client = JiraClient(get_settings(), cache)
    client.provider  # Initialize the real in-process world before freezing collection time.
    reference = datetime.combine(client.s_today(), datetime.min.time(), timezone.utc).replace(hour=12)
    class QueryClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return reference.astimezone(tz) if tz else reference.replace(tzinfo=None)
    monkeypatch.setattr(mytasks, "datetime", QueryClock)
    monkeypatch.setattr(jql_compiler, "datetime", QueryClock)
    now = [time.time()]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now[0])
    cache.configure(CachePolicy())
    calls = []
    original = client.provider.get_json
    def record(path, *args, **kwargs):
        calls.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(client.provider, "get_json", record)
    yield client, now, calls
    client.close()
    cache.close()


def _stream(client):
    return mytasks.iter_my_task_models(client, user="test.ui01", scope="assignee")


def _aggregate(client):
    entries = client.cache.entries_by_prefix("mt:", retained=True)
    key = next(key for key in entries if key.endswith(":light"))
    return key, entries[key], client.cache.metadata(key)


@pytest.mark.parametrize("age", [120, 650])
def test_warm_stream_zero_rest_preserves_collection_age_and_has_no_upstream_claim(stream_client, age):
    client, now, calls = stream_client
    initial = list(_stream(client))[-1]
    key, rows, source = _aggregate(client)
    assert initial["model"]["groups"] and rows
    now[0] += age
    calls.clear()
    with read_scope(cache_mode="warm"):
        repeated = list(_stream(client))[-1]
        copied = client.cache.metadata(key)
    assert repeated["model"]["groups"] == initial["model"]["groups"]
    assert calls == []
    assert copied["fetchedAt"] == source["fetchedAt"]
    assert not copied["upstreamValidated"]
    assert copied["compliant"]


def test_hot_boundary_is_measured_from_original_query_not_warm_stream_copy(stream_client):
    client, now, calls = stream_client
    list(_stream(client))
    key, _rows, source = _aggregate(client)
    now[0] += 120
    with read_scope(cache_mode="warm"):
        list(_stream(client))
    for age, hit in ((599, True), (600, True), (601, False)):
        now[0] = source["fetchedAt"] + age
        with read_scope(cache_mode="hot"):
            assert (client.cache.get(key) is not None) is hit


def test_stream_publication_uses_captured_query_source_not_a_later_aggregate_replacement(stream_client):
    client, now, calls = stream_client
    initial = list(_stream(client))[-1]
    key, rows, source = _aggregate(client)
    original_rows = deepcopy(rows)
    now[0] += 120
    with read_scope(cache_mode="warm"):
        stream = _stream(client)
        assert next(stream)["replace"] is False
        assert next(stream)["model"]["groups"]
        client.cache.set_if_fence(key, [], 600, client.cache.fence(), fetched_at=now[0],
                                  upstream_validated=False)
        assert list(stream)[-1]["model"]["groups"] == initial["model"]["groups"]
        assert client.cache.get(key) == original_rows
        assert client.cache.metadata(key)["fetchedAt"] == source["fetchedAt"]
        assert not client.cache.metadata(key)["upstreamValidated"]


def test_mutation_while_stream_is_yielded_cannot_publish_a_complete_old_query(stream_client):
    client, now, calls = stream_client
    list(_stream(client))
    key, _rows, _source = _aggregate(client)
    now[0] += 120
    with read_scope(cache_mode="warm"):
        stream = _stream(client)
        next(stream)
        next(stream)
        client.cache.invalidate(key)
        final = list(stream)[-1]
        assert client.cache.get(key) is None
    assert final["partial"] and final["partialErrors"]


def test_closing_yielded_stream_keeps_completed_leaf_cache_but_not_query_aggregate(stream_client):
    client, _now, _calls = stream_client
    with read_scope(cache_mode="warm") as outer:
        stream = _stream(client)
        next(stream)
        completed = next(stream)
        stream.close()
        assert completed["model"]["groups"]
        assert client.cache.entries_by_prefix("mt:", retained=True) == {}
        leaf_keys = client.cache.entries_by_prefix("jqlleaf:", retained=True)
        assert leaf_keys
        before = len(outer)
        assert client.cache.get(next(iter(leaf_keys))) is not None
        assert len(outer) > before


def test_partial_query_keeps_visible_rows_without_caching_a_complete_list(stream_client, monkeypatch):
    client, _now, _calls = stream_client
    original = client.iter_search_issue_chunks
    def incomplete(*args, **kwargs):
        for chunk in original(*args, **kwargs):
            yield {**chunk, "error": {"kind": "transport", "message": "incomplete fixture"}}
    monkeypatch.setattr(client, "iter_search_issue_chunks", incomplete)
    with read_scope(cache_mode="warm"):
        final = list(_stream(client))[-1]
    assert final["partial"] and final["partialErrors"]
    assert client.cache.entries_by_prefix("mt:", retained=True) == {}


@pytest.mark.parametrize("flag", [{"partial": True}, {"complete": False}])
def test_declared_partial_chunk_cannot_publish_a_complete_compatibility_list(stream_client, monkeypatch, flag):
    client, _now, _calls = stream_client
    original = client.iter_search_issue_chunks
    def incomplete(*args, **kwargs):
        for chunk in original(*args, **kwargs):
            yield {**chunk, **flag}
    monkeypatch.setattr(client, "iter_search_issue_chunks", incomplete)
    with read_scope(cache_mode="warm"):
        final = list(_stream(client))[-1]
    assert final["partial"] and final["partialErrors"]
    assert client.cache.entries_by_prefix("mt:", retained=True) == {}


def test_source_expiring_while_browser_processes_snapshot_is_not_republished_as_current(stream_client):
    client, now, _calls = stream_client
    list(_stream(client))
    key, _rows, source = _aggregate(client)
    now[0] = source["fetchedAt"] + 599
    with read_scope(cache_mode="hot"):
        stream = _stream(client)
        next(stream)
        next(stream)
        now[0] += 2
        final = list(stream)[-1]
        assert client.cache.get(key) is None
        assert client.cache.metadata(key)["fetchedAt"] == source["fetchedAt"]
    assert final["partial"]


def test_live_stream_keeps_actual_upstream_validation_for_query_aggregate(stream_client):
    client, _now, calls = stream_client
    with read_scope(cache_mode="live"):
        final = list(_stream(client))[-1]
        _key, _rows, source = _aggregate(client)
    assert calls and final["model"]["groups"] and not final["partial"]
    assert source["upstreamValidated"] and source["compliant"]
