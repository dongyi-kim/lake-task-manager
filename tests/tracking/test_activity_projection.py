"""Ignored Jira bookkeeping stays durable but never reaches Tracking readers."""
from copy import deepcopy

import pytest

from app.tracking.analysis import build_result
from app.tracking.presentation import present_comments
from test_backend_manager import Provider, manager


def event(eid, *, key="P-1", kind="fieldChanged", field="summary", before="old", after="new"):
    return {"id": eid, "issueKey": key, "kind": kind, "type": kind,
            "date": "2026-11-11T10:00:00+09:00", "field": field, "from": before, "to": after,
            "body": "timespent worklogid Resolved Closed", "commentId": eid}


def store_run(m, events, *, status="complete"):
    issues = [{"key": key, "summary": key, "type": "Task", "parentKey": "P-10", "epicKey": "P-10",
               "status": "Closed", "statusCategory": "done", "scopeReasons": ["periodAssignee"],
               "inScope": True, "events": [deepcopy(e) for e in events if e["issueKey"] == key],
               "eventIds": [e["id"] for e in events if e["issueKey"] == key]}
              for key in sorted({e["issueKey"] for e in events})]
    issues.append({"key": "P-10", "summary": "Epic", "type": "Epic", "events": [],
                   "eventIds": [], "contextOnly": True})
    plan = {"wbs": [{"id": "one", "name": "Work", "module": "M", "epics": [{"key": "P-10"}]}]}
    snapshot = {"id": "saved", "status": status, "coverage": {"complete": True},
                "metrics": {"restRequests": 42}, **build_result(issues, events, plan, [], True),
                "aiSummary": {"items": [{"text": "A prior summary", "evidenceIds": [events[-1]["id"]]}]}}
    m.store.insert(snapshot, *m.authorize())
    _, _, stored, checkpoint = m.store.read("saved")
    checkpoint["sources"] = {"P-1": {"history": {"histories": [{"items": [deepcopy(e) for e in events]}]}}}
    checkpoint["analyzed"] = {"P-1": {"issue": deepcopy(issues[0])}}
    m.store.save("saved", stored, checkpoint)
    return snapshot, checkpoint


@pytest.mark.parametrize("field", [
    "timespent", "Time Spent", "TIME_SPENT", "time-spent", "timeestimate", "Time Estimate",
    "timeoriginalestimate", "Original Estimate", "Remaining Estimate", "worklogid", "Worklog ID",
    "aggregatetimespent", "Aggregate Time Estimate", "aggregate_time_original_estimate",
])
def test_saved_bookkeeping_fields_are_hidden_without_changing_jira_sources(tmp_path, field):
    provider = Provider()
    m = manager(tmp_path, provider)
    store_run(m, [event("ignored", field=field), event("kept", kind="comment")])
    stored_before = m.store.read("saved")

    run = m.get("saved")

    assert [e["id"] for e in run["events"]] == ["kept"]
    assert [e["id"] for e in run["issues"][0]["events"]] == ["kept"]
    assert run["issues"][0]["eventIds"] == ["kept"]
    assert run["summary"]["fieldChanges"] == 0
    assert run["summary"]["comments"] == 1
    assert "aiSummary" not in run
    assert m.store.read("saved") == stored_before
    assert provider.calls == []


@pytest.mark.parametrize(("before", "after", "kind", "kept"), [
    ("Resolved", "Closed", "fieldChanged", False),
    ("  rEsOlVeD ", " CLOSED ", "fieldChanged", False),
    ("Resolved", "Closed", "completed", False),
    ("Resolved", "Closed", "reopened", False),
    ("In Progress", "Resolved", "completed", True),
    ("Resolved", "In Progress", "reopened", True),
    ("Closed", "Resolved", "fieldChanged", True),
    ("Finished", "Archived", "fieldChanged", True),
    ("Resolved", "Closed", "comment", True),
])
def test_only_resolved_to_closed_status_bookkeeping_is_hidden(tmp_path, before, after, kind, kept):
    m = manager(tmp_path, Provider())
    store_run(m, [event("transition", field="status", before=before, after=after, kind=kind)])

    run = m.get("saved")

    assert [e["id"] for e in run["events"]] == (["transition"] if kept else [])
    assert run["issues"][0]["status"] == "Closed"
    assert run["issues"][0]["statusCategory"] == "done"
    assert run["issues"][0]["scopeReasons"] == ["periodAssignee"]
    assert run["issues"][0]["inScope"] is True


def test_legacy_mixed_activity_recounts_every_tree_level_and_all_read_paths(tmp_path):
    provider = Provider()
    m = manager(tmp_path, provider)
    events = [event("spent", field="timespent", key="P-2"),
              event("closed", field="status", before="Resolved", after="Closed"),
              event("complete", kind="completed", field="status", before="Open", after="Resolved"),
              event("reopen", kind="reopened", field="status", before="Resolved", after="Open"),
              event("real-change", field="summary", after="timespent worklogid Resolved Closed"),
              event("comment", kind="comment")]
    store_run(m, events)
    stored_before = m.store.read("saved")

    for run in (m.get("saved"), m.list()[0], m.resume("saved", background=False), m.cancel("saved")):
        assert {e["id"] for e in run["events"]} == {"complete", "reopen", "real-change", "comment"}
        assert run["issues"][1]["events"] == []
        assert run["issues"][1]["eventIds"] == []
        assert run["summary"]["totalEvents"] == 4
        assert run["summary"]["uniqueIssues"] == 1
        assert run["summary"]["fieldChanges"] == 1
        assert run["summary"]["completed"] == run["summary"]["completedEvents"] == 1
        assert run["summary"]["reopened"] == run["summary"]["reopenedEvents"] == 1
        module = run["tree"][0]
        task, epic = module["children"][0], module["children"][0]["children"][0]
        for node in (module, task, epic):
            assert node["counts"]["totalEvents"] == 4
            assert node["counts"]["uniqueIssues"] == 1
        assert epic["children"][1]["counts"]["totalEvents"] == 0
        assert epic["children"][1]["contextOnly"] is True
        assert "aiSummary" not in run
        assert run["metrics"] == {"restRequests": 42}
        assert run["coverage"] == {"complete": True}
    assert m.store.read("saved") == stored_before
    assert provider.calls == []


def test_comment_presentation_does_not_mutate_the_supplied_snapshot(tmp_path):
    m = manager(tmp_path, Provider())
    snapshot, checkpoint = store_run(m, [event("spent", field="timespent"), event("comment", kind="comment")])
    original, original_checkpoint = deepcopy(snapshot), deepcopy(checkpoint)

    result = present_comments(snapshot, m.client, checkpoint)

    assert [e["id"] for e in result["events"]] == ["comment"]
    assert result["events"][0]["bodyText"] == "timespent worklogid Resolved Closed"
    assert snapshot == original
    assert checkpoint == original_checkpoint


def test_new_runs_filter_after_interpreting_status_history_and_keep_raw_checkpoint(tmp_path):
    class BookkeepingProvider(Provider):
        def get_json(self, path, params=None):
            result = super().get_json(path, params)
            if path.endswith("/status"):
                return [{"id": "1", "name": "Open", "statusCategory": {"key": "new"}},
                        {"id": "2", "name": "Resolved", "statusCategory": {"key": "done"}},
                        {"id": "3", "name": "Closed", "statusCategory": {"key": "done"}}]
            if path.endswith("/search"):
                raw = result["issues"][0]
                raw["fields"]["status"] = {"id": "3", "name": "Closed", "statusCategory": {"key": "done"}}
                raw["changelog"] = {"total": 1, "startAt": 0, "histories": [{"id": "history",
                    "created": "2026-11-11T10:00:00+0900", "author": {"name": "a"}, "items": [
                        {"field": "status", "from": "1", "to": "2", "fromString": "Open", "toString": "Resolved"},
                        {"field": "status", "from": "2", "to": "3", "fromString": "Resolved", "toString": "Closed"},
                        {"field": "timespent", "from": "0", "to": "3600"},
                        {"field": "summary", "fromString": "old", "toString": "new"}]}]}
            return result

    provider = BookkeepingProvider()
    m = manager(tmp_path, provider)
    run = m.create({"startDate": "2026-11-10", "endDate": "2026-11-12"}, background=False)

    assert run["status"] == "complete"
    assert run["summary"]["completed"] == 1
    assert run["summary"]["fieldChanges"] == 1
    assert all(e.get("field") != "timespent" for e in run["events"])
    assert not any(e.get("from") == "Resolved" and e.get("to") == "Closed" for e in run["events"])
    assert m.list()[0]["summary"] == run["summary"]
    checkpoint = m.store.read(run["id"])[3]
    assert len(checkpoint["sources"]["P-1"]["history"]["histories"][0]["items"]) == 4
    assert run["metrics"]["restRequests"] == len(provider.calls)


@pytest.mark.parametrize("root_events", ["present", "missing", "null"])
def test_regenerated_ai_summary_survives_reads_with_only_visible_evidence(tmp_path, monkeypatch, root_events):
    from app.tracking import summary

    provider = Provider()
    m = manager(tmp_path, provider)
    store_run(m, [event("ignored", field="timespent"), event("kept", kind="comment")])
    _, _, stored, checkpoint = m.store.read("saved")
    if root_events == "missing":
        stored.pop("events")
    elif root_events == "null":
        stored["events"] = None
    stored["metrics"]["llmTokens"] = 0
    m.store.save("saved", stored, checkpoint)
    stored_before = m.store.read("saved")
    payloads = []

    def invoke(payload):
        payloads.append(payload)
        return {"items": [{"text": "A useful comment", "evidenceIds": ["kept"]}]}, {"totalTokens": 0}

    monkeypatch.setattr(summary, "_ready", lambda: (True, ""))
    monkeypatch.setattr(summary, "_invoke", invoke)
    result = summary.summarize_snapshot(m.get("saved"))
    m.record_summary("saved", result)

    assert [e["id"] for e in payloads[0]["events"]] == ["kept"]
    assert payloads[0]["summary"]["totalEvents"] == 1
    assert payloads[0]["summary"]["fieldChanges"] == 0
    assert m.get("saved")["aiSummary"] == result
    assert m.list()[0]["aiSummary"] == result
    assert m.store.read("saved")[3] == stored_before[3]
    assert m.store.read("saved")[2].get("events") == stored_before[2].get("events")
    assert provider.calls == []


def test_filter_only_matches_known_fields_and_keeps_unrelated_comments(tmp_path):
    m = manager(tmp_path, Provider())
    events = [event("ignored", field="timespent"),
              event("custom", field="customfield_timespent"),
              event("description", field="description", after="timeestimate worklogid"),
              event("comment", kind="comment", field="timespent"),
              event("edit", kind="commentEdited", field="worklogid")]
    store_run(m, events)

    run = m.get("saved")

    assert {e["id"] for e in run["events"]} == {"custom", "description", "comment", "edit"}
    assert run["summary"]["comments"] == run["summary"]["commentEdits"] == 1
    assert run["summary"]["fieldChanges"] == 2


@pytest.mark.parametrize("root_events", ["missing", "null"])
@pytest.mark.parametrize("include_ignored", [False, True])
def test_legacy_issue_events_are_filtered_when_the_root_array_is_missing(tmp_path, root_events, include_ignored):
    m = manager(tmp_path, Provider())
    earlier = {**event("earlier"), "date": "2026-11-10T10:00:00+09:00"}
    events = [earlier, event("kept", kind="comment")]
    if include_ignored:
        events.append(event("ignored", field="timespent"))
    store_run(m, events)
    _, _, snapshot, checkpoint = m.store.read("saved")
    if root_events == "missing":
        snapshot.pop("events")
    else:
        snapshot["events"] = None
    snapshot["issues"][0]["events"].append(deepcopy(earlier))
    m.store.save("saved", snapshot, checkpoint)
    stored_before = m.store.read("saved")

    run = m.get("saved")

    assert [e["id"] for e in run["events"]] == ["kept", "earlier"]
    assert {e["id"] for e in run["issues"][0]["events"]} == {"kept", "earlier"}
    assert set(run["issues"][0]["eventIds"]) == {"kept", "earlier"}
    assert run["summary"]["totalEvents"] == 2
    assert run["tree"][0]["counts"]["totalEvents"] == 2
    assert m.store.read("saved") == stored_before


def test_an_explicitly_empty_root_event_array_stays_empty(tmp_path):
    m = manager(tmp_path, Provider())
    store_run(m, [event("kept", kind="comment")])
    _, _, snapshot, checkpoint = m.store.read("saved")
    snapshot["events"] = []
    m.store.save("saved", snapshot, checkpoint)

    assert m.get("saved")["events"] == []
