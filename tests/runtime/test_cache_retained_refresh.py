"""Refresh invalidates authority without erasing the retained display payload."""
import pytest

from app.infra.cache import Cache
from app.infra.cache_policy import CachePolicy, read_scope


@pytest.mark.parametrize("prefix", [None, "issue:"])
def test_invalidation_retains_payload_age_and_identity_but_not_authority(prefix):
    cache = Cache(":memory:")
    cache.configure(CachePolicy())
    try:
        key = "issue:mock:DL-1"
        value = {"fields": {"summary": "old"}}
        cache.set(key, value, 600)
        before = cache.metadata(key)
        fence = cache.fence()
        cache.invalidate(prefix)
        assert cache.get(key) is None
        assert cache.get_alive(key)[0] == value
        assert cache.get_stale_at(key)[1] == before["fetchedAt"]
        assert cache.metadata(key)["rowIdentity"] == before["rowIdentity"]
        assert cache.metadata(key)["invalidated"]
        with read_scope(cache_mode="cold"):
            assert cache.get(key) is None
        with read_scope(cache_mode="live"):
            assert cache.get(key) is None
        assert not cache.set_if_fence(key, value, 600, fence)
        cache.set(key, {"fields": {"summary": "new"}}, 600)
        assert cache.get(key)["fields"]["summary"] == "new"
        assert not cache.metadata(key)["invalidated"]
    finally:
        cache.close()


def test_invalidated_display_returns_immediately_and_queues_refresh():
    cache = Cache(":memory:")
    cache.configure(CachePolicy())
    queued = []
    cache.revalidator = lambda *args: queued.append(args)
    try:
        cache.set("epicmeta:mock:DL-1", {"title": "Epic"}, 600)
        cache.invalidate()
        value, hit = cache.get_or_set("epicmeta:mock:DL-1", 600,
                                       lambda: pytest.fail("display read blocked on producer"))
        assert value == {"title": "Epic"} and hit and len(queued) == 1
        cache.forget("epicmeta:mock:DL-1")
        assert cache.get_alive("epicmeta:mock:DL-1") is None
    finally:
        cache.close()


def test_budget_and_cursor_expiry_do_not_physically_evict_recent_payloads(monkeypatch):
    now = [100000.0]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now[0])
    cache = Cache(":memory:")
    cache.configure(CachePolicy())
    cache.PAYLOAD_BUDGET = 1
    try:
        cache.set("issue:mock:DL-1", {"fields": {"summary": "keep"}}, 600)
        cache.set("jqlsnap:mock:cursor", {"rows": [1]}, 60)
        now[0] += 86400 * 2
        cache.purge()
        assert cache.get_stale("issue:mock:DL-1")
        assert cache.get_stale("jqlsnap:mock:cursor")
        now[0] += 86400 * 29
        cache.purge()
        assert cache.get_stale("issue:mock:DL-1") is None
    finally:
        cache.close()


@pytest.mark.parametrize("method", ["forget", "forget_if_fence", "forget_prefixes"])
def test_known_ticket_permission_loss_removes_saved_task_preview(method):
    cache = Cache(":memory:")
    cache.configure(CachePolicy())
    try:
        cache.set("taskpreview:mock:scope", {"groups": [{"key": "DL-1"}]}, 600)
        if method == "forget_if_fence":
            cache.forget_if_fence("issueL:mock:DL-1", cache.fence())
        elif method == "forget_prefixes":
            cache.forget_prefixes(["issue:mock:DL-1", "epicmeta:mock:DL-1"])
        else:
            cache.forget("issueview:mock:DL-1")
        assert cache.get_alive("taskpreview:mock:scope") is None
    finally:
        cache.close()


def test_cold_gc_keeps_recently_used_old_source_but_purges_unused_source(monkeypatch):
    now = [100000.0]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now[0])
    cache = Cache(":memory:")
    cache.configure(CachePolicy())
    used, unused = "issue:mock:DL-1", "issue:mock:DL-2"
    value = {"fields": {"summary": "retained"}}
    try:
        cache.set(used, value, 600)
        cache.set(unused, value, 600)
        source_time = cache.metadata(used)["fetchedAt"]
        now[0] += 29 * 86400
        with read_scope(cache_mode="cold"):
            assert cache.get(used) == value
        now[0] += 2 * 86400
        cache.purge()
        assert cache.get_stale(used) == value
        assert cache.get_stale(unused) is None
        assert cache.metadata(used)["fetchedAt"] == source_time
        assert cache.get(used) is None
        with read_scope(cache_mode="cold"):
            assert cache.get(used) is None
    finally:
        cache.close()
