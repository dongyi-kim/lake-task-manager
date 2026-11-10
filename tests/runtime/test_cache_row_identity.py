"""A validation belongs to the exact row observed, even when collection clocks collide."""
import threading
from concurrent.futures import Future, ThreadPoolExecutor

import pytest

from app.infra.cache import Cache
from app.infra.cache_policy import CachePolicy, read_scope


@pytest.fixture
def cache(monkeypatch):
    monkeypatch.setattr("app.infra.cache.time.time", lambda: 100000.0)
    value = Cache(":memory:")
    value.configure(CachePolicy())
    yield value
    value.close()


def _copy(cache, key, value, batch):
    with read_scope(cache_mode="warm"):
        if batch:
            cache.set_many_if_fence([(key, value)], 600, cache.fence(), fetched_at=100000,
                                   upstream_validated=False)
        else:
            cache.set_if_fence(key, value, 600, cache.fence(), fetched_at=100000,
                               upstream_validated=False)


@pytest.mark.parametrize("batch", [False, True])
def test_other_request_same_time_copy_retires_live_proof_in_every_read_path(cache, batch):
    key = "comments:mock:issue"
    with read_scope(cache_mode="live"):
        assert cache.refresh(key, 600, lambda: ["upstream A"]) == ["upstream A"]
        assert cache.get(key) == ["upstream A"]
        _copy(cache, key, ["cached B"], batch)
        assert cache.metadata(key)["fetchedAt"] == 100000
        assert not cache.metadata(key)["upstreamValidated"]
        assert not cache.metadata(key)["compliant"]
        assert cache.get(key) is None
        assert cache.get_many([key]) == {}
        assert cache.entries_by_prefix("comments:mock:") == {}
        assert cache.refresh(key, 600, lambda: ["upstream C"]) == ["upstream C"]
        assert cache.get(key) == ["upstream C"]


@pytest.mark.parametrize("batch", [False, True])
def test_refresh_owner_keeps_its_result_without_certifying_a_callback_replacement(cache, batch):
    key = "comments:mock:issue"
    cache.on_upstream_ok = lambda: _copy(cache, key, ["cached B"], batch)
    with read_scope(cache_mode="live") as sources:
        assert cache.refresh(key, 600, lambda: ["upstream A"]) == ["upstream A"]
        assert sources[-1]["compliant"] and sources[-1]["upstreamValidated"]
        assert cache.get(key) is None
        assert not cache.metadata(key)["upstreamValidated"]
    assert cache.get(key) == ["cached B"]


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("evicted", [False, True])
def test_joiner_carries_captured_proof_without_blessing_a_later_same_time_row(cache, monkeypatch, batch, evicted):
    key = "comments:mock:issue"
    entered, joined, release, received, resume = [threading.Event() for _ in range(5)]
    class PausedFuture(Future):
        def result(self, *args, **kwargs):
            joined.set()
            result = super().result(*args, **kwargs)
            received.set()
            assert resume.wait(5)
            return result
    monkeypatch.setattr("app.infra.cache.Future", PausedFuture)
    cache.PAYLOAD_BUDGET = 0 if evicted else 512 * 1024 * 1024
    def produce():
        entered.set()
        assert release.wait(5)
        return ["upstream A"]
    def read():
        with read_scope(cache_mode="live") as sources:
            value = cache.refresh(key, 600, produce)
            reused = cache.get(key)
            return value, reused, cache.metadata(key), sources
    with ThreadPoolExecutor(2) as pool:
        owner = pool.submit(read)
        assert entered.wait(3)
        follower = pool.submit(read)
        try:
            assert joined.wait(3)
            release.set()
            assert received.wait(3)
            assert owner.result(timeout=3)[0] == ["upstream A"]
            cache.PAYLOAD_BUDGET = 512 * 1024 * 1024
            _copy(cache, key, ["cached B"], batch)
        finally:
            release.set()
            resume.set()
        value, reused, source, observed = follower.result(timeout=3)
    assert value == ["upstream A"]
    assert observed[-1]["fetchedAt"] == 100000 and observed[-1]["upstreamValidated"]
    assert reused is None
    assert not source["upstreamValidated"] and not source["compliant"]


@pytest.mark.parametrize("invalidate", [False, True])
def test_identity_is_shared_across_database_connections_and_survives_writer_restart(tmp_path, monkeypatch, invalidate):
    monkeypatch.setattr("app.infra.cache.time.time", lambda: 100000.0)
    path = tmp_path / "cache.db"
    reader = Cache(path)
    reader.configure(CachePolicy())
    try:
        reader.mark_ticket_viewed("actor", "DL-1", 123)
        with read_scope(cache_mode="live"):
            reader.set_if_fence("comments:mock:issue", ["upstream A"], 600, reader.fence())
            # Finish recency bookkeeping before switching independent database clients.
            reader._conn.commit()
            writer = Cache(path)
            try:
                assert reader.get("comments:mock:issue") == ["upstream A"]
                reader._conn.commit()
                if invalidate:
                    writer.invalidate("comments:mock:issue")
                _copy(writer, "comments:mock:issue", ["cached B"], True)
            finally:
                writer.close()
            assert reader.get("comments:mock:issue") is None
            assert not reader.metadata("comments:mock:issue")["upstreamValidated"]
        assert reader.ticket_read_states("actor", ["DL-1"]) == {"DL-1": 123}
    finally:
        reader.close()


def test_legacy_database_migration_preserves_payload_age_and_does_not_reuse_retired_identity(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "legacy.db"
    monkeypatch.setattr("app.infra.cache.time.time", lambda: 100000.0)
    with sqlite3.connect(path) as old:
        old.execute("CREATE TABLE cache(key TEXT PRIMARY KEY,payload TEXT NOT NULL,fetched_at REAL NOT NULL,ttl INTEGER NOT NULL)")
        old.execute("INSERT INTO cache VALUES ('comments:mock:issue','[\"legacy\"]',100000,600)")
    migrated = Cache(path)
    migrated.configure(CachePolicy())
    try:
        with read_scope(cache_mode="warm"):
            assert migrated.get("comments:mock:issue") == ["legacy"]
            old_identity = migrated.metadata("comments:mock:issue")["rowIdentity"]
            assert migrated.metadata("comments:mock:issue")["fetchedAt"] == 100000
        migrated.mark_ticket_viewed("actor", "DL-1", 123)
        migrated.invalidate("comments:mock:issue")
        migrated.set("comments:mock:issue", ["replacement"], 600)
        new_identity = migrated.metadata("comments:mock:issue")["rowIdentity"]
        assert new_identity != old_identity
    finally:
        migrated.close()
    restarted = Cache(path)
    try:
        assert restarted.get("comments:mock:issue") == ["replacement"]
        assert restarted.metadata("comments:mock:issue")["rowIdentity"] == new_identity
        assert restarted.metadata("comments:mock:issue")["fetchedAt"] == 100000
        assert restarted.ticket_read_states("actor", ["DL-1"]) == {"DL-1": 123}
        with read_scope(cache_mode="live"):
            assert restarted.get("comments:mock:issue") is None
            restarted.refresh("comments:mock:issue", 600, lambda: ["validated after restart"])
            assert restarted.get("comments:mock:issue") == ["validated after restart"]
    finally:
        restarted.close()
