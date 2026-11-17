"""Ticket bodies are independently versioned, bounded and policy-aware."""
import time

import pytest

from app.auth.base import QueryStateChanged
from app.infra.cache import Cache
from app.infra.cache_policy import CachePolicy, read_scope
from app.jira.ticket_body_cache import TicketBodyCache


def issue(key="DL-1", description="body", updated="2026-11-17T01:00:00.000+0000"):
    return {"key": key, "fields": {"updated": updated, "description": description}}


@pytest.fixture
def bodies():
    cache = Cache(":memory:")
    try:
        yield cache, TicketBodyCache(cache, "session-one", 600, cache.fence())
    finally:
        cache.close()


def test_known_empty_is_shared_but_absent_body_or_version_is_not(bodies):
    cache, store = bodies
    rows = [issue("DL-1", None), issue("DL-2", ""), issue("DL-3", "complete"),
            issue("DL-4"), issue("DL-5", updated=None)]
    del rows[3]["fields"]["description"]
    store.store(rows, time.time())
    assert store.read(rows, 1024) == {"DL-1": None, "DL-2": "", "DL-3": "complete"}
    assert TicketBodyCache(cache, "other-session", 600, cache.fence()).read(rows, 1024) == {}
    assert store.read([issue(updated="newer")], 1024) == {}


def test_source_age_is_preserved_and_live_requires_this_request_validation(bodies, monkeypatch):
    cache, store = bodies
    now = time.time()
    clock = [now]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])
    row = issue()
    with read_scope(cache_mode="live"):
        store.store([row], now)
        assert store.read([row], 1024) == {"DL-1": "body"}
    clock[0] += 70
    with read_scope(cache_mode="hot") as sources:
        assert store.read([row], 1024) == {"DL-1": "body"}
        assert sources and all(source["fetchedAt"] == now for source in sources)
        assert all(not source["upstreamValidated"] for source in sources)
    with read_scope(cache_mode="live"):
        assert store.read([row], 1024) == {}
    clock[0] += 600
    with read_scope(cache_mode="hot"):
        assert store.read([row], 1024) == {}
    with read_scope(cache_mode="cool"):
        assert store.read([row], 1024) == {"DL-1": "body"}


def test_warm_oversize_is_rejected_before_loading_any_body(bodies, monkeypatch):
    cache, store = bodies
    rows = [issue("DL-1", "a" * 80), issue("DL-2", "b" * 80)]
    store.store(rows, time.time())
    original = cache.get_many_with_metadata
    loaded = []
    def read(keys, **kwargs):
        keys = list(keys)
        loaded.extend(keys)
        return original(keys, **kwargs)
    monkeypatch.setattr(cache, "get_many_with_metadata", read)
    with pytest.raises(ValueError, match="16 MiB"):
        store.read(rows, 100)
    assert loaded and all(key.startswith("ticketbodymeta:") for key in loaded)


def test_immutable_pointer_does_not_combine_old_index_with_replaced_payload(bodies, monkeypatch):
    cache, store = bodies
    now = time.time()
    row = issue(description="old")
    store.store([row], now)
    original = cache.get_many_with_metadata
    replaced = [False]
    def read(keys, **kwargs):
        keys = list(keys)
        result = original(keys, **kwargs)
        if not replaced[0] and keys and keys[0].startswith("ticketbodymeta:"):
            replaced[0] = True
            store.store([issue(description="new")], now + 1)
        return result
    monkeypatch.setattr(cache, "get_many_with_metadata", read)
    assert store.read([row], 1024) == {"DL-1": "old"}
    assert store.read([row], 1024) == {"DL-1": "new"}


def test_old_writer_cannot_replace_newer_index_and_fence_rejects_publication(bodies):
    cache, store = bodies
    now = time.time()
    store.store([issue(description="new")], now)
    store.store([issue(description="old")], now - 10)
    assert store.read([issue()], 1024) == {"DL-1": "new"}
    cache.invalidate("unrelated")
    with pytest.raises(QueryStateChanged):
        store.store([issue(description="racing")], now + 1)


def test_index_without_payload_is_a_miss_and_cannot_impersonate_another_ticket(bodies):
    cache, store = bodies
    rows = [issue("DL-1", None), issue("DL-2", "secret")]
    store.store(rows, time.time())
    with cache._lock:
        pointers = [row[0] for row in cache._conn.execute(
            "SELECT key FROM cache WHERE key LIKE 'ticketbody:%'")]
    cache.invalidate(pointers[0])
    remaining = store.read(rows, 1024)
    assert len(remaining) == 1  # Eviction cannot turn the absent value into null.
    first_index = store._identity(rows[0])[2]
    second_index = store._identity(rows[1])[2]
    cache.set(first_index, cache.get(second_index), 600)
    assert "DL-1" not in store.read([rows[0]], 1024)


def test_revalidation_of_unchanged_body_does_not_accumulate_payload_copies(bodies):
    cache, store = bodies
    now = time.time()
    for offset in range(5):
        store.store([issue()], now + offset)
    with cache._lock:
        count = cache._conn.execute("SELECT count(*) FROM cache WHERE key LIKE 'ticketbody:%'").fetchone()[0]
    assert count == 1


def test_default_body_freshness_follows_configured_policy(bodies, monkeypatch):
    cache, _ = bodies
    cache.configure(CachePolicy(freshnessSeconds=60))
    store = TicketBodyCache(cache, "session-one", 600, cache.fence())
    now = time.time()
    store.store([issue()], now)
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now + 61)
    assert store.read([issue()], 1024) == {}
    with read_scope(cache_mode="cool"):
        assert store.read([issue()], 1024) == {"DL-1": "body"}
