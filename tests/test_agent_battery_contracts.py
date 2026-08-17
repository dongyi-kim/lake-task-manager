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


def test_duplicate_checker_reads_the_structured_form_without_requiring_prose_echo():
    exact = {"reply": "아래에서 선택해 주세요", "questions": [{
        "question": ('DL-9072 "[ETL] 프로듀서 Avro 직렬화 전환"에서 같은 작업을 진행 중. '
                     "근거: 동일 전환 범위. 기존 티켓에 범위를 추가할지 선택"),
        "options": ["DL-9072에 범위를 추가", "별도 티켓으로 분리"],
    }]}
    assert create_eval._duplicate_decision_ok(exact)
    assert not create_eval._duplicate_decision_ok({
        "reply": "DL-9072 중복", "questions": [{"question": "어떻게 할까요?"}]})


def test_create_common_checker_preserves_one_explicit_iso_due_on_root_payload():
    turns = ["범위는 최소 기능까지, 마감은 2026-09-30. 알아서 진행해"]
    exact = {"pending": {"items": [{
        "type": "Task", "summary": "파이프라인", "duedate": "2026-09-30",
    }]}}
    assert create_eval._creation_contract_flaws(exact, turns) == []

    changed = {"pending": {"items": [{
        "type": "Task", "summary": "파이프라인", "duedate": "2026-09-25",
    }]}}
    assert any("명시 마감일 불일치" in flaw
               for flaw in create_eval._creation_contract_flaws(changed, turns))

    missing = {"pending": {"items": [{"type": "Task", "summary": "파이프라인"}]}}
    assert any("payload 없음" in flaw
               for flaw in create_eval._creation_contract_flaws(missing, turns))


def test_create_common_checker_does_not_invent_a_due_contract_from_unrelated_or_ambiguous_dates():
    output = {"pending": {"items": [{"type": "Task", "summary": "파이프라인"}]}}
    assert create_eval._creation_contract_flaws(
        output, ["회의는 2026-09-30에 열렸고 초안을 만들어줘"],
    ) == []
    assert create_eval._creation_contract_flaws(
        output, ["A 마감은 2026-09-30, B 기한은 2026-10-02"],
    ) == []
    multiple_roots = {"pending": {"items": [
        {"type": "Task", "summary": "A", "duedate": "2026-09-30"},
        {"type": "Task", "summary": "B"},
    ]}}
    assert create_eval._creation_contract_flaws(
        multiple_roots, ["마감은 2026-09-30"],
    ) == []


def test_create_common_checker_uses_the_latest_explicit_due_turn():
    turns = [
        "마감은 2026-09-30으로 진행해",
        "마감일을 2026-10-02로 변경해줘",
    ]
    latest = {"pending": {"items": [{
        "type": "Task", "summary": "파이프라인", "duedate": "2026-10-02",
    }]}}
    assert create_eval._creation_contract_flaws(latest, turns) == []

    stale = {"pending": {"items": [{
        "type": "Task", "summary": "파이프라인", "duedate": "2026-09-30",
    }]}}
    assert any("명시 마감일 불일치" in flaw
               for flaw in create_eval._creation_contract_flaws(stale, turns))

    # One latest turn assigning two different due dates is ambiguous and stays a human check.
    assert create_eval._creation_contract_flaws(
        latest, [*turns, "A 마감은 2026-10-03, B 기한은 2026-10-05"],
    ) == []


def test_create_common_checker_rejects_bare_ordinal_damage_in_root_tree():
    turns = ["최소 기능 1차 구현까지 만들어줘"]
    output = {"pending": {
        "items": [{
            "type": "Task", "summary": "NDV 파이프라인 1차 구현",
            "description": "1차 구현 범위",
        }],
        "children": [{
            "type": "Sub-Task", "summary": "NDV 파이프라인 차 — 설계",
            "description": "1차 구현 설계",
        }],
    }}
    flaws = create_eval._creation_contract_flaws(output, turns)
    assert any("bare '차'" in flaw for flaw in flaws)

    output["pending"]["children"][0]["summary"] = "NDV 파이프라인 1차 — 설계"
    assert create_eval._creation_contract_flaws(output, turns) == []


def test_create_common_checker_inherits_root_ordinal_without_child_repetition():
    turns = ["최소 기능 1차 구현까지 만들어줘"]
    output = {"draft_items": [{
        "type": "Task",
        "summary": "NDV 파이프라인 1차 구현",
        "description": "1차 범위",
        "children": [{
            "type": "Sub-Task",
            "summary": "reader 호환성 검증",
            "description": "검증 결과 기록",
        }],
    }]}
    assert create_eval._creation_contract_flaws(output, turns) == []

    output["draft_items"][0]["children"][0]["summary"] = "reader 2차 호환성 검증"
    assert any("충돌하는 ordinal" in flaw
               for flaw in create_eval._creation_contract_flaws(output, turns))


def test_create_checker_reads_nested_draft_children_when_review_is_blocked():
    child = {
        "type": "Sub-Task",
        "summary": "NDV 파이프라인 1차 검증",
        "description": "<h3>작업 범위</h3><p>1차 검증</p>",
    }
    blocked = {
        "pending": {},
        "draft_items": [{
            "type": "Task",
            "summary": "NDV 파이프라인 1차 구현",
            "children": [child],
        }],
    }
    assert create_eval.kids(blocked) == [child]

    mirrored = {
        "pending": {"items": blocked["draft_items"], "children": [child]},
        "draft_items": blocked["draft_items"],
    }
    assert create_eval.kids(mirrored) == [child]


def test_create_checker_never_reports_an_unexplained_structural_failure():
    assert create_eval._all_contract_flaws(
        {}, ["작업 만들어줘"], [{}], structure_ok=True,
    ) == []
    flaws = create_eval._all_contract_flaws(
        {}, ["작업 만들어줘"], [{}], structure_ok=False,
    )
    assert flaws == ["케이스별 구조 계약 실패 — 해당 case review spec과 payload를 대조"]


def test_create_common_checker_uses_the_latest_explicit_ordinal_turn():
    turns = [
        "최소 기능 1차 구현까지 만들어줘",
        "범위를 2차 구현까지로 변경해줘",
    ]
    output = {"pending": {
        "items": [{
            "type": "Task", "summary": "NDV 파이프라인 2차 구현",
            "description": "2차 구현 범위",
        }],
        "children": [{
            "type": "Sub-Task", "summary": "NDV 파이프라인 2차 — 설계",
            "description": "2차 구현 설계",
        }],
    }}
    assert create_eval._creation_contract_flaws(output, turns) == []

    output["pending"]["items"][0]["summary"] = "NDV 파이프라인 1차 구현"
    output["pending"]["items"][0]["description"] = "1차 구현 범위"
    output["pending"]["children"][0]["summary"] = "NDV 파이프라인 1차 — 설계"
    output["pending"]["children"][0]["description"] = "1차 구현 설계"
    flaws = create_eval._creation_contract_flaws(output, turns)
    assert flaws
    assert any("원문 ordinal '2차'와 충돌" in flaw for flaw in flaws)
    assert any("root 범위 '2차'와 충돌" in flaw for flaw in flaws)


def test_create_common_checker_rejects_debug_and_generic_pages_only_in_user_facing_evidence():
    base = {"pending": {"items": [{"type": "Task", "summary": "NDV 파이프라인"}]}}
    debug = {**base, "reply": (
        "### 근거\n\n[1-a] 조회 결과 QueryPlan jira · pages=1 · returned=1 · "
        "canonicalJql=project in (DL)"
    )}
    assert any("debug 관측" in flaw
               for flaw in create_eval._creation_contract_flaws(debug, []))

    generic = {**base, "reply": (
        "### 근거\n\n[1] [starrocks/docs/README.md]("
        "https://github.com/StarRocks/starrocks/blob/main/docs/README.md)\n"
        "- Contributor License Agreement (CLA)와 Markdown syntax 안내"
    )}
    assert any("generic search/home" in flaw
               for flaw in create_eval._creation_contract_flaws(generic, []))

    direct = {**base, "reply": (
        "### 근거\n\n[1] [Iceberg catalog]("
        "https://docs.starrocks.io/docs/data_source/catalog/iceberg/iceberg_catalog/)"
    )}
    assert create_eval._creation_contract_flaws(direct, []) == []

    # Raw retrieval evidence may contain rejected candidates for reviewer inspection;
    # only promoting them into the user-facing source index is an automatic defect.
    internal_only = {**direct, "evaluationEvidence": {
        "webContext": "Search the documentation / docs/README.md / CLA",
    }}
    assert create_eval._creation_contract_flaws(internal_only, []) == []

    debug_after_evidence = {**base, "reply": direct["reply"] + (
        "\n\n### Local debug\n\nQueryPlan · canonicalJql=project in (DL)"
    )}
    assert create_eval._creation_contract_flaws(debug_after_evidence, []) == []


def test_create_question_gate_rejects_optional_structure_stop_and_vague_required_reason():
    optional_structure = {
        "reply": "### 확인 필요\n\n- 요청을 확정하려면 사용자 입력 필요",
        "questions": [{
            "question": "어떤 티켓 구조로 진행할까요?",
            "field": "structure",
            "required_input": False,
            "why_required": "",
        }],
    }
    flaws = create_eval._question_gate_flaws(optional_structure)
    assert any("required_input=false" in flaw for flaw in flaws)
    assert any("optional 구조 선호" in flaw for flaw in flaws)

    vague_required = {"questions": [{
        "question": "대상 테이블은 무엇인가요?",
        "field": "target",
        "required_input": True,
        "why_required": "확인 필요",
    }]}
    assert any("why_required" in flaw
               for flaw in create_eval._question_gate_flaws(vague_required))

    concrete_required = {"questions": [{
        "question": "대상 테이블은 무엇인가요?",
        "field": "target",
        "required_input": True,
        "why_required": "대상 테이블 없이는 생성할 파이프라인 범위와 완료 조건이 달라짐",
    }]}
    assert create_eval._question_gate_flaws(concrete_required) == []


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


def test_heterogeneous_meeting_create_checker_requires_explicit_unassigned_and_instructor_background():
    rows = [
        {"type": "Task", "summary": "writer", "epic": "DL-9200",
         "assignee": "skcc.i2011", "duedate": "2026-08-22",
         "description": "배경 회의 skcc.x1042\n작업 범위\n완료 조건"},
        {"type": "Task", "summary": "reader", "epic": "DL-9200",
         "assignee": "skcc.x1402", "duedate": "2026-08-25",
         "description": "배경 회의 skcc.x1042\n작업 범위\n완료 조건"},
        {"type": "Task", "summary": "로그 마스킹", "epic": "DL-9200",
         "assignee": "", "duedate": "2026-08-27",
         "description": "배경 회의 skcc.x1042\n작업 범위\n완료 조건"},
    ]
    output = {"pending": {"action": "create_tickets", "items": rows}, "questions": []}
    assert meeting_eval._meeting_fragment_create_ok(output, [output])
    rows[-1]["assignee"] = "skcc.x1042"
    assert not meeting_eval._meeting_fragment_create_ok(output, [output])


def test_incomplete_meeting_checker_requires_owner_or_unassigned_interview_before_draft():
    question = {"questions": [{"question": "reader 담당자를 정할까요, 미할당으로 둘까요?"}]}
    final = {"questions": [], "pending": {"action": "create_tickets", "items": [
        {"summary": "writer", "assignee": "skcc.i2011", "due": "2026-08-23",
         "description": "회의 배경 skcc.x1042"},
        {"summary": "reader", "assignee": "", "due": "2026-08-26",
         "description": "회의 배경 skcc.x1042"},
    ]}}
    assert meeting_eval._meeting_missing_owner_ok(final, [question, final])
    assert not meeting_eval._meeting_missing_owner_ok(final, [{**question, "pending": {"action": "create_ticket"}}, final])


def test_context_switch_checker_requires_only_the_latest_exact_change():
    exact = {
        "reply": "DL-9203 priority 변경 초안",
        "pending": {
            "action": "update_ticket", "key": "DL-9203",
            "changes": {"priority": "P4-Trivial"},
        },
    }
    contaminated = {
        **exact,
        "pending": {**exact["pending"], "changes": {
            "priority": "P4-Trivial", "duedate": "2026-08-31",
        }},
    }
    assert context_eval._ctx_unrelated_ok(exact, [])
    assert not context_eval._ctx_unrelated_ok(contaminated, [])


def test_context_return_checker_accepts_the_canonical_single_ticket_action():
    output = {
        "reply": "DL-9095 댓글 승인 초안",
        "pending": {
            "action": "add_ticket_comment", "key": "DL-9095", "changes": {},
            "comment": "2홉 100노드 성능 측정 결과와 원본 로그를 첨부해 주세요",
        },
    }
    turns = [{"reply": "DL-9090 진행"},
             {"reply": "{{mention:skcc.i2011}} 현재 미완료 할당 2건"}, output]
    assert context_eval._ctx_return_ok(output, turns)
    contaminated = list(turns)
    contaminated[1] = {"reply": "{{mention:skcc.i2011}} DL-9090 DL-9095"}
    assert not context_eval._ctx_return_ok(output, contaminated)
