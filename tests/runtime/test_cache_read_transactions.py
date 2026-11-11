"""LRU bookkeeping must not leak a writer lock or change captured read provenance."""
import pytest

from app.infra.cache import Cache
from app.infra.cache_policy import CachePolicy, read_scope


KEY = "comments:mock:DL-1"


@pytest.fixture
def caches(tmp_path, monkeypatch):
    clock = [100000.0]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])
    path = tmp_path / "cache.sqlite3"
    reader, writer = Cache(path), Cache(path)
    reader.configure(CachePolicy())
    writer.configure(CachePolicy())
    writer._conn.execute("PRAGMA busy_timeout=50")
    reader.set(KEY, ["original"], 600)
    try:
        yield reader, writer, clock
    finally:
        reader.close()
        writer.close()


@pytest.mark.parametrize("method", ["get", "get_many", "get_alive", "get_many_with_metadata",
                                   "retained", "get_with_metadata", "display_snapshot"])
def test_read_hit_releases_its_recency_write_before_another_connection_writes(caches, method):
    reader, writer, clock = caches
    clock[0] += 10
    if method == "retained":
        result = reader.get_many_with_metadata([KEY], retained=True)
    elif method == "display_snapshot":
        result = reader._display_snapshot(KEY)
    else:
        argument = [KEY] if method in {"get_many", "get_many_with_metadata"} else KEY
        result = getattr(reader, method)(argument)
    assert result
    assert reader._conn.in_transaction is False
    writer.set("comments:mock:DL-2", ["other connection"], 600)
    assert writer._conn.execute("SELECT accessed_at FROM cache_access WHERE key=?", (KEY,)).fetchone() == (100010,)


class CommitReplacement:
    def __init__(self, connection, replace):
        self.connection, self.replace = connection, replace

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def commit(self):
        self.connection.commit()
        if self.replace is not None:
            replacement, self.replace = self.replace, None
            replacement()


@pytest.mark.parametrize("method", ["paired_batch", "paired_single", "display_snapshot"])
def test_read_pairs_keep_original_source_when_another_connection_replaces_after_commit(caches, method):
    reader, writer, clock = caches
    source = reader.metadata(KEY)
    replacements = []

    def replace():
        clock[0] += 10
        writer.set(KEY, ["replacement"], 600)
        replacements.append(1)

    reader._conn = CommitReplacement(reader._conn, replace)
    with read_scope(cache_mode="warm") as observed:
        if method == "paired_batch":
            value, captured = reader.get_many_with_metadata([KEY])[KEY]
        elif method == "paired_single":
            value, captured = reader.get_with_metadata(KEY)
        else:
            (value, fetched), captured = reader._display_snapshot(KEY)
            assert fetched == source["fetchedAt"]
    assert replacements == [1]
    assert value == ["original"]
    assert captured["fetchedAt"] == source["fetchedAt"]
    assert captured["rowIdentity"] == source["rowIdentity"]
    assert observed
    assert all(row["fetchedAt"] == source["fetchedAt"] for row in observed)
    assert reader.metadata(KEY)["fetchedAt"] == 100010
    assert reader.metadata(KEY)["rowIdentity"] != captured["rowIdentity"]


def test_batched_read_commits_recency_once_for_multiple_sql_batches(caches):
    reader, writer, _clock = caches
    keys = [f"comments:mock:DL-{index}" for index in range(2, 405)]
    reader.set_many_if_fence([(key, [key]) for key in keys], 600, reader.fence())
    statements = []
    reader._conn.set_trace_callback(statements.append)
    assert len(reader.get_many(keys)) == 403
    reader._conn.set_trace_callback(None)
    assert sum(statement == "COMMIT" for statement in statements) == 1
    assert not reader._conn.in_transaction
    writer.set("comments:mock:DL-999", ["unblocked"], 600)


@pytest.mark.parametrize("failure", [False, True])
def test_read_does_not_commit_or_rollback_a_callers_existing_write(caches, monkeypatch, failure):
    reader, writer, _clock = caches
    reader._conn.execute("INSERT INTO cache_epoch VALUES ('caller-owned',1)")
    if failure:
        touch = reader._touch

        def failing_touch(keys):
            touch(keys)
            raise RuntimeError("recency failed")

        monkeypatch.setattr(reader, "_touch", failing_touch)
        with pytest.raises(RuntimeError, match="recency failed"):
            reader.get(KEY)
    else:
        assert reader.get_with_metadata(KEY)[0] == ["original"]
    assert reader._conn.in_transaction
    assert reader._conn.execute("SELECT value FROM cache_epoch WHERE namespace='caller-owned'").fetchone() == (1,)
    assert writer._conn.execute("SELECT value FROM cache_epoch WHERE namespace='caller-owned'").fetchone() is None
    reader._conn.rollback()
    assert reader._conn.execute("SELECT value FROM cache_epoch WHERE namespace='caller-owned'").fetchone() is None


def test_failed_read_rolls_back_only_its_own_partial_recency_update(caches, monkeypatch):
    reader, writer, clock = caches
    touch = reader._touch
    clock[0] += 10

    def failing_touch(keys):
        touch(keys)
        raise RuntimeError("recency failed")

    monkeypatch.setattr(reader, "_touch", failing_touch)
    with pytest.raises(RuntimeError, match="recency failed"):
        reader.get(KEY)
    assert not reader._conn.in_transaction
    assert reader._conn.execute("SELECT accessed_at FROM cache_access WHERE key=?", (KEY,)).fetchone() == (100000,)
    writer.set("comments:mock:DL-2", ["unblocked"], 600)
