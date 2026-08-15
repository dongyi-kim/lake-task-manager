# -*- coding: utf-8 -*-
"""실제 Base 판독에서 발견한 primary battery false-positive 회귀."""

import pytest

pytest.importorskip("langchain_core", reason="requirements-agent.txt 미설치")

from tools import agent_compose_eval as editor_eval  # noqa: E402
from tools import agent_create_suite as create_eval  # noqa: E402
from tools import agent_context_change_eval as context_eval  # noqa: E402
from tools import agent_meeting_eval as meeting_eval  # noqa: E402


def test_editor_seed_checker_requires_the_original_visible_text():
    seed = "<p>오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다</p>"
    assert editor_eval._seed_preserved(
        {"ok": True, "html": seed + "<p>높았습니다.</p>"}, seed,
    )
    assert not editor_eval._seed_preserved(
        {"ok": True, "html": "<p>성능 측정 결과를 정리했습니다.</p>"}, seed,
    )


def test_editor_contract_checker_rejects_resolution_and_renderer_contradictions():
    result = {
        "ok": True,
        "html": '{{ticket-inline:<a data-key="DL-9040">DL-9040</a>}}',
        "note": "확인되지 않은 항목이 있습니다: DL-9040",
        "references": [{"kind": "ticket", "key": "DL-9040", "resolved": True}],
    }
    flaws = editor_eval._editor_contract_flaws(result)
    assert any("이중 삽입" in flaw for flaw in flaws)
    assert any("resolved ticket" in flaw for flaw in flaws)


def test_create_checker_rejects_reply_payload_tier_mismatch():
    output = {
        "reply": "### Epic\n- 제목: 통계 파이프라인",
        "pending": {"items": [{"type": "Task", "summary": "통계 파이프라인",
                                "description": "<h3>배경</h3>충분한 본문"}]},
        "questions": [],
    }
    assert any("payload 타입" in flaw for flaw in create_eval._output_flaws(output))


def test_create_question_checkers_require_bug_identity_and_legal_parent_choice():
    vague_bug = {"questions": [{"question": "완료 조건은 무엇인가요?"}]}
    exact_bug = {"questions": [{"question": "어떤 배치 이름 또는 DAG에서 재현되나요?"}]}
    assert not create_eval._asks_for_bug_identity(vague_bug)
    assert create_eval._asks_for_bug_identity(exact_bug)

    vague_parent = {"questions": [{"question": "작업 배경은 무엇인가요?"}]}
    exact_parent = {"questions": [{"question": "상위 Task를 고를까요, 최상위 Task로 바꿀까요?"}]}
    assert not create_eval._rule1_ok(vague_parent, [])
    assert create_eval._rule1_ok(exact_parent, [])


def test_meeting_interview_checker_rejects_draft_before_ambiguous_identity_is_resolved():
    question = {
        "questions": [{"question": "준서TL과 PSR을 확인해 주세요", "options": [
            "skcc.x1103", "skcc.x1327",
        ]}],
    }
    assert meeting_eval._interview_then_resume([question, {}], "PSR")
    assert not meeting_eval._interview_then_resume([
        {**question, "pending": {"action": "create_ticket"}}, {},
    ], "PSR")


def test_context_switch_checker_requires_only_the_latest_exact_change():
    exact = {
        "reply": "DL-9203 priority 변경 초안",
        "pending": {
            "action": "update_ticket", "key": "DL-9203",
            "changes": {"priority": "P2-Major"},
        },
    }
    contaminated = {
        **exact,
        "pending": {**exact["pending"], "changes": {
            "priority": "P2-Major", "duedate": "2026-08-31",
        }},
    }
    assert context_eval._ctx_unrelated_ok(exact, [])
    assert not context_eval._ctx_unrelated_ok(contaminated, [])


def test_context_return_checker_accepts_the_canonical_single_ticket_action():
    output = {
        "reply": "DL-9095 댓글 승인 초안",
        "pending": {
            "action": "update_ticket", "key": "DL-9095", "changes": {},
            "comment": "2홉 100노드 성능 측정 결과와 원본 로그를 첨부해 주세요",
        },
    }
    assert context_eval._ctx_return_ok(output, [])
