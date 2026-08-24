"""Task-page base-first and per-parent SubTask synchronization contracts."""

import pytest

from app.domain.mytasks import (
    build_my_tasks,
    hydrate_my_task_epics,
    hydrate_my_task_group,
)
from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


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


@pytest.mark.parametrize("scope", [
    "assignee", "reporter", "both", "module:TEST",
    "assignee:test.ui02", "reporter:test.ui02", "epic:DL-9019",
    'jql:project = DL AND assignee = "test.ui01"',
])
def test_async_completion_preserves_every_task_scope(scope):
    client = _client()
    deferred = build_my_tasks(client, scope=scope, defer_children=True)
    complete = build_my_tasks(client, scope=scope)

    actual_groups = {group["key"]: group for group in deferred["groups"]}
    hydrated_keys = set()
    if deferred.get("syncId"):
        for shell in deferred["groups"]:
            if shell.get("childrenPending"):
                hydrated = hydrate_my_task_group(client, deferred["syncId"], shell["key"])
                hydrated_keys.add(shell["key"])
                claimed = {atom["key"] for atom in hydrated["atoms"]}
                for key, group in tuple(actual_groups.items()):
                    if key != shell["key"] and claimed:
                        copy = dict(group)
                        copy["atoms"] = [atom for atom in group["atoms"] if atom["key"] not in claimed]
                        actual_groups[key] = copy
                actual_groups[shell["key"]] = hydrated
        actual_epics = hydrate_my_task_epics(client, deferred["syncId"]) \
            if deferred.get("epicsPending") else deferred["epics"]
    else:
        actual_epics = deferred["epics"]

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
