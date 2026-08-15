# -*- coding: utf-8 -*-
"""Meeting interviews and abrupt context changes are deterministic workflow boundaries."""

import os

from langchain_core.messages import HumanMessage

os.environ.setdefault("JIRA_ENV", "mock")

from app.agent.tools.people_tools import set_person_context  # noqa: E402
from app.agent.workflow.agents.knowledge_curator import KnowledgeCurator  # noqa: E402
from app.agent.workflow.agents.query_runner import QueryRunner  # noqa: E402
from app.agent.workflow.agents.query_specialist import QuerySpecialist  # noqa: E402
from app.agent.workflow.agents.request_architect import RequestArchitect  # noqa: E402
from app.agent.workflow.agents.result_integrator import (  # noqa: E402
    ResultIntegrator,
    _canonicalize_meeting_reply,
)
from app.agent.workflow.agents.work_architect import (  # noqa: E402
    _apply_named_assignees,
    _canonicalize_meeting_mentions,
    _comment_input_missing,
    _drop_unrequested_meeting_create_fields,
    _ensure_meeting_reviewers,
    _explicit_parent_epic,
    _explicit_meeting_update_fields,
    shape_hint,
)
from app.agent.workflow.agents.people_advisor import (  # noqa: E402
    _all_assignees_user_specified,
    _user_fixed_assignments,
)
from app.agent.workflow.meeting_context import (  # noqa: E402
    canonicalize_reply_mentions,
    meeting_request_text,
    meeting_subject,
    resolved_people,
    unresolved_questions,
)
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


def test_negative_gate_condition_is_a_definition_not_an_unknown_term():
    set_person_context("meeting-negative-definition", ["DL-9200"])
    request = "회의 후속 Task. 준서TL이 RGP 기준을 담당. 모호한 용어는 조사 후 물어봐."
    answer = ("준서TL은 skcc.x1103 이준서. RGP는 Reader Gate Policy로, StarRocks가 "
              "Puffin NDV를 실제 소비한 증거가 없으면 운영 반영을 금지한다는 뜻이야.")
    state = {**_state(request, answer, request=request), "situation": "조사 완료"}
    assert unresolved_questions(state) == []


def test_meeting_ambiguity_contract_interviews_unfamiliar_local_acronym():
    set_person_context("meeting-local-acronym", ["DL-9200"])
    request = ("회의 결정에서 모호한 사람·용어는 자료를 찾아도 확정되지 않으면 먼저 물어봐. "
               "준서TL이 RGP 검증 기준을 작성한다.")
    state = {**_state(request), "situation": "내부 기록과 외부 공식 자료 조사 완료",
             "topic_dossier": "RGP의 회의 내 정의는 확인되지 않음"}
    assert "RGP" in str(unresolved_questions(state))


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
    resumed = _state(
        "Epic 아래 정확히 Task 3건의 초안을 만들어줘",
        "준서TL은 skcc.x1103 이준서야.",
        request="준서TL은 skcc.x1103 이준서야.",
    )
    resumed["turn_continuation"] = True
    resumed["messages"][0] = HumanMessage(
        content="회의록 기준 Epic 아래 정확히 Task 3건의 초안을 만들어줘")
    assert shape_hint(resumed)[0] == "multiple_tasks"


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
    _ensure_meeting_reviewers(state, items)
    assert "skcc.x1042" not in (items[0].get("description") or "")
    assert "skcc.x1042" in items[-1]["description"]


def test_all_user_fixed_assignees_skip_recommendation_without_losing_alignment():
    draft = {"items": [
        {"summary": "writer", "assignee": "skcc.i2011", "assignee_source": "user"},
        {"summary": "reader", "assignee": "skcc.x1402", "assignee_source": "user"},
    ]}
    assert _all_assignees_user_specified(draft)
    rows = _user_fixed_assignments(draft)
    assert [row["user"] for row in rows] == ["skcc.i2011", "skcc.x1402"]
    assert all(row["reasons"] == ["사용자 지정 담당자"] for row in rows)
    draft["items"][1].pop("assignee_source")
    assert not _all_assignees_user_specified(draft)


def test_meeting_comment_mentions_are_repaired_to_confirmed_identities():
    set_person_context("meeting-comments", ["DL-9201", "DL-9202"])
    request = "회의 결정 댓글: writer 결과는 @이다은, reader는 하은님, 준서TL이 검토"
    answer = "준서TL은 skcc.x1327 임준서야."
    state = _state(request, answer, request=request)
    plan = {"comment": ("writer 결과는 {{mention:skcc.i2101}}이 공유, "
                         "reader 결과는 하은님이 공유, 검토는 준서TL"), "comments": []}
    _canonicalize_meeting_mentions(state, plan)
    assert "{{mention:skcc.i2011}}" in plan["comment"]
    assert "{{mention:skcc.x1402}}" in plan["comment"]
    assert "{{mention:skcc.x1327}}" in plan["comment"]
    assert "skcc.i2101" not in plan["comment"]


def test_meeting_mentions_collapse_duplicate_badges_and_full_name_username_pair():
    set_person_context("meeting-full-name", ["DL-9200"])
    request = "회의 후속 Task. 준서TL이 PSR 증빙 담당."
    answer = "준서TL은 skcc.x1103 이준서이고 PSR은 PoC Success Review야."
    state = _state(request, answer, request=request)
    assert resolved_people(state)["이준서"] == "skcc.x1103"
    got = canonicalize_reply_mentions(
        state,
        "이준서(skcc.x1103) 담당. {{mention:skcc.x1103}} {{mention:skcc.x1103}}이 공유",
    )
    assert got.count("{{mention:skcc.x1103}}") == 2
    assert "이준서" not in got and "(skcc.x1103)" not in got


def test_explicit_meeting_epic_survives_interview_and_overrides_component_inference():
    request = "회의 결정에 따라 Epic DL-9200 아래 정확히 Task 3건을 만들어줘."
    answer = "준서TL은 skcc.x1103 이준서야. 초안을 계속해줘."
    state = {**_state(request, answer, request=answer), "turn_continuation": True,
             "mentioned_keys": []}
    assert _explicit_parent_epic(state) == "DL-9200"


def test_meeting_create_drops_only_optional_fields_absent_from_minutes():
    state = _state("회의 후속 Task를 만들어줘. 기한과 담당은 확정. 회의록에 없는 값은 발명하지 마.")
    items = [{"summary": "검증", "priority": "P3-Minor", "labels": ["invented"],
              "components": ["Catalog"], "duedate": "2026-08-30"}]
    _drop_unrequested_meeting_create_fields(state, items)
    assert "priority" not in items[0] and "labels" not in items[0]
    assert items[0]["components"] == ["Catalog"] and items[0]["duedate"] == "2026-08-30"


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


def test_jira_key_is_not_interviewed_as_a_local_meeting_acronym():
    set_person_context("meeting-key-not-term", ["DL-9200"])
    request = ("회의 후속 Task. 준서TL이 PSR 증빙 담당. Epic DL-9200 아래에 만들어줘. "
               "사람과 PSR 뜻은 조사 후에도 확정되지 않으면 물어봐.")
    state = {**_state(request), "situation": "내부·외부 조사 완료"}
    questions = unresolved_questions(state)
    assert "DL-9200" not in str(questions)
    assert "PSR" in str(questions)


def test_exact_meeting_update_fields_survive_identity_interview_resume():
    set_person_context("meeting-update-fields", ["DL-9203"])
    request = """회의 결정대로 DL-9203을 수정해줘.
- 제목: [Catalog] Puffin NDV 검증 기준 및 결과 템플릿
- priority: P1-Critical
- due: 2026-08-29
- component: Catalog
- labels 전체값: meeting-fixture, puffin-ndv, decision-20260815
- 본문 전체 교체: `결정 배경`, `작업 범위`, `검증 기준` 세 section. 5개 테이블 PoC 결과를 기록하되 StarRocks 소비 지원은 검증 전 확정하지 않음
- 준서TL이 RGP 기준의 소유자"""
    answer = ("준서TL은 skcc.x1103 이준서. RGP는 Reader Gate Policy이고 "
              "StarRocks 실제 소비 증거 전에는 운영 반영을 막는 기준이야.")
    state = {**_state(request, answer, request=answer), "intent": "modify",
             "turn_continuation": True}
    fields = _explicit_meeting_update_fields(state)
    assert set(fields) == {"summary", "priority", "duedate", "components", "labels", "description"}
    assert fields["summary"] == "[Catalog] Puffin NDV 검증 기준 및 결과 템플릿"
    assert fields["priority"] == "P1-Critical" and fields["duedate"] == "2026-08-29"
    assert fields["components"] == ["Catalog"]
    assert fields["labels"] == ["meeting-fixture", "puffin-ndv", "decision-20260815"]
    assert all(section in fields["description"] for section in ("결정 배경", "작업 범위", "검증 기준"))
    assert "skcc.x1103" in fields["description"]


def test_meeting_interview_keeps_original_request_and_comment_intent():
    request = "회의 결정사항을 DL-9201, DL-9202 두 건의 댓글로 알려줘."
    answer = "이 회의의 준서TL은 skcc.x1327 임준서야. 계속해줘."
    state = {**_state(request, answer, request=answer), "turn_continuation": True,
             "turns": 1, "questions": []}
    assert meeting_request_text(state) == request
    patch = RequestArchitect().apply(state, {
        "intent": "plan_work", "keywords": [], "goal": "댓글 작성", "tasks": [],
    })
    assert patch["intent"] == "modify"
    assert patch["request_text"] == request


def test_comment_only_result_contract_never_describes_status_as_a_change():
    state = {**_state("회의 결정을 DL-9201, DL-9202 댓글로 알려줘"), "intent": "modify",
             "change_plan": {"keys": ["DL-9201", "DL-9202"], "changes": {},
                             "comment": "회의 결정", "comments": []}}
    prompt = ResultIntegrator().task(state)
    assert "comment-only" in prompt and "no ticket field or status will change" in prompt
