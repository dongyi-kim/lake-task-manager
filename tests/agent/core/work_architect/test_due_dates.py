"""Deadline parsing regressions runnable independently from the full agent draft suite."""

from datetime import date, timedelta

import pytest

pytest.importorskip("langgraph", reason="requirements-agent.txt 미설치")

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from app.agent.workflow.agents.work_architect import (  # noqa: E402
    _apply_relative_due_to_single_draft,
    _authoritative_explicit_due,
    _relative_due,
)


def _state(text: str, **extra) -> dict:
    return {"messages": [HumanMessage(content=text)], **extra}


def test_relative_due_is_computed_by_code_not_the_model():
    due = date.fromisoformat(_relative_due("마감 다음주 수요일로 미루고"))
    assert due.weekday() == 2 and due > date.today()
    friday = date.fromisoformat(_relative_due("이번 주 금요일까지"))
    assert friday.weekday() == 4 and friday >= date.today()
    assert _relative_due("그냥 미뤄줘") == ""
    assert _relative_due("내일까지") == (date.today() + timedelta(days=1)).isoformat()


def test_this_week_due_is_the_current_or_next_workweek_friday():
    today = date.today()
    friday = today - timedelta(days=today.weekday()) + timedelta(days=4)
    if friday < today:
        friday += timedelta(days=7)
    assert _relative_due("이번 주까지. 알아서") == friday.isoformat()
    assert _relative_due("금주까지 처리") == friday.isoformat()


def test_explicit_due_accepts_one_repeated_valid_date_across_request_and_followup():
    state = _state(
        "기한은 2026-09-30으로 확정해",
        request_text="파이프라인 1차 구현을 2026-09-30까지 완료하는 Task로 만들어줘",
        turn_continuation=True,
    )
    assert _authoritative_explicit_due(state) == "2026-09-30"


def test_explicit_due_ignores_an_unrelated_history_date_in_the_same_request():
    request = "2026-08-15 장애 후속 NDV Task를 만들어줘. 마감은 2026-09-30으로 해"
    state = _state(request, request_text=request)
    assert _authoritative_explicit_due(state) == "2026-09-30"


def test_explicit_due_does_not_choose_between_distinct_or_invalid_dates():
    ambiguous = _state(
        "마감은 2026-09-30 또는 기한은 2026-10-07 중 하나야",
        request_text="파이프라인 Task를 만들어줘",
        turn_continuation=True,
    )
    invalid = _state(
        "마감은 2026-02-30으로 해",
        request_text="파이프라인 Task를 만들어줘",
        turn_continuation=True,
    )
    for state in (ambiguous, invalid):
        items = [{"summary": "파이프라인 구현", "duedate": "2099-01-01"}]
        assert _authoritative_explicit_due(state) == ""
        assert _apply_relative_due_to_single_draft(state, items) == ""
        assert items[0]["duedate"] == ""


def test_explicit_due_ignores_dates_from_stale_topic_messages():
    state = {
        "request_text": "새 NDV 파이프라인 Task를 만들어줘. 알아서",
        "turn_continuation": False,
        "messages": [
            HumanMessage(content="이전 CDC Task 마감은 2026-08-31로 해줘"),
            AIMessage(content="CDC Task 초안을 정리했습니다."),
            HumanMessage(content="새 NDV 파이프라인 Task를 만들어줘. 알아서"),
        ],
    }
    assert _authoritative_explicit_due(state) == ""


def test_explicit_due_uses_only_frozen_request_and_current_continuation():
    state = {
        "request_text": "NDV 파이프라인 Task를 2026-09-30까지 만들어줘",
        "turn_continuation": True,
        "messages": [
            HumanMessage(content="이전 CDC Task 마감은 2026-08-31로 해줘"),
            AIMessage(content="CDC Task 초안을 정리했습니다."),
            HumanMessage(content="NDV 파이프라인 Task를 2026-09-30까지 만들어줘"),
            AIMessage(content="기한을 2026-09-30으로 확정할까요?"),
            HumanMessage(content="2026-09-30"),
        ],
    }
    assert _authoritative_explicit_due(state) == "2026-09-30"


def test_current_followup_due_supersedes_frozen_original_due():
    state = {
        "request_text": "NDV 파이프라인 Task를 2026-09-30까지 만들어줘",
        "turn_continuation": True,
        "messages": [
            HumanMessage(content="NDV 파이프라인 Task를 2026-09-30까지 만들어줘"),
            AIMessage(content="초안을 만들었습니다."),
            HumanMessage(content="마감은 2026-10-07로 바꿔줘"),
        ],
    }
    assert _authoritative_explicit_due(state) == "2026-10-07"


def test_title_only_third_turn_preserves_latest_typed_draft_due_not_frozen_due():
    state = {
        "request_text": "NDV 파이프라인 Task를 2026-09-30까지 만들어줘",
        "turn_continuation": True,
        "draft": {"items": [{
            "summary": "[ETL] NDV 파이프라인 구현", "type": "Task",
            "duedate": "2026-10-07",
        }]},
        "messages": [
            HumanMessage(content="NDV 파이프라인 Task를 2026-09-30까지 만들어줘"),
            AIMessage(content="마감 2026-09-30 초안을 만들었습니다."),
            HumanMessage(content="마감은 2026-10-07로 바꿔줘"),
            AIMessage(content="마감을 2026-10-07로 수정했습니다."),
            HumanMessage(content="제목만 [ETL] NDV 파이프라인 적재 구현으로 바꿔줘"),
        ],
    }
    projected = [{
        "summary": "[ETL] NDV 파이프라인 적재 구현", "type": "Task",
        "duedate": "2026-09-30",
    }]
    assert _authoritative_explicit_due(state) == "2026-10-07"
    assert _apply_relative_due_to_single_draft(state, projected) == "2026-10-07"
    assert projected[0]["duedate"] == "2026-10-07"


def test_duration_timebox_becomes_a_deterministic_due_date():
    assert _relative_due("기간은 2주 정도") == (date.today() + timedelta(days=14)).isoformat()


def test_relative_due_does_not_guess_across_multiple_creation_items():
    items = [
        {"summary": "[Catalog] A", "type": "Task", "duedate": "2099-01-01"},
        {"summary": "[Runtime] B", "type": "Task", "duedate": "2099-01-02"},
    ]
    applied = _apply_relative_due_to_single_draft(
        _state("이번 주 금요일까지 A와 B를 각각 만들어줘"), items,
    )
    assert applied == ""
    assert [item["duedate"] for item in items] == ["2099-01-01", "2099-01-02"]
