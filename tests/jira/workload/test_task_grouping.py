"""Task grouping exposes only local membership of Epics already present in the snapshot."""
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
