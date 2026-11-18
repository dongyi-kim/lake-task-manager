"""Raw comment caches preserve source coverage and refuse unknown values."""
import time

import pytest

from app.infra.cache import Cache
from app.infra.cache_policy import read_scope
from app.jira.ticket_body_cache import TicketFieldCache


@pytest.fixture
def comments():
    cache = Cache(":memory:")
    try:
        yield cache, TicketFieldCache(cache, "session", 600, cache.fence(), "comment")
    finally:
        cache.close()


def issue(value, key="DL-1"):
    return {"key": key, "fields": {"updated": "2026-11-18T00:00:00Z", "comment": value}}


def test_raw_partial_object_and_empty_array_remain_distinct(comments):
    cache, store = comments
    partial = {"startAt": 5, "total": 100, "maxResults": 25,
               "comments": [{"id": str(i), "body": "body"} for i in range(25)]}
    rows = [issue(partial), issue([], "DL-2")]
    store.store(rows, time.time())
    assert store.read_latest(["DL-1", "DL-2"], 10000) == {"DL-1": partial, "DL-2": []}
    assert cache.get(store._head_key("DL-1"))


@pytest.mark.parametrize("value", [None, "", {}, {"comments": None}, [None], [{"body": "missing id"}]])
def test_unknown_or_malformed_comments_never_publish_heads(comments, value):
    cache, store = comments
    store.store([issue(value)], time.time())
    assert store.read_latest(["DL-1"], 10000) == {}
    assert cache.get(store._head_key("DL-1")) is None


def test_latest_obeys_live_policy_and_warm_size_preflight(comments, monkeypatch):
    cache, store = comments
    value = {"startAt": 0, "total": 1, "comments": [{"id": "1", "body": "x" * 200}]}
    now = time.time()
    with read_scope(cache_mode="live"):
        store.store([issue(value)], now)
        assert store.read_latest(["DL-1"], 1000) == {"DL-1": value}
    with read_scope(cache_mode="live"):
        assert store.read_latest(["DL-1"], 1000) == {}
    original = cache.get_with_metadata
    loaded = []
    def load(key, **kwargs):
        loaded.append(key)
        return original(key, **kwargs)
    monkeypatch.setattr(cache, "get_with_metadata", load)
    with pytest.raises(ValueError, match="16 MiB"):
        store.read_latest(["DL-1"], 100)
    assert not loaded
