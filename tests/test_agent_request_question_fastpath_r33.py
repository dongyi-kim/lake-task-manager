"""Typed Request-question ownership and the one-slot graph fast path."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from app.agent.workflow import graph as G
from app.agent.workflow.agents.auditor import Auditor
from app.agent.workflow.agents.people_advisor import PeopleAdvisor
from app.agent.workflow.agents.query_specialist import QuerySpecialist
from app.agent.workflow.agents.request_architect import RequestArchitect, SCHEMA
from app.agent.workflow.agents.work_architect import WorkArchitect
from app.agent.workflow.state import Intent


def _write_task(instruction: str) -> dict:
    return {
        "id": "task-1",
        "kind": "ticket",
        "instruction": instruction,
        "depends_on": [],
        "write_intent": True,
        "completion_criteria": ["사용자가 검토할 수 있는 초안을 제시한다"],
    }


def _planner_output(text: str, *, sufficient: bool = False,
                    questions: list[dict] | None = None) -> dict:
    return {
        "intent": Intent.PLAN_WORK,
        "keywords": ["데이터 품질"],
        "module": "",
        "mentioned_keys": [],
        "sufficient": sufficient,
        "goal": text,
        "tasks": [_write_task(text)],
        "request_questions": list(questions or []),
        "blocking_questions": [
            str(row.get("question") or "") for row in (questions or [])
        ],
        "assumptions": [],
    }


def test_request_question_schema_has_bounded_slot_enum():
    question = SCHEMA["properties"]["request_questions"]

    assert question["maxItems"] == 3
    assert set(question["items"]["properties"]["field"]["enum"]) == {
        "target", "action", "scope", "acceptance", "other",
    }


def test_one_missing_target_projects_one_question_contract_before_retrieval():
    text = "데이터 품질 작업 하나 만들어줘. 나머지는 알아서"
    output = _planner_output(text, questions=[
        {"question": "어느 데이터 자산을 대상으로 할까요?", "field": "target"},
        {"question": "1차 범위는 어디까지로 할까요?", "field": "scope"},
        {"question": "완료 기준을 더 지정할까요?", "field": "acceptance"},
    ])

    got = RequestArchitect().apply(
        {"messages": [HumanMessage(content=text)]}, output,
    )

    assert len(got["questions"]) == 1
    assert got["questions"][0] == {
        "contract": "question.v1",
        "question": "어느 데이터 자산을 대상으로 할까요?",
        "kind": "text",
        "options": [],
        "field": "target",
        "ownership": "user_required",
        "required_input": True,
        "why_required": "생성할 작업의 대상을 식별할 수 없음",
        "fallback": "",
    }
    assert got["request_plan"]["blocking_questions"] == [
        "어느 데이터 자산을 대상으로 할까요?",
        "1차 범위는 어디까지로 할까요?",
        "완료 기준을 더 지정할까요?",
    ]
    assert G.route_after_request_architect(got) == "respond"


def test_two_distinct_required_fields_keep_the_existing_semantic_path():
    text = "작업 하나 만들어줘"
    got = RequestArchitect().apply(
        {"messages": [HumanMessage(content=text)]},
        _planner_output(text, questions=[
            {"question": "어느 대상을 바꿀까요?", "field": "target"},
            {"question": "그 대상에 어떤 작업을 할까요?", "field": "action"},
        ]),
    )

    assert not got.get("questions")
    assert G.route_after_request_architect(got) == "investigate"


def test_optional_fields_never_acquire_graph_blocking_authority():
    text = "Workbench 도움말 팝업 Task 초안을 만들어줘"
    got = RequestArchitect().apply(
        {"messages": [HumanMessage(content=text)]},
        _planner_output(text, questions=[
            {"question": "1차 범위를 더 좁힐까요?", "field": "scope"},
            {"question": "완료 기준을 더 지정할까요?", "field": "acceptance"},
        ]),
    )

    assert not got.get("questions")
    assert G.route_after_request_architect(got) == "investigate"


def test_sufficient_concrete_create_rejects_a_model_mislabeled_target_question():
    """A model cannot relabel an optional preference as a missing target."""
    text = "Workbench 쿼리 편집기에 단축키 도움말 팝업 Task를 만들어줘"
    got = RequestArchitect().apply(
        {"messages": [HumanMessage(content=text)]},
        _planner_output(text, sufficient=True, questions=[{
            "question": "팝업의 세부 위치와 단축키 목록을 정할까요?",
            "field": "target",
        }]),
    )

    assert not got.get("questions")
    assert G.route_after_request_architect(got) == "investigate"


def test_resolved_typed_target_continuation_does_not_repeat_the_interview():
    original = "데이터 품질 작업 하나 만들어줘. 나머지는 알아서"
    answer = "orders_daily 테이블을 대상으로 해줘"
    prior_plan = {
        "goal": original,
        "tasks": [_write_task(original)],
        "request_questions": [{
            "question": "어느 데이터 자산을 대상으로 할까요?",
            "field": "target",
        }],
        "blocking_questions": ["어느 데이터 자산을 대상으로 할까요?"],
        "assumptions": [],
    }
    state = {
        "messages": [HumanMessage(content=answer)],
        "turn_continuation": True,
        "intent": Intent.PLAN_WORK,
        "request_text": original,
        "request_plan": prior_plan,
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": original,
            "intent": Intent.PLAN_WORK,
            "action": "create",
            "target_keys": [],
            "outcome_ids": ["task-1"],
            "decisions": [{
                "field": "target",
                "value": "orders_daily 테이블",
                "source": "interview_answer",
            }],
        },
    }

    got = RequestArchitect().apply(
        state,
        _planner_output(answer, questions=[{
            "question": "어느 데이터 자산을 대상으로 할까요?",
            "field": "target",
        }]),
    )

    assert not got.get("questions")
    assert G.route_after_request_architect(got) != "respond"


def test_graph_uses_only_request_classification_for_one_required_slot(monkeypatch):
    text = "데이터 품질 작업 하나 만들어줘. 나머지는 알아서"
    output = _planner_output(text, questions=[{
        "question": "어느 데이터 자산을 대상으로 할까요?",
        "field": "target",
    }])
    semantic_calls: list[str] = []

    def request_node(self):
        def run(state):
            semantic_calls.append("request_architect")
            return self.apply(state, output)
        return run

    def forbidden_node(role: str):
        def factory(_self):
            def run(_state):
                raise AssertionError(f"{role} must not run for a typed one-slot interview")
            return run
        return factory

    monkeypatch.setattr(RequestArchitect, "node", request_node)
    monkeypatch.setattr(QuerySpecialist, "node", forbidden_node("query_specialist"))
    monkeypatch.setattr(WorkArchitect, "node", forbidden_node("work_architect"))
    monkeypatch.setattr(PeopleAdvisor, "node", forbidden_node("people_advisor"))
    monkeypatch.setattr(Auditor, "node", forbidden_node("auditor"))

    state = G.build().invoke({
        "messages": [HumanMessage(content=text)],
        "thread_id": "request-question-fastpath",
        "approval_token": "",
        "comment_token": "",
        "draft": {},
        "change_plan": {},
    })

    assert semantic_calls == ["request_architect"]
    assert len(state["questions"]) == 1
    assert state["questions"][0]["contract"] == "question.v1"
    assert not (state.get("draft") or {}).get("items")
    assert not state.get("approval_token")
