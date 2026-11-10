"""Physical budgets and shared admission cover legacy transport and query fan-out."""

from concurrent.futures import CancelledError, ThreadPoolExecutor
from contextvars import ContextVar
from types import SimpleNamespace
import threading

import pytest

from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira import jql_budget
from app.jira.jira_client import JiraClient


def client_with_transport(get_json, parallel=True):
    client = JiraClient(get_settings(), Cache(":memory:"))
    client._provider = SimpleNamespace(get_json=get_json, supports_parallel=parallel)
    client._provider_built = True
    return client


def test_budget_can_fit_counts_only_new_query_identities_without_reserving_them():
    with jql_budget.physical_query_scope(2) as budget:
        budget.claim("project = DL")
        assert budget.remaining == 1
        assert budget.can_fit(["project = DL", "key = DL-1", "key=DL-1"])
        assert not budget.can_fit(["key = DL-1", "key = DL-2"])
        assert budget.remaining == 1


def test_decorator_shares_nested_calls_and_restores_the_callers_context():
    @jql_budget.budgeted_query
    def nested(query, max_parts=64):
        jql_budget.claim_physical_query(query)
        return jql_budget.current_budget().diagnostics()
    @jql_budget.budgeted_query
    def request(max_parts=64):
        nested("project = DL")
        return nested("project=DL")
    assert request(max_parts=1) == {"maxParts": 1, "physicalQueryCount": 1}
    assert jql_budget.current_budget() is None


def test_decorated_iterator_keeps_policy_and_budget_without_leaking_between_yields():
    mode = ContextVar("budget_iterator_mode", default="default")
    @jql_budget.budgeted_query
    def stream(max_parts=64):
        jql_budget.claim_physical_query("project = DL")
        yield mode.get(), jql_budget.current_budget()
        jql_budget.claim_physical_query("key = DL-1")
        yield "unreachable"
    token = mode.set("warm")
    try:
        iterator = stream(max_parts=1)
        mode.set("cold")
        value, budget = next(iterator)
        assert value == "warm" and budget.remaining == 0
        assert mode.get() == "cold" and jql_budget.current_budget() is None
        with jql_budget.physical_query_scope(1) as independent:
            assert independent is not budget
            independent.claim("key = DL-2")
        with pytest.raises(jql_budget.PhysicalQueryBudgetExceeded):
            next(iterator)
        assert jql_budget.current_budget() is None
    finally:
        mode.reset(token)


def test_actual_page_transport_counts_distinct_queries_and_never_dispatches_over_budget():
    requests = []
    def get_json(path, params):
        requests.append((path, params["jql"], params["startAt"]))
        return {"issues": [], "total": 0}
    client = client_with_transport(get_json)
    with jql_budget.physical_query_scope(1) as budget:
        client._legacy_search_issues_page("project = DL", fields="summary", write_through=False)
        client._legacy_search_issues_page("project = DL", start_at=100, fields="summary", write_through=False)
        with pytest.raises(jql_budget.PhysicalQueryBudgetExceeded):
            client._legacy_search_issues_page("key = DL-1", fields="summary", write_through=False)
        assert budget.diagnostics()["physicalQueryCount"] == 1
    assert requests == [("/rest/api/2/search", "project = DL", 0),
                        ("/rest/api/2/search", "project = DL", 100)]


def test_legacy_bad_field_retry_uses_same_budget_and_shared_transport(monkeypatch):
    calls = []
    def get_json(path, params):
        calls.append((path, dict(params)))
        return {"errorMessages": ["bad field"]} if len(calls) == 1 else {"issues": [], "total": 0}
    client = client_with_transport(get_json)
    monkeypatch.setattr(client, "_note_bad_field", lambda _: True)
    with jql_budget.physical_query_scope(1) as budget:
        assert client._legacy_search("assignee in membersOf('group')") == []
        assert budget.diagnostics()["physicalQueryCount"] == 1
    assert len(calls) == 2


@pytest.mark.parametrize("parallel,capacity", [(True, 8), (False, 1)])
def test_actual_transport_admission_is_shared_across_concurrent_requests(parallel, capacity):
    release, lock = threading.Event(), threading.Lock()
    active, peak = 0, 0
    def get_json(_path, params):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        assert release.wait(3)
        with lock:
            active -= 1
        return {"issues": [], "total": 0}
    client = client_with_transport(get_json, parallel)
    with ThreadPoolExecutor(capacity + 2) as pool:
        futures = [pool.submit(client._legacy_search_issues_page, f"key = DL-{i}", fields="summary")
                   for i in range(capacity + 2)]
        try:
            scheduler = client._query_scheduler
            with scheduler._condition:
                assert scheduler._condition.wait_for(lambda: len(scheduler._waiting) == 2, timeout=3)
        finally:
            release.set()
        for future in futures:
            future.result()
    assert peak == capacity


def test_pmap_preserves_input_order_and_nested_missing_counts():
    client = client_with_transport(lambda *_args, **_kwargs: {}, True)
    client.miss_begin()
    def nested(value):
        client.miss_add(value)
        return value * 10
    assert client._pmap([3, 1, 2], lambda value: client._pmap([value], nested)[0]) == [30, 10, 20]
    assert client.miss_count() == 6


def test_cancellation_between_actual_pages_prevents_the_following_transport():
    calls, cancelled = [], threading.Event()
    def get_json(_path, params):
        calls.append(params["startAt"])
        cancelled.set()
        return {"issues": [], "total": 0}
    client = client_with_transport(get_json)
    def query(_):
        client._legacy_search_issues_page("project = DL", fields="summary")
        client._legacy_search_issues_page("project = DL", start_at=100, fields="summary")
    with pytest.raises(CancelledError):
        list(client._query_scheduler.run_bounded([1], query, cancelled=cancelled))
    assert calls == [0]


def test_closing_decorated_iterator_finalizes_in_its_private_context():
    finalized = []
    @jql_budget.budgeted_query
    def stream(max_parts=64):
        try:
            yield jql_budget.current_budget()
        finally:
            finalized.append(jql_budget.current_budget())
    iterator = stream(max_parts=1)
    budget = next(iterator)
    iterator.close()
    assert finalized == [budget]
    assert jql_budget.current_budget() is None


def test_history_prefetch_preserves_expanded_batch_request_and_claims_its_budget():
    requests = []
    history = {"id": "1", "created": "2026-11-10T10:00:00.000+0900", "items": []}
    def get_json(path, params):
        requests.append((path, params))
        return {"issues": [{"key": key, "fields": {"created": "2026-11-01", "reporter": {"name": "a"}},
                            "changelog": {"histories": [history]}}
                           for key in ("DL-1", "DL-2")]}
    client = client_with_transport(get_json)
    with jql_budget.physical_query_scope(1) as budget:
        client.prefetch_changelogs(["DL-1", "DL-2", "DL-1"])
        assert budget.diagnostics()["physicalQueryCount"] == 1
        client.prefetch_changelogs(["DL-1", "DL-2"])
    assert requests == [("/rest/api/2/search", {"jql": "key in (DL-1,DL-2)",
                        "fields": "created,reporter", "expand": "changelog", "maxResults": 2})]
    assert client._changelog("DL-1") == {"created": "2026-11-01", "reporter": {"name": "a"},
                                           "histories": [history]}
