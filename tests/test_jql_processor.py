"""JQL canonicalization, decomposition and cache generation contracts."""

from datetime import datetime, timezone
import time

import pytest

from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient, MutationEvent
from app.jira.jql import JqlUnsupported, compile_jql, sort_issues


NOW = datetime(2026, 8, 24, 1, 2, tzinfo=timezone.utc)


def _compile(jql):
    return compile_jql(jql, user_id="test.ui01", timezone_name="Asia/Seoul",
                       now=NOW, ttl_seconds=900)


def test_and_order_and_in_values_have_one_canonical_query():
    left = _compile("C = 3 AND project in (ZZ, AA, ZZ) AND A = 1")
    right = _compile("A = 1 AND project in (AA, ZZ) AND C = 3")
    assert left == right
    assert left.leaves == ("A = 1 AND C = 3 AND project in (AA, ZZ)",)


def test_parentheses_expand_to_every_dnf_leaf():
    compiled = _compile("(A = 1 OR A = 2) AND (B = 3 OR C = 4)")
    assert compiled.leaves == (
        "A = 1 AND B = 3",
        "A = 1 AND C = 4",
        "A = 2 AND B = 3",
        "A = 2 AND C = 4",
    )


def test_not_is_pushed_to_atoms_before_dnf():
    compiled = _compile("NOT (A = 1 OR B = 2)")
    assert compiled.leaves == ("NOT (A = 1) AND NOT (B = 2)",)


def test_context_uses_user_and_ttl_bucket_in_jira_timezone():
    compiled = _compile(
        "assignee = currentUser() AND updated >= -14d "
        "AND created >= startOfWeek('-1w') ORDER BY updated DESC"
    )
    assert 'assignee = "test.ui01"' in compiled.canonical
    # NOW is 10:02 KST and the 15-minute bucket begins at 10:00 KST.
    assert 'updated >= "2026-08-10 10:00"' in compiled.canonical
    assert 'created >= "2026-08-17 00:00"' in compiled.canonical
    assert compiled.canonical.endswith("ORDER BY updated DESC, key ASC")


def test_now_remains_dynamic_but_unknown_function_falls_back():
    assert "duedate < now()" in _compile("duedate < now()").canonical
    with pytest.raises(JqlUnsupported):
        _compile("assignee in membersOf('jira-users')")


def test_dnf_expansion_is_capped():
    groups = [f"(F{i} = 1 OR F{i} = 2)" for i in range(7)]
    with pytest.raises(JqlUnsupported, match="64"):
        _compile(" AND ".join(groups))


def test_local_sort_is_stable_and_key_breaks_ties():
    rows = [
        {"key": "DL-10", "fields": {"updated": "2026-08-20"}},
        {"key": "DL-2", "fields": {"updated": "2026-08-20"}},
        {"key": "DL-1", "fields": {"updated": "2026-08-21"}},
    ]
    order = _compile("project = DL ORDER BY updated DESC").order
    assert [row["key"] for row in sort_issues(rows, order)] == ["DL-1", "DL-2", "DL-10"]


def test_cache_epoch_is_persistent_and_invalidation_fences_old_producer():
    cache = Cache(":memory:")
    assert cache.epoch("jql:mock") == 0
    assert cache.bump_epoch("jql:mock") == 1
    assert cache.epoch("jql:mock") == 1
    fence = cache.fence()
    cache.invalidate("issue:mock:DL-1")
    assert cache.set_if_fence("issue:mock:DL-1", {"stale": True}, 900, fence) is False
    assert cache.get("issue:mock:DL-1") is None
    fresh_fence = cache.fence()
    assert cache.set_many_if_fence(
        [("issue:mock:DL-1", {"key": "DL-1"}),
         ("issue:mock:DL-2", {"key": "DL-2"})], 900, fresh_fence)
    assert set(cache.get_many(["issue:mock:DL-1", "issue:mock:DL-2"])) == {
        "issue:mock:DL-1", "issue:mock:DL-2"
    }


def _count_searches(client):
    provider = client.provider
    original = provider.get_json
    calls = []

    def counted(path, params=None, **kwargs):
        if path == "/rest/api/2/search":
            calls.append(dict(params or {}))
        return original(path, params=params, **kwargs)

    provider.get_json = counted
    return calls


def test_client_executes_and_caches_every_or_leaf():
    client = JiraClient(get_settings(), Cache(":memory:"))
    calls = _count_searches(client)
    first = (
        "project = DL AND assignee = test.ui01 "
        "OR project = DL AND reporter = test.ui01 ORDER BY updated DESC"
    )
    second = (
        "reporter = test.ui01 AND project = DL "
        "OR assignee = test.ui01 AND project = DL ORDER BY updated DESC"
    )
    rows = client.search_issues(first, max_results=5)
    assert rows
    leaf_queries = {call["jql"] for call in calls}
    assert any("assignee = test.ui01" in query for query in leaf_queries)
    assert any("reporter = test.ui01" in query for query in leaf_queries)
    assert all(query.endswith("ORDER BY key ASC") for query in leaf_queries)
    count = len(calls)
    assert client.search_issues(second, max_results=5) == rows
    assert len(calls) == count
    # A later standalone leaf query reuses the leaf populated by the OR request.
    client.search_issues("project = DL AND assignee = test.ui01", max_results=5)
    assert len(calls) == count


def test_mutation_reuses_unaffected_leaves_and_evicts_only_field_dependencies():
    client = JiraClient(get_settings(), Cache(":memory:"))
    calls = _count_searches(client)
    query = (
        "project = DL AND assignee = test.ui01 "
        "OR project = DL AND reporter = test.ui01 ORDER BY updated DESC"
    )
    compiled = client._compile_jql(query)
    client.search_issues(query, max_results=20)
    leaf_keys = {
        leaf: client._jql_leaf_key(leaf)[2]
        for leaf in compiled.leaves
    }
    assignee_leaf = next(key for leaf, key in leaf_keys.items() if "assignee" in leaf)
    reporter_leaf = next(key for leaf, key in leaf_keys.items() if "reporter" in leaf)
    assert client.cache.get(assignee_leaf) is not None
    assert client.cache.get(reporter_leaf) is not None

    before_generation = client._jql_generation()
    before_calls = len(calls)
    client._apply_mutation_events((
        MutationEvent("description", "DL-9012", changed_fields=("description",)),
    ))
    assert client._jql_generation() == before_generation + 1
    assert client.cache.get(assignee_leaf) is not None
    assert client.cache.get(reporter_leaf) is not None
    client.search_issues(query, max_results=20)
    assert len(calls) == before_calls

    client._apply_mutation_events((
        MutationEvent("assignee", "DL-9012", changed_fields=("assignee",)),
    ))
    assert client.cache.get(assignee_leaf) is None
    assert client.cache.get(reporter_leaf) is not None
    client.search_issues(query, max_results=20)
    assert len(calls) == before_calls + 1


def test_rest_field_changes_cover_common_jql_aliases():
    fields = JiraClient._jql_changed_predicate_fields((
        MutationEvent("fields", "DL-1", changed_fields=(
            "components", "issuetype", "duedate", "resolutiondate", "status", "key",
        )),
    ))
    assert {"component", "type", "due", "resolved", "statuscategory", "issue"} <= fields


def test_leaf_payload_is_ids_and_issue_reverse_index_handles_delete():
    client = JiraClient(get_settings(), Cache(":memory:"))
    query = "key = DL-9012 ORDER BY updated DESC"
    compiled = client._compile_jql(query)
    rows = client.search_issues(query, max_results=5)
    assert [row["key"] for row in rows] == ["DL-9012"]

    leaf_generation, _digest, leaf_key = client._jql_leaf_key(compiled.leaves[0])
    assert client.cache.get(leaf_key) == ["DL-9012"]
    reverse = client.cache.entries_by_prefix(
        client._jql_index_prefix("issue", leaf_generation, "DL-9012"))
    assert set(reverse.values()) == {leaf_key}

    client._apply_mutation_events((MutationEvent("delete", "DL-9012"),))
    assert client.cache.get(leaf_key) is None


def test_create_invalidates_unfiltered_leaf_that_can_gain_the_new_issue():
    client = JiraClient(get_settings(), Cache(":memory:"))
    compiled = client._compile_jql("ORDER BY key ASC")
    client.search_issues("ORDER BY key ASC", max_results=5)
    _generation, _digest, leaf_key = client._jql_leaf_key(compiled.leaves[0])
    assert client.cache.get(leaf_key) is not None

    client._apply_mutation_events((
        MutationEvent("create", "DL-99999", changed_fields=("summary",)),
    ))
    assert client.cache.get(leaf_key) is None


def test_leaf_row_and_snapshot_keys_are_partitioned_by_user_context():
    client = JiraClient(get_settings(), Cache(":memory:"))
    client.current_user = lambda: {"id": "user-a"}
    _generation_a, _digest_a, leaf_a = client._jql_leaf_key("project = DL")
    projection_a = client._projection_signature("summary,status", True)
    snapshot_a = client.search_issues_page(
        "project = DL ORDER BY key ASC", max_results=2,
        fields="summary,status", light=True)["snapshotId"]

    client.current_user = lambda: {"id": "user-b"}
    _generation_b, _digest_b, leaf_b = client._jql_leaf_key("project = DL")
    projection_b = client._projection_signature("summary,status", True)
    snapshot_b = client.search_issues_page(
        "project = DL ORDER BY key ASC", max_results=2,
        fields="summary,status", light=True)["snapshotId"]

    assert leaf_a != leaf_b
    assert projection_a != projection_b
    assert snapshot_a != snapshot_b


def test_narrow_projection_never_poisons_shared_light_issue_cache():
    client = JiraClient(get_settings(), Cache(":memory:"))
    key = "DL-9012"
    client.search_issues_page(
        f"key = {key}", fields=["summary", "status"], light=True)
    assert client.cache.get(f"issueL:{client.env}:{key}") is None

    client.search_issues_page(
        f"key = {key}", fields=client._issue_fields(light=True), light=True)
    cached = client.cache.get(f"issueL:{client.env}:{key}")
    assert cached and (cached.get("fields") or {}).get("subtasks")


def test_large_serial_dnf_returns_bootstrap_snapshot_then_caches_every_leaf():
    client = JiraClient(get_settings(), Cache(":memory:"))
    calls = _count_searches(client)
    query = " OR ".join(
        f"project = DL AND assignee = test.ui0{index}"
        for index in range(1, 6)
    ) + " ORDER BY updated DESC"

    rows = client.search_issues(query, max_results=5)
    assert rows
    # A serial provider gets one complete foreground snapshot instead of blocking the UI on five
    # leaves.  The leaf cache warmer then executes all five without holding this response open.
    assert calls[0]["jql"].endswith("ORDER BY updated DESC, key ASC")
    assert " OR " in calls[0]["jql"]

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        leaf_queries = [call["jql"] for call in calls
                        if call["jql"].endswith("ORDER BY key ASC")]
        if len({value for value in leaf_queries}) == 5:
            break
        time.sleep(0.02)
    assert len({value for value in leaf_queries}) == 5

    count = len(calls)
    for index in range(1, 6):
        client.search_issues(
            f"project = DL AND assignee = test.ui0{index}", max_results=5)
    assert len(calls) == count


def test_snapshot_cursor_stays_stable_across_new_query_generation():
    client = JiraClient(get_settings(), Cache(":memory:"))
    query = "project = DL ORDER BY updated DESC"
    first = client.search_issues_page(query, max_results=3)
    assert first["snapshotId"] and first["hasMore"]
    old_id = first["snapshotId"]
    client._record_mutation(MutationEvent("test", "DL-9001"))
    second = client.search_issues_page(
        query, start_at=first["nextStartAt"], max_results=3, snapshot_id=old_id)
    assert second["snapshotId"] == old_id
    assert not ({row["key"] for row in first["issues"]}
                & {row["key"] for row in second["issues"]})
    fresh = client.search_issues_page(query, max_results=3)
    assert fresh["snapshotId"] != old_id


class _WriteProvider:
    def __init__(self, fail=False, fail_keys=()):
        self.fail = fail
        self.fail_keys = set(fail_keys)

    def put_json(self, path, body):
        if self.fail or any(f"/{key}" in path for key in self.fail_keys):
            raise RuntimeError("write failed")
        return {"ok": True}


def _write_client(fail=False):
    cache = Cache(":memory:")
    client = JiraClient(get_settings(), cache)
    client._provider = _WriteProvider(fail=fail)
    client._provider_built = True
    client._reprime = lambda *args, **kwargs: None
    return client, cache


def test_successful_field_write_invalidates_detail_lists_and_new_jql_generation():
    client, cache = _write_client()
    env = client.env
    issue = {"key": "DL-1", "fields": {"summary": "old", "parent": None}}
    for key in (f"issue:{env}:DL-1", f"issueL:{env}:DL-1",
                "mt:mine", "mytasks:view", "search:all", "wbs_build:plan", "vit_list:all"):
        cache.set(key, issue, 900)
    cache.set("workload:someone", {"keep": True}, 900)
    before = client._jql_generation()

    client.update_fields("DL-1", {"summary": "new"})

    assert client._jql_generation() == before + 1
    assert cache.get(f"issue:{env}:DL-1") is None
    assert cache.get(f"issueL:{env}:DL-1") is None
    assert cache.get("mt:mine") is None
    assert cache.get("mytasks:view") is None
    assert cache.get("search:all") is None
    assert cache.get("wbs_build:plan") is None
    assert cache.get("vit_list:all") is None
    assert cache.get("workload:someone") == {"keep": True}


def test_failed_write_keeps_cache_and_generation_unchanged():
    client, cache = _write_client(fail=True)
    env = client.env
    cache.set(f"issue:{env}:DL-1", {"key": "DL-1"}, 900)
    before = client._jql_generation()
    with pytest.raises(RuntimeError, match="write failed"):
        client.update_fields("DL-1", {"summary": "new"})
    assert client._jql_generation() == before
    assert cache.get(f"issue:{env}:DL-1") == {"key": "DL-1"}


def test_assignee_write_invalidates_workload_but_comment_write_does_not():
    client, cache = _write_client()
    env = client.env
    seed = {"key": "DL-1", "fields": {"parent": None}}
    cache.set(f"issueL:{env}:DL-1", seed, 900)
    cache.set("workload:someone", {"old": True}, 900)
    client.update_fields("DL-1", {"assignee": {"name": "test.ui02"}})
    assert cache.get("workload:someone") is None

    # A comment changes JQL updated-order generation and dialog caches, not workload aggregates.
    client, cache = _write_client()
    env = client.env
    cache.set(f"issueL:{env}:DL-1", seed, 900)
    cache.set(f"comments:{env}:DL-1", [{"id": "1"}], 900)
    cache.set("workload:someone", {"keep": True}, 900)
    client._provider.post_json = lambda *args, **kwargs: {"id": "2"}
    before = client._jql_generation()
    client.add_comment("DL-1", "hello")
    assert client._jql_generation() == before + 1
    assert cache.get(f"comments:{env}:DL-1") is None
    assert cache.get("workload:someone") == {"keep": True}


def test_bulk_partial_success_invalidates_only_successes_and_bumps_generation_once():
    client, cache = _write_client()
    client._provider.fail_keys = {"DL-2"}
    env = client.env
    for key in ("DL-1", "DL-2", "DL-3"):
        cache.set(f"issueL:{env}:{key}", {
            "key": key,
            "fields": {"summary": "old", "parent": None},
        }, 900)
    before = client._jql_generation()

    result = client.bulk_update([
        {"key": "DL-1", "changes": {"summary": "one"}},
        {"key": "DL-2", "changes": {"summary": "two"}},
        {"key": "DL-3", "changes": {"summary": "three"}},
    ], lambda _key, changes: changes)

    assert [row["key"] for row in result["updated"]] == ["DL-1", "DL-3"]
    assert [row["summary"] for row in result["failed"]] == ["DL-2"]
    assert client._jql_generation() == before + 1
    assert cache.get(f"issueL:{env}:DL-1") is None
    assert cache.get(f"issueL:{env}:DL-3") is None
    assert cache.get(f"issueL:{env}:DL-2")["fields"]["summary"] == "old"
