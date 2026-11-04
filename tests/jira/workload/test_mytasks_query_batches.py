"""Bounded module queries preserve Task stream membership, progress and cancellation."""

from datetime import datetime, timedelta, timezone
import re

import pytest

from app.domain import mytasks
from app.auth.base import SessionExpired
from app.infra import settings
from app.infra.cache import Cache
from app.jira.jira_client import JiraClient


@pytest.fixture
def client():
    return JiraClient(settings.get_settings(), Cache(":memory:"))


def _plan(client, **kwargs):
    calls = []
    mytasks.build_my_tasks(
        mytasks._TaskRowsClient(client, (), recorder=calls), user="test.ui01",
        scope="module:TEST", defer_children=True, create_sync=False, **kwargs)
    return calls


def _record_searches(client, monkeypatch, received_rows=None):
    searches = []
    original = client.provider.get_json

    def recorded(path, *args, **kwargs):
        if path == "/rest/api/2/search":
            searches.append((kwargs.get("params") or (args[0] if args else {})).get("jql"))
        response = original(path, *args, **kwargs)
        if path == "/rest/api/2/search" and received_rows is not None:
            received_rows.extend(response.get("issues") or [])
        return response

    monkeypatch.setattr(client.provider, "get_json", recorded)
    return searches


def _atom_keys(model):
    return {row["key"] for group in model["groups"] for row in group["atoms"]}


@pytest.mark.parametrize("filters", [
    {}, {"open_filter": "2w", "prog_filter": "1m", "done_filter": "1m"},
    {"include_done": True},
])
def test_fifteen_people_start_with_one_person_then_keep_bounded_queries(
        client, monkeypatch, filters):
    members = [f"etl.person{n:02d}" for n in range(15)]
    monkeypatch.setattr(settings, "load_people", lambda: {"TEST": members})
    grouped = _plan(client, **filters)
    assert len(grouped) == 5  # First person, three later batches, and the module component.
    grouped_leaves = [client._compile_jql(jql).leaves for _key, jql, _limit in grouped]
    assert list(map(len, grouped_leaves)) == (
        [2, 10, 10, 8, 1] if filters.get("include_done") else [6, 30, 30, 24, 3])

    monkeypatch.setattr(mytasks, "MYTASK_PEOPLE_BATCH_SIZE", 1)
    single = _plan(client, **filters)
    expected = {leaf for _key, jql, _limit in single
                for leaf in client._compile_jql(jql).leaves}
    assert {leaf for leaves in grouped_leaves for leaf in leaves} == expected
    assert len(expected) == (31 if filters.get("include_done") else 93)


def test_explicit_per_person_limit_keeps_its_existing_union_semantics(client, monkeypatch):
    monkeypatch.setattr(settings, "load_people", lambda: {
        "TEST": [f"etl.person{n:02d}" for n in range(15)]})
    calls = _plan(client, limit=3)
    assert len(calls) == 16
    assert all(limit == 3 for _key, _jql, limit in calls)


def test_module_person_literals_keep_unicode_and_escape_jql_quotes(client, monkeypatch):
    monkeypatch.setattr(settings, "load_people", lambda: {
        "TEST": ["강수아 회사", 'quoted"person', "back\\slash"]})
    calls = _plan(client)
    assert len(calls) == 3
    queries = [query for _key, query, _limit in calls[:2]]
    assert any("강수아 회사" in query for query in queries)
    assert all("\\uac15" not in query for query in queries)
    leaves = [leaf for query in queries for leaf in client._compile_jql(query).leaves]
    assert len(leaves) == 18
    assert any('quoted\\"person' in leaf for leaf in leaves)
    assert any("back\\\\slash" in leaf for leaf in leaves)


def test_recovered_planning_context_reconciles_the_real_progress_total(client, monkeypatch):
    original = client._compile_jql
    planning_failed = False

    def transient(jql, *, now=None):
        nonlocal planning_failed
        if now is not None and not planning_failed:
            planning_failed = True
            raise SessionExpired("temporary planning session failure")
        return original(jql, now=now)

    monkeypatch.setattr(client, "_compile_jql", transient)
    events = list(mytasks.iter_my_task_models(client, scope="assignee"))
    assert events[0]["progress"]["total"] == 1
    assert events[-1]["progress"] == {"completed": 3, "total": 3, "succeeded": 3, "failed": 0}
    assert events[-1]["model"]["groups"]


def test_multi_axis_fallback_query_is_not_mislabeled_as_done():
    assert mytasks._leaf_status_axis('statusCategory = Done OR statusCategory = "To Do"') is None


def test_slow_people_batches_keep_the_initial_relative_date_cutoffs(client, monkeypatch):
    monkeypatch.setattr(settings, "load_people", lambda: {
        "TEST": [f"etl.person{n:02d}" for n in range(15)]})
    clock = [datetime(2026, 11, 4, 0, 1, tzinfo=timezone.utc)]
    expected = [client._compile_jql(query, now=clock[0]).canonical
                for _key, query, _limit in _plan(client)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(mytasks, "datetime", Clock)
    started = []
    original = client.iter_search_issue_chunks

    def delayed(jql, **kwargs):
        started.append(jql)
        clock[0] += timedelta(hours=1)  # Cross many freshness buckets without wall-clock delay.
        yield from original(jql, **kwargs)

    monkeypatch.setattr(client, "iter_search_issue_chunks", delayed)
    events = list(mytasks.iter_my_task_models(client, scope="module:TEST"))
    assert events[-1]["progress"]["completed"] == 93
    assert started == expected


def test_grouped_module_stream_matches_individual_queries_and_stats(client, monkeypatch):
    people = ["skcc.i2011", "test.ui01", "test.ui02"]
    monkeypatch.setattr(settings, "load_people", lambda: {
        "TEST": people})
    searches = _record_searches(client, monkeypatch)
    grouped = list(mytasks.iter_my_task_models(client, scope="module:TEST"))
    actual = grouped[-1]["model"]
    assert actual["counts"]["total"] > 0
    complete = [leaf for event in grouped for leaf in event["completedLeaves"]]
    assert grouped[-1]["progress"]["completed"] == len(complete) == 21
    assert {leaf["axis"] for leaf in complete} == {"todo", "inprogress", "done"}
    assert searches  # The cold run really reached Jira.

    # Query every role/person/status directly through an independent Jira client. Comparing two
    # consumers of the warmed group cache could hide a wrong partition in both results.
    oracle = JiraClient(settings.get_settings(), Cache(":memory:"))
    searches.clear()
    for person in people:
        for role in ("assignee", "reporter"):
            leaves = [item["leaf"] for item in complete
                      if f'{role} = "{person}"' in item["leaf"]]
            assert len(leaves) == 3
            for leaf in leaves:
                query = leaf + " ORDER BY duedate ASC"
                expected = {row["key"] for row in oracle._legacy_search(query, max_results=None)}
                _generation, _digest, cache_key = client._jql_leaf_key(leaf)
                cached = client.cache.get(cache_key)
                assert cached is not None
                assert set(cached) == expected
                warm = list(client.iter_search_issue_chunks(query))[-1]["combined"]
                assert {row["key"] for row in warm} == expected
    assert searches == []

    # The whole model oracle also bypasses the strategic cache and uses its own issue/API cache.
    monkeypatch.setattr(mytasks, "MYTASK_PEOPLE_BATCH_SIZE", 1)
    monkeypatch.setattr(oracle, "search_issues", oracle._legacy_search)
    individual = mytasks.build_my_tasks(oracle, scope="module:TEST", defer_children=True)
    assert actual["groups"] == individual["groups"]
    assert actual["counts"] == individual["counts"]


def test_group_failure_finishes_each_logical_axis_and_does_not_cache_partial_list(
        client, monkeypatch):
    monkeypatch.setattr(settings, "load_people", lambda: {
        "TEST": ["a0", "a1", "a2", "a3", "test.ui01", "test.ui02"]})
    first_key, first_query, _limit = _plan(client)[0]
    compiled = client._compile_jql(first_query)
    failed_leaves = compiled.leaves
    original = client.iter_search_issue_chunks

    def fail_first_group(jql, **kwargs):
        if jql == compiled.canonical:
            yield {"leaf": jql, "leafIndex": 0, "leafTotal": len(failed_leaves),
                   "completedLeaves": failed_leaves, "issues": [], "combined": [],
                   "error": {"kind": "auth", "message": "session expired"}}
        else:
            yield from original(jql, **kwargs)

    monkeypatch.setattr(client, "iter_search_issue_chunks", fail_first_group)
    events = list(mytasks.iter_my_task_models(client, scope="module:TEST"))
    done = events[-1]
    assert done["done"] and done["partial"]
    assert done["progress"] == {"completed": 39, "total": 39, "succeeded": 33, "failed": 6}
    assert done["model"]["groups"]  # Later successful people/component rows still render.
    assert client.cache.get(first_key + ":light") is None
    failed = [leaf for event in events for leaf in event["completedLeaves"]
              if leaf["status"] == "error"]
    assert {leaf["axis"] for leaf in failed} == {"todo", "inprogress", "done"}
    assert len({leaf["leafIndex"] for leaf in failed}) == 6


def test_switching_filter_stops_before_the_next_people_batch(client, monkeypatch):
    # The first sorted user has the actual security-training fixture DL-9103. Cancelling before
    # the remaining five-person batch must retain this nonempty first person's memberships.
    people = ["skcc.x1042", "skcc.x1103", "skcc.i2011", "skcc.x1210", "skcc.i2044", "test.ui01"]
    target_person = "skcc.i2011"
    monkeypatch.setattr(settings, "load_people", lambda: {
        "TEST": people})
    calls = _plan(client)
    assert len(calls) == 3
    received_rows = []
    searches = _record_searches(client, monkeypatch, received_rows)
    started = []
    original = client.iter_search_issue_chunks

    def recorded(jql, **kwargs):
        started.append(jql)
        yield from original(jql, **kwargs)

    monkeypatch.setattr(client, "iter_search_issue_chunks", recorded)
    stream = mytasks.iter_my_task_models(client, scope="module:TEST", request_token="old")
    assert next(stream)["replace"] is False
    completed = next(stream)
    stream.close()
    assert len(completed["completedLeaves"]) == 6
    assert _atom_keys(completed["model"])
    assert "DL-9103" in _atom_keys(completed["model"])
    assert started == [client._compile_jql(calls[0][1]).canonical]
    assert client.cache.get(calls[0][0] + ":light") is None
    for leaf in completed["completedLeaves"]:
        _generation, _digest, cache_key = client._jql_leaf_key(leaf["leaf"])
        assert client.cache.get(cache_key) is not None
    received_keys = {row["key"] for row in received_rows}
    missing_parents = {
        (row["fields"].get("parent") or {}).get("key") for row in received_rows
        if (row["fields"].get("assignee") or {}).get("name") == target_person
    } - received_keys - {None}

    oracle = JiraClient(settings.get_settings(), Cache(":memory:"))
    monkeypatch.setattr(oracle, "search_issues", oracle._legacy_search)
    expected = mytasks.build_my_tasks(
        oracle, scope=f"assignee:{target_person}", defer_children=True)
    assert "DL-9103" in _atom_keys(expected)

    searches.clear()
    current = list(mytasks.iter_my_task_models(
        client, scope=f"assignee:{target_person}", request_token="new"))
    assert all(event["requestToken"] == "new" for event in current)
    assert _atom_keys(current[-1]["model"]) == _atom_keys(expected)
    assert current[-1]["model"]["counts"] == expected["counts"]
    # New scope may need parents not returned before cancellation. Only exact key hydration is
    # allowed: the completed memberships and their already returned issue rows must stay cached.
    for query in searches:
        match = re.fullmatch(r"key in \(([^)]+)\) ORDER BY key ASC", query, re.IGNORECASE)
        assert match, f"Unexpected membership refetch: {query}"
        hydrated_keys = {key.strip(' "') for key in match.group(1).split(",")}
        assert hydrated_keys <= missing_parents
        assert hydrated_keys.isdisjoint(received_keys)
