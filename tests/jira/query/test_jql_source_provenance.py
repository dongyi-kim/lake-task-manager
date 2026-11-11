"""Reusing search memberships must not make old issue evidence look newly fetched."""
from copy import deepcopy
import time

import pytest

from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.infra.cache_policy import read_scope
from app.jira.jira_client import JiraClient, MutationEvent


def client():
    return JiraClient(get_settings(), Cache(":memory:"))


def test_light_read_never_certifies_an_expired_value_after_concurrent_reprime(monkeypatch):
    c = client()
    key = "DL-9012"
    row = deepcopy(c.get_issue(key))
    row["fields"]["summary"] = "expired content"
    cache_key = f"issue:{c.env}:{key}"
    now = time.time()
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now)
    c.cache.set_if_fence(cache_key, row, 600, c.cache.fence(), fetched_at=now - 601)
    original = c.cache.get_stale_at

    def reprime_after_capture(candidate):
        captured = original(candidate)
        if candidate == cache_key:
            replacement = deepcopy(row)
            replacement["fields"]["summary"] = "refreshed content"
            c.cache.set(cache_key, replacement, 600)
        return captured

    monkeypatch.setattr(c.cache, "get_stale_at", reprime_after_capture)
    with read_scope(cache_mode="hot") as sources:
        result = c.get_issue_light(key)
    assert result["fields"]["summary"] != "expired content"
    assert sources and all(source["fetchedAt"] == now for source in sources)


@pytest.mark.parametrize("include_children", [False, True])
@pytest.mark.parametrize("mode", ["live", "hot"])
def test_explicit_timeline_policy_never_uses_deferred_stale_data(monkeypatch, include_children, mode):
    c = client()
    key = "DL-9100"
    now = time.time()
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now)
    stale = [{"kind": "expired-proof", "date": "2099-01-01", "srcKey": None}]
    for tier in ("self", "children"):
        c.cache.set_if_fence(f"timeline:{c.env}:{key}:{tier}:v2", stale, 600,
                             c.cache.fence(), fetched_at=now - 86401)
    scheduled = []
    monkeypatch.setattr(c, "_refresh_bg", lambda *args: scheduled.append(args))
    with read_scope(cache_mode=mode) as sources:
        result = c.ticket_timeline(key, defer=True, include_children=include_children)
    assert result and all(event["kind"] != "expired-proof" for event in result)
    assert sources and all(source.get("compliant") for source in sources)
    assert scheduled == []


@pytest.mark.parametrize("source_kind", ["issueL", "jqlrow"])
def test_hydration_preserves_cached_row_collection_time(source_kind, monkeypatch):
    c = client()
    row = c.get_issue_light("DL-9012")
    fields = c._issue_fields(light=True)
    generation = c._jql_generation()
    projection = c._projection_signature(fields, True)
    target = c._jql_row_key(generation, projection, row["key"])
    source = target if source_kind == "jqlrow" else f"issueL:{c.env}:{row['key']}"
    collected = time.time() - 100
    c.cache.set_if_fence(source, row, 600, c.cache.fence(), fetched_at=collected)
    def unexpected_fetch(*args):
        pytest.fail("Complete cached rows must not cause a Jira request")
    monkeypatch.setattr(c, "_fetch_exhaustive_jql", unexpected_fetch)

    assert c._hydrate_jql_leaf_rows([row["key"]], fields, True, generation, projection) == [row]
    assert c.cache.metadata(target)["fetchedAt"] == collected


def test_legacy_list_hit_preserves_write_through_source_age(monkeypatch):
    c = client()
    row = c.get_issue_light("DL-9012")
    collected = time.time() - 100
    c.cache.set_if_fence("mt:provenance", [row], 600, c.cache.fence(), fetched_at=collected)
    def unexpected_fetch(*args, **kwargs):
        pytest.fail("A valid list hit must not cause a Jira request")
    monkeypatch.setattr(c.provider, "get_json", unexpected_fetch)

    assert c._legacy_search("key = DL-9012", cache_key="mt:provenance", light=True) == [row]
    assert c.cache.metadata(f"issueL:{c.env}:{row['key']}")["fetchedAt"] == collected


def test_readiness_rejects_a_cached_row_for_a_different_issue():
    c = client()
    row = deepcopy(c.get_issue_light("DL-9012"))
    fields = "summary,assignee,reporter,status"
    generation = c._jql_generation()
    projection = c._projection_signature(fields, True)
    leaf = "key = DL-9012"
    c.cache.set(c._jql_leaf_key(leaf)[2], ["DL-9012"], 600)
    row["key"] = "DL-9013"
    c.cache.set(c._jql_row_key(generation, projection, "DL-9012"), row, 600)

    assert c._jql_leaf_cache_ready(leaf, fields, True, generation, projection) is False


def test_twenty_day_membership_is_invalidated_when_a_new_issue_can_enter(monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])
    c = client()
    leaf = "assignee = nobody"
    assert c.search_issues(leaf) == []
    membership = c._jql_leaf_key(leaf)[2]
    clock[0] += 20 * 86400
    with read_scope(cache_mode="cold"):
        assert c.cache.get(membership) == []
    c._record_mutation(MutationEvent("fields", "DL-9012", changed_fields=("assignee",)))
    with read_scope(cache_mode="cold"):
        assert c.cache.get(membership) is None


def test_parallel_map_keeps_request_policy_and_reports_cached_source_age(monkeypatch):
    c = client()
    keys = ["DL-9012", "DL-9013"]
    rows = [c.get_issue_light(key) for key in keys]
    collected = time.time() - 2 * 86400
    for key, row in zip(keys, rows):
        c.cache.set_if_fence(f"issueL:{c.env}:{key}", row, 600, c.cache.fence(),
                             fetched_at=collected)
    monkeypatch.setattr(c.provider, "supports_parallel", True)
    def unexpected_request(*args, **kwargs):
        pytest.fail("Copied cold policy should reuse both cached rows without Jira requests")
    monkeypatch.setattr(c.provider, "get_json", unexpected_request)
    with read_scope(cache_mode="cold") as sources:
        assert c._pmap(keys, c.get_issue_light) == rows
    assert len(sources) >= 2
    assert all(source["fetchedAt"] == collected for source in sources)


def test_live_snapshot_continuation_is_frozen_and_new_live_search_validates(monkeypatch):
    c = client()
    query = "key = DL-9012 OR key = DL-9013"
    calls, original = [], c.provider.get_json
    def record(path, **kwargs):
        calls.append(path)
        return original(path, **kwargs)
    monkeypatch.setattr(c.provider, "get_json", record)
    with read_scope(cache_mode="live"):
        first = c.search_issues_page(query, max_results=1)
    before = len(calls)
    with read_scope(cache_mode="live") as sources:
        second = c.search_issues_page(query, start_at=1, max_results=1, snapshot_id=first["snapshotId"])
    assert second["snapshotId"] == first["snapshotId"]
    assert second["snapshotFrozen"] is True
    assert second["issues"][0]["key"] != first["issues"][0]["key"]
    assert len(calls) == before
    assert sources and not any(source.get("upstreamValidated") for source in sources)
    with read_scope(cache_mode="live"):
        fresh = c.search_issues_page(query, max_results=1)
    assert len(calls) > before and fresh["snapshotId"] != first["snapshotId"]


def test_cool_snapshot_renews_cursor_lifetime_without_restamping_cached_sources(monkeypatch):
    c = client()
    now = [time.time()]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now[0])
    query = "key = DL-9012 OR key = DL-9013"
    with read_scope(cache_mode="cool"):
        first = c.search_issues_page(query, max_results=1)
    collected = now[0]
    now[0] += 2 * 86400
    def unexpected_fetch(*args, **kwargs):
        pytest.fail("Cool policy can reuse the same retained membership and rows")
    monkeypatch.setattr(c.provider, "get_json", unexpected_fetch)
    with read_scope(cache_mode="cool"):
        with pytest.raises(ValueError, match="snapshot"):
            c.search_issues_page(query, start_at=1, snapshot_id=first["snapshotId"])
        renewed = c.search_issues_page(query, max_results=1)
    assert renewed["snapshotId"] != first["snapshotId"]
    with read_scope(cache_mode="cool") as sources:
        second = c.search_issues_page(query, start_at=1, snapshot_id=renewed["snapshotId"])
    assert second["snapshotSourceFetchedAt"] == collected
    assert second["snapshotCreatedAt"] == now[0]
    assert sources and all(source["fetchedAt"] == collected for source in sources)


def test_snapshot_rejects_a_changed_explicit_cache_mode():
    c = client()
    query = "key = DL-9012 OR key = DL-9013"
    with read_scope(cache_mode="cool"):
        first = c.search_issues_page(query, max_results=1)
    with read_scope(cache_mode="warm"), pytest.raises(ValueError, match="snapshot"):
        c.search_issues_page(query, start_at=1, snapshot_id=first["snapshotId"])


def test_snapshot_cannot_escape_identity_checks_through_unsupported_query(monkeypatch):
    c = client()
    first = c.search_issues_page("key = DL-9012 OR key = DL-9013", max_results=1)
    def unexpected_query(*args, **kwargs):
        pytest.fail("A mismatched cursor must not dispatch a fallback Jira query")
    monkeypatch.setattr(c, "_legacy_search_issues_page", unexpected_query)
    with pytest.raises(ValueError, match="snapshot"):
        c.search_issues_page("assignee in membersOf('jira-users')", start_at=1,
                             snapshot_id=first["snapshotId"])


def test_deriving_cached_group_memberships_does_not_reprime_issue_rows(monkeypatch):
    c = client()
    now = [time.time()]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now[0])
    query = "assignee = test.ui01 OR reporter = test.ui01"
    rows = c.search_issues(query)
    assert rows
    collected = now[0]
    compiled = c._compile_jql(query)
    c.cache.invalidate_keys([c._jql_leaf_key(leaf)[2] for leaf in compiled.leaves])
    c.cache.bump_epoch(c._jql_epoch_namespace())
    now[0] += 100
    def unexpected_fetch(*args, **kwargs):
        pytest.fail("Cached union membership and canonical rows cover the derived leaves")
    monkeypatch.setattr(c.provider, "get_json", unexpected_fetch)
    assert c.search_issues(query) == rows
    generation = c._jql_generation()
    projection = c._projection_signature(c._issue_fields(light=True), True)
    for row in rows:
        source = c.cache.metadata(c._jql_row_key(generation, projection, row["key"]))
        assert source is not None and source["fetchedAt"] == collected


def test_multi_page_query_rows_are_dated_from_collection_start(monkeypatch):
    c = client()
    now = [time.time()]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now[0])
    rows = [c.get_issue_light(key) for key in ("DL-9012", "DL-9013")]
    collected = now[0]
    def delayed_page(query, start_at=0, **kwargs):
        now[0] += 10
        return {"issues": rows[start_at:start_at + 1], "total": 2,
                "hasMore": start_at == 0, "nextStartAt": 1 if start_at == 0 else None}
    monkeypatch.setattr(c, "_legacy_search_issues_page", delayed_page)
    page = c.search_issues_page("key = DL-9012 OR key = DL-9013")
    assert len(page["issues"]) == 2
    for row in rows:
        assert c.cache.metadata(f"issueL:{c.env}:{row['key']}")["fetchedAt"] == collected
    projection = c._projection_signature(c._issue_fields(light=True), True)
    for row in rows:
        assert c.cache.metadata(c._jql_row_key(c._jql_generation(), projection, row["key"]))["fetchedAt"] == collected


def test_compatibility_list_cache_retains_snapshot_evidence_age(monkeypatch):
    c = client()
    now = [time.time()]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now[0])
    query = "key = DL-9012"
    expected = c._search(query, cache_key="search:age")
    collected = now[0]
    now[0] += 100
    assert c._search(query, cache_key="search:age") == expected
    assert c.cache.metadata("search:age:light")["fetchedAt"] == collected


def test_prefetch_restores_evicted_issue_rows_with_original_snapshot_age(monkeypatch):
    c = client()
    now = [time.time()]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now[0])
    keys = ["DL-9012", "DL-9013"]
    c.search_issues_page("key in (DL-9012,DL-9013)", light=True)
    collected = now[0]
    c.cache.invalidate_keys([f"issueL:{c.env}:{key}" for key in keys])
    now[0] += 100
    def unexpected_fetch(*args, **kwargs):
        pytest.fail("The frozen search result already contains the full light projection")
    monkeypatch.setattr(c.provider, "get_json", unexpected_fetch)
    c.prefetch_issues(keys, light=True)
    for key in keys:
        assert c.cache.metadata(f"issueL:{c.env}:{key}")["fetchedAt"] == collected


def test_copied_cached_projection_does_not_claim_upstream_validation(monkeypatch):
    c = client()
    key = "DL-9012"
    row = c.get_issue_light(key)
    fields = c._issue_fields(light=True)
    generation, projection = c._jql_generation(), c._projection_signature(fields, True)
    def unexpected_fetch(*args, **kwargs):
        pytest.fail("This row is already cached")
    monkeypatch.setattr(c.provider, "get_json", unexpected_fetch)
    with read_scope(cache_mode="cool"):
        assert c._hydrate_jql_leaf_rows([key], fields, True, generation, projection) == [row]
        source = c.cache.metadata(c._jql_row_key(generation, projection, key))
        assert source["compliant"] is True
        assert source["upstreamValidated"] is False


def test_cached_snapshot_and_compatibility_copy_do_not_claim_new_upstream_validation(monkeypatch):
    c = client()
    query = "key = DL-9012"
    c.search_issues(query)
    def unexpected_fetch(*args, **kwargs):
        pytest.fail("The cached membership and row are sufficient")
    monkeypatch.setattr(c.provider, "get_json", unexpected_fetch)
    with read_scope(cache_mode="cool") as sources:
        assert c._search(query, cache_key="search:upstream-proof")
        assert c.cache.metadata("search:upstream-proof:light")["upstreamValidated"] is False
    assert sources and not any(source.get("upstreamValidated") for source in sources)


def test_legacy_list_cached_write_through_retains_validation_provenance():
    c = client()
    row = c.get_issue_light("DL-9012")
    c.cache.set("mt:old-proof", [row], 600)
    with read_scope(cache_mode="cool"):
        assert c._legacy_search("key = DL-9012", cache_key="mt:old-proof") == [row]
        assert c.cache.metadata(f"issueL:{c.env}:DL-9012")["upstreamValidated"] is False
