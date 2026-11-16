"""Current completion is snapshot context, never a period activity event."""
from copy import deepcopy
from datetime import datetime

import pytest

from app.tracking.analysis import analyze_issue, event_counts


CATEGORIES = {"1": "new", "2": "done", "3": "done", "4": "indeterminate",
              "open": "new", "resolved": "done", "closed": "done"}


def transition(at, before="1", after="2", ident="h1"):
    return {"id": ident, "created": at, "author": {"name": "other"},
            "items": [{"field": "status", "from": before, "to": after}]}


def raw_issue(*history, current="2"):
    return {"key": "P-1", "_trackingFetchedAt": datetime.fromisoformat("2026-11-17T12:00:00+09:00").timestamp(),
            "fields": {"created": "2026-10-01T00:00:00+09:00", "updated": "2026-11-17T11:00:00+09:00",
                       "assignee": {"name": "other"},
                       "status": {"id": current, "name": {"1": "Open", "2": "Resolved", "3": "Closed"}.get(current),
                                  "statusCategory": {"key": "done" if current != "1" else "new"}}},
            "changelog": {"startAt": 0, "total": len(history), "histories": list(history)}}


def analyze(raw, *, complete=True, selected=()):
    return analyze_issue(raw, [], datetime.fromisoformat("2026-11-10T00:00:00+09:00"),
                         datetime.fromisoformat("2026-11-13T00:00:00+09:00"), set(selected),
                         CATEGORIES, "epic", complete, True)


@pytest.mark.parametrize("at,period_events", [
    ("2026-11-09T23:59:59+09:00", 0),
    ("2026-11-11T10:00:00+09:00", 1),
    ("2026-11-16T10:00:00+09:00", 0),
])
def test_completion_context_uses_full_history_without_query_upper_clipping(at, period_events):
    issue, events, missing = analyze(raw_issue(transition(at)))
    assert issue["currentCompletion"] == {"at": at, "source": "completeStatusHistory"}
    assert len(events) == period_events
    assert event_counts(events)["completed"] == period_events
    assert missing == []


def test_done_to_done_preserves_boundary_and_recompletion_uses_latest_boundary():
    first = transition("2026-11-02T09:00:00+09:00")
    closed = transition("2026-11-03T09:00:00+09:00", "2", "3", "h2")
    issue, _, _ = analyze(raw_issue(first, closed, current="3"))
    assert issue["currentCompletion"]["at"] == first["created"]
    reopened = transition("2026-11-04T09:00:00+09:00", "3", "1", "h3")
    latest = transition("2026-11-16T09:00:00+09:00", "1", "2", "h4")
    issue, events, _ = analyze(raw_issue(first, closed, reopened, latest))
    assert issue["currentCompletion"]["at"] == latest["created"]
    assert events == []


def test_unattributable_completion_is_metadata_without_events_scope_or_counts():
    raw = raw_issue(transition("2026-11-11T10:00:00+09:00"))
    issue, events, missing = analyze(raw, selected={"selected"})
    assert issue["currentCompletion"]["at"] == "2026-11-11T10:00:00+09:00"
    assert events == issue["eventIds"] == issue["scopeReasons"] == []
    assert not issue["inScope"]
    assert event_counts(events)["completed"] == 0
    assert missing == []


@pytest.mark.parametrize("case", ["incomplete", "not_done", "no_boundary", "unknown", "bad_date",
    "disconnected", "final_mismatch", "same_time", "conflicting_category", "conflicting_duplicate",
    "newer_than_revision", "newer_than_fetch", "bad_revision", "no_observation_bound", "naive_date",
    "revision_after_fetch", "non_status_after_revision"])
def test_unproven_completion_is_omitted(case):
    at = "2026-11-11T10:00:00+09:00"
    raw = raw_issue(transition(at))
    complete = True
    if case == "incomplete":
        complete = False
    elif case == "not_done":
        raw["fields"]["status"]["statusCategory"]["key"] = "new"
    elif case == "no_boundary":
        raw = raw_issue(transition(at, "2", "3"), current="3")
    elif case == "unknown":
        raw = raw_issue(transition(at), transition("2026-11-12T10:00:00+09:00", "2", "unknown", "h2"))
    elif case == "bad_date":
        raw["changelog"]["histories"][0]["created"] = "not-a-date"
    elif case == "disconnected":
        raw = raw_issue(transition(at), transition("2026-11-12T10:00:00+09:00", "3", "2", "h2"))
    elif case == "final_mismatch":
        raw["fields"]["status"]["id"] = "3"
        raw["fields"]["status"]["name"] = "Closed"
    elif case == "same_time":
        raw = raw_issue(transition(at), transition(at, "2", "3", "h2"), current="3")
    elif case == "conflicting_category":
        raw["changelog"]["histories"][0]["items"][0]["toString"] = "Open"
    elif case == "conflicting_duplicate":
        duplicate = deepcopy(raw["changelog"]["histories"][0])
        duplicate["created"] = "2026-11-12T10:00:00+09:00"
        raw["changelog"]["histories"].append(duplicate)
    elif case == "newer_than_revision":
        raw["fields"]["updated"] = "2026-11-10T00:00:00+09:00"
    elif case == "newer_than_fetch":
        raw["_trackingFetchedAt"] = datetime.fromisoformat("2026-11-10T00:00:00+09:00").timestamp()
    elif case == "bad_revision":
        raw["fields"]["updated"] = "invalid"
    elif case == "no_observation_bound":
        raw["fields"].pop("updated")
        raw.pop("_trackingFetchedAt")
    elif case == "naive_date":
        raw["changelog"]["histories"][0]["created"] = "2026-11-11T10:00:00"
    elif case == "revision_after_fetch":
        raw["fields"]["updated"] = "2026-11-17T13:00:00+09:00"
    elif case == "non_status_after_revision":
        extra = transition("2026-11-17T11:30:00+09:00", ident="h2")
        extra["items"] = [{"field": "summary", "fromString": "a", "toString": "b"}]
        raw["changelog"]["histories"].append(extra)
    issue, _, _ = analyze(raw, complete=complete)
    assert "currentCompletion" not in issue


def test_missing_revision_can_use_actual_raw_fetch_time_but_never_query_upper():
    raw = raw_issue(transition("2026-11-16T10:00:00+09:00"))
    raw["fields"].pop("updated")
    issue, _, _ = analyze(raw)
    assert issue["currentCompletion"]["at"] == "2026-11-16T10:00:00+09:00"


def test_revision_alone_can_prove_context_and_analysis_preserves_source():
    raw = raw_issue(transition("2026-11-11T10:00:00+0900"))
    raw.pop("_trackingFetchedAt")
    original = deepcopy(raw)
    issue, _, _ = analyze(raw)
    assert issue["currentCompletion"] == {"at": "2026-11-11T10:00:00+09:00", "source": "completeStatusHistory"}
    assert raw == original


def test_later_nonstatus_history_also_invalidates_stale_raw_revision():
    raw = raw_issue(transition("2026-11-11T10:00:00+09:00"),
                    {"id": "h2", "created": "2026-11-17T11:01:00+09:00", "items": [{"field": "summary"}]})
    assert "currentCompletion" not in analyze(raw)[0]
