"""Literal public reports constrain private expectations, including same-item plans."""
from copy import deepcopy
from datetime import datetime

import pytest

from tools.codex_mcp_eval.etl.oracle import EventOracle
from tools.codex_mcp_eval.etl.projection import project
from tools.codex_mcp_eval.etl.world import _Author, _development_reports, YearWorldSpec


def at(day):
    return datetime.fromisoformat(day + "T00:00:00+09:00")


@pytest.fixture
def reports():
    author = _Author(YearWorldSpec())
    for key in ("DL-21743", "DL-21744", "DL-21745", "DL-21747", "DL-21748", "DL-21749"):
        author.issue(key, "development", at("2026-10-01"), key, "Public scope")
    _development_reports(author)
    return author


def item(author, document, identifier):
    report = next(e for e in author.events if e.payload.get("document") == document)
    return next(row for row in report.payload["items"] if row["item_id"] == identifier)


def reduced(author, cutoff="2026-11-10", known=None):
    return EventOracle(tuple(author.events), author.spec.observed_until).development(
        at(cutoff), knowledge_at=at(known) if known else None)


def test_future_scenario_does_not_borrow_implementation_date(reports):
    row = item(reports, "etl-dev-daily-2026-10-15", "reverse-implementation")
    assert row["text"] == (
        "시간창 역전 처리: 10월 15일 역순 입력 정렬 구현을 마쳤다. "
        "극단 지연 시나리오는 다음 릴리즈 검토 안건이다.")
    implementation, plan = row["claims"]
    assert implementation["occurred_at"] == at("2026-10-15").replace(hour=10)
    assert plan["stage"] == "plan" and plan["occurred_at"] is None and plan["outcome"] is None


def test_inspection_does_not_assert_acceptance_or_resolve_ambiguous_tasks(reports):
    row = item(reports, "etl-dev-weekly-2026-10-26", "late-window")
    assert row["text"] == "배치 역전 보정의 지연 구간 보정을 이번 주 검수했다. etl.user07이 공동 검토했다."
    assert row["claims"][0]["outcome"] is None
    view = reduced(reports)
    match = next(r for r in view["report_correspondence"] if r["item_id"] == "late-window")
    assert match["candidate_keys"] == ["DL-21745", "DL-21747"] and match["status"] == "ambiguous"
    for key in match["candidate_keys"]:
        assert view["issues"][key]["progress"]["test_accepted_deliverables"] == []


def test_uniquely_mapped_inspection_still_does_not_prove_acceptance(reports):
    reports.events = [e for e in reports.events if e.issue_key != "DL-21747"]
    view = reduced(reports)
    match = next(r for r in view["report_correspondence"] if r["item_id"] == "late-window")
    assert match["status"] == "verified" and match["candidate_keys"] == ["DL-21745"]
    progress = view["issues"]["DL-21745"]["progress"]
    assert progress["test_accepted_deliverables"] == []
    assert progress["reported_acceptance_events"] == 0


def test_meeting_plan_is_reported_with_same_item_scope_not_acceptance(reports):
    row = item(reports, "etl-dev-weekly-2026-10-26", "reverse-test")
    assert row["text"] == "시간창 역전 처리: 10월 22일 역순 재시도 시험을 수용했다. 인수 회의는 다음 주 예정이다."
    plan = next(c for c in row["claims"] if c["stage"] == "plan")
    assert (plan["deliverable"], plan["occurred_at"], plan["artifact"], plan["outcome"]) == ("인수 회의", None, None, None)
    before = reduced(reports, "2026-10-27", "2026-10-27")
    assert not any(r["claim"]["deliverable"] == "인수 회의" for r in before["report_correspondence"])
    for view in (reduced(reports, "2026-11-01", "2026-11-01"),
                 reduced(reports, "2026-11-01"), reduced(reports)):
        match = next(r for r in view["report_correspondence"] if r["claim"]["deliverable"] == "인수 회의")
        assert match["status"] == "verified" and match["candidate_keys"] == ["DL-21744"]
        assert "same_item_companion" in match["matched_dimensions"]
        assert len(match["proof_sources"]) == 2
        progress = view["issues"]["DL-21744"]["progress"]
        assert "인수 회의" not in progress["planned_deliverables"]
        assert "인수 회의" not in progress["completed_deliverables"]
        assert progress["reported_acceptance_events"] in (1, 2)


@pytest.mark.parametrize("variant", ["multiple_tasks", "cross_item", "unwritten_plan", "unwritten_companion", "plan_only", "future_companion", "cross_environment"])
def test_plan_inheritance_needs_literal_same_item_companion_without_competing_tasks(reports, variant):
    row = item(reports, "etl-dev-weekly-2026-10-26", "reverse-test")
    plan = {"deliverable": "인수 회의", "stage": "plan", "occurred_at": None, "artifact": None, "outcome": None}
    row["claims"] = [row["claims"][0], plan]
    if variant == "multiple_tasks":
        row["context"]["scopes"].append("배치 역전 보정")
        row["text"] += " 지연 구간 보정을 검수했다."
        row["claims"].append({**plan, "deliverable": "지연 구간 보정", "stage": "test_acceptance"})
    elif variant == "cross_item":
        row["claims"] = [plan]
    elif variant == "unwritten_plan":
        row["text"] = "시간창 역전 처리: 10월 22일 역순 재시도 시험을 수용했다."
    elif variant == "unwritten_companion":
        row["text"] = "시간창 역전 처리: 인수 회의는 다음 주 예정이다."
    elif variant == "plan_only":
        row["claims"][0]["stage"] = "plan"
    elif variant == "future_companion":
        row["claims"][0]["occurred_at"] = at("2026-12-01")
    else:
        row["context"]["environment"] = "test"
    view = reduced(reports)
    match = next(r for r in view["report_correspondence"] if r["claim"]["deliverable"] == "인수 회의")
    if variant == "multiple_tasks":
        assert match["status"] == "ambiguous" and match["candidate_keys"] == ["DL-21744", "DL-21745", "DL-21747"]
    else:
        assert match["status"] == "unresolved" and match["candidate_keys"] == []
    assert "인수 회의" not in view["issues"]["DL-21744"]["progress"]["planned_deliverables"]


def test_unmatched_acceptance_cannot_inherit_companion_scope(reports):
    row = item(reports, "etl-dev-weekly-2026-10-26", "reverse-test")
    row["text"] += " 인수 회의를 수용했다."
    row["claims"].append({"deliverable": "인수 회의", "stage": "final_acceptance",
                          "occurred_at": None, "artifact": None, "outcome": "accepted"})
    matches = [r for r in reduced(reports)["report_correspondence"]
               if r["claim"]["deliverable"] == "인수 회의" and r["claim"]["stage"] == "final_acceptance"]
    assert len(matches) == 1 and matches[0]["status"] == "unresolved"
    assert matches[0]["candidate_keys"] == []


def test_private_claim_annotations_do_not_change_public_projection(reports):
    stripped = deepcopy(reports.events)
    for event in stripped:
        if event.kind == "development_report":
            for row in event.payload["items"]:
                row["claims"] = []
    public, documents, _ = project(tuple(reports.events), reports.spec)
    plain, plain_documents, _ = project(tuple(stripped), reports.spec)
    assert public.issues == plain.issues
    assert public.plan == plain.plan
    assert documents == plain_documents


def test_existing_correction_history_and_undated_multiplicity_are_preserved(reports):
    then = reduced(reports, "2026-11-01", "2026-11-01")
    rebuilt = reduced(reports, "2026-11-01")
    now = reduced(reports)
    assert then["issues"]["DL-21743"]["progress"]["final_accepted_deliverables"] == ["계약 지문 비교기"]
    assert rebuilt["issues"]["DL-21743"]["progress"]["final_accepted_deliverables"] == []
    assert rebuilt["issues"]["DL-21743"]["progress"]["planned_deliverables"] == []
    assert now["issues"]["DL-21743"]["progress"]["planned_deliverables"] == ["추가 합계 검증"]
    assert now["issues"]["DL-21749"]["progress"]["reported_acceptance_events"] is None
    assert now["issues"]["DL-21749"]["progress"]["duplicate_status"] == "unknown"
