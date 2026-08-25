"""Task-page base-first and per-parent SubTask synchronization contracts."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.auth.base import SessionExpired
from app.domain.mytasks import (
    build_my_tasks,
    hydrate_my_task_epics,
    hydrate_my_task_group,
    hydrate_my_task_snapshot,
    iter_my_task_models,
)
from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient
from app.jira.jql import sort_issues


def _client():
    return JiraClient(get_settings(), Cache(":memory:"))


def _group(model, key):
    return next(group for group in model["groups"] if group["key"] == key)


def _without_async_metadata(group):
    result = dict(group)
    for key in ("childrenPending", "childrenLoaded", "childrenLoadedCount"):
        result.pop(key, None)
    return result


def _counts(groups):
    seen, atoms = set(), []
    for group in groups.values():
        for atom in group["atoms"]:
            if atom["key"] not in seen:
                seen.add(atom["key"])
                atoms.append(atom)
    return {
        "total": len(atoms),
        "overdue": sum(atom["statusCategory"] != "done" and atom["dueDays"] is not None
                       and atom["dueDays"] < 0 for atom in atoms),
        "today": sum(atom["statusCategory"] != "done" and atom["dueDays"] == 0
                     for atom in atoms),
        "week": sum(atom["statusCategory"] != "done" and atom["dueDays"] is not None
                    and 0 < atom["dueDays"] <= 7 for atom in atoms),
        "done": sum(atom["statusCategory"] == "done" for atom in atoms),
        "noDue": sum(atom["dueDays"] is None for atom in atoms),
    }


def test_deferred_base_returns_parent_before_peer_subtasks_and_hydrates_one_group_only(monkeypatch):
    client = _client()
    original = client.issues_by_keys
    calls = []

    def recorded(keys, light=False):
        calls.append((tuple(keys), light))
        return original(keys, light=light)

    monkeypatch.setattr(client, "issues_by_keys", recorded)
    base = build_my_tasks(client, scope="assignee", defer_children=True)

    parent = _group(base, "DL-9020")
    other_parent = _group(base, "DL-9025")
    assert base["syncId"]
    assert parent["childrenPending"] is True
    assert parent["kidsTotal"] == 4
    assert {row["key"] for row in parent["atoms"]} == {"DL-9021", "DL-9022"}
    assert parent["others"] == []
    # Base phase may fetch missing Parent rows, but must not batch the peer SubTasks yet.
    assert not any({"DL-9023", "DL-9024"} <= set(keys) for keys, _light in calls)
    assert all(light for _keys, light in calls)

    hydrated = hydrate_my_task_group(client, base["syncId"], "DL-9020")
    assert hydrated["childrenPending"] is False
    assert {row["key"] for row in hydrated["others"]} == {"DL-9023", "DL-9024"}
    # Hydrating one Parent does not mutate or wait for another Parent group.
    assert other_parent["childrenPending"] is True
    assert any(set(keys) == {"DL-9021", "DL-9022", "DL-9023", "DL-9024"}
               and light for keys, light in calls)


def test_each_async_group_matches_the_legacy_complete_model():
    client = _client()
    deferred = build_my_tasks(client, scope="assignee", defer_children=True)
    complete = build_my_tasks(client, scope="assignee")
    expected = {group["key"]: group for group in complete["groups"]}

    pending = [group for group in deferred["groups"] if group.get("childrenPending")]
    assert len(pending) >= 2
    for shell in pending:
        actual = hydrate_my_task_group(client, deferred["syncId"], shell["key"])
        assert _without_async_metadata(actual) == _without_async_metadata(expected[shell["key"]])


def test_group_hydration_returns_monotonic_authoritative_snapshots_without_duplicate_rows():
    client = _client()
    deferred = build_my_tasks(client, scope="module:TEST", defer_children=True)
    pending = [group for group in deferred["groups"] if group.get("childrenPending")]
    assert len(pending) >= 2

    snapshots = [hydrate_my_task_snapshot(client, deferred["syncId"], group["key"])
                 for group in pending]
    first, second, final = snapshots[0], snapshots[1], snapshots[-1]

    assert first["contract"] == second["contract"] == "task-snapshot.v1"
    assert first["type"] == second["type"] == "snapshot"
    assert first["syncId"] == second["syncId"] == deferred["syncId"]
    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert final["sequence"] == len(pending)
    assert final["model"]["snapshotSequence"] == len(pending)
    assert final["model"]["syncPending"] == 0
    rows = [row for group in final["model"]["groups"]
            for row in list(group["atoms"]) + list(group["others"])]
    keys = [row["key"] for row in rows]
    assert len(keys) == len(set(keys))


def test_parallel_group_hydration_serializes_authoritative_snapshot_versions():
    client = _client()
    deferred = build_my_tasks(client, scope="assignee", defer_children=True)
    pending = [group for group in deferred["groups"] if group.get("childrenPending")][:2]
    assert len(pending) == 2

    with ThreadPoolExecutor(max_workers=2) as pool:
        snapshots = list(pool.map(
            lambda group: hydrate_my_task_snapshot(client, deferred["syncId"], group["key"]),
            pending))

    assert sorted(snapshot["sequence"] for snapshot in snapshots) == [1, 2]
    latest = max(snapshots, key=lambda snapshot: snapshot["sequence"])["model"]
    latest_groups = {group["key"]: group for group in latest["groups"]}
    assert all(latest_groups[group["key"]]["childrenPending"] is False for group in pending)


@pytest.mark.parametrize("scope", [
    "assignee", "reporter", "both", "module:TEST", "module:Workbench",
    "assignee:test.ui02", "reporter:test.ui02", "epic:DL-9019",
    'jql:project = DL AND assignee = "test.ui01"',
])
def test_async_completion_preserves_every_task_scope(scope):
    client = _client()
    deferred = build_my_tasks(client, scope=scope, defer_children=True)
    complete = build_my_tasks(client, scope=scope)

    hydrated_keys = set()
    actual = deferred
    if deferred.get("syncId"):
        for shell in deferred["groups"]:
            if shell.get("childrenPending"):
                snapshot = hydrate_my_task_snapshot(client, deferred["syncId"], shell["key"])
                hydrated_keys.add(shell["key"])
                actual = snapshot["model"]
        actual_epics = hydrate_my_task_epics(client, deferred["syncId"]) \
            if deferred.get("epicsPending") else deferred["epics"]
    else:
        actual_epics = deferred["epics"]

    actual_groups = {group["key"]: group for group in actual["groups"]}
    expected_groups = {group["key"]: group for group in complete["groups"]}
    assert set(actual_groups) == set(expected_groups)
    for key, expected in expected_groups.items():
        actual = actual_groups[key]
        assert [atom["key"] for atom in actual["atoms"]] == [atom["key"] for atom in expected["atoms"]]
        assert [atom["key"] for atom in actual["others"]] == [atom["key"] for atom in expected["others"]]
        if key in hydrated_keys:
            assert _without_async_metadata(actual) == _without_async_metadata(expected)
    assert actual_epics == complete["epics"]
    assert _counts(actual_groups) == complete["counts"]


def test_subtask_hydration_writes_light_issue_cache_without_poisoning_full_rows():
    client = _client()
    deferred = build_my_tasks(client, scope="assignee", defer_children=True)
    hydrate_my_task_group(client, deferred["syncId"], "DL-9020")

    for key in ("DL-9021", "DL-9022", "DL-9023", "DL-9024"):
        assert client.cache.get(f"issueL:{client.env}:{key}") is not None
        assert client.cache.get(f"issue:{client.env}:{key}") is None


def test_expired_or_invalidated_filter_snapshot_cannot_hydrate():
    client = _client()
    deferred = build_my_tasks(client, scope="assignee", defer_children=True)
    client.cache.invalidate("mytasks:")

    with pytest.raises(LookupError, match="만료"):
        hydrate_my_task_group(client, deferred["syncId"], "DL-9020")


@pytest.mark.parametrize("scope", [
    "assignee", "reporter", "both", "module:TEST", "module:Workbench", "mymodules",
    "assignee:test.ui02", "epic:DL-9019",
    'jql:project = DL AND assignee = "test.ui01"',
])
def test_progressive_leaf_union_matches_authoritative_task_model(scope):
    client = _client()
    events = list(iter_my_task_models(client, scope=scope, request_token="filter-a"))

    assert all(event["contract"] == "task-snapshot.v1" for event in events)
    assert all(event["type"] == "snapshot" for event in events)
    assert all(event["requestToken"] == "filter-a" for event in events)
    assert [event["sequence"] for event in events] == list(range(len(events)))
    assert events[0]["replace"] is False
    assert events[-1]["done"] is True
    chunks = [event for event in events if event["completedLeaf"]]
    assert chunks
    assert [event["progress"]["completed"] for event in chunks] == \
        list(range(1, len(chunks) + 1))
    assert all(event["progress"]["total"] == len(chunks) for event in chunks)

    # Every partial response is already an authoritative union: keys are unique, order and
    # statistics are server-produced, and the browser never needs to merge a leaf model.
    for event in chunks:
        model = event["model"]
        atom_keys = [atom["key"] for group in model["groups"] for atom in group["atoms"]]
        assert len(atom_keys) == len(set(atom_keys))
        assert event["statistics"] == model["counts"]

    streamed = events[-1]["model"]
    direct = build_my_tasks(client, scope=scope, defer_children=True)
    streamed_groups = {
        group["key"]: [atom["key"] for atom in group["atoms"]]
        for group in streamed["groups"]
    }
    direct_groups = {
        group["key"]: [atom["key"] for atom in group["atoms"]]
        for group in direct["groups"]
    }
    assert streamed_groups == direct_groups
    assert streamed["counts"] == direct["counts"]


def test_completed_stream_leaf_is_cached_before_the_browser_can_stop_consuming():
    client = _client()
    query = (
        'assignee = "test.ui01" AND ('
        'statusCategory = "To Do" OR statusCategory = "In Progress" '
        'OR statusCategory = Done) ORDER BY duedate ASC'
    )
    stream = client.iter_search_issue_chunks(query)
    first = next(stream)
    stream.close()

    _generation, _digest, cache_key = client._jql_leaf_key(first["leaf"])
    assert client.cache.get(cache_key) is not None
    for issue in first["issues"]:
        assert client.cache.get(f"issueL:{client.env}:{issue['key']}") is not None


def test_fully_warm_subquery_coalesces_leafs_into_one_authoritative_snapshot():
    client = _client()
    cold = list(iter_my_task_models(client, scope="assignee", request_token="cold"))
    warm = list(iter_my_task_models(client, scope="assignee", request_token="warm"))

    cold_leaf_events = [event for event in cold if event["completedLeaves"]]
    warm_leaf_events = [event for event in warm if event["completedLeaves"]]
    assert len(cold_leaf_events) > 1
    assert len(warm_leaf_events) == 1
    assert len(warm_leaf_events[0]["completedLeaves"]) == warm[-1]["progress"]["total"]
    assert warm[-1]["model"]["groups"] == cold[-1]["model"]["groups"]


def test_leaf_failures_are_isolated_classified_and_later_leaves_still_arrive(monkeypatch):
    client = _client()
    original = client._cached_jql_leaf

    def flaky(leaf, *args, **kwargs):
        if "DL-9008" in leaf:
            raise PermissionError("HTTP 403 permission denied")
        if "DL-9028" in leaf:
            raise SessionExpired("HTTP 401 session expired")
        if "DL-9030" in leaf:
            raise RuntimeError("temporary Jira failure")
        return original(leaf, *args, **kwargs)

    monkeypatch.setattr(client, "_cached_jql_leaf", flaky)
    chunks = list(client.iter_search_issue_chunks(
        "key = DL-9008 OR key = DL-9028 OR key = DL-9030 OR key = DL-9020"))

    errors = [chunk["error"]["kind"] for chunk in chunks if chunk.get("error")]
    assert sorted(errors) == ["auth", "other", "permission"]
    assert any(any(issue["key"] == "DL-9020" for issue in chunk["issues"])
               for chunk in chunks if not chunk.get("error"))
    assert len(chunks) == 4


def test_partial_task_stream_keeps_successes_and_does_not_publish_partial_mt_cache(monkeypatch):
    client = _client()
    original = client._cached_jql_leaf

    def one_denied_leaf(leaf, *args, **kwargs):
        if "statuscategory = done" in leaf.lower():
            raise PermissionError("HTTP 403 permission denied")
        return original(leaf, *args, **kwargs)

    monkeypatch.setattr(client, "_cached_jql_leaf", one_denied_leaf)
    events = list(iter_my_task_models(client, scope="assignee"))
    complete = events[-1]

    assert any((event.get("completedLeaf") or {}).get("status") == "error"
               and event["completedLeaf"]["error"]["kind"] == "permission"
               for event in events)
    assert complete["type"] == "snapshot"
    assert complete["done"] is True
    assert complete["partial"] is True
    assert complete["progress"]["failed"] == 1
    assert complete["model"]["groups"]
    assert client.cache.get(f"mt:{client.env}:asg:test.ui01:all.all.1w:light") is None


def test_authoritative_snapshot_is_independent_of_leaf_completion_order(monkeypatch):
    baseline = _client()
    expected = list(iter_my_task_models(baseline, scope="both"))[-1]["model"]

    candidate = _client()
    original = candidate.iter_search_issue_chunks

    def reversed_chunks(jql, fields=None, light=True):
        chunks = list(original(jql, fields=fields, light=light))
        combined = {}
        order = candidate._compile_jql(jql).order
        for chunk in reversed(chunks):
            for issue in chunk.get("issues") or ():
                combined[issue["key"]] = issue
            current = dict(chunk)
            current["combined"] = sort_issues(combined.values(), order)
            yield current

    monkeypatch.setattr(candidate, "iter_search_issue_chunks", reversed_chunks)
    events = list(iter_my_task_models(candidate, scope="both", request_token="reverse"))
    actual = events[-1]["model"]

    assert actual["groups"] == expected["groups"]
    assert actual["counts"] == expected["counts"]
    assert [event["sequence"] for event in events] == list(range(len(events)))


def test_done_window_cannot_crowd_active_axes_out_of_the_task_model(monkeypatch):
    """A Done-only filter change must not compete with To Do/In Progress for one global cap."""
    client = _client()
    me = "test.ui01"

    def raw(key, category, *, due=None, resolved=None):
        category_key = {"todo": "new", "inprogress": "indeterminate", "done": "done"}[category]
        fields = {
            "summary": key, "issuetype": {"name": "Task", "subtask": False},
            "status": {"name": category, "statusCategory": {"key": category_key}},
            "assignee": {"name": me, "displayName": me},
            "reporter": {"name": me, "displayName": me},
            "priority": {"name": "P2-Major"}, "duedate": due,
            "resolutiondate": resolved, "created": "2026-01-01T09:00:00+0900",
            "updated": "2026-08-25T09:00:00+0900", "components": [],
            "subtasks": [], "parent": None,
        }
        fields[client.s.sp_field_id] = None
        fields[client.s.epic_link_field_id] = None
        return {"key": key, "fields": fields}

    progress = [raw(f"DL-97{i:04d}", "inprogress") for i in range(30)]
    todo = [raw(f"DL-96{i:04d}", "todo") for i in range(5)]
    done = [raw(f"DL-98{i:04d}", "done", due="2026-01-01",
                resolved="2026-08-15T18:00:00+0900") for i in range(230)]

    def chunks(jql, fields=None, light=True):
        compiled = client._compile_jql(jql)
        combined = {}
        for index, leaf in enumerate(compiled.leaves):
            lowered = leaf.lower()
            if "statuscategory = done" in lowered:
                rows = done[:20] if "resolved >= -7d" in jql.lower() else done
            elif "statuscategory = \"in progress\"" in lowered:
                rows = progress
            elif "statuscategory = \"to do\"" in lowered:
                rows = todo
            else:
                rows = []
            for row in rows:
                combined[row["key"]] = row
            yield {
                "leaf": leaf, "leafIndex": index, "leafTotal": len(compiled.leaves),
                "issues": rows, "combined": sort_issues(combined.values(), compiled.order),
                "fallback": False, "coalesceCached": False,
            }

    monkeypatch.setattr(client, "iter_search_issue_chunks", chunks)
    week = list(iter_my_task_models(client, scope="assignee", done_filter="1w"))[-1]["model"]
    month = list(iter_my_task_models(client, scope="assignee", done_filter="1m"))[-1]["model"]

    def keys(model, category):
        return {atom["key"] for group in model["groups"] for atom in group["atoms"]
                if atom["statusCategory"] == category}

    expected_progress = {row["key"] for row in progress}
    assert keys(week, "inprogress") == expected_progress
    assert keys(month, "inprogress") == expected_progress
    assert keys(month, "done") == {row["key"] for row in done}
    assert month["counts"]["total"] == len(progress) + len(todo) + len(done)


def test_epic_metadata_uses_long_ttl_and_ticket_invalidation_evicts_it(monkeypatch):
    client = _client()
    meta = client.epic_metadata("DL-9019")

    assert meta and meta["title"] != "DL-9019"
    assert client.EPIC_META_TTL > client.s.cache_ttl_seconds
    cache_key = f"epicmeta:{client.env}:DL-9019"
    assert client.cache.get(cache_key) == meta

    monkeypatch.setattr(client, "_reprime", lambda *args, **kwargs: None)
    client._invalidate_ticket("DL-9019")
    assert client.cache.get(cache_key) is None
