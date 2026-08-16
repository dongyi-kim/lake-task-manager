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
    _render_assignment_section,
)
from app.agent.workflow.agents.work_architect import (  # noqa: E402
    _apply_named_assignees,
    _canonicalize_meeting_mentions,
    _comment_input_missing,
    _drop_unneeded_meeting_questions,
    _drop_meeting_sibling_exclusions,
    _drop_unrequested_meeting_create_fields,
    _ensure_meeting_background_attribution,
    _ensure_meeting_reviewers,
    _explicit_parent_epic,
    _explicit_meeting_update_fields,
    _meeting_unchanged_fields,
    _recover_decided_meeting_tasks,
    shape_hint,
)
from app.agent.workflow.agents.people_advisor import (  # noqa: E402
    _all_assignees_user_specified,
    _user_fixed_assignments,
)
from app.agent.workflow.meeting_context import (  # noqa: E402
    canonicalize_meeting_owner_table,
    canonicalize_reply_mentions,
    is_meeting_request,
    meeting_owner_records,
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


def test_heterogeneous_meeting_labels_are_recognized_and_attachment_filename_is_not_the_subject():
    request = ("첨부 회의 메모를 정리해줘. from: @이다은\n"
               "Puffin 결과 확인 by 하은님\n최하은: StarRocks reader 검증")
    state = _state(request)
    assert is_meeting_request(state)
    assert meeting_subject(state) == "Puffin StarRocks"


def test_meeting_person_parsing_does_not_interview_particles_or_the_word_not_decided():
    set_person_context("meeting-person-boundaries", ["DL-9200"])
    request = ("회의 메모. 참석자는 다은님, 최하은, {{최민서:1042}}\n"
               "from: @이다은\n최하은: reader 검증\n"
               "이다은의 의견: 확대는 이번 결정 아님\n"
               "하은님 의견 — 실제 소비 전에는 지원이라고 쓰지 않음")
    state = {**_state(request), "situation": "관련 자료 조사 완료"}
    assert unresolved_questions(state) == []
    assert set(resolved_people(state).values()) == {"skcc.i2011", "skcc.x1402", "skcc.x1042"}


def test_meeting_owner_records_support_by_colon_aliases_and_explicit_unassigned():
    set_person_context("meeting-attribution-forms", ["DL-9200"])
    request = """비정형 회의 기록에서 Task 3건을 만들어줘.
@이다은: writer 증빙은 제가 맡음 — 2026-08-22
reader 검증 결과 정리는 by 하은님, 2026-08-25
최하은: 실행계획도 포함
로그 마스킹 체크리스트 — 담당자는 정하지 못함. 미할당, 2026-08-27"""
    records = meeting_owner_records(_state(request))
    writer = next(row for row in records if "writer" in row["work"])
    reader = next(row for row in records if "reader" in row["work"])
    masking = next(row for row in records if "마스킹" in row["work"])
    assert (writer["owner"], writer["due"]) == ("skcc.i2011", "2026-08-22")
    assert (reader["owner"], reader["due"]) == ("skcc.x1402", "2026-08-25")
    assert (masking["owner"], masking["due"]) == ("", "2026-08-27")
    assert not any("배경에는 회의 논의" in row["work"] for row in records)


def test_meeting_create_background_keeps_discussion_and_requester_without_assigning_them():
    set_person_context("meeting-background-attribution", ["DL-9200"])
    request = ("회의 기록으로 Task 생성. from: {{최민서:1042}}\n"
               "민서M의 지시: writer 증빙을 분리\n@이다은 — writer 증빙, 2026-08-22")
    state = _state(request)
    items = [{"summary": "writer 증빙", "assignee": "skcc.i2011",
              "description": "<h3>배경</h3><p>증빙 정리 요청</p><h3>작업 범위</h3>범위<h3>완료 조건</h3>완료"}]
    _ensure_meeting_background_attribution(state, items)
    assert "회의 논의" in items[0]["description"] and "skcc.x1042" in items[0]["description"]
    assert items[0]["assignee"] == "skcc.i2011"


def test_meeting_optional_fields_left_undecided_are_dropped_without_followup_interview():
    request = ("회의 기록으로 Task를 만들어줘. writer 증빙은 @이다은 담당. "
               "priority/component/labels는 결정하지 않음")
    state = _state(request)
    items = [{"summary": "writer 증빙", "priority": "P3-Minor",
              "components": ["Catalog"], "labels": ["puffin"]}]
    _drop_unrequested_meeting_create_fields(state, items)
    assert set(items[0]) == {"summary"}
    questions = [{"question": "각 Task의 컴포넌트를 선택해 주세요.",
                  "field": "component", "kind": "choice"}]
    assert _drop_unneeded_meeting_questions(state, questions) == []


def test_meeting_interview_asks_for_missing_owner_or_unassigned_but_skips_rejected_opinion_actor():
    set_person_context("meeting-minimum-interview", ["DL-9200"])
    request = """첨부 회의 메모로 Task 1건을 만들어줘.
StarRocks reader 운영 판정 자료 정리 by ... 2026-08-26까지
준서TL의 의견: scratch 라벨 제안 → 채택하지 않음, 신원은 이번 변경에 필요 없음"""
    state = {**_state(request), "situation": "관련 기록 조사 완료"}
    questions = unresolved_questions(state)
    text = str(questions)
    assert "reader 운영 판정" in text and "미할당" in text
    assert "준서TL" not in text and "skcc.x1103" not in text

    answered = {**_state(request, "reader 운영 판정 자료는 미할당으로 만들어.", request=request),
                "situation": "관련 기록 조사 완료", "turn_continuation": True}
    assert unresolved_questions(answered) == []


def test_meeting_owner_followup_merges_original_deadline_and_recovers_exact_task_count():
    set_person_context("meeting-owner-resume-recovery", ["DL-9200"])
    request = """첨부 회의 메모를 바탕으로 Epic DL-9200 아래 Task 2건을 만들어줘.
@이다은 — writer 증빙 패키지 정리, 2026-08-23까지
StarRocks reader 운영 판정 자료 정리 by ... 2026-08-26까지
담당 얘기를 쓰다가 회의 종료. 요청·지시자는 최민서M."""
    answer = ("reader 운영 판정 자료 정리는 아직 담당자를 정하지 못했어. 미할당으로 만들어. "
              "요청·지시자는 skcc.x1042 최민서가 맞아.")
    state = {**_state(request, answer, request=answer), "intent": "plan_work",
             "turn_continuation": True, "situation": "관련 자료 조사 완료"}
    records = meeting_owner_records(state)
    reader = next(row for row in records if "reader" in row["work"])
    assert reader["due"] == "2026-08-26" and reader["owner_decision"] == "unassigned"
    items = _recover_decided_meeting_tasks(state)
    assert len(items) == 2
    assert items[0]["assignee"] == "skcc.i2011"
    assert "assignee" not in items[1] and items[1]["assignee_source"] == "user_unassigned"
    assert [item["duedate"] for item in items] == ["2026-08-23", "2026-08-26"]
    scope_question = [{"question": "각 Task의 작업 범위를 구체적으로 알려주세요.",
                       "field": "scope", "kind": "text", "required_input": True}]
    assert _drop_unneeded_meeting_questions(state, scope_question) == []
    duplicate_parent = [{"question": "DL-9200에서 같은 작업을 진행 중입니다.",
                         "field": "duplicate", "kind": "choice", "required_input": True}]
    assert _drop_unneeded_meeting_questions(state, duplicate_parent) == []


def test_meeting_created_task_bodies_do_not_repeat_sibling_titles_as_exclusions():
    state = _state("회의 기록으로 Epic DL-9200 아래 Task 2건을 만들어줘")
    items = [
        {"summary": "writer 증빙", "description":
         "<h3>작업 범위</h3><ul><li>포함: writer 증빙</li>"
         "<li>제외: reader 증빙 패키지</li></ul>"},
        {"summary": "reader 검증", "description":
         "<h3>작업 범위</h3><ul><li>포함: reader 검증</li>"
         "<li>제외(별도 ticket): writer 증빙</li></ul>"},
    ]
    _drop_meeting_sibling_exclusions(state, items)
    assert "reader 검증" not in items[0]["description"]
    assert "writer 증빙" not in items[1]["description"]


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


def test_explicit_unassigned_is_a_user_assignment_decision_not_a_recommendation_gap():
    draft = {"items": [
        {"summary": "writer", "assignee": "skcc.i2011", "assignee_source": "user"},
        {"summary": "reader", "assignee": "", "assignee_source": "user_unassigned"},
    ]}
    assert _all_assignees_user_specified(draft)
    rows = _user_fixed_assignments(draft)
    assert rows[1]["user"] == ""
    assert rows[1]["reasons"] == ["사용자 지정 미할당"]


def test_assignment_renderer_removes_every_stale_owner_section():
    items = [{"summary": "writer", "assignee": "skcc.i2011"},
             {"summary": "reader", "assignee": "skcc.x1402"}]
    assignments = [
        {"index": 0, "user": "skcc.i2011", "reasons": ["사용자 지정 담당자"]},
        {"index": 1, "user": "skcc.x1402", "reasons": ["사용자 지정 담당자"]},
    ]
    raw = ("### 담당자 및 배정 근거\n- Task 1: [~skcc.x1103]\n- Task 2: [~skcc.x1103]\n\n"
           "### 담당 제안\n| 티켓 | 추천 |\n|---|---|\n| old | [~skcc.x1103] |\n\n"
           "### 검증 결과\n정상")
    got = _render_assignment_section(raw, items, assignments)
    assert got.count("### 담당 제안") == 1
    assert "[~skcc.x1103]" not in got
    assert "[~skcc.i2011]" in got and "[~skcc.x1402]" in got


def test_assignment_renderer_replaces_bare_assignment_reason_heading():
    items = [{"summary": "NDV 생성", "assignee": "skcc.x1103"}]
    assignments = [{"index": 0, "user": "skcc.x1103", "reasons": ["진행중 8건"]}]
    got = _render_assignment_section(
        "### 배정 근거\n- 최하은이 예전에 해봄\n\n### 승인 요청\n승인해 주세요.",
        items, assignments)
    assert "최하은" not in got
    assert got.count("### 담당 제안") == 1
    assert "[~skcc.x1103]" in got


def test_meeting_comment_mentions_are_repaired_to_confirmed_identities():
    set_person_context("meeting-comments", ["DL-9201", "DL-9202"])
    request = "회의 결정 댓글: writer 결과는 @이다은, reader는 하은님, 준서TL이 검토"
    answer = "준서TL은 skcc.x1327 임준서야."
    state = _state(request, answer, request=request)
    plan = {"comment": ("writer 결과는 {{mention:skcc.i2101}}이 공유, "
                         "reader 결과는 하은님이 공유, 검토는 준서TL"), "comments": []}
    _canonicalize_meeting_mentions(state, plan)
    assert "[~skcc.i2011]" in plan["comment"]
    assert "[~skcc.x1402]" in plan["comment"]
    assert "[~skcc.x1327]" in plan["comment"]
    assert "skcc.i2101" not in plan["comment"]


def test_meeting_comment_preserves_markdown_and_drops_excluded_scope_metadata():
    set_person_context("meeting-comment-format", ["DL-9201", "DL-9202", "DL-7001"])
    request = ("회의 결정사항을 DL-9201, DL-9202 댓글로 알려줘. writer는 @이다은. "
               "배경 이력은 DL-7001이지만 그 티켓에는 댓글을 달지 않음. 준서TL이 검토")
    answer = "준서TL은 skcc.x1327 임준서야."
    state = _state(request, answer, request=request)
    plan = {"comment": ("### 회의 결정사항 - 1차 PoC 대상 5개\n- 최종 검토는 "
                        "[~skcc.x1327] 임준서님이 담당합니다. ### 참고\n"
                        "- 배경 이력은 DL-7001에 기록되어 있습니다"), "comments": []}
    _canonicalize_meeting_mentions(state, plan)
    body = plan["comment"]
    assert "### 회의 결정사항\n\n- 1차" in body
    assert "[~skcc.x1327]" in body and "임준서님" not in body
    assert "DL-7001" not in body and "### 참고" not in body
    assert "\n- 최종" in body


def test_same_field_value_treats_list_order_as_a_noop():
    from app.agent.workflow.agents.work_architect import _same_field_value

    assert _same_field_value(["Catalog"], ["Catalog"])
    assert _same_field_value(["a", "b"], ["b", "a"])
    assert not _same_field_value(["a"], ["a", "b"])


def test_bulk_meeting_comment_keeps_role_mentions_and_markdown(monkeypatch):
    from app.agent.workflow.agents import work_architect as module

    monkeypatch.setattr(module, "_client_issue", lambda key: {"fields": {
        "summary": key, "assignee": {"name": "skcc.owner"}}})
    rows = module._bulk_comment_preview(
        ["DL-1"],
        "### 회의 결정사항\n\n- writer: [~skcc.writer]\n- 최종 검토: [~skcc.reviewer]",
    )
    body = rows[0]["body"]
    assert body.startswith("### 회의 결정사항\n\n- 알림: [~skcc.owner]")
    assert "[~skcc.writer]" in body and "[~skcc.reviewer]" in body


def test_bulk_change_plan_uses_all_authoritative_meeting_decisions(monkeypatch):
    from app.agent.workflow.agents import work_architect as module

    set_person_context("meeting-bulk-decision", ["DL-9201", "DL-9202", "DL-7001"])
    request = """회의 결정사항을 관련 Task DL-9201, DL-9202 두 건의 댓글로 알려줘.
- 1차 PoC 대상은 5개 테이블
- StarRocks reader 검증 전 운영 반영 보류
- writer 결과는 @이다은, reader 결과는 하은님이 공유
- 준서TL이 최종 검토
- 배경 이력은 DL-7001이지만 그 티켓에는 댓글을 달지 않음"""
    answer = "이 회의의 준서TL은 skcc.x1327 임준서야. 댓글 초안을 계속해줘."
    state = {**_state(request, answer, request=request), "intent": "modify",
             "bulk_targets": ["DL-9201", "DL-9202"]}
    monkeypatch.setattr(module, "_ticket_exists", lambda key: key in ("DL-9201", "DL-9202"))
    monkeypatch.setattr(module, "_client_issue", lambda key: {"fields": {
        "summary": key, "assignee": {"name": "skcc.owner"}}})

    plan, questions = module._change_plan(
        state, {"change": {"keys": ["DL-9201", "DL-9202"],
                            "comment": "담당자에게 알림"}, "rationale": ""}, [], [])
    module._canonicalize_meeting_mentions(state, plan)
    assert not questions and plan["keys"] == ["DL-9201", "DL-9202"]
    assert all(value in plan["comment"] for value in ("5개", "StarRocks", "운영 반영 보류"))
    assert "DL-7001" not in plan["comment"]
    assert all("5개" in row["body"] and "StarRocks" in row["body"]
               for row in plan["comments"])
    assert "[~skcc.i2011]" in plan["comment"] and "[~skcc.x1327]" in plan["comment"]


def test_meeting_comment_is_built_from_authoritative_decision_bullets():
    from app.agent.workflow.agents.work_architect import _meeting_decision_comment

    state = _state(
        "회의 결정사항을 DL-1, DL-2 댓글로 알려줘.\n\n"
        "- 1차 대상은 5개\n"
        "- writer 결과는 @이다은이 공유\n"
        "- 준서TL이 최종 검토\n"
        "- 배경 이력은 DL-9지만 그 티켓에는 댓글을 달지 않음"
    )
    body = _meeting_decision_comment(state, "")
    assert body.startswith("### 회의 결정사항")
    assert "5개" in body and "@이다은" in body and "준서TL" in body
    assert "DL-9" not in body


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


def test_resolved_meeting_identity_drops_only_stale_identity_warning():
    set_person_context("meeting-stale-identity-warning", ["DL-9200"])
    request = "회의 후속 정리. 하은님이 reader 검증."
    state = _state(request)
    got = canonicalize_reply_mentions(
        state,
        "### 미결·검증\n- 하은님의 정확한 신원 확인 필요\n- reader 실제 소비 여부 확인 필요",
    )
    assert "신원 확인 필요" not in got
    assert "reader 실제 소비 여부 확인 필요" in got


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
    assert "components" not in items[0] and items[0]["duedate"] == "2026-08-30"


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


def test_meeting_reply_owner_table_uses_explicit_deadline_assignments():
    set_person_context("meeting-reply-owner-alignment", ["DL-9200"])
    request = ("회의록 참석: @이다은, {{최민서:1042}}, 하은님. "
               "담당·기한: @이다은은 writer PoC를 2026-08-22까지, "
               "하은님은 StarRocks reader 검증을 2026-08-25까지, "
               "{{최민서:1042}}는 검증 기준 초안을 2026-08-28까지 작성")
    state = {**_state(request), "intent": "ask", "questions": []}
    raw = ("| 작업 | 담당 | 기한 |\n|---|---|---|\n"
           "| writer PoC | {{mention:UI픽스처01}} | 2026-08-22 |\n"
           "| StarRocks reader 검증 | {{mention:UI픽스처01}} | 2026-08-25 |\n"
           "| 검증 기준 초안 | {{mention:UI픽스처01}} | 2026-08-28 |")
    got = canonicalize_meeting_owner_table(state, raw)
    assert "| writer PoC | {{mention:skcc.i2011}} | 2026-08-22 |" in got
    assert "| StarRocks reader 검증 | {{mention:skcc.x1402}} | 2026-08-25 |" in got
    assert "| 검증 기준 초안 | {{mention:skcc.x1042}} | 2026-08-28 |" in got
    assert "UI픽스처01" not in got


def test_meeting_owner_table_never_rewrites_type_or_value_columns_without_an_owner_header():
    set_person_context("meeting-table-shape", ["DL-9200"])
    request = "회의 기록: @이다은 — writer 정리, 2026-08-22"
    state = _state(request)
    approval = """| # | 유형 | 제목 | 상위 | 담당 | 기한 |
|---:|---|---|---|---|---|
| 1 | Task | writer 정리 | DL-9200 | skcc.x1210 | 2026-08-22 |"""
    got = canonicalize_meeting_owner_table(state, approval)
    assert "| 1 | Task | writer 정리 | DL-9200 | {{mention:skcc.i2011}} | 2026-08-22 |" in got

    summary = """| 항목 | 값 | 근거 |
|---|---|---|
| 20개 전체 확대 | 이번 결정 아님 | [1] |"""
    assert canonicalize_meeting_owner_table(state, summary) == summary


def test_meeting_reply_preserves_explicit_background_ticket_in_combined_evidence():
    set_person_context("meeting-reply-explicit-source", ["DL-7001"])
    request = ("회의록을 조사해 요약해줘. 참석: @이다은. "
               "배경은 DL-7001에서 후보를 정했고 운영 반영은 보류하기로 결정.")
    state = {**_state(request), "intent": "ask", "questions": []}
    raw = ("### 결정사항\n\n운영 반영 보류\n\n### 근거\n\n"
           "- [회의록](https://confluence.example/minutes)\n\n### 외부 공식 근거\n\n- 문서")
    got = _canonicalize_meeting_reply(raw, state)
    assert "{{ticket-detail:DL-7001}}" in got
    assert got.index("{{ticket-detail:DL-7001}}") < got.index("### 외부 공식 근거")


def test_curator_drops_only_meeting_terms_defined_by_interview():
    request = "회의 후속 정리. PSR 뜻은 기록에 없으니 조사 후 물어봐."
    answer = "PSR은 PoC Success Review이고 5개 모두 오차 5% 이내여야 해."
    state = _state(request, answer, request=request)
    out = KnowledgeCurator().apply(state, {
        "concepts": [], "our_context": "PSR 정의 확인", "references": [],
        "gaps": ["PSR의 정확한 정의와 적용 방법 확인 필요", "reader 진행 상황 확인 필요"],
    })
    assert out["knowledge_brief"]["gaps"] == ["reader 진행 상황 확인 필요"]


def test_final_meeting_reply_drops_only_resolved_term_gap():
    from app.agent.workflow.meeting_context import prune_resolved_reply_gaps

    request = "회의 후속 정리. PSR 뜻은 기록에 없으니 조사 후 물어봐."
    answer = "PSR은 PoC Success Review이고 5개 모두 오차 5% 이내여야 해."
    state = _state(request, answer, request=request)
    raw = ("### 미결·검증\n\n"
           "- PSR 정의는 관련 문서에 없어 확인 필요\n"
           "- StarRocks reader 실제 소비 여부 확인 필요\n\n"
           "### 근거\n\n"
           "[1] [회의록](https://wiki.example/minutes)\n"
           "- 문서 본문에서 PSR 정의가 기록되지 않음")

    got = prune_resolved_reply_gaps(state, raw)

    unresolved = got.split("### 근거", 1)[0]
    assert "PSR" not in unresolved
    assert "reader 실제 소비" in unresolved
    assert "PSR 정의가 기록되지 않음" in got  # source limitation remains true


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
    assert "회의에서 확정된 변경 사항 반영" not in fields["description"]
    assert "기준이야" not in fields["description"]
    assert "확정하지 않기로 결정" in fields["description"]


def test_exact_meeting_update_body_preserves_each_labelled_decision_without_duplication():
    set_person_context("meeting-update-labelled-body", ["DL-9203"])
    request = """회의 기록에서 마지막 합의만 DL-9203 수정 초안에 반영해줘.
- 제목: [Catalog] Puffin 증거 패키지 정리
- due: 2026-08-30
- 본문 전체 교체: `결정 배경`, `작업 범위`, `완료 조건`. 결정 배경에 이 회의와 요청·지시자 {{최민서:1042}}를 기록. 작업 범위는 5개 표본의 writer 결과와 StarRocks 실제 소비 증거를 한 패키지로 정리. 완료 조건은 내부 결과와 외부 근거를 구분하고 미확인 지원을 확정처럼 쓰지 않는 것
- priority/component/labels는 변경하지 않음"""
    state = {**_state(request), "intent": "modify"}
    description = _explicit_meeting_update_fields(state)["description"]
    assert description.count("요청·지시자") == 1
    assert description.count("5개 표본") == 1
    assert description.count("미확인 지원") == 1
    assert "## 결정 배경\n이 회의와 요청·지시자" in description
    assert "## 작업 범위\n5개 표본" in description
    assert "## 완료 조건\n내부 결과" in description


def test_final_meeting_decision_block_excludes_rejected_optional_field_changes():
    request = """회의 기록에서 마지막 합의만 DL-9203 수정 초안에 반영해줘.
민서M 의견: priority를 P1-Critical로 올릴까? → 결론 안 냄
[회의 종료 직전 합의]
- 제목: [Catalog] Puffin 증거 패키지 정리
- due: 2026-08-30
- 본문 전체 교체: 결정 배경과 완료 조건
- priority/component/labels는 변경하지 않음"""
    state = {**_state(request), "intent": "modify"}
    assert _meeting_unchanged_fields(state) == {"priority", "components", "labels"}


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


def test_meeting_summary_preserves_explicit_operational_hold_decision_word():
    request = ("회의 결정: StarRocks reader 검증 전 운영 반영 보류. "
               "reader 검증은 하은님이 2026-08-25까지 담당")
    state = {**_state(request), "intent": "ask", "questions": []}
    reply = _canonicalize_meeting_reply(
        "### 결정사항\n- 운영 반영은 reader 소비 증거가 나온 뒤에 진행한다\n\n"
        "### 담당·기한\n| 작업 | 담당 | 기한 |\n|---|---|---|\n"
        "| reader 검증 | {{mention:skcc.x1402}} | 2026-08-25 |\n\n"
        "### 미결·검증\n- 구체적인 담당자가 확인되지 않음", state)
    assert "운영 반영 보류" in reply and "증거가 나온 뒤에 진행" in reply
    assert "담당자가 확인되지" not in reply


def test_comment_only_result_contract_never_describes_status_as_a_change():
    state = {**_state("회의 결정을 DL-9201, DL-9202 댓글로 알려줘"), "intent": "modify",
             "change_plan": {"keys": ["DL-9201", "DL-9202"], "changes": {},
                             "comment": "회의 결정", "comments": []}}
    prompt = ResultIntegrator().task(state)
    assert "comment-only" in prompt and "no ticket field or status will change" in prompt


def test_comment_only_approval_reply_is_derived_from_the_exact_payload():
    state = {**_state("회의 결정을 DL-9201, DL-9202 댓글로 알려줘"), "intent": "modify",
             "approval_token": "pending",
             "change_plan": {
                 "keys": ["DL-9201", "DL-9202"], "changes": {},
                 "comments": [
                     {"key": "DL-9201", "body": "[~skcc.i2011] 5개 표본 결과를 공유해 주세요."},
                     {"key": "DL-9202", "body": "[~skcc.x1402] reader 결과를 공유해 주세요."},
                 ],
             }}
    reply = ResultIntegrator()._run(state)["reply"]
    assert "댓글 승인 초안" in reply
    assert "아직 게시되지 않음" in reply
    assert "필드·상태 변경 없음" in reply
    assert "삭제" not in reply
    assert "현재 승인할 티켓 초안 없음" not in reply
    assert all(key in reply for key in ("DL-9201", "DL-9202"))


def test_meeting_comment_storage_uses_jira_mentions_and_drops_scope_meta():
    from app.agent.workflow.agents.work_architect import _canonicalize_meeting_mentions

    state = _state(
        "회의 결정: writer 결과는 @이다은이 공유. 배경은 DL-7001이지만 그 티켓에는 댓글을 달지 않음")
    state["meeting_people"] = {"이다은": "skcc.i2011"}
    plan = {"keys": ["DL-9201"], "comments": [{
        "key": "DL-9201",
        "body": "{{mention:skcc.i2011}} writer 결과를 공유. "
                "배경은 DL-7001이지만 그 티켓에는 댓글을 달지 않음.",
    }]}
    _canonicalize_meeting_mentions(state, plan)
    body = plan["comments"][0]["body"]
    assert "[~skcc.i2011]" in body
    assert "{{mention:" not in body
    assert "댓글을 달지" not in body


def test_meeting_update_description_uses_canonical_jira_mention():
    set_person_context("meeting-update-description-mention", ["DL-9203"])
    state = _state("회의 최종 결정. 요청·지시자 {{최민서:1042}}를 본문에 기록")
    plan = {"key": "DL-9203", "changes": {
        "description": "## 결정 배경\n요청·지시자 {{최민서:1042}}를 기록",
    }}
    _canonicalize_meeting_mentions(state, plan)
    body = plan["changes"]["description"]
    assert "[~skcc.x1042]" in body and "{{최민서:1042}}" not in body
