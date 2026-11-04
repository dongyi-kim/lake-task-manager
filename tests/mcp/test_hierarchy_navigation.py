"""Hierarchy navigation shares UI caches and preserves identities across pages."""

import anyio
from mcp import Client

from app.auth.base import PermissionDenied
from app.jira.hierarchy_service import get_descendants


def test_descendants_share_ui_caches_and_inherit_epic_without_parent_date_filter(build_env):
    env = build_env()
    epic = env.client.create_child(None, "Epic", "root")["key"]
    task = env.client.create_child(epic, "Task", "old parent")["key"]
    child = env.client.create_child(task, "Sub-task", "recent child")["key"]
    # Prime the same membership/issue paths used by the UI, not a new report cache.
    env.client.ticket_children(epic)
    env.client.ticket_children(task)
    env.client.get_issue(task)  # full cache may contain body; response must stay light
    initial = get_descendants(env.client, epic, projects=["DL"], max_results=2)
    before = len(env.calls)
    final = get_descendants(env.client, epic, projects=["DL"], start_at=2, max_results=2)
    assert len(env.calls) == before
    assert initial["complete"] and final["complete"]
    assert all("description" not in row["issue"]["fields"] for row in initial["issues"])
    assert {"created", "updated", "resolutiondate"} <= initial["issues"][0]["issue"]["fields"].keys()
    assert initial["total"] == 3 and initial["nextStartAt"] == 2
    assert final["issues"][0]["key"] == child
    assert final["issues"][0]["parentKey"] == task
    assert final["issues"][0]["epicKey"] == epic
    assert final["issues"][0]["depth"] == 2
    task_rows = get_descendants(env.client, task, projects=["DL"], include_root=False)
    assert [row["key"] for row in task_rows["issues"]] == [child]



def test_descendant_failed_branches_retry_without_losing_loaded_rows(build_env, monkeypatch):
    env = build_env()
    root = env.client.create_child(None, "Task", "root")["key"]
    child = env.client.create_child(root, "Sub-task", "child")["key"]
    read, blocked = env.client.get_issue_light, True
    def get(key, **kwargs):
        if key == child and blocked:
            raise PermissionDenied("/issue/" + key)
        return read(key, **kwargs)
    monkeypatch.setattr(env.client, "get_issue_light", get)
    for _ in range(2):
        result = get_descendants(env.client, root, projects=["DL"])
        assert result["partial"] and result["total"] == 1
        assert result["missing"] == [{"key": child, "parentKey": root, "kind": "permission", "stage": "issue"}]
    blocked = False
    recovered = get_descendants(env.client, root, projects=["DL"])
    assert recovered["complete"] and recovered["total"] == 2



def test_five_children_duplicate_epic_path_and_subtask_root(build_env):
    env = build_env()
    epic = env.client.create_child(None, "Epic", "epic")["key"]
    task = env.client.create_child(epic, "Task", "old task")["key"]
    children = [env.client.create_child(task, "Sub-task", f"child {n}")["key"] for n in range(5)]
    env.client.update_fields(children[0], {env.client.s.epic_link_field_id: epic})
    directly_linked = env.client._search(f'project in ("DL") AND "Epic Link" = {epic}',
                                         max_results=None, light=True)
    assert {row["key"] for row in directly_linked} == {task, children[0]}
    result = get_descendants(env.client, epic, projects=["DL"])
    assert result["complete"] and result["total"] == 7
    rows, page = list(result["issues"]), result
    while page["hasMore"]:
        page = get_descendants(env.client, epic, projects=["DL"], start_at=page["nextStartAt"])
        rows.extend(page["issues"])
    assert len(rows) == len({row["key"] for row in rows}) == 7
    assert all(row["parentKey"] == task and row["depth"] == 2
               for row in rows if row["key"] in children)
    before = len(env.calls)
    assert get_descendants(env.client, epic, projects=["DL"])["complete"]
    assert len(env.calls) == before
    subtask = get_descendants(env.client, children[0], projects=["DL"])
    assert subtask["complete"] and subtask["issues"][0]["epicKey"] == epic



def test_hierarchy_sdk_and_project_scope(build_env):
    env = build_env()
    key = env.client.create_child(None, "Task", "SDK hierarchy")["key"]
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            tree = await mcp.call_tool("get_descendants", {"root_key": key})
            assert not tree.is_error and tree.structured_content["data"]["total"] == 1
            denied = await mcp.call_tool("get_descendants", {"root_key": "OTHER-1"})
            assert denied.is_error
    anyio.run(scenario)
