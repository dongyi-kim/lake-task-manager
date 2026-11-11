"""The parent picker filters its ordered 200-row discovery prefix, not a whole project."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import time

import pytest

from app.auth.base import PermissionDenied, QueryStateChanged, SessionExpired, UpstreamUnavailable
from app.infra.cache import Cache
from app.infra.cache_policy import read_scope, reduce_read_metadata
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient
from app.jira.search_preview import search_prefix


QUERY = "project = DL ORDER BY updated DESC"
FIELDS = "summary,status,updated"


@pytest.fixture
def picker(monkeypatch):
    client = JiraClient(get_settings(), Cache(":memory:"))
    rows, calls = [], []
    # Equal timestamps exercise the canonical natural-key tie break.
    for index in range(1, 10001):
        updated = datetime(2026, 11, 10, tzinfo=timezone.utc) - timedelta(days=(index - 1) // 50)
        rows.append({"key": f"DL-{index}", "fields": {
            "summary": f"Candidate {index}", "updated": updated.isoformat(),
            "issuetype": {"name": "Task", "subtask": False}, "status": {"name": "Open", "statusCategory": {"key": "new"}},
            "assignee": {"name": "other"}, "reporter": {"name": "other"},
            "components": [{"name": "ETL"}], client.s.epic_link_field_id: None,
        }})
    control = {"cap": 100, "fault": None}

    def fetch(path, params=None):
        assert path == "/rest/api/2/search"
        calls.append(dict(params))
        start = params.get("startAt", 0)
        size = min(params["maxResults"], control["cap"])
        data = {"startAt": start, "maxResults": size, "total": len(rows),
                "issues": deepcopy(rows[start:start + size])}
        if control["fault"]:
            control["fault"](start, data)
        return data

    monkeypatch.setattr(client.provider, "get_json", fetch)
    monkeypatch.setattr(client, "my_task_context", lambda: {"me": "me", "modules": ["ETL"], "taskKeys": ["DL-200"]})
    monkeypatch.setattr(client, "epic_metadata_title_map", lambda keys, **_: {key: "Public epic" for key in keys})
    try:
        yield client, rows, calls, control
    finally:
        client.close()
        client.cache.close()


def test_picker_reads_only_first_200_ordered_rows_before_filters_and_ranking(picker):
    client, rows, calls, _ = picker
    rows[0]["fields"]["issuetype"] = {"name": "Epic", "subtask": False}
    rows[1]["fields"]["issuetype"] = {"name": "Sub-Task", "subtask": True}
    rows[2]["fields"][client.s.epic_link_field_id] = "DL-epic"
    result = client.parent_task_candidates("", limit=100, exclude_linked=True)
    # The mine row at the final prefix boundary ranks first. Excluded rows still
    # consume their original prefix positions; later project rows cannot refill them.
    expected = ["DL-200"] + [f"DL-{index}" for index in range(4, 103)]
    assert [item["key"] for item in result["items"]] == expected
    assert len(calls) == 2
    assert [call["startAt"] for call in calls] == [0, 100]
    assert all(call["jql"] == "project = DL ORDER BY updated DESC, key ASC" for call in calls)
    calls.clear()
    assert client.parent_task_candidates("", limit=100, exclude_linked=True) == result
    assert calls == []
    # Different q uses the same proven prefix, without broadening its search scope.
    assert client.parent_task_candidates("DL-200")["items"][0]["key"] == "DL-200"
    assert client.parent_task_candidates("DL-201")["items"] == []
    assert calls == []


def test_small_server_page_caps_advance_to_200_without_refilling_after_it(picker):
    client, _, calls, control = picker
    control["cap"] = 37
    page = search_prefix(client, QUERY, fields=FIELDS)
    assert [row["key"] for row in page["issues"]] == [f"DL-{index}" for index in range(1, 201)]
    assert [call["startAt"] for call in calls] == [0, 37, 74, 111, 148, 185]
    assert calls[-1]["maxResults"] == 15
    assert page["total"] == 10000 and page["hasMore"] and page["nextStartAt"] == 200


def test_prefix_cannot_supply_complete_membership_but_complete_query_can_supply_prefix(picker):
    client, rows, calls, _ = picker
    del rows[231:]
    prefix = search_prefix(client, QUERY)
    compiled = client._compile_jql(QUERY)
    assert client.cache.get(client._jql_leaf_key(compiled.whole_query)[2]) is None
    calls.clear()
    whole = client.search_issues_page(QUERY, max_results=100)
    assert whole["total"] == 231 and len(calls) == 3
    assert prefix["issues"] == whole["issues"] + rows[100:200]
    # A fresh complete membership can also serve a different bounded-prefix size.
    calls.clear()
    reused = search_prefix(client, QUERY, limit=150)
    assert [row["key"] for row in reused["issues"]] == [f"DL-{index}" for index in range(1, 151)]
    assert reused["total"] == 231 and reused["hasMore"] and calls == []


@pytest.mark.parametrize("failure", [UpstreamUnavailable, SessionExpired, PermissionDenied])
def test_later_page_failure_never_publishes_successful_prefix_or_picker(picker, failure):
    client, _, calls, control = picker
    def fault(start, data):
        if start:
            raise failure("second page failed")
    control["fault"] = fault
    with pytest.raises(failure, match="second page"):
        client.parent_task_candidates()
    assert len(calls) == 2
    assert not client.cache.entries_by_prefix("search:preview:")
    assert not client.cache.entries_by_prefix("epic_cand:")
    assert not client.cache.entries_by_prefix("jqlleaf:")


@pytest.mark.parametrize("failure", ["unknown-total", "changed-total", "duplicate", "wrong-offset",
                                     "empty-early", "missing-order-field", "wrong-order"])
def test_uncertain_prefix_rejected_instead_of_cached_partial_or_empty_success(picker, failure):
    client, _, _, control = picker
    def fault(start, data):
        if failure == "unknown-total": data["total"] = None
        elif failure == "changed-total" and start: data["total"] += 1
        elif failure == "duplicate" and start: data["issues"][0]["key"] = "DL-1"
        elif failure == "wrong-offset" and start: data["startAt"] = 0
        elif failure == "empty-early" and start: data["issues"] = []
        elif failure == "missing-order-field": data["issues"][0]["fields"].pop("updated")
        elif failure == "wrong-order": data["issues"][:2] = reversed(data["issues"][:2])
    control["fault"] = fault
    with pytest.raises(ValueError):
        client.parent_task_candidates()
    assert not client.cache.entries_by_prefix("search:preview:")
    assert not client.cache.entries_by_prefix("epic_cand:")


def test_fence_change_between_pages_cannot_publish_or_return_a_prefix(picker):
    client, _, calls, control = picker
    def invalidate(start, data):
        if start:
            client.cache.invalidate()
    control["fault"] = invalidate
    with pytest.raises(QueryStateChanged):
        search_prefix(client, QUERY)
    assert len(calls) == 2
    assert not client.cache.entries_by_prefix("search:preview:")


def test_known_empty_project_is_a_proven_empty_prefix(picker):
    client, rows, calls, _ = picker
    rows.clear()
    assert client.parent_task_candidates()["items"] == []
    assert len(calls) == 1


def test_prefix_cache_preserves_source_age_and_revalidates_live(picker, monkeypatch):
    client, _, calls, _ = picker
    clock = [time.time()]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])
    with read_scope(cache_mode="hot") as sources:
        first = search_prefix(client, QUERY, fields=FIELDS)
        collected = reduce_read_metadata(sources)["fetchedAt"]
    assert len(calls) == 2
    clock[0] += 2 * 86400
    with read_scope(cache_mode="cool") as sources:
        assert search_prefix(client, QUERY, fields=FIELDS) == first
        assert reduce_read_metadata(sources)["fetchedAt"] == collected
    assert len(calls) == 2
    for mode in ("hot", "live", "live"):
        with read_scope(cache_mode=mode) as sources:
            assert search_prefix(client, QUERY, fields=FIELDS) == first
            assert reduce_read_metadata(sources)["compliant"] is True
    assert len(calls) == 8


def test_prefix_identity_separates_projection_scope_and_actor(picker, monkeypatch):
    client, _, calls, _ = picker
    search_prefix(client, QUERY, fields=FIELDS)
    search_prefix(client, QUERY, fields=FIELDS + ",issuetype")
    search_prefix(client, QUERY.replace("DL", "OPS"), fields=FIELDS)
    monkeypatch.setattr(client, "_jql_user_context", lambda: "different-actor")
    search_prefix(client, QUERY, fields=FIELDS)
    assert len(calls) == 8
    assert calls[-3]["jql"].startswith("project = OPS ")


def test_incomplete_cached_projection_does_not_replace_the_picker_prefix(picker):
    client, rows, calls, _ = picker
    del rows[231:]
    client.search_issues_page(QUERY, fields=FIELDS)
    calls.clear()
    prefix = search_prefix(client, QUERY)
    assert len(prefix["issues"]) == 200 and len(calls) == 2
    assert all("issuetype" in row["fields"] for row in prefix["issues"])


def test_picker_retries_one_fenced_prefix_without_reusing_its_partial_rows(picker):
    client, _, calls, control = picker
    changed = []
    def mutate_once(start, data):
        if start and not changed:
            changed.append(True)
            client.cache.invalidate()
    control["fault"] = mutate_once
    result = client.parent_task_candidates(limit=1)
    assert [row["key"] for row in result["items"]] == ["DL-200"]
    assert [call["startAt"] for call in calls] == [0, 100, 0, 100]
