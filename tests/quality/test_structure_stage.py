# -*- coding: utf-8 -*-
"""구조 합의 단계 — **뼈대 먼저, 살은 나중**(사용자 요청).

왜: 복합 산출물의 본문까지 다 써서 한 번에 내밀면, 구조가 틀렸을 때 사용자가 고칠 것이
너무 많다. 티켓 넷의 배경·범위·DoD 를 다 읽고 나서야 "2번은 1번에 합쳐야지"를 말하게
되고, 그 시점에 우리는 이미 본문 넷을 쓴 뒤다.
"""
import os
import sys

import pytest

os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("langgraph", reason="requirements-agent.txt 미설치")

from app.agent.workflow.agents.work_architect import (   # noqa: E402
    is_composite, structure_accepted, structure_feedback, structure_question, structure_tree)


def _msg(text):
    from langchain_core.messages import HumanMessage
    return {"messages": [HumanMessage(content=text)]}


def test_only_composite_output_needs_a_structure_round():
    """티켓 하나짜리는 뼈대를 물을 것이 없다 — 왕복만 늘어난다."""
    assert not is_composite([{"summary": "하나"}])
    assert not is_composite([{"summary": "하나", "children": [{"summary": "a"}]}])
    assert is_composite([{"summary": "하나"}, {"summary": "둘"}])
    # ★ 자식이 둘셋 붙은 모양은 **복합이 아니다** — 관계가 단순해 본문까지 함께 봐도 된다.
    #   처음엔 2건부터 복합으로 봤다가 생성 스위트가 20/20 → 16/20 으로 떨어졌다
    #   ("Task 만들어줘, P1, 금요일까지" 같은 단순 요청까지 구조 확인을 받았다).
    assert not is_composite([{"summary": "하나", "children": [{"summary": "a"}, {"summary": "b"}]}])
    assert is_composite([{"summary": "하나", "children": [{"summary": c} for c in "abcd"]}])


def test_approval_with_an_edit_attached_is_not_approval():
    """★ "좋아, 근데 3번은 빼줘" 를 승인으로 읽으면 **사용자의 수정이 통째로 증발한다.**

    이 한 줄이 이 기능의 핵심이다 — 사람은 승인과 수정을 한 문장에 섞어 말한다.
    """
    assert structure_accepted(_msg("이 구조로 진행한다"))
    assert structure_accepted(_msg("좋아요 그대로 가주세요"))
    assert not structure_accepted(_msg("좋아, 근데 3번은 빼줘"))
    assert not structure_accepted(_msg("네 근데 1번을 둘로 나눠주세요"))
    assert not structure_accepted(_msg(""))


def test_edits_are_recognised_as_feedback_not_noise():
    for said in ("3번 빼줘", "1번이랑 2번 합쳐", "PoC 를 둘로 쪼개줘",
                 "검증 단계 하나 추가해줘", "2-1 제목 바꿔줘"):
        assert structure_feedback(_msg(said)), said
    assert not structure_feedback(_msg("고마워요"))


def test_the_tree_shows_relationships_not_values():
    """사용자가 고치는 것은 값이 아니라 **관계**다(합치기·나누기·올리기).
    들여쓴 나무가 그 관계를 그대로 보여 준다 — 표로는 안 보인다."""
    tree = structure_tree([
        {"summary": "[ETL] 통계 생성 기능", "components": ["ETL"],
         "children": [{"summary": "feasibility test"}, {"summary": "PoC"}]},
        {"summary": "[Runtime] 인덱스 조정"},
    ], epic="DL-102")
    assert "DL-102 (Epic)" in tree
    assert "1. [ETL] 통계 생성 기능" in tree and "[ETL]" in tree
    assert "1-1. feasibility test" in tree and "1-2. PoC" in tree
    assert "└─" in tree                      # 마지막 자식은 꺾쇠가 다르다(관계가 보인다)
    assert "2. [Runtime] 인덱스 조정" in tree


def test_the_structure_question_says_what_will_be_made():
    q = structure_question([{"summary": "a", "children": [{"summary": "x"}, {"summary": "y"}]},
                            {"summary": "b"}])
    assert "Task 2건" in q["question"] and "Sub-Task 2건" in q["question"]
    assert q["kind"] == "choice"
    # 고칠 수 있다는 것을 **보기에서** 알려 준다 — 없으면 사용자는 승인만 할 수 있다고 읽는다
    assert any("고칠" in o for o in q["options"])


def test_structure_feedback_never_becomes_a_ticket_modification():
    """합의 중의 "빼줘"는 **아직 없는 티켓**을 고치라는 말이 아니다.

    실측(STRUCT2): 뼈대 제안 다음 턴의 "좋아, 근데 문서화는 빼줘" 가 intent=modify 로 갔다.
    그 갈래로 새면 사용자의 수정은 초안에 반영되지 않고 변경 카드만 헛돈다.
    """
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.state import Intent

    st = dict(_msg("좋아, 근데 문서화는 빼줘"),
              structure_plan=[{"summary": "수집"}, {"summary": "문서화"}], structure_ok=False)
    assert RequestArchitect().apply(st, {"intent": Intent.MODIFY, "keywords": []})["intent"] \
        == Intent.PLAN_WORK

    # 합의가 끝났으면 원래대로 — 그때부터 "빼줘"는 진짜 티켓 변경일 수 있다
    st2 = dict(st, structure_ok=True)
    assert RequestArchitect().apply(st2, {"intent": Intent.MODIFY, "keywords": []})["intent"] \
        == Intent.MODIFY
