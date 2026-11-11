"""Batched display reads preserve durable recency and request-local proof."""
from app.infra.cache import Cache
from app.infra.cache_policy import CachePolicy, read_scope
import pytest


@pytest.fixture
def stores(tmp_path, monkeypatch):
    clock = [100000.0]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])
    path = tmp_path / "cache.sqlite3"
    reader, writer = Cache(path), Cache(path)
    reader.configure(CachePolicy())
    writer.configure(CachePolicy())
    writer._conn.execute("PRAGMA busy_timeout=50")
    try:
        yield reader, writer, clock
    finally:
        reader.close()
        writer.close()


def test_alive_batch_crosses_sql_batches_but_commits_durable_lru_once(stores):
    reader, writer, clock = stores
    keys = [f"issueL:mock:DL-{index}" for index in range(405)]
    reader.set_many_if_fence([(key, {"key": key}) for key in keys], 600, reader.fence())
    sources = {key: reader.metadata(key) for key in keys}
    clock[0] += 10
    statements = []
    reader._conn.set_trace_callback(statements.append)
    try:
        found = reader.get_alive_many([*keys, keys[0]], record=False)
    finally:
        reader._conn.set_trace_callback(None)
    assert set(found) == set(keys)
    assert all(found[key] == ({"key": key}, sources[key]["fetchedAt"]) for key in keys)
    assert sum(sql == "COMMIT" for sql in statements) == 1
    assert not reader._conn.in_transaction
    assert writer._conn.execute("SELECT count(*) FROM cache_access WHERE accessed_at=100010").fetchone() == (405,)
    writer.set("comments:mock:DL-other", [], 600)  # No retained SQLite writer lock.
    assert all(reader.metadata(key)["rowIdentity"] == sources[key]["rowIdentity"] for key in keys)


def test_quiet_alive_batch_retains_explicit_policy_observation_and_display_rules(stores):
    reader, _writer, clock = stores
    key, display = "issueL:mock:DL-1", "issueL:mock:DL-2"
    reader.set(key, {"key": "DL-1"}, 600)
    reader.set(display, {"key": "DL-2"}, 600)
    reader.invalidate_keys([display])
    clock[0] += 1000
    with read_scope() as sources:
        assert reader.get_alive_many([key, display], record=False) == {
            key: ({"key": "DL-1"}, 100000), display: ({"key": "DL-2"}, 100000)}
    assert sources == []
    with read_scope(cache_mode="warm") as sources:
        assert reader.get_alive_many([key, display], record=False) == {key: ({"key": "DL-1"}, 100000)}
    assert len(sources) == 1 and sources[0]["compliant"] is True
    assert sources[0]["rowIdentity"] == reader.metadata(key)["rowIdentity"]
    with read_scope(cache_mode="hot"):
        assert reader.get_alive_many([key, display], record=False) == {}
    assert reader.get_alive_many([key, display], dead_ttl=999) == {}


def test_alive_batch_cannot_commit_a_transaction_owned_by_its_caller(stores):
    reader, writer, clock = stores
    key = "comments:mock:DL-1"
    reader.set(key, [], 600)
    clock[0] += 10
    reader._conn.execute("BEGIN")
    reader._conn.execute("UPDATE cache_access SET accessed_at=1 WHERE key=?", (key,))
    assert reader.get_alive_many([key]) == {key: ([], 100000)}
    assert reader._conn.in_transaction
    reader._conn.rollback()
    assert writer._conn.execute("SELECT accessed_at FROM cache_access WHERE key=?", (key,)).fetchone() == (100000,)


def test_alive_batch_returns_captured_proof_but_foreign_same_clock_replacement_retires_live_validation(stores):
    reader, writer, _clock = stores
    key = "comments:mock:DL-1"
    class ReplaceAfterCommit:
        def __init__(self, connection):
            self.connection, self.replace = connection, True
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def commit(self):
            self.connection.commit()
            if self.replace:
                self.replace = False
                writer.set_if_fence(key, ["foreign"], 600, writer.fence(),
                                    fetched_at=100000, upstream_validated=False)
    with read_scope(cache_mode="live"):
        reader.refresh(key, 600, lambda: ["validated"])
        before = reader.metadata(key)
        reader._conn = ReplaceAfterCommit(reader._conn)
        with read_scope() as observed:  # Inherits this exact LIVE request policy.
            assert reader.get_alive_many([key], record=False) == {key: (["validated"], 100000)}
        assert observed[0]["rowIdentity"] == before["rowIdentity"] and observed[0]["upstreamValidated"]
        assert reader.metadata(key)["rowIdentity"] != before["rowIdentity"]
        assert reader.get_alive_many([key], record=False) == {}
        writer.invalidate_keys([key])
        assert reader.get_alive_many([key], record=False) == {}
    assert reader.get_alive_many([key]) == {key: (["foreign"], 100000)}
