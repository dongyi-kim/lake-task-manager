from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from app.agent.workflow.agents.request_architect import RequestArchitect
from app.agent.workflow.agents.work_architect import _current_request_boundary_text
from app.agent.workflow.continuation import capture_continuation_decisions
from app.agent.workflow.session import _turn_start_patch
from app.agent.workflow.state import Intent


def _task(task_id: str, instruction: str, *, kind: str = "ticket") -> dict:
    return {
        "id": task_id,
        "kind": kind,
        "instruction": instruction,
        "depends_on": [],
        "write_intent": kind in {"ticket", "comment", "write"},
        "completion_criteria": ["사용자가 요청한 결과를 준비한다"],
    }


def _contract(*, root: str, intent: str, action: str,
              targets: list[str] | None = None,
              outcomes: list[str] | None = None) -> dict:
    return {
        "version": "continuation.v1",
        "root_request": root,
        "intent": intent,
        "action": action,
        "target_keys": list(targets or []),
        "outcome_ids": list(outcomes or []),
        "decisions": [],
    }


def _decision_map(patch: dict) -> dict[str, str]:
    return {
        row["field"]: row["value"]
        for row in patch["continuation_contract"]["decisions"]
    }


@pytest.mark.parametrize(("field", "utterance", "expected"), [
    ("target", "대상은 DL-100 말고 DL-200으로 해줘", "DL-200"),
    ("target", "대상을 DL-100에서 DL-200으로 바꿔", "DL-200"),
    ("duedate", "마감은 2026-09-01 대신 2026-10-01로 해줘", "2026-10-01"),
    ("duedate", "마감을 2026-09-01에서 2026-10-01로 변경해", "2026-10-01"),
    ("assignee", "담당자는 skcc.x1103 아니고 미할당으로 해줘", "미할당"),
    ("assignee", "담당자를 미할당에서 skcc.x1103으로 바꿔", "skcc.x1103"),
    ("parent", "부모는 DL-9201 말고 최상위 Task로 해줘", "최상위 Task"),
    ("parent", "부모를 최상위 Task에서 DL-9201로 변경해", "DL-9201"),
])
def test_scalar_corrections_use_the_textually_latest_typed_value(field, utterance, expected):
    decisions = capture_continuation_decisions(
        utterance,
        [{"field": field, "question": f"{field} 값을 알려 주세요", "required_input": True}],
    )

    assert {row["field"]: row["value"] for row in decisions}[field] == expected


def test_create_progressive_interview_accumulates_scope_due_and_target_decisions():
    """ASK2: a partial answer must enrich, not replace, the original create contract."""
    root = "데이터 품질 개선 작업 하나 만들어줘"
    plan = {
        "goal": "데이터 품질 개선 Task 생성",
        "tasks": [_task("quality-task", "데이터 품질 개선 Task 생성")],
        "blocking_questions": [],
        "assumptions": [],
    }
    prior = {
        "intent": Intent.PLAN_WORK,
        "request_text": root,
        "request_plan": plan,
        "questions": [{
            "field": "target",
            "question": "어떤 대상에 무엇을 해야 하는지 알려 주세요.",
            "required_input": True,
        }],
        "continuation_contract": _contract(
            root=root, intent=Intent.PLAN_WORK, action="create",
            outcomes=["quality-task"],
        ),
    }

    scope_due = "널 비율 체크만 이번에 하고, 나머지는 다음에. 이번 주까지. 알아서"
    second = _turn_start_patch(scope_due, prior)

    assert second["turn_continuation"] is True
    assert second["request_text"] == root
    assert second["request_plan"] == plan
    decisions = _decision_map(second)
    assert "널 비율" in decisions["scope"]
    assert "이번 주" in decisions["duedate"]
    assert decisions["scope"] != decisions["duedate"]
    assert "이번 주까지" not in decisions["scope"]
    assert decisions["duedate"] == "이번 주까지"

    target = "Lake 배치 적재 테이블 중 신규 등록 30개를 대상으로 해"
    third_prior = {
        **prior,
        **second,
        "questions": [{
            "field": "target",
            "question": "검증 대상을 알려 주세요.",
            "required_input": True,
        }],
    }
    third = _turn_start_patch(target, third_prior)

    assert third["turn_continuation"] is True
    assert third["request_plan"] == plan
    decisions = _decision_map(third)
    assert "널 비율" in decisions["scope"]
    assert "이번 주" in decisions["duedate"]
    assert "신규 등록 30개" in decisions["target"]
    boundary = _current_request_boundary_text({
        **third_prior,
        **third,
        "messages": [HumanMessage(content=target)],
    })
    assert all(value in boundary for value in ("데이터 품질", "널 비율", "이번 주", "신규 등록 30개"))
    assert boundary.count("이번 주까지") == 1


def test_meeting_comment_answer_preserves_comment_action_and_exact_targets():
    """MTG3: an identity answer plus 'continue comments' is not a new create request."""
    root = (
        "회의 결정사항을 관련 Task DL-9201, DL-9202 두 건의 댓글로 알려줘. "
        "필드는 바꾸지 말고 DL-7001에는 댓글을 달지 마. 준서TL을 확인해."
    )
    comment = _task(
        "meeting-comments",
        "DL-9201과 DL-9202에만 회의 결정 댓글 초안 작성",
        kind="comment",
    )
    plan = {
        "goal": "관련 Task 두 건에 comment-only 초안 작성",
        "tasks": [comment],
        "blocking_questions": [],
        "assumptions": [],
    }
    prior = {
        "intent": Intent.MODIFY,
        "keywords": ["회의 결정", "댓글"],
        "module": "DataOps",
        "request_text": root,
        "request_plan": plan,
        "mentioned_keys": ["DL-9201", "DL-9202", "DL-7001"],
        "questions": [{
            "field": "person:준서",
            "question": "준서TL의 정확한 username을 알려 주세요.",
            "required_input": True,
        }],
        "situation": "관련 Task와 회의 근거 조사 완료",
        "continuation_contract": _contract(
            root=root, intent=Intent.MODIFY, action="comment",
            targets=["DL-9201", "DL-9202"], outcomes=["meeting-comments"],
        ),
    }
    answer = "이 회의의 준서TL은 skcc.x1327 임준서야. 두 관련 Task의 댓글 초안을 계속해줘."

    continued = _turn_start_patch(answer, prior)

    assert continued["turn_continuation"] is True
    assert continued["intent"] == Intent.MODIFY
    assert continued["request_text"] == root
    assert continued["request_plan"] == plan
    assert continued["continuation_contract"]["action"] == "comment"
    assert continued["continuation_contract"]["target_keys"] == ["DL-9201", "DL-9202"]
    assert "skcc.x1327" in _decision_map(continued)["person:준서"]

    state = {**continued, "messages": [HumanMessage(content=answer)]}
    got = RequestArchitect().apply(state, {
        # Reproduce r27: the model read the short answer as a fresh create instruction.
        "intent": Intent.PLAN_WORK,
        "keywords": ["잘못된 새 작업"],
        "module": "WrongModule",
        "goal": "관련 Task 후속 작업 생성",
        "tasks": [_task("wrong-create", answer)],
    })

    assert got["intent"] == Intent.MODIFY
    assert got["request_plan"]["tasks"] == [comment]
    assert got["mentioned_keys"] == ["DL-9201", "DL-9202"]
    assert got["keywords"] == ["회의 결정", "댓글", "DL-9201", "DL-9202"]
    assert got["module"] == "DataOps"
    assert got["continuation_contract"]["action"] == "comment"


def test_meeting_owner_and_parent_answers_keep_the_authoritative_create_ledger():
    root = "회의 메모대로 Epic DL-9200 아래 Task 2건을 만들어줘"
    tasks = [
        _task("writer", "writer 증빙 Task 생성"),
        _task("reader", "reader 운영 판정 자료 Task 생성"),
    ]
    plan = {
        "goal": "회의 후속 Task 두 건 생성",
        "tasks": tasks,
        "blocking_questions": [],
        "assumptions": [],
    }
    base = {
        "intent": Intent.PLAN_WORK,
        "request_text": root,
        "request_plan": plan,
        "mentioned_keys": ["DL-9200"],
        "continuation_contract": _contract(
            root=root, intent=Intent.PLAN_WORK, action="create",
            targets=["DL-9200"], outcomes=["writer", "reader"],
        ),
    }
    examples = [
        (
            {"field": "owner:reader 운영 판정 자료", "question": "reader 자료 담당자는?"},
            "reader 운영 판정 자료는 아직 담당자를 정하지 못했어. 미할당으로 만들어. "
            "나머지는 회의 메모 그대로 진행해.",
            "owner:reader 운영 판정 자료",
        ),
        (
            {"field": "assignee", "question": "Task 담당자는?"},
            "담당자는 skcc.x1103 이준서야. Task 초안을 계속 만들어줘.",
            "assignee",
        ),
        (
            {"field": "parent", "question": "어느 Epic 아래에 둘까요?"},
            "상위 Epic은 DL-9200으로 하고 Task 초안을 계속 만들어줘.",
            "parent",
        ),
    ]

    for question, answer, expected_field in examples:
        patch = _turn_start_patch(answer, {**base, "questions": [question]})
        assert patch["turn_continuation"] is True, answer
        assert patch["intent"] == Intent.PLAN_WORK
        assert patch["request_plan"] == plan
        assert patch["continuation_contract"]["action"] == "create"
        assert patch["continuation_contract"]["outcome_ids"] == ["writer", "reader"]
        assert expected_field in _decision_map(patch)


def test_existing_ticket_update_answer_cannot_be_reclassified_as_creation():
    root = "DL-9300 담당자를 확인한 사람으로 변경해줘"
    update = _task(
        "assignee-update", "DL-9300 담당자 변경", kind="modify",
    )
    update["write_intent"] = True
    plan = {
        "goal": "DL-9300 담당자 변경",
        "tasks": [update],
        "blocking_questions": [],
        "assumptions": [],
    }
    prior = {
        "intent": Intent.MODIFY,
        "keywords": ["담당자 변경"],
        "module": "DataOps",
        "request_text": root,
        "request_plan": plan,
        "mentioned_keys": ["DL-9300"],
        "questions": [{
            "field": "assignee", "question": "새 담당자의 username은?",
            "required_input": True,
        }],
        "continuation_contract": _contract(
            root=root, intent=Intent.MODIFY, action="update",
            targets=["DL-9300"], outcomes=["assignee-update"],
        ),
    }
    answer = "담당자는 skcc.x1103이야. 기존 Task 변경을 계속해줘."
    continued = _turn_start_patch(answer, prior)

    assert continued["turn_continuation"] is True
    assert continued["continuation_contract"]["action"] == "update"
    assert _decision_map(continued)["assignee"] == "skcc.x1103"

    got = RequestArchitect().apply(
        {**continued, "messages": [HumanMessage(content=answer)]},
        {
            "intent": Intent.PLAN_WORK,
            "keywords": ["새 Task"],
            "module": "WrongModule",
            "goal": "담당자 확인 Task 생성",
            "tasks": [_task("wrong-create", "담당자 확인 Task 생성")],
        },
    )

    assert got["intent"] == Intent.MODIFY
    assert got["request_plan"] == plan
    assert got["mentioned_keys"] == ["DL-9300"]
    assert got["keywords"] == ["담당자 변경", "DL-9300"]
    assert got["module"] == "DataOps"
    assert got["continuation_contract"]["action"] == "update"


def test_single_target_outcome_replacements_keep_the_typed_target_across_cancellations():
    """CTX3: replacing effects does not erase the one verified mutation identity."""
    root = "DL-9203의 priority와 due를 바꾸는 승인 전 초안을 보여줘"
    first_task = _task(
        "update", "DL-9203 priority와 due 변경", kind="write",
    )
    prior = {
        "intent": Intent.MODIFY,
        "request_text": root,
        "request_plan": {
            "goal": "DL-9203 필드 변경", "tasks": [first_task],
            "blocking_questions": [], "assumptions": [],
        },
        "mentioned_keys": ["DL-9203"],
        "continuation_contract": _contract(
            root=root, intent=Intent.MODIFY, action="update",
            targets=["DL-9203"], outcomes=["update"],
        ),
    }

    comment_text = (
        "방금 필드 변경은 취소. 대신 같은 티켓에 "
        "'회의 결정사항을 공유합니다' 댓글만 다는 초안으로 바꿔줘."
    )
    comment_turn = _turn_start_patch(comment_text, prior)
    assert comment_turn["turn_continuation"] is True
    assert comment_turn["continuation_contract"]["target_keys"] == ["DL-9203"]
    comment_patch = RequestArchitect().apply(
        {**comment_turn, "messages": [HumanMessage(content=comment_text)]},
        {
            "intent": Intent.MODIFY, "keywords": ["회의 결정"],
            "goal": "DL-9203 댓글 초안", "sufficient": True,
            "request_questions": [], "requested_effects": [], "target_selectors": [],
            "tasks": [_task("comment-draft", comment_text, kind="comment")],
            "blocking_questions": [], "assumptions": [],
        },
    )
    assert comment_patch["continuation_contract"]["action"] == "comment"
    assert comment_patch["continuation_contract"]["target_keys"] == ["DL-9203"]

    title_text = (
        "그 댓글도 취소. 최종 요청은 제목만 "
        "'[Catalog] Puffin NDV 결과 템플릿 정리'로 변경하는 거야. "
        "다른 변경 없이 승인 전 초안만 보여줘."
    )
    title_turn = _turn_start_patch(title_text, {**prior, **comment_patch})
    assert title_turn["turn_continuation"] is True
    assert title_turn["continuation_contract"]["target_keys"] == ["DL-9203"]
    title_patch = RequestArchitect().apply(
        {**title_turn, "messages": [HumanMessage(content=title_text)]},
        {
            "intent": Intent.MODIFY, "keywords": ["Puffin NDV"],
            "goal": "DL-9203 제목 변경", "sufficient": True,
            "request_questions": [], "target_selectors": [],
            "requested_effects": [{
                "target": "DL-9203", "field": "summary",
                "value": "[Catalog] Puffin NDV 결과 템플릿 정리",
                "literal": "[Catalog] Puffin NDV 결과 템플릿 정리",
            }],
            "tasks": [_task("title-update", title_text, kind="write")],
            "blocking_questions": [], "assumptions": [],
        },
    )
    assert title_patch["continuation_contract"]["action"] == "update"
    assert title_patch["continuation_contract"]["target_keys"] == ["DL-9203"]
    # Corrections deliberately stay off the exact scalar fast path, but the semantic Work
    # projection and final deterministic audit must retain the same typed target.
    from app.agent.workflow.agents.auditor import final_authority_review
    from app.agent.workflow.agents.work_architect import WorkArchitect
    final_state = {
        **title_turn, **title_patch, "messages": [HumanMessage(content=title_text)],
    }
    work = WorkArchitect().apply(final_state, {
        "questions": [], "mode": "task", "items": [], "rationale": "최종 제목만 변경",
        "change": {
            "key": "DL-9203",
            "summary": "[Catalog] Puffin NDV 결과 템플릿 정리",
        },
    })
    review = final_authority_review({**final_state, **work}, require_effect=True)
    assert work["change_plan"].get("changes") == {
        "summary": "[Catalog] Puffin NDV 결과 템플릿 정리",
    }, work
    assert review["ok"] is True, review["errors"]


def test_outcome_replacement_does_not_carry_multiple_or_new_targets():
    root = "DL-9203과 DL-9204에 댓글을 남겨줘"
    prior = {
        "intent": Intent.MODIFY,
        "request_text": root,
        "request_plan": {
            "goal": "두 티켓 댓글", "tasks": [
                _task("comments", "DL-9203과 DL-9204 댓글", kind="comment"),
            ],
        },
        "mentioned_keys": ["DL-9203", "DL-9204"],
        "continuation_contract": _contract(
            root=root, intent=Intent.MODIFY, action="comment",
            targets=["DL-9203", "DL-9204"], outcomes=["comments"],
        ),
    }

    ambiguous = _turn_start_patch("그 댓글은 취소하고 같은 티켓 제목만 바꿔줘", prior)
    switched = _turn_start_patch(
        "그 댓글은 취소하고 DL-9300 제목만 새 제목으로 바꿔줘", prior,
    )

    assert ambiguous["turn_continuation"] is False
    assert ambiguous["continuation_contract"] == {}
    assert switched["turn_continuation"] is False
    assert switched["continuation_contract"] == {}


def test_read_summary_interview_answer_preserves_the_original_read_contract():
    root = "Puffin 회의 결정과 담당·기한을 세 줄로 요약해줘"
    summary_task = _task(
        "summary", "Puffin 회의 결정·담당·기한 세 줄 요약", kind="research",
    )
    plan = {
        "goal": "Puffin 회의 결정 세 줄 요약",
        "tasks": [summary_task],
        "blocking_questions": [],
        "assumptions": [],
    }
    prior = {
        "intent": Intent.ASK,
        "keywords": ["Puffin", "회의", "결정"],
        "module": "DataOps",
        "answer_depth": "brief",
        "request_text": root,
        "request_plan": plan,
        "questions": [{
            "field": "target",
            "question": "어느 관련 Task를 요약할까요?",
            "required_input": True,
        }],
        "continuation_contract": _contract(
            root=root, intent=Intent.ASK, action="read", targets=["DL-9000"],
            outcomes=["summary"],
        ),
    }
    answer = "DL-9201과 DL-9202를 말한 거야. 회의 결정 요약을 계속해줘."
    continued = _turn_start_patch(answer, prior)

    assert continued["turn_continuation"] is True
    assert continued["request_text"] == root
    assert continued["request_plan"] == plan
    assert continued["continuation_contract"]["target_keys"] == ["DL-9201", "DL-9202"]

    got = RequestArchitect().apply(
        {**continued, "messages": [HumanMessage(content=answer)]},
        {
            "intent": Intent.PLAN_WORK,
            "keywords": ["잘못된 생성 주제"],
            "module": "WrongModule",
            "goal": "요약 Task 생성",
            "tasks": [_task("wrong-write", "요약 Task 생성")],
        },
    )

    assert got["intent"] == Intent.ASK
    assert got["request_plan"] == plan
    assert got["request_text"] == root
    assert got["answer_depth"] == "brief"
    assert got["mentioned_keys"] == ["DL-9201", "DL-9202"]
    assert got["keywords"] == ["Puffin", "회의", "결정", "DL-9201", "DL-9202"]
    assert got["module"] == "DataOps"
    assert got["continuation_contract"]["action"] == "read"


def test_new_topic_resets_contract_while_typed_addition_merges_outcomes():
    root = "Puffin 구현 Task를 만들어줘"
    original = _task("implementation", "Puffin 구현 Task 생성")
    plan = {
        "goal": "Puffin 구현 Task 생성",
        "tasks": [original],
        "blocking_questions": [],
        "assumptions": [],
    }
    prior = {
        "intent": Intent.PLAN_WORK,
        "request_text": root,
        "request_plan": plan,
        "questions": [],
        "draft": {"items": [{"summary": "Puffin 구현"}]},
        "continuation_contract": _contract(
            root=root, intent=Intent.PLAN_WORK, action="create",
            outcomes=["implementation"],
        ),
    }

    fresh = _turn_start_patch("완전히 다른 보안 교육 Task를 새로 만들어줘", prior)
    assert fresh["turn_continuation"] is False
    assert fresh["request_plan"] == {}
    assert fresh["continuation_contract"] == {}

    answer = "검증 Task도 하나 더 만들어줘"
    continued = _turn_start_patch(answer, prior)
    assert continued["turn_continuation"] is True
    added = _task("validation", "Puffin 검증 Task 생성")
    got = RequestArchitect().apply(
        {**continued, "messages": [HumanMessage(content=answer)]},
        {
            "intent": Intent.PLAN_WORK,
            "keywords": ["Puffin", "검증"],
            "goal": "검증 Task 추가",
            "tasks": [added],
        },
    )
    assert [row["id"] for row in got["request_plan"]["tasks"]] == [
        "implementation", "validation",
    ]
    assert got["continuation_contract"]["outcome_ids"] == [
        "implementation", "validation",
    ]
