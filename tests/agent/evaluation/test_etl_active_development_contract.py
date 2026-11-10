"""Ongoing inventory follows public status time, not private work intervals."""
from datetime import datetime

import pytest

from tools.codex_mcp_eval.etl.events import Event
from tools.codex_mcp_eval.etl.oracle import EventOracle
from tools.codex_mcp_eval.etl.world import build_etl_world


def at(value):
    return datetime.fromisoformat(value + "+09:00")


def issue(key="DL-1", *, status="In Progress", created="2026-01-01T09:00:00",
          public=True, stream="development", private_end="2026-01-20T00:00:00"):
    return Event(key + ":created", key, stream, "issue", at(created), at(created), {
        "initial_status": status, "active_until": at(private_end),
        "active_intervals": [(at("2026-01-01T09:00:00"), at(private_end))],
    }, (f"jira:{key}:description",) if public else ())


def transition(value, when, *, key="DL-1", recorded=None, public=True):
    return Event(key + ":status:" + when, key, "development", "field", at(when),
                 at(recorded or when), {"field": "status", "value": value},
                 (f"jira:{key}:status:{when}",) if public else ())


def inventory(*events, start="2026-01-10T00:00:00", end="2026-01-11T00:00:00",
              observed="2026-01-20T00:00:00"):
    return EventOracle(tuple(events), at(observed)).active_development(at(start), at(end))


@pytest.fixture(scope="module")
def bundle():
    return build_etl_world()


def test_public_status_keeps_rework_active_until_the_actual_resolution_minute(bundle):
    public = bundle.public_sources["jira:DL-21017:history:1000"]
    assert public["recordedAt"] == "2026-07-20T16:05:00+09:00"
    assert public["items"][0]["toString"] == "Resolved"
    assert "DL-21017" in bundle.oracle.active_development(
        at("2026-07-20T16:00:00"), at("2026-07-20T16:05:00"))
    assert "DL-21017" not in bundle.oracle.active_development(
        at("2026-07-20T16:05:00"), at("2026-07-20T16:06:00"))


def test_all_twelve_monthly_inventory_counts_remain_unchanged(bundle):
    expected = (99, 107, 107, 107, 107, 107, 107, 107, 107, 106, 106, 108)
    actual = tuple(len(bundle.oracle.active_development(start, end))
                   for start, end in bundle.spec.months)
    assert actual == expected


@pytest.mark.parametrize("status,created,change,expected", [
    ("Open", "2026-01-01T09:00:00", ("In Progress", "2026-01-10T00:00:00"), ["DL-1"]),
    ("Open", "2026-01-01T09:00:00", ("In Progress", "2026-01-11T00:00:00"), []),
    ("In Progress", "2026-01-01T09:00:00", ("Resolved", "2026-01-10T00:00:00"), []),
    ("In Progress", "2026-01-01T09:00:00", ("Resolved", "2026-01-11T00:00:00"), ["DL-1"]),
    ("In Progress", "2026-01-11T00:00:00", None, []),
    ("Resolved", "2026-01-01T09:00:00", ("Reopened", "2026-01-10T00:00:00"), ["DL-1"]),
    ("Resolved", "2026-01-01T09:00:00", ("Reopened", "2026-01-11T00:00:00"), []),
    ("Open", "2026-01-01T09:00:00", None, []),
], ids=["start-inclusive", "start-at-end-excluded", "closed-at-start-excluded",
        "closed-at-end-included", "created-at-end-excluded", "reopened-included",
        "reopened-at-end-excluded", "open-wait-excluded"])
def test_half_open_status_membership(status, created, change, expected):
    events = [issue(status=status, created=created)]
    if change:
        events.append(transition(*change))
    assert inventory(*events) == expected


def test_ongoing_status_does_not_end_at_a_private_authored_work_interval():
    assert inventory(issue(private_end="2026-01-02T00:00:00")) == ["DL-1"]


def test_hidden_status_and_private_work_notes_do_not_change_public_inventory():
    private_note = Event("private", "DL-2", "development", "development_private",
        at("2026-01-09T00:00:00"), at("2026-01-09T00:00:00"),
        {"field": "status", "value": "In Progress"}, ())
    assert inventory(
        issue(), issue("DL-2", status="Open"), issue("DL-3", public=False),
        issue("DL-4", stream="operations"), private_note,
        transition("Resolved", "2026-01-09T00:00:00", public=False),
        transition("In Progress", "2026-01-09T00:00:00", key="DL-2",
                   recorded="2026-01-21T00:00:00"),
    ) == ["DL-1"]


def test_late_public_status_uses_occurrence_time_not_publication_time():
    assert inventory(issue(), transition("Resolved", "2026-01-09T00:00:00",
                                         recorded="2026-01-12T00:00:00")) == []


def test_final_public_state_is_bounded_by_the_observation_horizon():
    events = (issue(), transition("Resolved", "2026-01-09T00:00:00",
                                  recorded="2026-01-12T00:00:00"))
    assert inventory(*events, start="2026-01-09T00:00:00", observed="2026-01-10T00:00:00") == ["DL-1"]
    assert inventory(*events, observed="2026-01-10T00:00:00") == []


def test_repeated_transitions_count_a_ticket_once_in_occurrence_order():
    events = (issue(status="Open"), transition("Resolved", "2026-01-15T00:00:00"),
              transition("In Progress", "2026-01-09T00:00:00"),
              transition("Reopened", "2026-01-16T00:00:00"))
    assert inventory(*events, end="2026-01-18T00:00:00") == ["DL-1"]
    assert inventory(*events, start="2026-01-15T00:00:00", end="2026-01-16T00:00:00") == []


@pytest.mark.parametrize("start,end", [
    ("2026-01-10T00:00:00", "2026-01-10T00:00:00"),
    ("2026-01-11T00:00:00", "2026-01-10T00:00:00"),
])
def test_inventory_requires_a_nonempty_period(start, end):
    with pytest.raises(ValueError, match="nonempty"):
        inventory(issue(), start=start, end=end)
