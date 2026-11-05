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
    # Cache clocks also affect date.today(). Keep the mock Jira calendar anchored before
    # 1970-based TTL tests initialize the process-wide World used by later UI/domain tests.
    from app.mock.world import get_world
    world = get_world()
    monkeypatch.setattr("app.mock.world.get_world", lambda: world)
    now = [100000.0]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now[0])
    return now


@pytest.fixture
def cache():
    value = Cache(":memory:")
    value.configure(CachePolicy())
    yield value
    value.close()


@pytest.mark.parametrize("suffix", ["", ":evidence:v1:100:100"])
def test_active_done_and_comments_have_independent_boundaries(cache, clock, suffix):
    cache.set("issue:mock:DL-1", issue(), 900)
    cache.set("issue:mock:DL-2", issue(True), 900)
    comment_key = "comments:mock:DL-2" + suffix
    cache.set(comment_key, ["comment"], 900)
    clock[0] += 599
    assert cache.get("issue:mock:DL-1")
    clock[0] += 2
    assert cache.get("issue:mock:DL-1") is None
    assert cache.get("issue:mock:DL-2")
    assert cache.get(comment_key)
    clock[0] = 103601
    assert cache.get(comment_key) is None
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


@pytest.mark.parametrize("kind", ["confpage", "confsearch"])
def test_confluence_uses_general_freshness_and_display_retention(cache, clock, kind):
    key = f"{kind}:mock:context"
    cache.set(key, {"title": "Shared document"}, 60)
    clock[0] += 599
    assert cache.get(key)
    clock[0] += 2
    assert cache.get(key) is None
    assert cache.get_alive(key)
    clock[0] = 186401
    assert cache.get_alive(key) is None


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
    client = JiraClient(SimpleNamespace(jira_env="prod", jira_base="https://jira.test", project_key="DL",
                                      cache_ttl_seconds=600), cache)
    cache.set("issue:prod:DL-1", issue(True), 600)
    clock[0] += 3600
    client._provider = SimpleNamespace(get_json=lambda *a, **k: pytest.fail("fresh full hit must satisfy light"))
    client._provider_built = True
    assert client.get_issue_light("DL-1", strict=True) == issue(True)


def test_post_mutation_read_uses_new_flight_and_old_result_cannot_overwrite(cache):
    key = "issue:mock:DL-1"
    started, release = threading.Event(), threading.Event()
    old, new = {**issue(), "version": "old"}, {**issue(), "version": "new"}
    def old_producer():
        started.set()
        assert release.wait(3)
        return old
    with ThreadPoolExecutor(2) as pool:
        earlier = pool.submit(cache.refresh, key, 600, old_producer)
        assert started.wait(2)
        cache.invalidate(key)
        after_write = pool.submit(cache.get_or_set, key, 600, lambda: new)
        try:
            assert after_write.result(timeout=2) == (new, False)
        finally:
            release.set()
        assert earlier.result(timeout=2) == old
    assert cache.get(key) == new
    assert not cache._flights


def test_retired_generation_permission_failure_cannot_forget_new_success(cache):
    from app.auth.base import PermissionDenied
    key = "issue:mock:DL-1"
    started, release = threading.Event(), threading.Event()
    def old_producer():
        started.set()
        assert release.wait(3)
        raise PermissionDenied("old provider denied")
    with ThreadPoolExecutor(1) as pool:
        earlier = pool.submit(cache.refresh, key, 600, old_producer)
        assert started.wait(2)
        cache.invalidate(key)
        cache.set(key, issue(), 600)
        release.set()
        with pytest.raises(PermissionDenied):
            earlier.result(timeout=2)
    assert cache.get(key) == issue()


def test_invalidated_relation_swr_fetches_latest_but_can_display_offline(cache):
    key = "children:mock:DL-1"
    cache.set(key, ["old-child"], 600)
    cache.invalidate(key)
    def unexpected_background(*_args):
        pytest.fail("The first post-write read must await its latest result")
    assert cache.get_or_set_swr(key, 600, lambda: ["new-child"], unexpected_background) == (["new-child"], "miss")
    cache.invalidate(key)
    def offline():
        raise ConnectionError("offline")
    assert cache.get_or_set_swr(key, 600, offline, unexpected_background) == (["new-child"], "stale")
    assert cache.metadata(key)["invalidated"]


def test_readonly_aggregates_display_immediately_but_leaf_membership_waits(cache, clock):
    queued = []
    cache.revalidator = lambda *args: queued.append(args)
    keys = [f"{kind}:mock:fixture" for kind in ("workload", "workload_bucket", "activity")]
    for key in [*keys, "jqlleaf:mock:fixture"]:
        cache.set(key, ["old"], 600)
    clock[0] += 601
    for key in keys:
        assert cache.get_or_set(key, 600, lambda: pytest.fail("aggregate must not block")) == (["old"], True)
    assert len(queued) == len(keys)
    assert cache.get_or_set("jqlleaf:mock:fixture", 600, lambda: ["new"]) == (["new"], False)


def test_new_queries_refresh_after_ten_minutes_and_old_cursors_stay_frozen(cache, clock, monkeypatch):
    from app.infra.settings import get_settings
    from app.jira.jira_client import JiraClient
    client = JiraClient(get_settings(), cache)
    client.current_user = lambda: {"id": "fixture-user"}
    calls = []
    def leaf(*_args):
        calls.append(1)
        return [{"key": f"DL-{n}", "fields": {"summary": f"version-{len(calls)}",
                 "status": {"statusCategory": {"key": "new"}}}} for n in (1, 2)]
    monkeypatch.setattr(client, "_fetch_jql_leaf", leaf)
    query = "key in (DL-1, DL-2) ORDER BY key"
    first = client.search_issues_page(query, max_results=1, fields="summary,status")
    clock[0] += 599
    assert client.search_issues_page(query, max_results=1, fields="summary,status")["snapshotId"] == first["snapshotId"]
    clock[0] += 2
    latest = client.search_issues_page(query, max_results=1, fields="summary,status")
    assert latest["snapshotId"] != first["snapshotId"] and len(calls) == 2
    assert latest["issues"][0]["fields"]["summary"] == "version-2"
    old_page = client.search_issues_page(query, start_at=1, max_results=1, fields="summary,status",
                                        snapshot_id=first["snapshotId"])
    assert old_page["issues"][0]["key"] == "DL-2"
    assert old_page["issues"][0]["fields"]["summary"] == "version-1"
    clock[0] += 1200
    cache.purge()
    with pytest.raises(ValueError, match="snapshot"):
        client.search_issues_page(query, fields="summary,status", snapshot_id=first["snapshotId"])
    assert cache.get_stale(f"jqlsnap:v{client.JQL_CACHE_VERSION}:{client.env}:{first['snapshotId']}") is None
    assert client.search_issues_page(query, fields="summary,status", snapshot_id=latest["snapshotId"])["issues"]


def test_legacy_id_only_cursor_keeps_its_thirty_minute_rows(cache, clock, monkeypatch):
    from app.infra.settings import get_settings
    from app.jira.jira_client import JiraClient
    client = JiraClient(get_settings(), cache)
    client.current_user = lambda: {"id": "fixture-user"}
    compiled = client._compile_jql("key = DL-1")
    fields = "summary,status"
    projection = client._projection_signature(fields, True)
    client._store_jql_rows([issue()], 0, projection)
    legacy_id = "0.legacy-fixture"
    cache.set(f"jqlsnap:v{client.JQL_CACHE_VERSION}:{client.env}:{legacy_id}", {
        "canonical": compiled.canonical, "context": client._jql_user_context(),
        "projection": projection, "light": True, "generation": 0, "issueKeys": ["DL-1"],
    }, client.JQL_SNAPSHOT_TTL)
    clock[0] += 601
    _sid, restored = client._jql_snapshot(compiled, fields, True, snapshot_id=legacy_id)
    assert restored["issues"] == [issue()]


def test_retained_metadata_does_not_make_a_fresh_core_payload_dead(cache, clock):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.infra.cache_policy import CacheHeadersMiddleware, note_read
    epic_key, issue_key = "epicmeta:mock:DL-2", "issue:mock:DL-1"
    cache.set(epic_key, {"key": "DL-2", "title": "Retained Epic"}, 600)
    epic_source_at = clock[0]
    clock[0] += 10 * 86400
    cache.set(issue_key, issue(), 600)
    def composite():
        return {"ticket": cache.get(issue_key), "epic": cache.get_alive(epic_key)[0]}
    cache.refresh("workload:mock:fixture", 600, composite)
    assert cache.metadata("workload:mock:fixture")["fetchedAt"] == clock[0]
    assert cache.get("workload:mock:fixture") and cache.get_alive("workload:mock:fixture")
    assert cache.metadata(epic_key)["fetchedAt"] == epic_source_at
    app = FastAPI()
    app.add_middleware(CacheHeadersMiddleware, cache=cache)
    app.get("/api/composite")(composite)
    @app.get("/api/metadata")
    def metadata():
        return cache.get_alive(epic_key)[0]
    @app.get("/api/dead-source")
    def dead():
        note_read({"state": "dead", "fetchedAt": 1, "freshUntil": 2, "displayUntil": 3})
        return {"unavailable": True}
    with TestClient(app) as client:
        result = client.get("/api/composite")
        assert result.headers["x-ltm-cache-state"] == "stale"
        assert result.headers["x-ltm-display-kind"] == "general"
        assert float(result.headers["x-ltm-fetched-at"]) == clock[0]
        assert float(result.headers["x-ltm-display-until"]) == clock[0] + 86400
        meta = client.get("/api/metadata")
        assert meta.headers["x-ltm-display-kind"] == "metadata"
        assert float(meta.headers["x-ltm-fetched-at"]) == epic_source_at
        assert client.get("/api/dead-source").headers["x-ltm-cache-state"] == "dead"


def test_strict_epic_metadata_refreshes_while_streaming_cache_stays_immediate(cache, clock, monkeypatch):
    from app.infra.settings import get_settings
    from app.jira.jira_client import JiraClient
    client = JiraClient(get_settings(), cache)
    key = f"epicmeta:{client.env}:DL-2"
    cache.set(key, {"key": "DL-2", "title": "Old name"}, 600)
    clock[0] += 10 * 86400
    queued, fetched = [], []
    cache.revalidator = lambda *_: None
    monkeypatch.setattr(client, "_refresh_bg", lambda *args: queued.append(args))
    monkeypatch.setattr(client, "prefetch_issues", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client, "_fetch_epic_metadata", lambda _key:
                        fetched.append(1) or {"key": "DL-2", "title": "New name"})
    assert client.epic_metadata_cached_many(["DL-2"])[0]["title"] == "Old name"
    assert len(queued) == 1 and not fetched
    with read_scope(strict=True):
        assert client.epic_metadata_many(["DL-2"])[0]["title"] == "New name"
    assert fetched == [1] and cache.metadata(key)["fetchedAt"] == clock[0]


@pytest.mark.parametrize("complete", [True, False])
def test_fresh_issue_view_records_only_successful_source_metadata(cache, clock, complete):
    from types import SimpleNamespace
    from app.infra.settings import get_settings
    from app.jira.jira_client import JiraClient
    client = JiraClient(get_settings(), cache)
    client._provider = SimpleNamespace(get_json=lambda *_args, **_kwargs: issue() if complete else {"error": "incomplete"})
    client._provider_built = True
    with read_scope() as sources:
        result = client._get_issue_view("DL-1", fresh=True)
    if complete:
        assert result == issue() and sources[-1]["kind"] == "issueview"
        assert sources[-1]["fetchedAt"] == clock[0]
    else:
        assert result is None and not sources
        assert cache.get(f"issueview:{client.env}:DL-1") is None
