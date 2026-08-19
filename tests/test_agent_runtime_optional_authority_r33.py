"""Runtime-owned implementation choices must not become user-owned blockers.

These fixtures intentionally use a generic UI artifact.  The contract under test is the
authority boundary (user request -> request plan -> Work -> Auditor), not a product phrase.
"""

import os
import sys

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("langgraph", reason="requirements-agent.txt not installed")

from langchain_core.messages import HumanMessage  # noqa: E402

from app.agent import approval  # noqa: E402
from app.agent.workflow import graph as G  # noqa: E402
from app.agent.workflow.agents import auditor  # noqa: E402
from app.agent.workflow.agents.request_architect import RequestArchitect  # noqa: E402
from app.agent.workflow.agents.work_architect import (  # noqa: E402
    _delegated_question_is_blocking,
)
from app.agent.workflow.anchors import bind_single_outcome_contract  # noqa: E402
from app.agent.workflow.state import Intent  # noqa: E402


@pytest.fixture(autouse=True)
def _approval_isolation():
    approval.clear()
    yield
    approval.clear()


def _description(*, include_explicit_constraints: bool = False) -> str:
    constraint = (
        "<li>키보드로 닫을 수 있어야 하고 개인정보를 저장하면 안 됨</li>"
        if include_explicit_constraints else ""
    )
    return (
        "<h3>배경</h3><p>설정 편집기의 도움말 패널 요청을 관리한다.</p>"
        "<h3>작업 범위</h3><ul><li>포함: 도움말 패널 추가</li>"
        "<li>제외: 요청에 없는 연관 기능 변경</li>"
        f"{constraint}</ul><h3>완료 조건 (DoD)</h3><ul>"
        "<li>패널의 열기와 닫기를 UI 테스트로 확인한다</li>"
        "<li>승인된 범위와 구현 결과가 일치함을 리뷰한다</li></ul>"
    )


def _delegated_state(*, explicit_constraints: bool = False) -> dict:
    request = (
        "설정 편집기에 도움말 패널 하나 추가해줘. "
        + (
            "키보드로 닫을 수 있어야 하고 개인정보를 저장하면 안 돼. "
            if explicit_constraints else ""
        )
        + "나머지 구현 방식은 네가 정해."
    )
    expanded = (
        request
        + " 메뉴 항목 목록, 표시 위치, 호출 방식을 구현 범위로 명확히 정의한다."
    )
    return {
        "thread_id": "runtime-owned-authority",
        "request_text": request,
        "messages": [HumanMessage(content=request)],
        "request_plan": {
            "goal": "도움말 패널 작업 초안",
            "tasks": [{
                "id": "task-1",
                "kind": "ticket",
                "instruction": expanded,
                "depends_on": [],
                "write_intent": True,
                "completion_criteria": [
                    "메뉴 항목 목록, 표시 위치, 호출 방식이 본문에 명시됨",
                ],
            }],
            "blocking_questions": [],
            "assumptions": [
                "사용자가 지정하지 않은 표시 위치와 호출 방식은 기본값으로 정한다",
            ],
        },
        "draft": {
            "mode": "task",
            "items": [{
                "summary": "[UI] 설정 편집기 도움말 패널 추가",
                "type": "Task",
                "description": _description(
                    include_explicit_constraints=explicit_constraints,
                ),
            }],
        },
    }


def _optional_model_finding() -> dict:
    return {
        "index": 0,
        "check": "request",
        "finding_kind": "request_coverage",
        "field": "summary",
        "expected": "메뉴 항목 목록, 표시 위치, 호출 방식의 구현 범위 정의",
        "actual": "설정 편집기 도움말 패널 추가",
        "message": (
            "요청된 메뉴 항목 목록, 표시 위치, 호출 방식이 초안에 명시되지 않았습니다."
        ),
        "fix": "메뉴 항목 목록, 표시 위치, 호출 방식을 본문 또는 DoD에 명시하세요.",
    }


def test_single_write_plan_cannot_promote_model_added_details_to_authority():
    request = (
        "설정 편집기에 도움말 패널 하나 추가해줘. "
        "키보드로 닫혀야 하고 개인정보를 저장하면 안 돼. 나머지는 네가 정해."
    )
    projected = RequestArchitect().apply(
        {"messages": [HumanMessage(content=request)]},
        {
            "intent": Intent.PLAN_WORK,
            "goal": "도움말 패널 작업",
            "keywords": ["설정 편집기", "도움말 패널"],
            "tasks": [{
                "id": "task-1",
                "kind": "ticket",
                "instruction": (
                    "설정 편집기에 도움말 패널을 추가하고 메뉴 목록, 표시 위치, "
                    "호출 방식을 필수 구현 범위로 정의한다."
                ),
                "depends_on": [],
                "write_intent": True,
                "completion_criteria": ["세부 UI 사양을 모두 명시한다"],
            }],
        },
    )

    instruction = projected["request_plan"]["tasks"][0]["instruction"]
    assert instruction == request
    assert "키보드로 닫혀야" in instruction
    assert "개인정보를 저장하면 안 돼" in instruction


def test_planner_only_details_are_runtime_owned_under_explicit_delegation():
    state = _delegated_state()
    finding = _optional_model_finding()

    blocking, advice = auditor._partition_model_problems(state, [finding])

    assert blocking == []
    assert advice == [finding]


def test_planner_cannot_create_user_authority_without_a_delegation_phrase():
    state = _delegated_state()
    request = "설정 편집기에 도움말 패널 하나 추가해줘."
    state["request_text"] = request
    state["messages"] = [HumanMessage(content=request)]

    blocking, advice = auditor._partition_model_problems(
        state, [_optional_model_finding()],
    )

    assert blocking == []
    assert advice == [_optional_model_finding()]


def test_user_authored_acceptance_and_safety_constraints_remain_blocking():
    state = _delegated_state(explicit_constraints=False)
    request = (
        "설정 편집기에 도움말 패널 하나 추가해줘. 키보드로 닫을 수 있어야 하고 "
        "개인정보를 저장하면 안 돼. 나머지 구현 방식은 네가 정해."
    )
    state["request_text"] = request
    state["messages"] = [HumanMessage(content=request)]
    state["request_plan"]["tasks"][0]["instruction"] = (
        request + " 메뉴 항목 목록, 표시 위치, 호출 방식도 정한다."
    )
    finding = {
        "index": 0,
        "check": "request",
        "finding_kind": "missing_requirement",
        "field": "description",
        "expected": "키보드로 닫을 수 있어야 하고 개인정보를 저장하면 안 됨",
        "actual": "해당 완료 조건 없음",
        "message": (
            "사용자가 명시한 키보드 종료와 개인정보 비저장 조건이 누락됐습니다."
        ),
        "fix": "키보드로 닫을 수 있어야 하고 개인정보를 저장하면 안 된다는 조건을 넣으세요.",
    }

    blocking, advice = auditor._partition_model_problems(state, [finding])

    assert blocking == [finding]
    assert advice == []


def test_delegation_does_not_make_an_unknown_work_target_optional():
    state = _delegated_state()
    vague = "작업 하나 만들어줘. 나머지는 네가 정해."
    state["request_text"] = vague
    state["messages"] = [HumanMessage(content=vague)]

    blocking, advice = auditor._partition_model_problems(
        state, [_optional_model_finding()],
    )

    assert blocking
    assert advice == []


def test_runtime_optional_finding_cannot_reappear_as_a_synthetic_axis_failure(
        monkeypatch):
    state = _delegated_state()
    bind_single_outcome_contract(state, state["draft"])
    monkeypatch.setattr(auditor, "_machine_check", lambda _state: {
        "ok": True, "errors": [], "warnings": [], "text": "ok",
    })
    finding = _optional_model_finding()

    reviewed = auditor.Auditor().apply(state, {
        "grounded": True,
        "rule_compliant": True,
        "answers_request": False,
        "problems": [finding],
        "summary": "구현 세부가 누락됨",
    })

    assert reviewed["review"]["ok"] is True
    assert reviewed["review"]["problems"] == []
    assert reviewed["review"]["checks"]["answers_request"] is True
    assert G.route_after_auditor({**state, **reviewed}) == "propose"
    staged = G._propose({**state, **reviewed})
    assert staged["approval_token"]
    assert approval.peek(staged["approval_token"])["action"] == "create_tickets"


def test_work_question_contract_keeps_runtime_preferences_non_blocking():
    state = _delegated_state()
    optional = {
        "field": "presentation",
        "question": "표시 위치와 호출 방식을 어떻게 정할까요?",
        "required_input": True,
        "why_required": "구현 방식을 선택해야 합니다.",
    }
    assert _delegated_question_is_blocking(state, optional) is False

    vague = dict(state)
    vague["request_text"] = "작업 하나 만들어줘. 나머지는 네가 정해."
    vague["messages"] = [HumanMessage(content=vague["request_text"])]
    required = {
        "field": "target",
        "question": "어떤 대상을 바꾸는 작업인가요?",
        "required_input": True,
        "why_required": "실행할 대상을 식별할 수 없습니다.",
    }
    assert _delegated_question_is_blocking(vague, required) is True
