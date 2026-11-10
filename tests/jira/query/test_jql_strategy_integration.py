"""Execution choices share proven evidence while bounding actual physical queries."""
import pytest

from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


QUERY = "key = DL-9012 OR key = DL-9013"


def observed_client(monkeypatch):
    client = JiraClient(get_settings(), Cache(":memory:"))
    calls = []
    original = client.provider.get_json

    def request(path, **kwargs):
        if path == "/rest/api/2/search":
            calls.append(kwargs["params"].copy())
        return original(path, **kwargs)

    monkeypatch.setattr(client.provider, "get_json", request)
    return client, calls


@pytest.mark.parametrize("strategy,expected", [("off", 1), ("full", 2), ("people", 1), ("auto", 1)])
def test_requested_strategy_reaches_actual_transport(monkeypatch, strategy, expected):
    client, calls = observed_client(monkeypatch)
    page = client.search_issues_page(QUERY, strategy=strategy)
    assert {row["key"] for row in page["issues"]} == {"DL-9012", "DL-9013"}
    assert len(calls) == expected
    assert page["execution"]["requestedStrategy"] == strategy
    assert page["execution"]["physicalQueryCount"] == expected


def test_tight_budget_falls_back_before_dispatch(monkeypatch):
    client, calls = observed_client(monkeypatch)
    page = client.search_issues_page(QUERY, strategy="full", max_parts=1)
    assert page["total"] == 2 and len(calls) == 1
    assert page["execution"]["effectiveStrategy"] == "off"
    assert page["execution"]["fallbackReason"] == "physical_query_budget"


@pytest.mark.parametrize("first,second", [("full", "off"), ("off", "full")])
def test_complete_union_reused_across_decomposition_without_rest(monkeypatch, first, second):
    client, calls = observed_client(monkeypatch)
    page = client.search_issues_page(QUERY, strategy=first)
    calls.clear()
    repeated = client.search_issues_page(QUERY, strategy=second)
    assert repeated["issues"] == page["issues"] and calls == []
    assert repeated["execution"]["physicalQueryCount"] == 0


@pytest.mark.parametrize("changed", [{"strategy": "off"}, {"max_parts": 1}])
def test_cursor_binds_strategy_and_budget(monkeypatch, changed):
    client, calls = observed_client(monkeypatch)
    page = client.search_issues_page(QUERY, strategy="full", max_results=1)
    calls.clear()
    with pytest.raises(ValueError, match="snapshot"):
        client.search_issues_page(QUERY, max_results=1, start_at=1,
                                 snapshot_id=page["snapshotId"], **{"strategy": "full", **changed})
    assert calls == []


@pytest.mark.parametrize("options", [{"strategy": "invalid"}, {"max_parts": True}, {"max_parts": 0}, {"max_parts": 65}])
def test_bad_options_fail_before_transport(monkeypatch, options):
    client, calls = observed_client(monkeypatch)
    with pytest.raises(ValueError):
        client.search_issues_page(QUERY, **options)
    assert calls == []


def test_cached_whole_projection_upgrade_prefers_one_search_to_many_hydration_queries(monkeypatch):
    client, calls = observed_client(monkeypatch)
    query = "key = DL-9012 OR key = DL-9013 OR key = DL-9014"
    first = client.search_issues_page(query, fields="summary", strategy="off")
    assert first["total"] == 3
    monkeypatch.setattr(client, "ISSUE_BATCH", 1)
    calls.clear()
    page = client.search_issues_page(query, fields="summary,status", strategy="full", max_parts=1)
    assert page["total"] == 3 and len(calls) == 1
    assert "key in" not in calls[0]["jql"].lower()
    assert all("status" in row["fields"] for row in page["issues"])


def test_empty_cache_budget_fallback_keeps_cursor_projection_stable_after_warming(monkeypatch):
    client, calls = observed_client(monkeypatch)
    query = 'assignee = "test.ui01" OR reporter = "test.ui02"'
    page = client.search_issues_page(query, fields="summary", strategy="full", max_parts=1, max_results=1)
    calls.clear()
    next_page = client.search_issues_page(query, fields="summary", strategy="full", max_parts=1,
        max_results=1, start_at=1, snapshot_id=page["snapshotId"])
    assert next_page["snapshotId"] == page["snapshotId"] and calls == []


@pytest.mark.parametrize("max_parts", [1, 64])
def test_projection_upgrade_batches_missing_rows_across_cached_memberships(monkeypatch, max_parts):
    client, calls = observed_client(monkeypatch)
    client.search_issues_page(QUERY, fields="summary", strategy="full")
    calls.clear()
    page = client.search_issues_page(QUERY, fields="summary,status", strategy="full", max_parts=max_parts)
    assert page["total"] == 2 and len(calls) == 1
    assert "key in" in calls[0]["jql"].lower()
    assert all(key in calls[0]["jql"] for key in ("DL-9012", "DL-9013"))
    assert page["execution"]["effectiveStrategy"] == "full"


def test_off_uses_whole_query_limit_without_repeating_a_leaf_limit_fallback(monkeypatch):
    client, calls = observed_client(monkeypatch)
    monkeypatch.setattr(client, "JQL_LEAF_RESULT_LIMIT", 1)
    page = client.search_issues_page(QUERY, strategy="off", max_parts=1)
    assert page["total"] == 2 and len(calls) == 1
    assert page["execution"]["physicalQueryCount"] == 1


def test_completed_full_stream_reuses_union_in_off_page_without_rest(monkeypatch):
    client, calls = observed_client(monkeypatch)
    chunks = list(client.iter_search_issue_chunks(QUERY, strategy="full"))
    assert len(chunks[-1]["combined"]) == 2
    calls.clear()
    page = client.search_issues_page(QUERY, strategy="off")
    assert page["issues"] == chunks[-1]["combined"] and calls == []
