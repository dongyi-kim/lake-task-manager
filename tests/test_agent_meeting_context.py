# -*- coding: utf-8 -*-
"""Meeting interviews and abrupt context changes are deterministic workflow boundaries."""

import os

from langchain_core.messages import HumanMessage

os.environ.setdefault("JIRA_ENV", "mock")

from app.agent.tools.people_tools import set_person_context  # noqa: E402
from app.agent.workflow.agents.knowledge_curator import KnowledgeCurator  # noqa: E402
from app.agent.workflow.agents.query_runner import QueryRunner  # noqa: E402
from app.agent.workflow.agents.query_specialist import QuerySpecialist  # noqa: E402
from app.agent.workflow.agents.result_integrator import _canonicalize_meeting_reply  # noqa: E402
from app.agent.workflow.agents.work_architect import (  # noqa: E402
    _apply_named_assignees,
    _canonicalize_meeting_mentions,
    _comment_input_missing,
    shape_hint,
)
from app.agent.workflow.meeting_context import meeting_subject, unresolved_questions  # noqa: E402
from app.agent.workflow.session import _turn_start_patch  # noqa: E402


def _state(*messages, request=""):
    return {"messages": [HumanMessage(content=value) for value in messages],
            "request_text": request or (messages[0] if messages else "")}


def test_every_meeting_case_interviews_ambiguous_person_and_local_term_after_research():
    set_person_context("meeting-interview", ["DL-9200"])
    request = (
        "회의록을 조사해 정리해줘. 참석: @이다은, 하은님, 준서TL. 담당은 @이다은은 writer. "
        "준서TL이 PSR 기준을 맡음. PSR 뜻은 기록에 없고 준서TL도 확정되지 않으면 물어봐."
    )
    state = {**_state(request), "situation": "내부 티켓과 외부 공식 문서를 조사했으나 정의 미확정",
             "topic_dossier": "PSR 정의 확인 필요"}
    questions = unresolved_questions(state)
    text = str(questions)
    assert "skcc.x1103" in text and "skcc.x1327" in text
    assert "PSR" in text
    assert "이다은" not in text and "하은" not in text


def test_meeting_interview_answer_binds_person_and_term_without_reasking():
    set_person_context("meeting-resume", ["DL-9200"])
    request = "회의 후속 Task. 준서TL이 PSR 증빙 담당. 둘 다 자료로 확정되지 않으면 물어봐."
    answer = ("준서TL은 skcc.x1103 이준서. PSR은 PoC Success Review이고 "
              "테이블별 NDV 오차와 StarRocks 실제 소비 로그가 증빙이야.")
    state = {**_state(request, answer, request=request), "situation": "조사 완료",
             "topic_dossier": "PSR 정의 확인 필요"}
    assert unresolved_questions(state) == []


def test_new_request_clears_stale_research_and_draft_but_interview_answer_keeps_research():
    prior = {
        "request_text": "fdc 데이터 히스토리", "topic_dossier": "old dossier",
        "situation": "old situation", "draft": {"items": [{"summary": "old"}]},
        "questions": [], "mentioned_keys": ["DL-9041"], "turns": 2,
    }
    fresh = _turn_start_patch(
        "이건 그만. 완전히 다른 요청이야. DL-9203 priority만 P2로 바꿔줘", prior)
    assert fresh["request_text"].startswith("이건 그만")
    assert fresh["topic_dossier"] == "" and fresh["situation"] == ""
    assert fresh["draft"] == {} and fresh["turns"] == 0
    assert not fresh["turn_continuation"]

    prior["questions"] = [{"question": "PSR 뜻?", "required_input": True}]
    continued = _turn_start_patch("PSR은 PoC Success Review야. 계속해줘.", prior)
    assert continued["request_text"] == prior["request_text"]
    assert continued["topic_dossier"] == "old dossier"
    assert continued["turn_continuation"]


def test_exact_meeting_task_count_and_singular_task_are_user_selected_shapes():
    assert shape_hint(_state("Epic 아래 정확히 Task 3건의 초안을 만들어줘"))[0] == "multiple_tasks"
    assert shape_hint(_state("회의 후속 Task를 만들어줘"))[0] == "single_task"


def test_meeting_owner_lines_override_recommendations_but_reviewer_does_not():
    set_person_context("meeting-owner", ["DL-9200"])
    request = """회의 후속 Task 3건을 만들어줘.
1. @이다은 — Iceberg Puffin NDV writer PoC
2. 하은님 — StarRocks reader 검증
3. 준서TL — RGP 검증 기준 및 결과 템플릿
   - {{최민서:1042}}가 리뷰"""
    answer = "준서TL은 skcc.x1103 이준서야."
    state = _state(request, answer, request=request)
    items = [
        {"summary": "[ETL] Iceberg Puffin NDV writer PoC", "assignee": "skcc.i2044"},
        {"summary": "[Workbench] StarRocks reader 검증", "assignee": "skcc.x1210"},
        {"summary": "[Catalog] RGP 검증 기준 및 결과 템플릿", "assignee": "skcc.i2044"},
    ]
    _apply_named_assignees(state, items)
    assert [row["assignee"] for row in items] == ["skcc.i2011", "skcc.x1402", "skcc.x1103"]
    assert all(row["assignee_source"] == "user" for row in items)


def test_meeting_comment_mentions_are_repaired_to_confirmed_identities():
    set_person_context("meeting-comments", ["DL-9201", "DL-9202"])
    request = "회의 결정 댓글: writer 결과는 @이다은, reader는 하은님, 준서TL이 검토"
    answer = "준서TL은 skcc.x1327 임준서야."
    state = _state(request, answer, request=request)
    plan = {"comment": ("writer는 {{mention:skcc.x1327}} 이다은님, reader는 하은님, "
                         "검토는 준서TL"), "comments": []}
    _canonicalize_meeting_mentions(state, plan)
    assert "{{mention:skcc.i2011}}" in plan["comment"]
    assert "{{mention:skcc.x1402}}" in plan["comment"]
    assert "{{mention:skcc.x1327}}" in plan["comment"]
    assert "{{mention:skcc.x1327}} 이다은" not in plan["comment"]


def test_explicit_no_comment_never_asks_for_comment_body():
    state = _state("DL-9203 본문을 바꿔줘. 댓글은 남기지 마.")
    assert not _comment_input_missing(state, {"key": "DL-9203", "changes": {"summary": "x"}})


def test_blank_comment_query_is_removed_and_runner_rejects_defense_in_depth():
    raw = {"queries": [{"id": "all-comments", "source": "comments", "query": "", "where": "",
                        "order_by": "updated DESC", "fields": [], "completeness": "all",
                        "page_size": 50, "depends_on": []}], "joins": [], "uncertainty": []}
    planned = QuerySpecialist().apply(_state("회의록을 조사해줘"), raw)["query_plan"]
    assert planned["queries"] == []
    assert "댓글 전수조회" in " ".join(planned["uncertainty"])

    output = QueryRunner()._run({"query_plan": raw, "messages": [HumanMessage(content="회의록")]})
    result = output["query_results"][0]["result"]
    assert result["returned"] == 0 and "허용되지" in result["error"]


def test_meeting_reply_repairs_alias_tokens_and_lists_every_resolved_attendee():
    set_person_context("meeting-reply", ["DL-9200"])
    request = ("회의록 참석: @이다은, {{최민서:1042}}, 하은님, 현우차장, 준서TL. "
               "담당·기한을 정리해줘.")
    answer = "준서TL은 skcc.x1103 이준서야."
    state = {**_state(request, answer, request=request), "intent": "ask", "questions": []}
    raw = ("### 결정사항\n\n정리\n\n### 담당·기한\n\n"
           "| 작업 | 담당 | 기한 |\n|---|---|---|\n"
           "| writer | {{mention:이다은}} | 2026-08-22 |\n"
           "| reader | 하은님 | 2026-08-25 |\n"
           "| 기준 | {{mention:최민서:1042}} | 2026-08-28 |")
    got = _canonicalize_meeting_reply(raw, state)
    for uid in ("skcc.i2011", "skcc.x1042", "skcc.x1402", "skcc.x1560", "skcc.x1103"):
        assert f"{{{{mention:{uid}}}}}" in got
    assert "{{mention:이다은}}" not in got and "하은님" not in got
    assert "### 참석자" in got


def test_curator_drops_only_meeting_terms_defined_by_interview():
    request = "회의 후속 정리. PSR 뜻은 기록에 없으니 조사 후 물어봐."
    answer = "PSR은 PoC Success Review이고 5개 모두 오차 5% 이내여야 해."
    state = _state(request, answer, request=request)
    out = KnowledgeCurator().apply(state, {
        "concepts": [], "our_context": "PSR 정의 확인", "references": [],
        "gaps": ["PSR의 정확한 정의와 적용 방법 확인 필요", "reader 진행 상황 확인 필요"],
    })
    assert out["knowledge_brief"]["gaps"] == ["reader 진행 상황 확인 필요"]


def test_titled_meeting_uses_full_technical_subject_instead_of_one_action_keyword():
    state = _state("## 2026-08-15 Iceberg Puffin NDV 도입 실무회의\n- StarRocks reader 검증")
    assert meeting_subject(state) == "Iceberg Puffin NDV"
