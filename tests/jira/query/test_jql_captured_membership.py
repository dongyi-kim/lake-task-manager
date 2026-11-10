"""Captured membership evidence cannot borrow a later query's collection age."""
from types import SimpleNamespace

from app.infra.cache import Cache
from app.infra.cache_policy import read_scope
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient
from app.jira.jql_execution import group_query, plan_groups


def test_partition_keeps_captured_age_when_union_refreshes_before_partition(monkeypatch):
    clock, assignee, calls = [100000.0], ["a"], []
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])

    def get_json(path, params):
        query = params["jql"]
        calls.append(query)
        rows = [] if assignee[0] == "c" and " OR " not in query and "key in" not in query else [{
            "key": "DL-1", "fields": {"summary": "row", "assignee": {"name": assignee[0]},
                "reporter": {"name": "z"}, "status": {"statusCategory": {"key": "indeterminate"}}}}]
        return {"issues": rows, "total": len(rows)}

    client = JiraClient(get_settings(), Cache(":memory:"))
    client._provider = SimpleNamespace(get_json=get_json, supports_parallel=False)
    client._provider_built = True
    try:
        compiled = client._compile_jql("assignee = a OR reporter = z")
        group = plan_groups(compiled, client.s.epic_link_field_id)[0]
        query = group_query(group)
        fields = "summary,assignee,reporter,status"
        generation = client._jql_generation()
        projection = client._projection_signature(fields, True)
        dependencies = tuple(sorted({field for member in group for field in member.fields}))
        original = client._cached_jql_leaf
        original(query, dependencies, fields, True, generation, projection)
        clock[0] += 650
        refreshed = False

        def replace_after_capture(leaf, *args, **kwargs):
            nonlocal refreshed
            captured = original(leaf, *args, **kwargs)
            if leaf == query and not refreshed:
                refreshed = True
                assignee[0] = "c"
                with read_scope(cache_mode="live"):
                    original(leaf, *args, **kwargs)
            return captured

        monkeypatch.setattr(client, "_cached_jql_leaf", replace_after_capture)
        with read_scope(cache_mode="warm"):
            client._execute_jql_group(group, fields, True, generation, projection)
        monkeypatch.setattr(client, "_cached_jql_leaf", original)
        with read_scope(cache_mode="hot"):
            result = client.search_issues_page("assignee = a", fields=fields)
        assert result["issues"] == []
        assert calls[-1] == "assignee = a ORDER BY key ASC"
    finally:
        client.close()
        client.cache.close()


def test_exact_opaque_projection_replay_uses_no_repair_query():
    calls = []

    def get_json(path, params):
        calls.append(params["jql"])
        return {"issues": [{"key": "DL-1", "fields": {"summary": "usable partial"}}], "total": 1}

    client = JiraClient(get_settings(), Cache(":memory:"))
    client._provider = SimpleNamespace(get_json=get_json, supports_parallel=False)
    client._provider_built = True
    try:
        first = client.search_issues_page("key = DL-1", fields="summary,status")
        client.cache.forget_prefixes(["jqlcurrent:", "jqlsnap:"])
        calls.clear()
        repeated = client.search_issues_page("key = DL-1", fields="summary,status")
        assert repeated["issues"] == first["issues"]
        assert calls == []
    finally:
        client.close()
        client.cache.close()


def test_same_timestamp_membership_replacement_cannot_reuse_previous_opaque_rows(monkeypatch):
    monkeypatch.setattr("app.infra.cache.time.time", lambda: 100000.0)
    current, calls = ["DL-1"], []

    def get_json(path, params):
        calls.append(params["jql"])
        return {"issues": [{"key": current[0], "fields": {"summary": current[0]}}], "total": 1}

    client = JiraClient(get_settings(), Cache(":memory:"))
    client._provider = SimpleNamespace(get_json=get_json, supports_parallel=False)
    client._provider_built = True
    try:
        query = "project = DL"
        first = client.search_issues_page(query, fields="summary,status")
        assert first["issues"][0]["key"] == "DL-1"
        membership = client._jql_leaf_key(client._compile_jql(query).leaves[0])[2]
        client.cache.set_if_fence(membership, ["DL-2"], 600, client.cache.fence(), fetched_at=100000)
        client.cache.forget_prefixes(["jqlcurrent:", "jqlsnap:"])
        current[0] = "DL-2"
        with read_scope(cache_mode="hot"):
            result = client.search_issues_page(query, fields="summary,status")
        assert [row["key"] for row in result["issues"]] == ["DL-2"]
    finally:
        client.close()
        client.cache.close()
