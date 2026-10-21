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


def test_evidence_window_refreshes_old_but_ui_valid_cache_synchronously(tmp_path):
    cache = Cache(str(tmp_path / "freshness.sqlite3"))
    try:
        now = time.time()
        cache.set("issue:one", {"value": "old"}, 3600)
        _fetched_at(cache, "issue:one", now - 1800)
        assert cache.get("issue:one") == {"value": "old"}
        marker = cache.provenance_marker(max_age_seconds=900)
        assert cache.get("issue:one") is None
        assert cache.get_many(["issue:one"]) == {}
        assert cache.entries_by_prefix("issue:") == {}
        background = []
        with ThreadPoolExecutor(max_workers=1) as pool:
            value, how = pool.submit(propagate_provenance_context(
                lambda: cache.get_or_set_swr("issue:one", 3600,
                    lambda: {"value": "current"}, lambda *args: background.append(args))
            )).result()
        proof = cache.provenance_since(marker)
        assert value == {"value": "current"} and how == "miss"
        assert not background and proof["upstream"] and not proof["servedStale"]
        assert proof["oldestFetchedAt"] >= now
        assert cache.effective_ttl(3600) == 3600
    finally:
        cache.close()


def test_evidence_refresh_failure_keeps_old_timestamp_and_partial_proof(tmp_path):
    cache = Cache(str(tmp_path / "offline.sqlite3"))
    try:
        old = time.time() - 1800
        cache.set("issue:one", {"value": "old"}, 3600)
        _fetched_at(cache, "issue:one", old)
        marker = cache.provenance_marker(max_age_seconds=900)

        def unavailable():
            raise OSError("upstream unavailable")

        value, how = cache.get_or_set_swr("issue:one", 3600, unavailable, None)
        proof = cache.provenance_since(marker)
        assert value == {"value": "old"} and how == "stale"
        assert proof["servedStale"] and not proof["upstream"]
        assert proof["oldestFetchedAt"] == old
    finally:
        cache.close()


def test_nested_evidence_age_limit_is_request_local_and_restored(tmp_path):
    cache = Cache(str(tmp_path / "nested.sqlite3"))
    try:
        outer = cache.provenance_marker(max_age_seconds=900)
        inner = cache.provenance_marker(max_age_seconds=300)
        assert cache.effective_ttl(3600) == 300
        assert cache.effective_ttl(60) == 60
        # Unrelated UI worker has no inherited evidence window.
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(cache.effective_ttl, 3600).result() == 3600
        cache.provenance_since(inner)
        assert cache.effective_ttl(3600) == 900
        cache.provenance_since(outer)
        assert cache.effective_ttl(3600) == 3600
    finally:
        cache.close()
