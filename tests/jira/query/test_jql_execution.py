"""Physical request batching preserves exact logical membership and cache isolation."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient, MutationEvent
from app.jira.jql_execution import group_query, plan_groups


def client():
    return JiraClient(get_settings(), Cache(":memory:"))


def spy(c, monkeypatch):
    requests, fetch = [], c._fetch_exhaustive_jql
    def call(query, fields, light, result_limit):
        requests.append(query)
        return fetch(query, fields, light, result_limit)
    monkeypatch.setattr(c, "_fetch_exhaustive_jql", call)
    return requests


def test_opaque_or_is_cached_once_and_does_not_speculatively_execute_leaves(monkeypatch):
    c = client()
    calls = spy(c, monkeypatch)
    query = "key = DL-9012 OR key = DL-9013"
    rows = c.search_issues(query)
    assert {row["key"] for row in rows} == {"DL-9012", "DL-9013"}
    assert len(calls) == 1 and " OR " in calls[0]
    assert c.search_issues("key = DL-9013 OR key = DL-9012") == rows
    assert len(calls) == 1 and not getattr(c, "_bg_inflight", None)
    for leaf in c._compile_jql(query).leaves:
        assert c.cache.get(c._jql_leaf_key(leaf)[2]) is None


def test_opaque_or_reuses_already_warm_memberships(monkeypatch):
    c = client()
    calls = spy(c, monkeypatch)
    for key in ("DL-9012", "DL-9013"):
        c.search_issues(f"key = {key}")
    assert len(calls) == 2
    assert len(c.search_issues("key = DL-9012 OR key = DL-9013")) == 2
    assert len(calls) == 2


def test_partial_warm_stream_yields_cached_rows_before_fetching_missing_membership(monkeypatch):
    c = client()
    first = 'assignee = "test.ui01" AND statusCategory = "To Do"'
    second = 'assignee = "test.ui02" AND statusCategory = "To Do"'
    expected = c.search_issues(first, max_results=None)
    calls = spy(c, monkeypatch)
    stream = c.iter_search_issue_chunks(f"({first}) OR ({second})")
    warm = next(stream)
    assert {row["key"] for row in warm["issues"]} == {row["key"] for row in expected}
    assert calls == []
    rest = list(stream)
    assert len(rest) == 1 and len(calls) == 1
    assert "test.ui02" in calls[0] and "test.ui01" not in calls[0]


def test_missing_selector_keeps_group_results_without_poisoning_logical_leafs(monkeypatch):
    c = client()
    seed = c.get_issue_light("DL-9012")
    seed = deepcopy(seed)
    seed["fields"].pop("reporter", None)
    monkeypatch.setattr(c, "_fetch_exhaustive_jql", lambda *_args: [seed])
    query = "assignee = test.ui01 OR reporter = test.ui02"
    chunks = list(c.iter_search_issue_chunks(query))
    assert len(chunks) == 1 and chunks[0]["issues"] == [seed]
    assert len(chunks[0]["completedLeaves"]) == 2
    for leaf in c._compile_jql(query).leaves:
        assert c.cache.get(c._jql_leaf_key(leaf)[2]) is None


def test_group_failure_is_one_request_and_no_successful_empty_cache(monkeypatch):
    c = client()
    calls = []
    def fail(*args):
        calls.append(args)
        raise RuntimeError("injected transport failure")
    monkeypatch.setattr(c, "_fetch_exhaustive_jql", fail)
    query = "assignee = a OR assignee = b OR reporter = a"
    chunks = list(c.iter_search_issue_chunks(query))
    assert len(calls) == len(chunks) == 1
    assert chunks[0]["error"]["kind"] == "other"
    assert len(chunks[0]["completedLeaves"]) == 3
    for leaf in c._compile_jql(query).leaves:
        assert c.cache.get(c._jql_leaf_key(leaf)[2]) is None


def test_group_singleflight_and_all_memberships_survive_stream_close(monkeypatch):
    c = client()
    entered, release = threading.Event(), threading.Event()
    calls = []
    def fetch(*args):
        calls.append(args)
        entered.set()
        assert release.wait(3)
        return []
    monkeypatch.setattr(c, "_fetch_exhaustive_jql", fetch)
    query = "assignee = a OR assignee = b"
    def consume():
        stream = c.iter_search_issue_chunks(query)
        first = next(stream)
        stream.close()
        return first
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(consume)
        assert entered.wait(3)
        second = pool.submit(consume)
        release.set()
        assert len(first.result()["completedLeaves"]) == 2
        second.result()
    assert len(calls) == 1
    for leaf in c._compile_jql(query).leaves:
        assert c.cache.get(c._jql_leaf_key(leaf)[2]) == []


def test_group_result_cannot_publish_after_mutation(monkeypatch):
    c = client()
    query = "assignee = a OR reporter = b"
    def fetch(*_args):
        c._record_mutation(MutationEvent("fields", "DL-1", changed_fields=("assignee",)))
        return []
    monkeypatch.setattr(c, "_fetch_exhaustive_jql", fetch)
    chunks = list(c.iter_search_issue_chunks(query))
    assert len(chunks) == 1 and chunks[0].get("error")
    for leaf in c._compile_jql(query).leaves:
        assert c.cache.get(c._jql_leaf_key(leaf)[2]) is None


@pytest.mark.parametrize("fault", ["failure", "duplicate", "total-change"])
def test_incomplete_physical_batch_never_primes_membership_or_issue_cache(monkeypatch, fault):
    c = client()
    query = "assignee = a OR assignee = b"
    calls = []
    def page(_query, start_at=0, **_kwargs):
        calls.append(start_at)
        if start_at and fault == "failure":
            raise RuntimeError("injected later-page failure")
        key = "DL-1" if not start_at or fault == "duplicate" else "DL-2"
        row = {"key": key, "fields": {"assignee": {"name": "a" if not start_at else "b"}}}
        return {"issues": [row], "total": 3 if start_at and fault == "total-change" else 2,
                "hasMore": not start_at, "nextStartAt": 1 if not start_at else None}
    monkeypatch.setattr(c, "_legacy_search_issues_page", page)
    with pytest.raises((ValueError, RuntimeError)):
        c.search_issues(query)
    assert calls == [0, 1]
    for leaf in c._compile_jql(query).leaves:
        assert c.cache.get(c._jql_leaf_key(leaf)[2]) is None
    assert c.cache.get("issueL:mock:DL-1") is None


def test_cached_group_derivation_keeps_original_collection_age(monkeypatch):
    c = client()
    query = "assignee = test.ui01 OR reporter = test.ui01"
    c.search_issues(query)
    compiled = c._compile_jql(query)
    group = plan_groups(compiled, c.s.epic_link_field_id)[0]
    group_key = c._jql_leaf_key(group_query(group))[2]
    source = c.cache.metadata(group_key)["fetchedAt"] - 100
    c.cache.set_if_fence(group_key, c.cache.get(group_key), c.s.cache_ttl_seconds,
                         c.cache.fence(), fetched_at=source)
    c.cache.invalidate_keys([c._jql_leaf_key(leaf)[2] for leaf in compiled.leaves])
    c.cache.bump_epoch(c._jql_epoch_namespace())
    calls = spy(c, monkeypatch)
    c.search_issues(query)
    assert calls == []
    for leaf in compiled.leaves:
        assert c.cache.metadata(c._jql_leaf_key(leaf)[2])["fetchedAt"] == source


def test_partition_projection_never_promotes_narrow_rows_to_full_issue_cache():
    c = client()
    page = c.search_issues_page("assignee = test.ui01 OR reporter = test.ui01", fields="summary")
    assert page["issues"]
    for row in page["issues"]:
        assert {"assignee", "reporter"} <= row["fields"].keys()
        assert c.cache.get(f"issueL:{c.env}:{row['key']}") is None


def test_batch_budget_does_not_reduce_old_leaf_union_capacity(monkeypatch):
    c = client()
    query = " OR ".join(f'assignee = user{i} AND statusCategory = "{category}"'
                        for i in range(5) for category in ("To Do", "In Progress", "Done"))
    limits = []
    monkeypatch.setattr(c, "_fetch_exhaustive_jql", lambda _q, _f, _l, limit: limits.append(limit) or [])
    c.search_issues(query)
    assert limits == [15 * c.JQL_LEAF_RESULT_LIMIT]
