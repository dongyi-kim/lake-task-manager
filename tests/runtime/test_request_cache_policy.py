"""Explicit request age limits are independent of UI display and cache retention."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.infra.cache import Cache
from app.infra.cache_policy import CachePolicy, read_scope


@pytest.fixture
def cache(monkeypatch):
    now = [100000.0]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now[0])
    value = Cache(":memory:")
    value.configure(CachePolicy())
    yield value, now
    value.close()


def test_request_policy_inherits_through_nested_refresh_scope():
    from app.infra.cache_policy import current_read_policy, resolve_read_policy
    assert resolve_read_policy("hot").max_age_seconds == 600
    assert resolve_read_policy("warm").max_age_seconds == 86400
    assert resolve_read_policy("cool").max_age_seconds == 604800
    assert resolve_read_policy("cold").max_age_seconds == 2592000
    with read_scope(cache_mode="live"):
        outer = current_read_policy()
        with read_scope(strict=True):
            assert current_read_policy() is outer
    assert current_read_policy().mode is None
    with pytest.raises(ValueError):
        resolve_read_policy("cold", force_refresh=True)


@pytest.mark.parametrize("batch", [False, True])
def test_cached_copy_setters_preserve_age_without_inventing_upstream_validation(cache, batch):
    store, now = cache
    store.set("comments:mock:source", ["cached"], 600)
    now[0] += 2 * 86400
    with read_scope(cache_mode="cool"):
        value, source = store.get_with_metadata("comments:mock:source")
        kwargs = {"fetched_at": source["fetchedAt"], "upstream_validated": source["upstreamValidated"]}
        if batch:
            assert store.set_many_if_fence([("comments:mock:copy", value)], 600, store.fence(), **kwargs)
        else:
            assert store.set_if_fence("comments:mock:copy", value, 600, store.fence(), **kwargs)
        copied = store.metadata("comments:mock:copy")
        assert copied["fetchedAt"] == 100000
        assert copied["compliant"] and not copied["upstreamValidated"]


@pytest.mark.parametrize("batch", [False, True])
def test_uncertified_copy_clears_same_request_live_validation(cache, batch):
    store, now = cache
    with read_scope(cache_mode="live"):
        store.set_if_fence("comments:mock:source", ["upstream"], 600, store.fence())
        assert store.get("comments:mock:source") == ["upstream"]
        if batch:
            store.set_many_if_fence([("comments:mock:source", ["copied"])], 600, store.fence(),
                                   fetched_at=now[0], upstream_validated=False)
        else:
            store.set_if_fence("comments:mock:source", ["copied"], 600, store.fence(),
                              fetched_at=now[0], upstream_validated=False)
        assert store.get("comments:mock:source") is None
        assert not store.metadata("comments:mock:source")["upstreamValidated"]


def test_shared_reducer_preserves_http_and_mcp_source_selection():
    from app.infra.cache_policy import reduce_read_metadata, resolve_read_policy
    rows = [
        {"kind": "issue", "state": "fresh", "fetchedAt": 100100, "freshUntil": 100700,
         "displayUntil": 186500, "compliant": True, "upstreamValidated": True},
        {"kind": "epicmeta", "state": "stale", "fetchedAt": 100000, "freshUntil": 100050,
         "displayUntil": 2692000, "compliant": False, "upstreamValidated": False},
    ]
    policy = resolve_read_policy("live")
    assert reduce_read_metadata(rows, policy)["fetchedAt"] == 100100
    all_sources = reduce_read_metadata(rows, policy, source_selection="all")
    assert all_sources["fetchedAt"] == 100000
    assert all_sources["state"] == "stale"
    assert all_sources["oldestComponentFetchedAt"] == 100000
    assert not all_sources["compliant"]
    assert not all_sources["upstreamValidated"]
    with pytest.raises(ValueError):
        reduce_read_metadata(rows, source_selection="unknown")


@pytest.mark.parametrize("mode,age,hit", [("hot", 600, True), ("hot", 601, False),
    ("warm", 86400, True), ("warm", 86401, False),
    ("cool", 604799, True), ("cool", 604800, True), ("cool", 604801, False), ("cold", 2592000, True),
    ("cold", 2592001, False)])
def test_explicit_age_boundaries_override_ui_policy(cache, mode, age, hit):
    store, now = cache
    store.set("issue:mock:DL-1", {"fields": {}}, 1)
    now[0] += age
    with read_scope(cache_mode=mode):
        assert (store.get("issue:mock:DL-1") is not None) is hit
        assert bool(store.get_many(["issue:mock:DL-1"])) is hit


def test_modes_share_retained_sources_through_restart_and_invalidation(tmp_path, monkeypatch):
    now = [100000.0]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now[0])
    path = tmp_path / "cache.db"
    store = Cache(path)
    store.configure(CachePolicy())
    store.set("comments:mock:DL-1", ["original"], 600)
    store.close()
    now[0] += 2 * 86400
    reopened = Cache(path)
    try:
        reopened.configure(CachePolicy())
        with read_scope(cache_mode="warm"):
            assert reopened.get("comments:mock:DL-1") is None
        for mode in ("cool", "cold"):
            with read_scope(cache_mode=mode) as sources:
                assert reopened.get("comments:mock:DL-1") == ["original"]
                assert sources[-1]["fetchedAt"] == 100000
        assert reopened.get("comments:mock:DL-1") is None  # Omission keeps its existing TTL.
        reopened.invalidate("comments:mock:DL-1")
        for mode in ("warm", "cool", "cold"):
            with read_scope(cache_mode=mode):
                assert reopened.get("comments:mock:DL-1") is None
                assert reopened.get_alive("comments:mock:DL-1") is None
    finally:
        reopened.close()


def test_verified_immutable_body_retains_age_without_expiring_aggregate(cache):
    from app.infra.cache_policy import note_read
    store, now = cache
    old = now[0]
    now[0] += 20 * 86400
    def compose():
        store.get_or_set("confhead:mock:page", 600, lambda: {"version": 1})
        note_read({"kind": "confbody", "state": "fresh", "fetchedAt": old,
                   "freshUntil": old + 2592000, "displayUntil": old + 2592000,
                   "immutableRevision": True, "compliant": True, "upstreamValidated": True,
                   "latestRevisionCheckedAt": now[0]})
        return {"body": "unchanged revision"}
    with read_scope(cache_mode="hot") as sources:
        assert store.refresh("confview:mock:aggregate", 600, compose) == {"body": "unchanged revision"}
        assert sources[-1]["fetchedAt"] == now[0]
        assert next(row for row in sources if row["kind"] == "confbody")["fetchedAt"] == old
    assert store.metadata("confview:mock:aggregate")["fetchedAt"] == now[0]


@pytest.mark.parametrize("proof", [{}, {"latestRevisionCheckedAt": True},
    {"latestRevisionCheckedAt": 100000}, {"latestRevisionCheckedAt": 1828000, "compliant": False}])
def test_immutable_flag_without_current_head_proof_cannot_launder_aggregate_age(cache, proof):
    from app.infra.cache_policy import note_read
    store, now = cache
    old = now[0]
    now[0] += 20 * 86400
    def compose():
        note_read({"kind": "confbody", "state": "fresh", "fetchedAt": old,
                   "freshUntil": old + 2592000, "displayUntil": old + 2592000,
                   "immutableRevision": True, "compliant": True, "upstreamValidated": True, **proof})
        return {"body": "unchanged revision"}
    with read_scope(cache_mode="hot"), pytest.raises(RuntimeError):
        store.refresh("confview:mock:aggregate", 600, compose)


def test_shared_aggregate_flight_preserves_immutable_body_provenance_for_both_readers(cache, monkeypatch):
    from app.infra.cache_policy import note_read
    from concurrent.futures import Future
    store, now = cache
    old = now[0]
    now[0] += 20 * 86400
    entered, joined, release = threading.Event(), threading.Event(), threading.Event()
    class ObservedFuture(Future):
        def result(self, *args, **kwargs):
            joined.set()
            return super().result(*args, **kwargs)
    monkeypatch.setattr("app.infra.cache.Future", ObservedFuture)
    def compose():
        entered.set()
        assert release.wait(3)
        note_read({"kind": "confbody", "state": "fresh", "fetchedAt": old,
                   "freshUntil": old + 2592000, "displayUntil": old + 2592000,
                   "immutableRevision": True, "compliant": True, "upstreamValidated": True,
                   "latestRevisionCheckedAt": now[0]})
        return {"body": "unchanged revision"}
    def read():
        with read_scope(cache_mode="hot") as sources:
            result = store.refresh("confview:mock:aggregate", 600, compose)
        return result, sources
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(read)
        assert entered.wait(2)
        second = pool.submit(read)
        try:
            assert joined.wait(2)
        finally:
            release.set()
        for result, sources in (first.result(), second.result()):
            assert result == {"body": "unchanged revision"}
            body = next(row for row in sources if row["kind"] == "confbody")
            assert body["fetchedAt"] == old
            assert sources[-1]["fetchedAt"] == now[0]


@pytest.mark.parametrize("mode", ["hot", "warm", "cool", "cold"])
def test_ticket_http_fresh_alias_rejects_conflicting_mode_before_business_read(cache, mode):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from app.infra.cache_policy import CacheHeadersMiddleware
    from app.routes.tickets import build_ticket_router
    calls = []
    client = SimpleNamespace(ticket_view=lambda *args, **kwargs: calls.append(1) or {})
    app = FastAPI()
    app.include_router(build_ticket_router(get_client=lambda: client))
    app.add_middleware(CacheHeadersMiddleware, cache=cache[0])
    with TestClient(app) as http:
        assert http.get("/api/ticket/DL-1?fresh=1&cache_mode=" + mode).status_code == 400
    assert not calls


def test_ticket_http_fresh_alias_performs_live_validation_without_changing_other_routes(cache):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from app.infra.cache_policy import CacheHeadersMiddleware, current_read_policy
    from app.routes.tickets import build_ticket_router
    store, now = cache
    calls = []
    store.set("issueview:mock:DL-1", {"fields": {}, "version": "old"}, 600)
    def view(key, fresh=False):
        assert fresh and current_read_policy().mode.value == "live"
        def upstream():
            calls.append(1)
            return {"fields": {}, "version": "new"}
        return store.get_or_set("issueview:mock:DL-1", 600, upstream)[0]
    app = FastAPI()
    app.include_router(build_ticket_router(get_client=lambda: SimpleNamespace(ticket_view=view)))
    @app.get("/api/unrelated")
    def unrelated(fresh: int = 0):
        return {"mode": current_read_policy().mode}
    app.add_middleware(CacheHeadersMiddleware, cache=store)
    with TestClient(app) as http:
        for query in ("fresh=1", "fresh=2&cache_mode=live"):
            result = http.get("/api/ticket/DL-1?" + query)
            assert result.status_code == 200 and result.json()["version"] == "new"
        assert http.get("/api/unrelated?fresh=1&cache_mode=warm").json() == {"mode": "warm"}
    assert len(calls) == 2


def test_confluence_http_refresh_alias_validates_upstream_and_rejects_conflict(cache):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from app.infra.cache_policy import CacheHeadersMiddleware, current_read_policy
    from app.routes.confluence import build_confluence_router
    store, now = cache
    calls = []
    def upstream(path, params):
        mode = current_read_policy().mode
        calls.append(mode.value if mode is not None else None)
        return {"id": "100", "title": "Page", "type": "page", "status": "current",
                "space": {"key": "DL"}, "version": {"number": 1}, "ancestors": [],
                "body": {"view": {"value": "<p>body</p>"}, "storage": {"value": "<p>body</p>"}}}
    client = SimpleNamespace(cache=store, env="mock", _conf_get_json=upstream,
                             s=SimpleNamespace(confluence_base="https://docs.test", search_confluence_spaces=["DL"]))
    app = FastAPI()
    app.include_router(build_confluence_router(get_client=lambda: client))
    app.add_middleware(CacheHeadersMiddleware, cache=store)
    with TestClient(app) as http:
        assert http.get("/api/confluence/pages/100").status_code == 200
        before = len(calls)
        assert http.get("/api/confluence/pages/100?refresh=true&cache_mode=cool").status_code == 400
        assert len(calls) == before
        for flag in ("true", "1", "on"):
            before = len(calls)
            assert http.get("/api/confluence/pages/100?refresh=" + flag).status_code == 200
            assert len(calls) > before
            assert all(mode == "live" for mode in calls[before:])
        assert http.get("/api/confluence/pages/100?refresh=false&cache_mode=warm").status_code == 200


def test_http_cache_policy_failure_is_retryable_without_login_or_transport_side_effects(monkeypatch):
    import app.main as main
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from app.infra.cache import CacheReadPolicyError
    effects = []
    def view(*_args, **_kwargs):
        raise CacheReadPolicyError("Read changed during validation")
    client = SimpleNamespace(ticket_view=view, mark_upstream_down=lambda *a: effects.append("down"),
                             mark_session_dead=lambda *a: effects.append("login"),
                             direct_session_state=lambda: effects.append("auth probe"))
    monkeypatch.setattr(main, "_client", client)
    response = TestClient(main.app, raise_server_exceptions=False).get("/api/ticket/DL-1?cache_mode=live")
    assert response.status_code == 503
    assert response.json()["retryable"] is True and response.json()["needLogin"] is False
    assert response.json()["code"] == "cache_policy_unavailable"
    assert response.headers["retry-after"] == "1"
    assert not effects


def test_explicit_hit_never_schedules_and_failure_never_uses_display(cache):
    store, now = cache
    store.set("comments:mock:DL-1", ["old"], 600)
    store.always_revalidate = ("comments:",)
    store.revalidator = lambda *_: pytest.fail("explicit hit must not schedule SWR")
    now[0] += 700
    with read_scope(cache_mode="warm"):
        assert store.get_or_set("comments:mock:DL-1", 600, lambda: ["new"]) == (["old"], True)
    def offline():
        raise ConnectionError("offline")
    with read_scope(cache_mode="hot"), pytest.raises(ConnectionError):
        store.get_or_set("comments:mock:DL-1", 600, offline)
    store.invalidate("comments:mock:DL-1")
    with read_scope(cache_mode="cold"), pytest.raises(ConnectionError):
        store.get_or_set_swr("comments:mock:DL-1", 600, offline, store.revalidator)


def test_live_revalidates_prior_cache_but_reuses_same_request_validation(cache):
    store, now = cache
    store.set("comments:mock:DL-1", ["old"], 600)
    calls = []
    def produce():
        calls.append(1)
        return ["upstream"]
    with read_scope(cache_mode="live") as sources:
        assert store.get("comments:mock:DL-1") is None
        assert store.get_or_set("comments:mock:DL-1", 600, produce) == (["upstream"], False)
        assert store.get("comments:mock:DL-1") == ["upstream"]
        assert sources[-1]["upstreamValidated"]
        assert sources[-1]["compliant"]
    with read_scope(cache_mode="live"):
        assert store.get("comments:mock:DL-1") is None
        store.get_or_set("comments:mock:DL-1", 600, produce)
    assert len(calls) == 2


def test_retention_survives_twenty_days_and_keeps_reverse_indexes(cache):
    store, now = cache
    store.set("issue:mock:DL-1", {"fields": {}}, 600)
    store.set("jqlidx:mock:DL-1:leaf", {"leaf": "all"}, 600)
    now[0] += 20 * 86400
    store.purge()
    assert store.get_alive("issue:mock:DL-1") is None
    assert store.entries_by_prefix("jqlidx:mock:DL-1:") == {}
    assert store.entries_by_prefix("jqlidx:mock:DL-1:", retained=True)
    with read_scope(cache_mode="cold"):
        assert store.get("issue:mock:DL-1") == {"fields": {}}


def test_atomic_reads_return_matching_values_and_source_times(cache):
    store, now = cache
    store.set("comments:mock:DL-1", ["old"], 600)
    with read_scope(cache_mode="warm"):
        result = store.get_many_with_metadata(["comments:mock:DL-1"])
    now[0] += 20
    store.set("comments:mock:DL-1", ["new"], 600)
    assert result["comments:mock:DL-1"][0] == ["old"]
    assert result["comments:mock:DL-1"][1]["fetchedAt"] == 100000
    now[0] += 20 * 86400
    with read_scope(cache_mode="live"):
        retained = store.get_many_with_metadata(["comments:mock:DL-1"], retained=True)
        assert retained["comments:mock:DL-1"][0] == ["new"]
        assert retained["comments:mock:DL-1"][1]["fetchedAt"] == 100020
        assert not retained["comments:mock:DL-1"][1]["compliant"]


@pytest.mark.parametrize("value", [None, {"error": "offline"}, {"partial": True, "issues": []},
                                  {"complete": False}, {"ok": False}, {"missing": 1}])
def test_live_incomplete_producer_cannot_complete_as_success(cache, value):
    with read_scope(cache_mode="live"), pytest.raises(RuntimeError):
        cache[0].get_or_set("comments:mock:DL-1", 600, lambda: value)


def test_live_assembly_cannot_certify_unvalidated_component(cache):
    from app.infra.cache_policy import note_read
    store, now = cache
    store.set("comments:mock:DL-1", ["old"], 600)
    source = store.metadata("comments:mock:DL-1")
    def compose():
        note_read(source)
        return ["assembled"]
    with read_scope(cache_mode="live"), pytest.raises(RuntimeError) as error:
        store.refresh("timeline:mock:DL-1", 600, compose)
    assert error.value.retryable is True
    assert error.value.status == 503


def test_live_concurrent_requests_share_only_the_upstream_flight(cache):
    store, now = cache
    entered, release, joined = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def produce():
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return ["upstream"]
    def read():
        with read_scope(cache_mode="live") as sources:
            value = store.get_or_set("comments:mock:DL-1", 600, produce)[0]
            return value, sources
    from concurrent.futures import Future
    class ObservedFuture(Future):
        def result(self, *args, **kwargs):
            joined.set()
            return super().result(*args, **kwargs)
    from unittest.mock import patch
    with patch("app.infra.cache.Future", ObservedFuture), ThreadPoolExecutor(2) as pool:
        first = pool.submit(read)
        assert entered.wait(2)
        second = pool.submit(read)
        try:
            assert joined.wait(2)
        finally:
            release.set()
        for value, sources in (first.result(), second.result()):
            assert value == ["upstream"]
            assert sources[-1]["upstreamValidated"] and sources[-1]["compliant"]
    assert len(calls) == 1


def test_joined_live_flight_rechecks_mutation_generation(cache):
    store, now = cache
    entered, release, joined = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def produce():
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
            return ["old"]
        return ["after-mutation"]
    def read():
        with read_scope(cache_mode="live"):
            return store.get_or_set("comments:mock:DL-1", 600, produce)[0]
    from concurrent.futures import Future
    class ObservedFuture(Future):
        def result(self, *args, **kwargs):
            joined.set()
            return super().result(*args, **kwargs)
    from unittest.mock import patch
    with patch("app.infra.cache.Future", ObservedFuture), ThreadPoolExecutor(2) as pool:
        first = pool.submit(read)
        assert entered.wait(2)
        second = pool.submit(read)
        try:
            assert joined.wait(2)
            store.invalidate("comments:mock:DL-1")
        finally:
            release.set()
        assert first.result() == ["after-mutation"]
        assert second.result() == ["after-mutation"]
    assert store.get("comments:mock:DL-1") == ["after-mutation"]


def test_payload_budget_uses_access_order_without_erasing_local_ui_state(cache):
    store, now = cache
    store.PAYLOAD_BUDGET = 26
    store.set("user:mock:first", "12345678", 600)
    now[0] += 1
    store.set("user:mock:second", "abcdefgh", 600)
    now[0] += 1
    assert store.get("user:mock:first")
    store.mark_ticket_viewed("actor", "DL-1", 123)
    store.set("user:mock:third", "ABCDEFGH", 600)
    assert store.get("user:mock:second") is None
    assert store.get("user:mock:first") == "12345678"
    assert store.get("user:mock:third") == "ABCDEFGH"
    assert store.ticket_read_states("actor", ["DL-1"]) == {"DL-1": 123}


def test_budget_tracks_overwrites_display_archiving_and_database_reopen(tmp_path, monkeypatch):
    now = [100000.0]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now[0])
    path = tmp_path / "cache.db"
    store = Cache(path)
    try:
        store.configure(CachePolicy())
        store.PAYLOAD_BUDGET = 26
        store.set("user:first", "12345678", 600)  # 10 UTF-8 JSON bytes
        now[0] += 1
        store.set("user:second", "abcdefgh", 600)
        now[0] += 1
        store.set("user:first", "x", 600)  # Replacing 10 bytes with 3 frees 7.
        now[0] += 1
        store.set("user:third", "ABCDEFGH", 600)
        assert len(store.get_many(["user:first", "user:second", "user:third"])) == 3
        store.invalidate("user:first")  # Move payload to display storage without duplicating its bytes.
        now[0] += 1
        store.set("user:first", "123456789012", 600)  # 14 + 10 + 10 requires one eviction.
        assert store.get("user:second") is None
        now[0] += 1
        assert store.get("user:third") == "ABCDEFGH"
    finally:
        store.close()
    reopened = Cache(path)
    try:
        reopened.configure(CachePolicy())
        reopened.PAYLOAD_BUDGET = 26
        now[0] += 1
        reopened.set("user:fourth", "한", 600)  # UTF-8 JSON payload is 5 bytes.
        assert reopened.get("user:first") is None
        assert reopened.get("user:third") == "ABCDEFGH"
        assert reopened.get("user:fourth") == "한"
    finally:
        reopened.close()


@pytest.mark.parametrize("query", ["cache_mode=bad", "cache_mode=", "cache_mode=warm&cache_mode=hot", "cache_mode=cold&revalidate=1"])
def test_http_rejects_invalid_or_conflicting_mode_before_handler(cache, query):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.infra.cache_policy import CacheHeadersMiddleware
    app = FastAPI()
    app.add_middleware(CacheHeadersMiddleware, cache=cache[0])
    called = []
    @app.get("/api/example")
    def endpoint():
        called.append(1)
        return {}
    with TestClient(app) as client:
        assert client.get("/api/example?" + query).status_code == 400
    assert not called
