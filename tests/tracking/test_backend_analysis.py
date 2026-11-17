from datetime import datetime, timezone

from app.tracking.analysis import analyze_issue, period_bounds, build_result, identity_aliases


def raw_issue():
    return {"key": "P-1", "fields": {"summary": "Work", "created": "2026-11-01T00:00:00+0900",
        "updated": "2026-11-20T00:00:00+0900", "creator": {"name": "creator"},
        "reporter": {"name": "reporter"}, "assignee": {"name": "later"},
        "status": {"id": "3", "name": "Archived", "statusCategory": {"key": "done"}},
        "issuetype": {"name": "Task"}, "components": [{"name": "Module"}]},
        "changelog": {"total": 5, "histories": [
            {"id": "1", "created": "2026-11-11T00:00:00+0900", "author": {"name": "worker"},
             "items": [{"field": "status", "from": "1", "to": "2"}]},
            {"id": "2", "created": "2026-11-11T01:00:00+0900", "author": {"name": "worker"},
             "items": [{"field": "status", "from": "2", "to": "3"}]},
            {"id": "3", "created": "2026-11-12T00:00:00+0900", "author": {"name": "other"},
             "items": [{"field": "status", "from": "3", "to": "1"}]},
            {"id": "4", "created": "2026-11-12T01:00:00+0900", "author": {"name": "worker"},
             "items": [{"field": "status", "from": "1", "to": "2"}]},
            {"id": "5", "created": "2026-11-19T00:00:00+0900", "author": {"name": "other"},
             "items": [{"field": "assignee", "from": "worker", "to": "later"}]}]}}


def test_inclusive_kst_dates_and_frozen_today():
    start, end = period_bounds("2026-11-10", "2026-11-16", datetime(2026, 11, 16, 2, tzinfo=timezone.utc))
    assert start.isoformat() == "2026-11-10T00:00:00+09:00"
    assert end.isoformat() == "2026-11-16T11:00:00+09:00"


def test_category_completion_reopen_actor_and_period_assignee_scope():
    raw = raw_issue()
    comments = [{"id": "8", "created": "2026-11-10T23:59:59+0900", "updated": "2026-11-11T00:00:00+0900",
                 "author": {"name": "other"}, "updateAuthor": {"name": "worker"}, "body": "latest"}]
    start, end = period_bounds("2026-11-10", "2026-11-12")
    issue, events, missing = analyze_issue(raw, comments, start, end, {"worker"},
        {"1": "todo", "2": "done", "3": "done"}, "epic_field", True, True)
    assert [e["kind"] for e in events].count("completed") == 2
    assert [e["kind"] for e in events].count("reopened") == 1
    assert [e["kind"] for e in events].count("comment") == 1
    assert [e["kind"] for e in events].count("commentEdited") == 1
    assert issue["scopeReasons"] == ["periodAssignee", "actor"]
    assert missing == []
    # Reporter is never substituted for creator, and no out-of-period creation leaks in.
    assert all(e["kind"] != "created" for e in events)


def test_unknown_status_and_truncated_history_cannot_claim_completeness():
    start, end = period_bounds("2026-11-10", "2026-11-12")
    issue, events, missing = analyze_issue(raw_issue(), [], start, end, set(), {}, "epic_field", False, True)
    assert not issue["coverage"]["complete"]
    assert missing
    assert not any(e["kind"] in ("completed", "reopened") for e in events)


def test_assignee_responsibility_is_checked_at_each_event_not_any_time_in_period():
    raw = raw_issue()
    raw["fields"]["assignee"] = {"name": "later"}
    raw["changelog"] = {"total": 1, "histories": [{"id": "1", "created": "2026-11-11T12:00:00+0900",
        "author": {"name": "manager"}, "items": [{"field": "assignee", "from": "worker", "to": "later"}]}]}
    comments = [{"id": "a", "created": "2026-11-11T10:00:00+0900", "author": {"name": "other"}},
                {"id": "b", "created": "2026-11-11T13:00:00+0900", "author": {"name": "other"}},
                {"id": "c", "created": "2026-11-11T14:00:00+0900", "author": {"name": "worker"}}]
    start, end = period_bounds("2026-11-10", "2026-11-12")
    _, events, _ = analyze_issue(raw, comments, start, end, {"worker"}, {}, "epic_field", True, True)
    assert [e["commentId"] for e in events if e["kind"] == "comment"] == ["a", "c"]


def test_dc_user_keys_resolve_from_global_immutable_aliases_and_unknown_is_partial():
    raw = raw_issue()
    raw["fields"]["assignee"] = {"name": "bob", "key": "JIRAUSER2"}
    raw["changelog"] = {"total": 1, "histories": [{"id": "1", "created": "2026-11-11T12:00:00+0900",
        "author": {"name": "manager"}, "items": [{"field": "assignee", "from": "JIRAUSER1", "to": "JIRAUSER2"}]}]}
    comments = [{"id": "a", "created": "2026-11-11T10:00:00+0900", "author": {"name": "other"}}]
    start, end = period_bounds("2026-11-10", "2026-11-12")
    issue, events, missing = analyze_issue(raw, comments, start, end, {"alice"}, {}, "epic_field", True, True)
    assert issue["scopeUncertain"] is True
    assert any(m["source"] == "assignmentIdentity" for m in missing)
    aliases = identity_aliases({"fields": {"assignee": {"name": "alice", "key": "JIRAUSER1"}}})
    issue, events, missing = analyze_issue(raw, comments, start, end, {"alice"}, {}, "epic_field", True, True, aliases)
    assert issue["inScope"] is True
    assert [e["commentId"] for e in events if e["kind"] == "comment"] == ["a"]
    assert missing == []


def test_shared_epic_tree_deduplicates_rollup_and_preserves_both_paths():
    start, end = period_bounds("2026-11-10", "2026-11-12")
    raw = raw_issue()
    raw["fields"]["epic_field"] = "P-10"
    issue, events, _ = analyze_issue(raw, [], start, end, set(), {"1": "todo", "2": "done", "3": "done"}, "epic_field", True, True)
    epic = {"key": "P-10", "summary": "Shared", "type": "Epic", "parentKey": "", "epicKey": "", "eventIds": []}
    plan = {"wbs": [{"id": "a", "module": "A", "name": "A work", "epics": [{"key": "P-10"}]},
                    {"id": "b", "module": "B", "name": "B work", "epics": [{"key": "P-10"}]}]}
    result = build_result([issue, epic], events, plan, [], True)
    assert result["summary"]["completed"] == 1
    assert result["summary"]["completedEvents"] == 2
    assert len(result["tree"]) == 2
    assert [(node["id"], node["kind"], node["moduleId"]) for node in result["tree"]] == [
        ("wbs:a", "wbs", "A"), ("wbs:b", "wbs", "B")]
    assert [node["children"][0]["id"] for node in result["tree"]] == [
        "wbs:a/P-10", "wbs:b/P-10"]
    assert [node["children"][0]["children"][0]["id"] for node in result["tree"]] == [
        "wbs:a/P-10/P-1", "wbs:b/P-10/P-1"]
    assert all(node["counts"]["completed"] == 1 for node in result["tree"])


def test_wbs_roots_keep_module_filter_and_unlinked_task_evidence():
    from copy import deepcopy

    issues = [{"key": "P-10", "summary": "Shared Epic", "type": "Epic"},
              {"key": "P-1", "summary": "Mapped task", "type": "Task", "epicKey": "P-10"},
              {"key": "P-20", "summary": "Unlinked task", "type": "Task"},
              {"key": "P-21", "summary": "Unlinked subtask", "type": "Sub-Task", "parentKey": "P-20"}]
    events = [{"id": "mapped", "issueKey": "P-1", "kind": "comment", "date": "2026-11-11"},
              {"id": "unlinked", "issueKey": "P-21", "kind": "completed", "date": "2026-11-12"}]
    plan = {"wbs": [{"id": key, "module": module, "name": name, "epics": [{"key": "P-10"}]}
                    for key, module, name in [("a", "A", "First work"), ("b", "B", "Other module"),
                                               ("c", "A", "Second work")]]}
    before = deepcopy((issues, events, plan))

    result = build_result(issues, events + [events[1]], plan, ["A"], False)

    assert [(node["id"], node["name"], node["kind"]) for node in result["tree"]] == [
        ("wbs:a", "First work", "wbs"), ("wbs:c", "Second work", "wbs"),
        ("unmapped", "WBS Task 미연결", "wbs")]
    assert [node["moduleId"] for node in result["tree"][:2]] == ["A", "A"]
    unmapped = result["tree"][-1]
    assert unmapped["virtual"] is True
    assert unmapped["wbsUnassigned"] is True
    assert unmapped["children"][0]["id"] == "unmapped/P-20"
    assert unmapped["children"][0]["children"][0]["id"] == "unmapped/P-20/P-21"
    assert unmapped["counts"]["completed"] == 1
    assert unmapped["counts"]["provisional"] is True
    assert result["summary"]["totalEvents"] == 2
    assert {event["id"] for event in result["events"]} == {"mapped", "unlinked"}
    assert (issues, events, plan) == before
