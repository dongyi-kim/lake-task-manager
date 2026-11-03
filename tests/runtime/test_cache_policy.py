"""Lifetime boundaries and policy changes exercise real SQLite entries and producers."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from app.infra.cache import Cache
from app.infra.cache_policy import CachePolicy, read_scope


def issue(done=False):
    return {"key": "DL-1", "fields": {"summary": "cached",
            "status": {"statusCategory": {"key": "done" if done else "new"}}}}


@pytest.fixture
def clock(monkeypatch):
    now = [100000.0]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now[0])
    return now


@pytest.fixture
def cache():
    value = Cache(":memory:")
    value.configure(CachePolicy())
    yield value
    value.close()


def test_active_done_and_comments_have_independent_boundaries(cache, clock):
    cache.set("issue:mock:DL-1", issue(), 900)
    cache.set("issue:mock:DL-2", issue(True), 900)
    cache.set("comments:mock:DL-2", ["comment"], 900)
    clock[0] += 599
    assert cache.get("issue:mock:DL-1")
    clock[0] += 2
    assert cache.get("issue:mock:DL-1") is None
    assert cache.get("issue:mock:DL-2")
    assert cache.get("comments:mock:DL-2")
    clock[0] = 103601
    assert cache.get("comments:mock:DL-2") is None
    assert cache.get("issue:mock:DL-2")
    clock[0] = 186399
    assert cache.get_alive("issue:mock:DL-1")
    clock[0] += 2
    assert cache.get_alive("issue:mock:DL-1") is None
    assert cache.get("issue:mock:DL-2") is None


def test_general_list_policy_does_not_inherit_done(cache, clock):
    cache.set("workload:mock:person", [issue(True)], 86400)
    cache.set("jqlleaf:mock:all-done", ["DL-1"], 86400)
    clock[0] += 601
    assert cache.get("workload:mock:person") is None
    assert cache.get("jqlleaf:mock:all-done") is None


def test_configure_reuses_source_age_and_get_many(cache, clock):
    cache.set("userbadge:mock:DL-1", {"title": "Epic title"}, 43200)
    original = cache.get_stale_at("userbadge:mock:DL-1")
    clock[0] += 700
    assert cache.get_many(["userbadge:mock:DL-1"]) == {}
    assert cache.get_many(["userbadge:mock:DL-1"], alive=True)
    cache.configure(CachePolicy(freshnessSeconds=1200), revision=2)
    assert cache.get_many(["userbadge:mock:DL-1"])
    assert cache.get_stale_at("userbadge:mock:DL-1") == original
    assert cache.metadata("userbadge:mock:DL-1")["freshUntil"] == 101200


def test_fresh_hit_zero_requests_stale_immediate_and_dead_not_returned(cache, clock):
    calls, queued = [], []
    def producer():
        calls.append(True)
        return issue()
    cache.revalidator = lambda *args: queued.append(args)
    cache.set("issue:mock:DL-1", issue(), 600)
    assert cache.get_or_set("issue:mock:DL-1", 600, producer)[1]
    assert not calls and not queued
    clock[0] += 601
    assert cache.get_or_set("issue:mock:DL-1", 600, producer)[1]
    assert not calls and len(queued) == 1
    clock[0] += 86400
    def failed():
        raise ConnectionError("offline")
    with pytest.raises(ConnectionError):
        cache.get_or_set_swr("issue:mock:DL-1", 600, failed, cache.revalidator)


def test_revalidation_never_returns_old_value_and_singleflights(cache, clock):
    cache.set("issue:mock:DL-1", issue(), 600)
    clock[0] += 601
    started, release = threading.Event(), threading.Event()
    calls = []
    def producer():
        calls.append(1)
        started.set()
        assert release.wait(3)
        return {**issue(), "new": True}
    def read():
        with read_scope(strict=True):
            return cache.get_or_set("issue:mock:DL-1", 600, producer)[0]
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(read)
        assert started.wait(2)
        second = pool.submit(read)
        release.set()
        assert first.result()["new"] and second.result()["new"]
    assert calls == [1]


def test_write_fence_prevents_old_refresh_and_preserves_unrelated(cache):
    cache.set("user:mock:alice", "Alice Full Name", 600)
    def racing():
        cache.invalidate("issue:mock:DL-1")
        return issue()
    cache.refresh("issue:mock:DL-1", 600, racing)
    assert cache.get("issue:mock:DL-1") is None
    assert cache.get("user:mock:alice") == "Alice Full Name"


def test_derived_values_keep_original_collection_time(cache, clock):
    cache.set("issue:mock:DL-1", issue(), 600)
    clock[0] += 100
    cache.refresh("related:mock:DL-1", 600, lambda: [cache.get("issue:mock:DL-1")])
    assert cache.get_stale_at("related:mock:DL-1")[1] == 100000


@pytest.mark.parametrize("patch", [
    {"freshnessSeconds": True}, {"freshnessSeconds": 61}, {"freshnessSeconds": 0},
    {"displaySeconds": 3600}, {"resolvedCommentsFreshnessSeconds": 86460},
    {"extra": 1},
])
def test_invalid_policy_rejected(patch):
    with pytest.raises(ValidationError):
        CachePolicy(**patch)


def test_prefs_storage_failure_does_not_apply_runtime_policy(tmp_path, monkeypatch):
    from app.infra import prefs
    from app.infra.settings import Settings
    monkeypatch.setattr(prefs, "_path", lambda: tmp_path / "app_prefs.json")
    settings = Settings()
    policy = CachePolicy(freshnessSeconds=1200)
    settings.set_cache_policy(policy)
    assert Settings().cache_policy == policy
    before = settings.policy_revision
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(prefs, "save", fail)
    with pytest.raises(OSError):
        settings.set_cache_policy(CachePolicy())
    assert settings.cache_policy == policy and settings.policy_revision == before


def test_epic_and_photo_keep_30_days_with_daily_freshness(cache, clock):
    for key, value in [("epicmeta:mock:DL-1", {"title": "Epic name"}),
                       ("avatar_url:mock:alice", "/photo/1"),
                       ("avatar_bytes:mock:alice", {"b64": "aGVsbG8="})]:
        cache.set(key, value, 600)
    clock[0] += 86400 - 1
    assert cache.get("epicmeta:mock:DL-1")
    assert cache.get("avatar_bytes:mock:alice")
    clock[0] += 2
    assert cache.get("epicmeta:mock:DL-1") is None
    assert cache.get("avatar_bytes:mock:alice") is None
    cache.purge()
    assert cache.get_many(["epicmeta:mock:DL-1"], alive=True)
    assert cache.get_alive("avatar_bytes:mock:alice")
    clock[0] = 100000 + 30 * 86400 - 1
    cache.purge()
    assert cache.get_alive("epicmeta:mock:DL-1")
    assert cache.get_alive("avatar_bytes:mock:alice")
    clock[0] += 2
    assert cache.get_alive("epicmeta:mock:DL-1") is None
    assert cache.get_alive("avatar_bytes:mock:alice") is None


def test_mutation_preserves_display_but_new_read_must_fetch(cache, clock):
    key = "issue:mock:DL-1"
    cache.set(key, issue(), 600)
    cache.invalidate(key)
    assert cache.get(key) is None
    assert cache.get_alive(key)[0] == issue()
    cache.revalidator = lambda *_: pytest.fail("mutation must not return SWR as authoritative")
    changed = {**issue(), "new": True}
    assert cache.get_or_set(key, 600, lambda: changed) == (changed, False)
    assert not cache.metadata(key)["invalidated"]
    cache.invalidate(key)
    cache.forget(key)
    assert cache.get_alive(key) is None


def test_permission_loss_removes_display_fallback(cache, clock):
    from app.auth.base import PermissionDenied
    cache.set("issue:mock:DL-1", issue(), 600)
    clock[0] += 601
    def denied():
        raise PermissionDenied("Forbidden")
    with pytest.raises(PermissionDenied):
        cache.get_or_set("issue:mock:DL-1", 600, denied)
    assert cache.get_alive("issue:mock:DL-1") is None


def test_failure_and_partial_response_never_become_fresh(cache):
    for value in ({"error": "network"}, {"partial": True, "issues": []}, None):
        cache.refresh("related:mock:DL-1", 600, lambda: value)
        assert cache.get("related:mock:DL-1") is None
    cache.refresh("related:mock:DL-1", 600, lambda: [])
    assert cache.get("related:mock:DL-1") == []


def test_http_source_headers_and_explicit_revalidation(cache, clock):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.infra.cache_policy import CacheHeadersMiddleware
    app = FastAPI()
    app.add_middleware(CacheHeadersMiddleware, cache=cache)
    cache.set("issue:mock:DL-1", issue(), 600)
    queued, produced = [], []
    cache.revalidator = lambda *args: queued.append(args)
    def producer():
        produced.append(1)
        return {**issue(), "fresh": True}
    @app.get("/api/ticket/DL-1")
    def get_ticket():
        return cache.get_or_set("issue:mock:DL-1", 600, producer)[0]
    clock[0] += 601
    with TestClient(app) as client:
        stale = client.get("/api/ticket/DL-1")
        assert stale.headers["x-ltm-cache-state"] == "stale"
        assert float(stale.headers["x-ltm-fetched-at"]) == 100000
        assert not produced and len(queued) == 1
        fresh = client.get("/api/ticket/DL-1?revalidate=1")
        assert fresh.json()["fresh"]
        assert fresh.headers["x-ltm-cache-state"] == "fresh"
        assert float(fresh.headers["x-ltm-fetched-at"]) == 100601
        client.get("/api/ticket/DL-1?revalidate=1")
        assert produced == [1]


def test_full_done_issue_satisfies_light_request_beyond_general_ttl(cache, clock):
    from types import SimpleNamespace
    from app.jira.jira_client import JiraClient
    client = JiraClient(SimpleNamespace(jira_env="prod", cache_ttl_seconds=600), cache)
    cache.set("issue:prod:DL-1", issue(True), 600)
    clock[0] += 3600
    client._provider = SimpleNamespace(get_json=lambda *a, **k: pytest.fail("fresh full hit must satisfy light"))
    client._provider_built = True
    assert client.get_issue_light("DL-1", strict=True) == issue(True)
