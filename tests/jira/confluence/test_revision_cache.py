"""Revision cache invariants independent of transport and live Confluence access."""
import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.auth.base import PermissionDenied, UpstreamError, UpstreamUnavailable
from app.infra.cache import Cache
from app.infra.cache_policy import CachePolicy, primary_sources, read_scope
from app.jira import confluence_service as conf
from app.local_mcp.confluence_read_tools import build_cql, read_document


@pytest.fixture
def source():
    cache = Cache(":memory:")
    cache.configure(CachePolicy())
    settings = SimpleNamespace(confluence_base="https://docs.test/wiki", search_confluence_spaces=["DL"])
    client = SimpleNamespace(cache=cache, s=settings, env="prod")
    row = {"id": "100", "type": "page", "title": "Original", "status": "current", "space": {"key": "DL"},
           "version": {"number": 1, "when": "2026-11-01T00:00:00Z", "by": {"username": "author"}},
           "body": {"storage": {"value": "<p>first revision</p>", "representation": "storage"}}, "ancestors": []}
    versions, calls = {1: row}, []
    state = SimpleNamespace(current=1, failure=None)
    def fetch(path, params=None):
        calls.append((path, copy.deepcopy(params)))
        if state.failure:
            raise state.failure
        selected = (params or {}).get("version", state.current)
        if selected not in versions:
            raise UpstreamError(404, path)
        result = copy.deepcopy(versions[selected])
        if "body." not in (params or {}).get("expand", ""):
            result.pop("body", None)
        return result
    client._conf_get_json = fetch
    def publish(text="second revision"):
        state.current += 1
        versions[state.current] = copy.deepcopy(row)
        versions[state.current]["version"]["number"] = state.current
        versions[state.current]["body"]["storage"]["value"] = "<p>" + text + "</p>"
    yield SimpleNamespace(client=client, settings=settings, cache=cache, state=state, calls=calls,
                          versions=versions, fetch=fetch, publish=publish)
    cache.close()


def test_force_revision_check_fetches_only_metadata_and_reuses_original_body(source):
    first = conf.body(source.client, "100")
    assert len(source.calls) == 2
    before = first["cacheInfo"]["bodyFetchedAt"]
    check = conf.check_revision(source.client, "100", 1)
    assert check["isLatest"] and check["draftState"] == "unknown"
    assert check["lastModifiedBy"] == {"username": "author"}
    assert "body" not in source.calls[-1][1]["expand"]
    again = conf.body(source.client, "100")
    assert len(source.calls) == 3 and again["cacheInfo"]["bodyFetchedAt"] == before
    source.publish()
    assert conf.check_revision(source.client, "100", 1)["isLatest"] is False
    assert conf.body(source.client, "100")["version"]["number"] == 2
    assert conf.body(source.client, "100", version=1)["body"] == first["body"]
    assert len([p for _, p in source.calls if "body.storage" in p["expand"]]) == 2


def test_live_checks_latest_revision_but_reuses_immutable_body_age(source):
    first = conf.body(source.client, "100")
    original_age = first["cacheInfo"]["bodyFetchedAt"]
    with read_scope(cache_mode="live") as observed:
        again = conf.body(source.client, "100")
    assert len(source.calls) == 3  # Existing head+body, then only a new head check.
    assert again["body"] == first["body"]
    assert again["cacheInfo"]["bodyFetchedAt"] == original_age
    assert any(row.get("immutableRevision") for row in observed)
    assert all(row.get("compliant") for row in observed)


def test_live_revision_reuse_never_hides_permission_failure(source):
    conf.body(source.client, "100")
    source.state.failure = PermissionDenied("revoked")
    with read_scope(cache_mode="live"), pytest.raises(PermissionDenied):
        conf.body(source.client, "100")
    assert not source.cache.entries_by_prefix("confbody:", retained=True)


def test_browser_body_pair_primes_mcp_source_in_one_versioned_request(source):
    original = source.fetch
    def fetch(path, params=None):
        data = original(path, params)
        if 'body.view' in params.get('expand', ''):
            data['body']['view'] = {'value': '<p>rendered first revision</p>'}
        return data
    source.client._conf_get_json = fetch
    view = conf.body(source.client, '100', representation='view', prefetch_storage=True)
    source_body = conf.body(source.client, '100')
    assert len(source.calls) == 2  # head + view/storage together
    assert view['body'] == {'view': {'value': '<p>rendered first revision</p>'}}
    assert set(source_body['body']) == {'storage'}
    fetched = source_body['cacheInfo']['bodyFetchedAt']
    assert conf.body(source.client, '100')['cacheInfo']['bodyFetchedAt'] == fetched
    assert len(source.calls) == 2


def test_incomplete_combined_body_does_not_poison_either_cache(source):
    # The source fixture returns storage only: the complete paired response is required.
    with pytest.raises(UpstreamUnavailable):
        conf.body(source.client, '100', representation='view', prefetch_storage=True)
    assert not source.cache.entries_by_prefix('confbody:')
    assert not source.cache.entries_by_prefix('confview:')


def test_combined_body_respects_write_generation_fence(source):
    original = source.fetch
    def fetch(path, params=None):
        data = original(path, params)
        if 'body.view' in params.get('expand', ''):
            data['body']['view'] = {'value': '<p>old</p>'}
            source.cache.invalidate('conf')  # A write completes while the old read is pending.
        return data
    source.client._conf_get_json = fetch
    conf.body(source.client, '100', representation='view', prefetch_storage=True)
    assert not source.cache.entries_by_prefix('confbody:')
    assert not source.cache.entries_by_prefix('confview:')


def test_30_day_retention_survives_purge_and_preserves_source_age(source, monkeypatch):
    now = time.time()
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now)
    conf.body(source.client, "100")
    body_key = next(iter(source.cache.entries_by_prefix("confbody:")))
    now += 20 * 86400
    source.cache.purge()
    assert source.cache.get(body_key) is not None
    with read_scope() as rows:
        result = conf.body(source.client, "100")
    assert result["cacheInfo"]["bodyFetchedAt"] == now - 20 * 86400
    assert all(r["kind"] != "confbody" for r in primary_sources(rows))
    assert len([p for _, p in source.calls if "body.storage" in p["expand"]]) == 1
    source.cache.configure(CachePolicy(confluenceBodyRetentionSeconds=10 * 86400))
    assert source.cache.get(body_key) is None
    source.cache.purge()
    assert source.cache.get_stale(body_key) is None


def test_permission_revocation_removes_all_revisions_but_network_failure_preserves_them(source):
    conf.body(source.client, "100")
    source.publish()
    conf.check_revision(source.client, "100")
    conf.body(source.client, "100")
    assert len(source.cache.entries_by_prefix("confbody:")) == 2
    source.state.failure = UpstreamUnavailable("offline")
    with pytest.raises(UpstreamUnavailable):
        conf.check_revision(source.client, "100")
    assert len(source.cache.entries_by_prefix("confbody:")) == 2
    source.state.failure = PermissionDenied("revoked")
    with pytest.raises(PermissionDenied):
        conf.check_revision(source.client, "100")
    assert not source.cache.entries_by_prefix("confbody:")
    assert not source.cache.entries_by_prefix("confhead:")


def test_missing_historical_version_does_not_remove_current_content(source):
    conf.body(source.client, "100")
    with pytest.raises(UpstreamError):
        conf.body(source.client, "100", version=90)
    assert conf.body(source.client, "100")["version"]["number"] == 1


def test_pinned_chunks_do_not_mix_versions_after_publication(source):
    runtime = SimpleNamespace(client=source.client, settings=source.settings)
    first = read_document(runtime, "100", max_chars=5)
    source.publish("a completely different document")
    conf.check_revision(source.client, "100")
    second = read_document(runtime, "100", offset=first["nextOffset"], version=first["version"]["number"])
    assert first["content"] + second["content"] == "first revision"
    assert second["cacheInfo"]["isLatest"] is False
    with pytest.raises(ValueError, match="version.number"):
        read_document(runtime, "100", offset=5)


def test_wrong_version_response_is_not_cached(source):
    source.client._conf_get_json = lambda path, params=None: source.fetch(path, {**params, "version": 1})
    with pytest.raises(UpstreamUnavailable):
        conf.body(source.client, "100", version=2)
    assert not source.cache.entries_by_prefix("confbody:")


def test_concurrent_latest_checks_share_one_upstream_request(source):
    entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
    def blocked(path, params=None):
        entered.set()
        assert release.wait(3)
        return source.fetch(path, params)
    source.client._conf_get_json = blocked
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(conf.check_revision, source.client, "100", 1)
        assert entered.wait(3)
        flight = next(iter(source.cache._flights.values()))
        original_result = flight.result
        def await_result(*args, **kwargs):
            waiting.set()
            return original_result(*args, **kwargs)
        flight.result = await_result
        two = pool.submit(conf.check_revision, source.client, "100", 1)
        assert waiting.wait(3)
        release.set()
        assert one.result()["isLatest"] and two.result()["isLatest"]
    assert len(source.calls) == 1


def test_old_refresh_cannot_repopulate_head_after_successful_write(source):
    def raced(path, params=None):
        data = source.fetch(path, params)
        source.publish()
        conf.invalidate(source.client, ["100"])
        return data
    source.client._conf_get_json = raced
    result = conf.check_revision(source.client, "100", 1)
    assert result["checkedAt"] is None
    assert not source.cache.entries_by_prefix("confhead:")
    source.client._conf_get_json = source.fetch
    assert conf.check_revision(source.client, "100", 1)["isLatest"] is False


def test_revision_cache_budget_evicts_storage_only_not_response(source, monkeypatch):
    monkeypatch.setattr(source.cache, "CONFLUENCE_BODY_BUDGET", 20)
    result = conf.body(source.client, "100")
    assert result["body"]["storage"]["value"] == "<p>first revision</p>"
    assert not source.cache.entries_by_prefix("confbody:")


@pytest.mark.parametrize("query", ["type=page) OR space=OTHER OR (type=page", "title = 'unterminated", "title='" + "가" * 6000 + "'"],
                         ids=["scope_escape", "unclosed_quote", "utf8_limit"])
def test_raw_cql_cannot_escape_configured_space_wrapper(query):
    with pytest.raises(ValueError):
        build_cql(["DL"], cql=query)


def test_raw_cql_preserves_quoted_order_and_parenthesized_or():
    query = build_cql(["DL"], cql='title = "ORDER BY title" OR label = "runbook" ORDER BY title ASC')
    assert query == 'space in ("DL") AND (title = "ORDER BY title" OR label = "runbook") ORDER BY title ASC'
