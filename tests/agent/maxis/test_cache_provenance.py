from concurrent.futures import ThreadPoolExecutor
import time

from app.infra.cache import Cache, propagate_provenance_context


def _fetched_at(cache: Cache, key: str, instant: float) -> None:
    with cache._lock:
        cache._conn.execute(
            "UPDATE cache SET fetched_at=? WHERE key=?", (instant, key),
        )
        cache._conn.commit()


def test_acquisition_provenance_follows_parallel_worker_reads(tmp_path):
    cache = Cache(str(tmp_path / "provenance.sqlite3"))
    try:
        now = time.time()
        cache.set("one", {"key": "one"}, 3600)
        cache.set("two", {"key": "two"}, 3600)
        _fetched_at(cache, "one", now - 120)
        _fetched_at(cache, "two", now - 60)

        marker = cache.provenance_marker()
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(
                propagate_provenance_context(cache.get), ("one", "two"),
            ))
        observed = cache.provenance_since(marker)

        assert rows == [{"key": "one"}, {"key": "two"}]
        assert observed["count"] == 2
        assert abs(observed["oldestFetchedAt"] - (now - 120)) < 2
        assert abs(observed["newestFetchedAt"] - (now - 60)) < 2
    finally:
        cache.close()


def test_parallel_acquisitions_do_not_consume_each_others_cache_events(tmp_path):
    cache = Cache(str(tmp_path / "isolated.sqlite3"))
    try:
        now = time.time()
        cache.set("old", {"key": "old"}, 3600)
        cache.set("new", {"key": "new"}, 3600)
        _fetched_at(cache, "old", now - 300)
        _fetched_at(cache, "new", now - 10)

        def acquire(key):
            marker = cache.provenance_marker()
            with ThreadPoolExecutor(max_workers=1) as child:
                child.submit(propagate_provenance_context(cache.get), key).result()
            return cache.provenance_since(marker)

        with ThreadPoolExecutor(max_workers=2) as parents:
            old, new = list(parents.map(acquire, ("old", "new")))

        assert old["count"] == new["count"] == 1
        assert abs(old["oldestFetchedAt"] - (now - 300)) < 2
        assert abs(new["oldestFetchedAt"] - (now - 10)) < 2
    finally:
        cache.close()
