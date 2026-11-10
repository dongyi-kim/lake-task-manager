"""Hand-authored SP expectations; never derive oracle answers from product reducers."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from tools.codex_mcp_eval.etl.events import Event
from tools.codex_mcp_eval.etl.oracle import EventOracle
from tools.codex_mcp_eval.etl.projection import project


def dt(day):
    return datetime.fromisoformat(f"2026-01-{day:02d}T00:00:00+09:00")


def issue(key, *, kind="Task", sp=0, status="In Progress", epic=None,
          parent=None, module="ETL", labels=(), omit_sp=False):
    payload = {"type": kind, "sp": sp, "initial_status": status, "epic": epic,
               "parent": parent, "module": module, "assignee": "etl.user00",
               "summary": key, "description": "Authored evidence", "labels": list(labels),
               "active_until": dt(20)}
    if omit_sp:
        payload.pop("sp")
    return Event(key, key, "development_epics" if kind == "Epic" else "development",
                 "issue", dt(1), dt(1), payload, (f"jira:{key}:description",))


def field(key, name, value, day, *, recorded=None):
    identity = f"{key}:{name}:{day}"
    return Event(identity, key, "development", "field", dt(day), dt(recorded or day),
                 {"field": name, "value": value}, (f"jira:{identity}",))


def mapping(weights, day=1):
    return Event(f"mapping:{day}", "", "development", "mapping", dt(day), dt(day),
                 {"wbs": "ETL-1", "epics": weights, "document": "mapping",
                  "page_id": "1", "title": "Mapping", "version": day, "text": "Public mapping"},
                 (f"confluence:1:version:{day}",))


def snapshot(*events, at=10, known=None):
    return EventOracle(tuple(events), dt(20)).development(
        dt(at), knowledge_at=dt(known) if known else None)


@pytest.mark.parametrize("kind,module", [
    ("Bug", "ETL"), ("Ops", "ETL"), ("운영", "ETL"), ("Task", "사용자 VoC"),
])
def test_excluded_work_has_no_sp_contribution_but_remains_an_issue(kind, module):
    events = (issue("DL-1", kind="Epic"),
              issue("DL-2", epic="DL-1", sp=1, status="Closed"),
              issue("DL-3", kind=kind, module=module, epic="DL-1", sp=13))
    result = snapshot(*events)
    assert result["epics"]["DL-1"] == {"sp_total": 1, "sp_done": 1, "percent": 100.0}
    assert result["issues"]["DL-3"]["type"] == kind
    # DL-2 was already Closed at creation; only DL-3 belongs to ongoing inventory.
    # Exclusion from SP must not exclude that ongoing Bug/Ops/VoC work item.
    assert EventOracle(events, dt(20)).active_development(dt(1), dt(10)) == ["DL-3"]


@pytest.mark.parametrize("sp,omit,wanted", [(None, False, 1), (0, True, 1), (0, False, 0)])
def test_missing_sp_defaults_to_one_but_explicit_zero_survives(sp, omit, wanted):
    result = snapshot(issue("DL-1", kind="Epic"),
                      issue("DL-2", epic="DL-1", sp=sp, omit_sp=omit, status="Resolved"),
                      issue("DL-3", epic="DL-1", sp=3))
    assert result["epics"]["DL-1"] == {
        "sp_total": 3 + wanted, "sp_done": wanted, "percent": 25.0 if wanted else 0.0}


def test_mock_sp_remains_in_denominator_and_subtask_is_not_counted_twice():
    result = snapshot(issue("DL-1", kind="Epic"),
                      issue("DL-2", epic="DL-1", sp=2, status="Resolved"),
                      issue("DL-3", epic="DL-1", sp=3, labels=("mock",)),
                      issue("DL-4", kind="Sub-Task", epic="DL-1", parent="DL-2", sp=20,
                            status="Closed"))
    assert result["epics"]["DL-1"] == {"sp_total": 5, "sp_done": 2, "percent": 40.0}
    assert result["issues"]["DL-2"]["child_completion_percent"] == 100.0


@pytest.mark.parametrize("children", [(), (issue("DL-2", epic="DL-1", sp=0),)])
def test_known_empty_or_zero_sp_epic_is_zero_not_unknown(children):
    result = snapshot(issue("DL-1", kind="Epic"), *children,
                      mapping({"DL-1": 1}))
    assert result["epics"]["DL-1"] == {"sp_total": 0, "sp_done": 0, "percent": 0.0}
    assert result["wbs"]["ETL-1"]["percent"] == 0.0
    missing = snapshot(issue("DL-1", kind="Epic"), *children, mapping({"DL-404": 1}))
    assert missing["wbs"]["ETL-1"]["percent"] is None  # Missing Epic is still unknown.


def test_percentage_uses_raw_sp_before_rounding_display_totals():
    result = snapshot(issue("DL-1", kind="Epic"),
                      issue("DL-2", epic="DL-1", sp=1.04, status="Closed"),
                      issue("DL-3", epic="DL-1", sp=0.02))
    assert result["epics"]["DL-1"] == {"sp_total": 1.1, "sp_done": 1.0, "percent": 98.1}


def test_wbs_weights_rounded_epic_percentages_then_rounds_result():
    result = snapshot(issue("DL-1", kind="Epic"), issue("DL-2", kind="Epic"),
                      issue("DL-10", epic="DL-1", sp=1, status="Closed"),
                      issue("DL-11", epic="DL-1", sp=1),
                      issue("DL-20", epic="DL-2", sp=1, status="Closed"),
                      issue("DL-21", epic="DL-2", sp=20), mapping({"DL-1": 1, "DL-2": 2}))
    assert result["epics"]["DL-2"]["percent"] == 4.8
    # (50 + 2*4.8)/3 rounds to19.9; weighting unrounded1/21 instead gives19.8.
    assert result["wbs"]["ETL-1"]["percent"] == 19.9


def test_historical_status_knowledge_parent_epic_and_mapping_are_preserved():
    events = (issue("DL-1", kind="Epic"), issue("DL-2", kind="Epic"),
              issue("DL-10", kind="Bug", epic="DL-1", sp=8),
              issue("DL-20", epic="DL-1", sp=1),
              issue("DL-11", kind="Sub-Task", parent="DL-10", status="Closed"),
              field("DL-20", "status", "Closed", 2, recorded=8),
              field("DL-20", "epic", "DL-2", 4), field("DL-11", "parent", "DL-20", 5),
              mapping({"DL-1": 3, "DL-2": 1}), mapping({"DL-1": 2, "DL-2": 1}, 6))
    known = snapshot(*events, at=4, known=4)
    reconstructed = snapshot(*events, at=4)
    current = snapshot(*events)
    assert known["epics"]["DL-1"]["percent"] == 0.0
    assert reconstructed["epics"]["DL-1"]["percent"] == 100.0
    assert reconstructed["issues"]["DL-11"]["parent"] == "DL-10"
    assert reconstructed["issues"]["DL-10"]["child_completion_percent"] == 100.0
    assert current["issues"]["DL-11"]["parent"] == "DL-20"
    assert current["issues"]["DL-10"]["child_completion_percent"] is None
    assert current["epics"]["DL-1"]["percent"] == 0.0  # Only the excluded Bug remains.
    assert current["epics"]["DL-2"]["percent"] == 100.0
    assert known["wbs"]["ETL-1"]["epic_weights"] == {"DL-1": 3, "DL-2": 1}
    assert current["wbs"]["ETL-1"]["epic_weights"] == {"DL-1": 2, "DL-2": 1}
    assert current["wbs"]["ETL-1"]["percent"] == 33.3


def test_projected_plan_is_accepted_by_actual_wbs_dashboard_builder():
    from app.domain.rollup import build

    spec = SimpleNamespace(start=dt(1), end=dt(20), observed_until=dt(20))
    world, sources, documents = project((mapping({"DL-1": 2, "DL-2": 1}),), spec)
    dashboard = build(world.plan, {
        "DL-1": {"doneSp": 586, "totalSp": 586, "progressPct": 100},
        "DL-2": {"doneSp": 618, "totalSp": 643, "progressPct": 96.1},
    }, generated_at="2026-01-20")
    assert dashboard["wbs"][0]["moduleId"] == "ETL"
    assert dashboard["wbs"][0]["progressPct"] == 98.7
    assert world.plan["wbs"][0]["moduleId"] == "ETL"  # Existing alias remains available.
    assert len(sources) == 1 and len(documents) == 1
