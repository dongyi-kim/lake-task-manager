"""Task grouping exposes only local membership of Epics already present in the snapshot."""
from datetime import timedelta

import pytest

from app.domain import task_grouping
from app.domain.mytasks import build_my_tasks, hydrate_my_task_snapshot
from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


def test_projection_keeps_configuration_order_and_only_relevant_memberships():
    plan = {"wbs": [
        {"id": "A", "module": "first", "name": "First", "epics": [{"key": "DL-1"}, {"key": "SECRET-2"}]},
        {"id": "B", "module": "second", "name": "Second", "epics": [{"key": "DL-1"}]},
        {"id": "C", "module": "hidden", "name": "Hidden", "epics": [{"key": "SECRET-3"}]},
    ]}
    meta = task_grouping.task_grouping_meta([{"epic": "DL-1"}, {"epic": None}], plan)
    assert [row["id"] for row in meta["wbs"]] == ["A", "B"]
    assert all(row["epics"] == ["DL-1"] for row in meta["wbs"])
    assert "SECRET" not in str(meta) and "Hidden" not in str(meta)
    assert task_grouping.task_grouping_meta([], plan)["revision"] == meta["revision"]
    plan["wbs"].reverse()
    assert task_grouping.task_grouping_meta([], plan)["revision"] != meta["revision"]


def test_child_snapshot_refreshes_local_grouping_without_epic_requests(monkeypatch):
    client = JiraClient(get_settings(), Cache(":memory:"))
    plan = {"wbs": [{"id": "A", "module": "TEST", "name": "Original",
                     "epics": [{"key": "DL-9019"}]}]}
    monkeypatch.setattr(task_grouping, "load_plan", lambda: plan)
    monkeypatch.setattr(client, "epic_metadata_many", lambda _keys: (_ for _ in ()).throw(
        AssertionError("grouping must not request Epic metadata")))
    model = build_my_tasks(client, scope="assignee", defer_children=True)
    assert model["groupingMeta"]["wbs"][0]["name"] == "Original"
    before = model["groupingMeta"]["revision"]
    plan["wbs"][0]["name"] = "Updated"
    snapshot = hydrate_my_task_snapshot(client, model["syncId"], "DL-9020")["model"]
    assert snapshot["groupingMeta"]["wbs"][0]["name"] == "Updated"
    assert snapshot["groupingMeta"]["revision"] != before


@pytest.mark.parametrize("parent_matched", [False, True], ids=["old-context-parent", "recent-matched-parent"])
def test_parent_query_membership_survives_build_and_child_snapshot(monkeypatch, parent_matched):
    client = JiraClient(get_settings(), Cache(":memory:"))
    me, parent_key, child_key, peer_key = "test.ui01", "DL-99100", "DL-99101", "DL-99102"
    today = client.s_today()

    def raw(key, *, parent=None, assignee=me, done=False):
        fields = {
            "summary": key, "issuetype": {"name": "Sub-Task" if parent else "Task", "subtask": bool(parent)},
            "status": {"name": "Closed" if done else "Open",
                       "statusCategory": {"key": "done" if done else "new"}},
            "assignee": {"name": assignee}, "reporter": {"name": me},
            "priority": {"name": "High" if parent else "Low"},
            "components": [], "subtasks": [],
        }
        if parent:
            fields["parent"] = {"key": parent}
        return {"key": key, "fields": fields}

    parent = raw(parent_key, done=True)
    resolved = today if parent_matched else today - timedelta(days=365)
    parent["fields"].update({
        "resolutiondate": resolved.isoformat() + "T09:00:00.000+0900",
        "subtasks": [{"key": child_key}, {"key": peer_key}],
    })
    child = raw(child_key, parent=parent_key)
    peer = raw(peer_key, parent=parent_key, assignee="test.ui02")
    rows = {row["key"]: row for row in (parent, child, peer)}

    def publish(found):
        # These fake read boundaries must publish the same dated canonical
        # projections as real search/key reads; supplied dictionaries alone
        # cannot establish parent membership provenance.
        client.cache.set_many_if_fence(
            ((f"issueL:{client.env}:{row['key']}", row) for row in found),
            client.s.cache_ttl_seconds, client.cache.fence())
        return found

    def search(jql, **_kwargs):
        assert "resolved >= -7d" in jql
        return publish([child, parent] if parent_matched else [child])

    def details(keys, **_kwargs):
        return {"issues": publish([rows[key] for key in keys]), "missing": [], "complete": True}

    def unexpected_provider_read(path, **_kwargs):
        pytest.fail(f"Grouping fixture unexpectedly fell back to provider: {path}")

    monkeypatch.setattr(client, "current_user", lambda: {"id": me, "name": "Me"})
    monkeypatch.setattr(client, "search_issues", search)
    monkeypatch.setattr(client, "issues_by_keys_result", details)
    monkeypatch.setattr(client.provider, "get_json", unexpected_provider_read)
    model = build_my_tasks(client, scope="assignee", done_filter="1w", defer_children=True)
    group = model["groups"][0]
    assert group["key"] == parent_key and group["mine"] is True
    assert group["queryMatched"] is parent_matched
    assert group["childrenPending"] is True
    assert model["counts"]["done"] == 0

    # Hydration must recover query membership from the original matched keys, even for a
    # cached snapshot predating this display flag. Broad assignee membership is insufficient.
    sync_key = f"mytasks:sync:{client.env}:{model['syncId']}"
    state = client.cache.get(sync_key)
    state["model"]["groups"][0].pop("queryMatched")
    client.cache.set(sync_key, state, 60)
    snapshot = hydrate_my_task_snapshot(client, model["syncId"], parent_key)["model"]
    hydrated = snapshot["groups"][0]
    assert hydrated["mine"] is True and hydrated["queryMatched"] is parent_matched
    assert hydrated["childrenLoaded"] is True
    assert [row["key"] for row in hydrated["atoms"]] == [child_key]
    assert [row["key"] for row in hydrated["others"]] == [peer_key]


def test_bad_optional_wbs_config_does_not_block_task_projection(monkeypatch):
    def invalid():
        raise ValueError("invalid local plan")
    monkeypatch.setattr(task_grouping, "load_plan", invalid)
    assert task_grouping.task_grouping_meta([{"epic": "DL-1"}])["unavailable"] is True


@pytest.mark.parametrize("raw", [None, "scalar", ["list"]], ids=["empty-yaml", "scalar-yaml", "list-yaml"])
def test_structurally_invalid_wbs_yaml_keeps_tasks_available(monkeypatch, raw):
    from app.infra import settings
    monkeypatch.setattr(settings, "_read_yaml", lambda _path: raw)
    assert task_grouping.task_grouping_meta([{"epic": "DL-1"}]) == {
        "revision": None, "wbs": [], "unavailable": True}
