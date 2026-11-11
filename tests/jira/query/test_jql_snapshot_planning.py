"""Frozen complete search results do not need their entity coverage proved again."""
from contextlib import nullcontext
import time

import pytest

from app.infra.cache import Cache
from app.infra.cache_policy import read_scope, reduce_read_metadata
from app.infra.settings import get_settings
from app.jira import jira_client
from app.jira.cache_policy import MutationEvent
from app.jira.jira_client import JiraClient


QUERY = "key in (DL-9012, DL-9013) ORDER BY key ASC"


@pytest.fixture
def observed_client(tmp_path, monkeypatch):
    cache = Cache(str(tmp_path / "search.sqlite3"))
    client = JiraClient(get_settings(), cache)
    calls = []
    original = client.provider.get_json

    def request(path, **kwargs):
        if path == "/rest/api/2/search":
            calls.append(kwargs["params"].copy())
        return original(path, **kwargs)

    monkeypatch.setattr(client.provider, "get_json", request)
    try:
        yield client, calls
    finally:
        cache.close()


def observe_planning(client, monkeypatch):
    counts = {"plans": 0, "entityProofs": 0}
    plan, resolve = jira_client.execution_plan, client._resolve_cached_jql_rows

    def planning(*args, **kwargs):
        counts["plans"] += 1
        return plan(*args, **kwargs)

    def proof(*args, **kwargs):
        counts["entityProofs"] += 1
        return resolve(*args, **kwargs)

    monkeypatch.setattr(jira_client, "execution_plan", planning)
    monkeypatch.setattr(client, "_resolve_cached_jql_rows", proof)
    return counts


@pytest.mark.parametrize("strategy", ["auto", "off", "people", "full"])
def test_complete_snapshot_hit_and_small_cursor_need_no_new_entity_proof(observed_client, monkeypatch, strategy):
    client, calls = observed_client
    first = client.search_issues_page(QUERY, fields="summary", max_results=1, strategy=strategy)
    counts = observe_planning(client, monkeypatch)
    calls.clear()

    repeated = client.search_issues_page(QUERY, fields="summary", max_results=1, strategy=strategy)
    second = client.search_issues_page(QUERY, fields="summary", max_results=1, start_at=1,
                                     snapshot_id=first["snapshotId"], strategy=strategy)

    assert repeated["issues"] == first["issues"]
    assert repeated["snapshotId"] == second["snapshotId"] == first["snapshotId"]
    assert [first["issues"][0]["key"], second["issues"][0]["key"]] == ["DL-9012", "DL-9013"]
    assert second["snapshotSourceFetchedAt"] == first["snapshotSourceFetchedAt"]
    assert second["snapshotCreatedAt"] == first["snapshotCreatedAt"]
    assert repeated["execution"]["requestedStrategy"] == strategy
    assert repeated["execution"]["physicalQueryCount"] == second["execution"]["physicalQueryCount"] == 0
    assert counts == {"plans": 0, "entityProofs": 0}
    assert calls == []


@pytest.mark.parametrize("changed", [{"fields": "summary,status"}, {"strategy": "off"}, {"max_parts": 1}])
def test_cursor_rejects_changed_identity_without_planning(observed_client, monkeypatch, changed):
    client, calls = observed_client
    options = {"fields": "summary", "strategy": "full", "max_parts": 64}
    first = client.search_issues_page(QUERY, max_results=1, **options)
    counts = observe_planning(client, monkeypatch)
    calls.clear()
    with pytest.raises(ValueError, match="snapshot"):
        client.search_issues_page(QUERY, snapshot_id=first["snapshotId"], start_at=1,
                                 **{**options, **changed})
    assert counts == {"plans": 0, "entityProofs": 0} and calls == []


@pytest.mark.parametrize("options", [{"strategy": "invalid"}, {"max_parts": True}, {"max_parts": 0}])
def test_existing_snapshot_cannot_bypass_option_validation(observed_client, options):
    client, calls = observed_client
    first = client.search_issues_page(QUERY, max_results=1)
    calls.clear()
    with pytest.raises(ValueError):
        client.search_issues_page(QUERY, snapshot_id=first["snapshotId"], **options)
    assert calls == []


@pytest.mark.parametrize("mode", [None, "live"])
def test_expired_or_new_live_search_still_plans(observed_client, monkeypatch, mode):
    client, calls = observed_client
    now = [time.time()]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now[0])
    scope = lambda: read_scope(cache_mode=mode) if mode else nullcontext()
    with scope():
        first = client.search_issues_page(QUERY, max_results=1)
    counts = observe_planning(client, monkeypatch)
    calls.clear()
    if mode is None:
        now[0] += client.JQL_SNAPSHOT_TTL + client.s.cache_ttl_seconds + 1
        with pytest.raises(ValueError, match="snapshot"):
            client.search_issues_page(QUERY, snapshot_id=first["snapshotId"])
    else:
        with scope():
            second = client.search_issues_page(QUERY, max_results=1, start_at=1,
                                              snapshot_id=first["snapshotId"])
        assert second["snapshotFrozen"] and calls == []
    assert counts == {"plans": 0, "entityProofs": 0}
    with scope():
        refreshed = client.search_issues_page(QUERY, max_results=1)
    assert counts["plans"] == 1 and calls
    assert refreshed["snapshotId"] != first["snapshotId"]
    assert refreshed["issues"] == first["issues"]


@pytest.mark.parametrize("strategy,selectors", [
    ("auto", {"assignee", "reporter"}), ("people", {"assignee", "reporter"}),
    ("full", set()), ("off", set()),
])
def test_budget_fallback_preserves_initial_partition_projection(observed_client, monkeypatch, strategy, selectors):
    client, calls = observed_client
    query = ('(assignee = "test.ui01" OR reporter = "test.ui02" OR statusCategory = Done) '
             'AND key in (DL-9012, DL-9013, DL-9014)')
    first = client.search_issues_page(query, fields="summary", max_results=1,
                                     strategy=strategy, max_parts=1)
    assert len(calls) == 1
    assert set(calls[0]["fields"].split(",")) == {"summary"} | selectors
    counts = observe_planning(client, monkeypatch)
    calls.clear()
    repeated = client.search_issues_page(query, fields="summary", max_results=1,
                                        strategy=strategy, max_parts=1)
    second = client.search_issues_page(query, fields="summary", max_results=1, start_at=1,
                                     strategy=strategy, max_parts=1, snapshot_id=first["snapshotId"])
    assert repeated["snapshotId"] == second["snapshotId"] == first["snapshotId"]
    assert repeated["issues"] == first["issues"]
    assert counts == {"plans": 0, "entityProofs": 0} and calls == []


def test_projection_change_and_mutation_require_a_new_snapshot_plan(observed_client, monkeypatch):
    client, calls = observed_client
    first = client.search_issues_page(QUERY, fields="summary")
    counts = observe_planning(client, monkeypatch)
    upgraded = client.search_issues_page(QUERY, fields="summary,status")
    assert counts["plans"] == 1
    assert upgraded["snapshotId"] != first["snapshotId"]
    assert all("status" in row["fields"] for row in upgraded["issues"])
    client._record_mutation(MutationEvent("fields", "DL-9012", changed_fields=("summary",)))
    calls.clear()
    changed = client.search_issues_page(QUERY, fields="summary,status")
    assert counts["plans"] == 2 and calls
    assert changed["snapshotId"] != upgraded["snapshotId"]


def test_abandoned_coverage_does_not_date_a_fresh_whole_query(observed_client, monkeypatch):
    client, calls = observed_client
    now = [time.time()]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: now[0])
    client.search_issues_page("key = DL-9012", fields="summary,status", strategy="full")
    now[0] += 10
    calls.clear()
    with read_scope(cache_mode="hot") as sources:
        page = client.search_issues_page("key = DL-9012 OR key = DL-9013 OR key = DL-9014",
                                        fields="summary,status", strategy="full", max_parts=1)
    assert page["execution"]["fallbackReason"] == "physical_query_budget"
    assert len(calls) == 1 and page["total"] == 3
    assert page["snapshotSourceFetchedAt"] == now[0]
    assert reduce_read_metadata(sources)["fetchedAt"] == now[0]
