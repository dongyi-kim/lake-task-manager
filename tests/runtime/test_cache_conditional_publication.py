"""Optional conditional batch rows cannot replace newer authoritative memberships."""
import json

import pytest

from app.infra.cache import Cache
from app.infra.cache_policy import CachePolicy, read_scope


KEY = "childkeys:v1:mock:DL-1"


@pytest.fixture
def caches(tmp_path, monkeypatch):
    clock = [100000.0]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])
    path = tmp_path / "cache.sqlite3"
    cache, other = Cache(path), Cache(path)
    cache.configure(CachePolicy())
    other._conn.execute("PRAGMA busy_timeout=50")
    try:
        yield cache, other, clock
    finally:
        cache.close()
        other.close()


@pytest.mark.parametrize("connection", ["same", "other"])
@pytest.mark.parametrize("delta", [0, 50])
def test_equal_or_newer_membership_keeps_identity_age_proof_and_recency(caches, connection, delta):
    cache, other, clock = caches
    with read_scope(cache_mode="live"):
        owner = cache if connection == "same" else other
        owner.set_if_fence(KEY, ["DL-NEW"], 600, owner.fence(), fetched_at=clock[0] + delta)
        before = cache.metadata(KEY)
        # A skipped conditional row must not delete even an existing display copy.
        cache._conn.execute("INSERT INTO cache_display VALUES (?,?,?,?)", (KEY, json.dumps(["display"]), 99999, 600))
        cache._conn.commit()
        clock[0] += 100
        count = cache._set_count
        accessed = cache._conn.execute("SELECT accessed_at FROM cache_access WHERE key=?", (KEY,)).fetchone()
        assert cache.set_many_if_fence([(KEY, ["DL-OLD"])], 600, cache.fence(),
            fetched_at=100000, preserve_newer_keys={KEY})
        after = cache.metadata(KEY)
        assert cache.get_stale_at(KEY)[0] == ["DL-NEW"]
        assert after["rowIdentity"] == before["rowIdentity"]
        assert after["fetchedAt"] == before["fetchedAt"]
        assert after["upstreamValidated"] == before["upstreamValidated"]
        assert cache._set_count == count
        assert cache._conn.execute("SELECT accessed_at FROM cache_access WHERE key=?", (KEY,)).fetchone() == accessed
        assert cache._conn.execute("SELECT payload FROM cache_display WHERE key=?", (KEY,)).fetchone()
        assert not cache._conn.in_transaction
        other.set("user:unblocked", [], 600)


def test_foreign_connection_replacement_immediately_before_conditional_sql_is_preserved(caches):
    cache, other, _clock = caches
    original = cache._conn
    replacements = []

    class InterleavingConnection:
        def __getattr__(self, name):
            return getattr(original, name)

        def execute(self, sql, args=()):
            if "WHERE NOT EXISTS" in sql and "INTO cache" in sql and not replacements:
                replacements.append(True)
                other.set_if_fence(KEY, ["DL-NEW"], 600, other.fence(), fetched_at=100050)
            return original.execute(sql, args)

    cache._conn = InterleavingConnection()
    with read_scope(cache_mode="live"):
        assert cache.set_many_if_fence([(KEY, ["DL-OLD"])], 600, cache.fence(),
            fetched_at=100000, preserve_newer_keys={KEY})
        assert replacements
        assert cache.get_stale_at(KEY)[0] == ["DL-NEW"]
        assert not cache.metadata(KEY)["upstreamValidated"]
    assert not cache._conn.in_transaction


def test_only_designated_rows_are_conditional_and_written_rows_share_one_commit(caches):
    cache, other, clock = caches
    ordinary = "issueL:mock:DL-1"
    cache.set_if_fence(ordinary, {"newer": True}, 600, cache.fence(), fetched_at=clock[0] + 10)
    cache._conn.execute("INSERT INTO cache_display VALUES (?,?,?,?)", (KEY, "[]", clock[0] + 50, 600))
    cache._conn.commit()
    statements = []
    cache._conn.set_trace_callback(statements.append)
    try:
        with read_scope(cache_mode="live"):
            assert cache.set_many_if_fence([(ordinary, {"original_behavior": True}), (KEY, [])], 600,
                cache.fence(), fetched_at=clock[0], preserve_newer_keys={KEY})
            assert cache.get_stale_at(ordinary)[0] == {"original_behavior": True}
            assert cache.get_stale_at(KEY)[0] == []
            assert cache.metadata(KEY)["upstreamValidated"]
        assert sum(statement == "COMMIT" for statement in statements) == 1
        assert not cache._conn.execute("SELECT payload FROM cache_display WHERE key=?", (KEY,)).fetchone()
    finally:
        cache._conn.set_trace_callback(None)
    other.set("user:unblocked", [], 600)


def test_stale_fence_cannot_publish_even_missing_conditional_rows(caches):
    cache, _other, _clock = caches
    old = cache.fence()
    cache.invalidate("mutation-marker:")
    assert not cache.set_many_if_fence([(KEY, [])], 600, old, preserve_newer_keys={KEY})
    assert cache.metadata(KEY) is None
    assert not cache._conn.in_transaction


def test_replacing_older_conditional_row_preserves_payload_accounting_triggers(caches):
    cache, _other, clock = caches
    cache.set(KEY, ["old"], 600)
    before = cache.metadata(KEY)
    clock[0] += 10
    with read_scope(cache_mode="live"):
        assert cache.set_many_if_fence([(KEY, ["new", "larger"])], 600, cache.fence(),
            fetched_at=clock[0], preserve_newer_keys={KEY})
        assert cache.get_stale_at(KEY)[0] == ["new", "larger"]
        assert cache.metadata(KEY)["rowIdentity"] != before["rowIdentity"]
        assert cache.metadata(KEY)["upstreamValidated"]
    accounted = cache._conn.execute("SELECT payload_bytes FROM cache_payload_total WHERE id=1").fetchone()[0]
    actual = cache._conn.execute("SELECT sum(length(CAST(payload AS BLOB))) FROM cache").fetchone()[0]
    assert accounted == actual


@pytest.mark.parametrize("upstream", [False, True])
def test_opt_in_identical_equal_row_requires_upstream_and_preserves_storage(caches, upstream):
    cache, _other, clock = caches
    cache.set(KEY, ["DL-1"], 600)
    original = cache.metadata(KEY)
    count = cache._set_count
    clock[0] += 10
    with read_scope(cache_mode="live"):
        assert cache.set_many_if_fence([(KEY, ["DL-1"])], 600, cache.fence(), fetched_at=100000,
            upstream_validated=upstream, preserve_newer_keys={KEY}, revalidate_equal_preserved=True)
        source = cache.metadata(KEY)
        assert source["rowIdentity"] == original["rowIdentity"]
        assert source["fetchedAt"] == original["fetchedAt"]
        assert source["upstreamValidated"] is upstream
        assert cache._set_count == count
        assert cache._conn.execute("SELECT accessed_at FROM cache_access WHERE key=?", (KEY,)).fetchone() == (100000,)
    assert not cache._conn.in_transaction


@pytest.mark.parametrize("delta,payload", [(0, ["different"]), (10, ["DL-1"])])
def test_opt_in_never_validates_different_or_newer_preserved_row(caches, delta, payload):
    cache, other, _clock = caches
    other.set_if_fence(KEY, payload, 600, other.fence(), fetched_at=100000 + delta)
    original = cache.metadata(KEY)
    with read_scope(cache_mode="live"):
        assert cache.set_many_if_fence([(KEY, ["DL-1"])], 600, cache.fence(), fetched_at=100000,
            preserve_newer_keys={KEY}, revalidate_equal_preserved=True)
        assert not cache.metadata(KEY)["upstreamValidated"]
        assert cache.get_stale_at(KEY)[0] == payload
        assert cache.metadata(KEY)["rowIdentity"] == original["rowIdentity"]


def test_replacement_after_equal_revalidation_cannot_borrow_its_row_identity_proof(caches):
    cache, other, _clock = caches
    cache.set(KEY, ["DL-1"], 600)
    original = cache._conn
    replacements = []

    class ReplacedAfterCommit:
        def __getattr__(self, name):
            return getattr(original, name)

        def commit(self):
            original.commit()
            if not replacements:
                replacements.append(True)
                other.set_if_fence(KEY, ["replacement"], 600, other.fence(), fetched_at=100000)

    cache._conn = ReplacedAfterCommit()
    with read_scope(cache_mode="live"):
        assert cache.set_many_if_fence([(KEY, ["DL-1"])], 600, cache.fence(), fetched_at=100000,
            preserve_newer_keys={KEY}, revalidate_equal_preserved=True)
        assert replacements
        assert cache.get(KEY) is None
        assert cache.get_stale_at(KEY)[0] == ["replacement"]
        assert not cache.metadata(KEY)["upstreamValidated"]
