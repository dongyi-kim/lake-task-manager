# -*- coding: utf-8 -*-
"""실제 Base 판독에서 발견한 primary battery false-positive 회귀."""

import pytest

pytest.importorskip("langchain_core", reason="requirements-agent.txt 미설치")

from tools import agent_compose_eval as editor_eval  # noqa: E402
from tools import agent_create_suite as create_eval  # noqa: E402
from tools import agent_context_change_eval as context_eval  # noqa: E402
from tools import agent_eval_contracts as eval_contracts  # noqa: E402
from tools import agent_eval_fact_relations as fact_relations  # noqa: E402
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


def _reviewed_pending(pending, *, kind, actions, target_count):
    return {
        "reply": "",
        "pending": pending,
        "questions": [],
        "review": {
            "ok": True,
            "errors": [],
            "final_authority": {
                "kind": kind,
                "actions": actions,
                "target_count": target_count,
            },
        },
    }


def test_common_checker_keeps_an_intermediate_structured_failure_red():
    final = _reviewed_pending(
        {"action": "update_ticket", "key": "DL-9203",
         "changes": {"summary": "새 제목"}},
        kind="update", actions=["update_ticket"], target_count=1,
    )
    failed = {
        "error": "private provider validation payload",
        "trace": [{"node": "work_architect",
                   "note": "실패: structured output 실패 — private payload"}],
    }

    flaws = eval_contracts.automatic_contract_flaws([failed, final])

    assert any("turn[0]" in flaw and "structured" in flaw for flaw in flaws)
    assert "private provider" not in " ".join(flaws)


def test_common_checker_enforces_required_and_optional_question_stop_boundaries():
    premature = {
        "questions": [{"question": "담당자를 골라 주세요", "required_input": True,
                       "why_required": "사람을 잘못 지정하면 다른 사용자에게 업무가 배정됨"}],
        "pending": {"action": "create_tickets", "items": [{"summary": "초안"}]},
    }
    optional_stop = {
        "questions": [{"question": "선호하는 구조가 있나요?", "required_input": False}],
    }
    required_stop = {
        "questions": [{"question": "담당자를 골라 주세요", "required_input": True,
                       "why_required": "사람을 잘못 지정하면 다른 사용자에게 업무가 배정됨"}],
    }
    final = _reviewed_pending(
        {"action": "create_tickets", "items": [{"summary": "확정 초안"}]},
        kind="create", actions=["create_tickets"], target_count=1,
    )

    assert any("required_input=true" in flaw
               for flaw in eval_contracts.automatic_contract_flaws([premature]))
    assert any("optional" in flaw
               for flaw in eval_contracts.automatic_contract_flaws([optional_stop]))
    assert not any("required_input" in flaw or "optional" in flaw
                   for flaw in eval_contracts.automatic_contract_flaws(
                       [required_stop, final],
                   ))


def test_common_checker_rejects_reply_pending_and_review_action_reversals():
    output = _reviewed_pending(
        {"action": "add_ticket_comment", "key": "DL-9095",
         "changes": {}, "comment": "결과를 첨부해 주세요"},
        kind="create", actions=["create_tickets"], target_count=2,
    )
    output["reply"] = "### 티켓 생성 승인 초안\n\n**총 2건 · 아직 생성되지 않음**"
    output["review"]["summary"] = "티켓 생성 승인 초안 검증 통과"

    flaws = eval_contracts.automatic_contract_flaws([output])

    assert any("reply" in flaw and "action" in flaw for flaw in flaws)
    assert any("review" in flaw and "kind" in flaw for flaw in flaws)
    assert any("review" in flaw and "action" in flaw for flaw in flaws)
    assert any("review/pending narrative action" in flaw for flaw in flaws)
    assert any("cardinality" in flaw for flaw in flaws)


def test_common_checker_compares_exact_create_fields_in_the_approval_table():
    output = _reviewed_pending(
        {
            "action": "create_tickets",
            "items": [
                {"type": "Task", "summary": "writer 증빙", "epic": "DL-9200",
                 "assignee": "skcc.i2011", "duedate": "2026-08-22"},
                {"type": "Task", "summary": "reader 검증", "epic": "DL-9200",
                 "assignee": "skcc.x1402", "duedate": "2026-08-25"},
            ],
        },
        kind="create", actions=["create_tickets"], target_count=2,
    )
    output["reply"] = """### 티켓 승인 초안

**총 3건 · 아직 생성되지 않음**

| # | 유형 | 제목 | 상위 | 담당 | 기한 |
|---:|---|---|---|---|---|
| 1 | Task | writer 증빙 | DL-9201 | [~skcc.x1042] | 2026-08-23 |
| 2 | Task | reader 검증 | DL-9200 | [~skcc.x1402] | 2026-08-25 |
"""

    flaws = eval_contracts.automatic_contract_flaws([output])

    assert any("cardinality" in flaw for flaw in flaws)
    assert any("parent" in flaw for flaw in flaws)
    assert any("owner" in flaw for flaw in flaws)
    assert any("due" in flaw for flaw in flaws)


def test_common_checker_counts_create_children_without_corrupting_review_root_count():
    output = _reviewed_pending(
        {
            "action": "create_tickets",
            "items": [{"type": "Task", "summary": "통계 파이프라인", "assignee": ""}],
            "children": [
                {"type": "Sub-Task", "summary": "설계", "parent_index": 0,
                 "assignee": "skcc.i2011"},
                {"type": "Sub-Task", "summary": "검증", "parent_index": 0,
                 "assignee": "skcc.x1402"},
            ],
        },
        kind="create", actions=["create_tickets"], target_count=1,
    )
    output["reply"] = """### 티켓 승인 초안

**총 3건 · 아직 생성되지 않음**

| # | 유형 | 제목 | 상위 | 담당 | 기한 |
|---:|---|---|---|---|---|
| 1 | Task | 통계 파이프라인 | 최상위 | 미할당 | — |
| 2 | Sub-Task | 설계 | 통계 파이프라인 | [~skcc.i2011] | — |
| 3 | Sub-Task | 검증 | 통계 파이프라인 | [~skcc.x1402] | — |
"""

    assert eval_contracts.automatic_contract_flaws([output]) == []


def test_common_checker_does_not_treat_no_further_edits_as_an_update_action():
    output = _reviewed_pending(
        {"action": "create_tickets", "items": [{"type": "Task", "summary": "초안"}]},
        kind="create", actions=["create_tickets"], target_count=1,
    )
    output["review"]["summary"] = (
        "요청 조건을 충족하며 별도의 추가 수정 없이 승인하여 생성할 수 있습니다."
    )
    assert eval_contracts.automatic_contract_flaws([output]) == []


def test_common_checker_distinguishes_comment_draft_wording_from_ticket_creation():
    comment = _reviewed_pending(
        {
            "action": "add_ticket_comment", "key": "DL-9095", "changes": {},
            "comment": "성능 측정 결과와 원본 로그를 첨부해 주세요",
        },
        kind="comment", actions=["add_ticket_comment"], target_count=1,
    )
    comment["reply"] = "### 댓글 승인 초안\n\n아직 게시되지 않았습니다."
    comment["review"]["summary"] = "댓글 승인 초안을 생성했습니다."
    assert eval_contracts.automatic_contract_flaws([comment]) == []

    create = _reviewed_pending(
        {"action": "create_tickets", "items": [{"type": "Task", "summary": "초안"}]},
        kind="create", actions=["create_tickets"], target_count=1,
    )
    create["review"]["summary"] = "댓글 승인 초안을 생성했습니다."
    assert any("review/pending narrative action" in flaw
               for flaw in eval_contracts.automatic_contract_flaws([create]))


def test_common_checker_rejects_an_empty_update_card():
    output = _reviewed_pending(
        {"action": "update_ticket", "key": "DL-9203", "changes": {}},
        kind="none", actions=[], target_count=0,
    )
    assert any("실행 가능한 effect" in flaw
               for flaw in eval_contracts.automatic_contract_flaws([output]))


@pytest.mark.parametrize(("pending", "actions"), [
    (
        {
            "action": "update_ticket", "key": "DL-9203",
            "changes": {"status": "Done"}, "comment": "",
        },
        ["transition_ticket"],
    ),
    (
        {
            "action": "update_ticket", "key": "DL-9203",
            "changes": {"link": "blocks → DL-9204"}, "comment": "연결 사유",
        },
        ["link_tickets", "add_ticket_comment"],
    ),
])
def test_common_checker_normalizes_transition_and_link_approval_cards(pending, actions):
    output = _reviewed_pending(
        pending, kind="update", actions=actions, target_count=1,
    )
    assert eval_contracts.automatic_contract_flaws([output]) == []


def test_common_checker_preserves_bulk_action_authority_for_one_declared_target():
    output = _reviewed_pending(
        {
            "action": "update_tickets", "keys": ["DL-9203"],
            "changes": {"priority": "P4-Trivial"}, "comment": "변경 사유",
        },
        kind="update", actions=["update_tickets", "add_ticket_comments"],
        target_count=1,
    )
    assert eval_contracts.automatic_contract_flaws([output]) == []


def test_common_checker_compares_exact_update_field_removals():
    output = _reviewed_pending(
        {
            "action": "update_ticket", "key": "DL-9203",
            "changes": {"duedate": "", "parent": "", "assignee": ""},
        },
        kind="update", actions=["update_ticket"], target_count=1,
    )
    output["reply"] = """### 변경 승인 초안

| 필드 | 현재 | 변경 |
|---|---|---|
| 기한 | 2026-08-22 | 2026-08-23 |
| parent | DL-9200 | DL-9201 |
| 담당자 | skcc.i2011 | skcc.x1042 |
"""

    flaws = eval_contracts.automatic_contract_flaws([output])

    assert any("due" in flaw for flaw in flaws)
    assert any("parent" in flaw for flaw in flaws)
    assert any("owner" in flaw for flaw in flaws)

    output["reply"] = """### 변경 승인 초안

| 필드 | 현재 | 변경 |
|---|---|---|
| 기한 | 2026-08-22 | — |
| parent | DL-9200 | — |
| 담당자 | skcc.i2011 | 미할당 |
"""
    assert eval_contracts.automatic_contract_flaws([output]) == []


@pytest.mark.parametrize(("html", "needle"), [
    ('<p>참조 D-9040</p>', "pseudo ticket"),
    ('<p>@이다은 검토 요청</p>', "raw mention"),
    ('<p>[설계 문서](https://docs.example.test/design)</p>', "Markdown"),
    ('<p>문서 https://docs.example.test/design 참고</p>', "bare URL"),
])
def test_editor_checker_rejects_generic_final_renderer_defects(html, needle):
    flaws = eval_contracts.editor_renderer_contract_flaws({"ok": True, "html": html})
    assert any(needle in flaw for flaw in flaws)


def test_editor_checker_accepts_canonical_rendered_references():
    result = {
        "ok": True,
        "html": (
            '<p><a class="jira-badge tkt" data-key="DL-9040" '
            'href="/browse/DL-9040">DL-9040</a> '
            '<span data-type="mention" data-id="skcc.i2011">@이다은</span> '
            '<a href="https://docs.example.test/design">설계 문서</a></p>'
        ),
        "references": [
            {"kind": "ticket", "key": "DL-9040", "resolved": True},
            {"kind": "person", "key": "skcc.i2011", "resolved": True},
        ],
    }
    assert eval_contracts.editor_renderer_contract_flaws(result) == []


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


def _starr1_payload(
    *,
    writer="5개 표본의 writer PoC 수행을 완료하고 결과를 확보함",
    validation="StarRocks reader와 optimizer 소비 검증은 진행 중이며 지원 여부는 미확정",
    rollout="reader 검증 완료 전까지 운영 반영을 보류함",
):
    return {
        "reply": "",
        "pending": {
            "items": [{
                "type": "Task",
                "summary": "StarRocks Puffin NDV 통계 파이프라인 1차 구현",
                "description": (
                    f"<h3>배경</h3><p>{writer}</p>"
                    "<h3>작업 범위</h3><ul><li>최소 기능 1차 구현</li>"
                    "<li>제외: 전체 테이블 확대</li></ul>"
                    "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                    f"<li data-checked=\"false\">{rollout}</li></ul>"
                ),
            }],
            "children": [{
                "type": "Sub-Task",
                "summary": "reader 호환성 검증",
                "description": (
                    "<h3>작업 범위</h3><ul>"
                    f"<li>{validation}</li></ul>"
                    "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                    "<li data-checked=\"false\">검증 로그와 판정 결과를 기록함</li></ul>"
                ),
            }],
        },
    }


def test_starr1_checker_reads_payload_descriptions_not_reply_or_retrieval_evidence():
    assert create_eval._starr1_contract_flaws(_starr1_payload()) == []

    prose_only = _starr1_payload(
        writer="writer PoC 관련 작업",
        validation="reader 검증 작업",
        rollout="운영 반영 제외",
    )
    prose_only["reply"] = (
        "5개 표본 writer PoC 완료. reader와 optimizer 소비 검증은 진행 중·미확정. "
        "reader 검증 완료 전 운영 반영 보류."
    )
    prose_only["evaluationEvidence"] = {"evidence": [prose_only["reply"]]}

    flaws = create_eval._starr1_contract_flaws(prose_only)
    assert len(flaws) == 3
    assert any("5개 표본 writer PoC 완료" in flaw for flaw in flaws)
    assert any("진행 중·미확정" in flaw for flaw in flaws)
    assert any("운영 반영 보류" in flaw for flaw in flaws)


@pytest.mark.parametrize(("override", "expected"), [
    ({"writer": "writer PoC 수행을 완료함"}, "5개 표본 writer PoC 완료"),
    ({"validation": "reader 소비 검증은 진행 중"}, "진행 중·미확정"),
    ({"rollout": "운영 반영은 이번 1차 범위에서 제외함"}, "운영 반영 보류"),
])
def test_starr1_checker_reports_each_missing_internal_validation_fact(override, expected):
    flaws = create_eval._starr1_contract_flaws(_starr1_payload(**override))
    assert any(expected in flaw and "누락" in flaw for flaw in flaws)


def test_starr1_writer_completion_is_not_reversed_by_nearby_reader_progress():
    output = _starr1_payload(writer=(
        "writer 생성 PoC가 완료되었고 reader 검증은 진행 중임"
    ))
    flaws = create_eval._starr1_contract_flaws(output)
    assert any("5개 표본 writer PoC 완료" in flaw and "누락" in flaw for flaw in flaws)
    assert not any("writer PoC를 미완료" in flaw for flaw in flaws)


def test_starr1_writer_completion_ignores_another_actors_progress_state():
    output = _starr1_payload(writer=(
        "5개 표본 writer PoC 완료했고 reader 검증은 진행 중이다"
    ))
    flaws = create_eval._starr1_contract_flaws(output)
    assert not any("writer PoC" in flaw for flaw in flaws)


def test_starr1_writer_completion_composes_adjacent_same_subject_facts():
    output = _starr1_payload(writer=(
        "writer PoC를 완료했다. writer PoC 대상은 5개 표본이다"
    ))
    flaws = create_eval._starr1_contract_flaws(output)
    assert not any("writer PoC" in flaw for flaw in flaws)


def test_starr1_writer_sample_count_must_belong_to_the_writer_completion_fact():
    output = _starr1_payload(writer=(
        "writer PoC 완료. 5개 표본은 reader 테스트 대상"
    ))
    flaws = create_eval._starr1_contract_flaws(output)
    assert any("5개 표본 writer PoC 완료" in flaw for flaw in flaws)


@pytest.mark.parametrize("planning", ["예정", "계획", "목표"])
def test_starr1_writer_planned_completion_is_not_completed_evidence(planning):
    output = _starr1_payload(
        writer=f"5개 표본 writer PoC 완료 {planning}",
    )
    flaws = create_eval._starr1_contract_flaws(output)
    assert any("5개 표본 writer PoC 완료" in flaw for flaw in flaws)


def test_starr1_reader_reversal_is_not_masked_by_optimizer_uncertainty():
    output = _starr1_payload(validation=(
        "reader 검증 완료 및 지원 확정. optimizer 검증은 진행 중이며 미확정"
    ))
    flaws = create_eval._starr1_contract_flaws(output)
    assert any("reader/optimizer" in flaw and "완료·확정" in flaw for flaw in flaws)


def test_starr1_validation_composes_adjacent_same_actor_pair_facts():
    output = _starr1_payload(validation=(
        "reader와 optimizer 소비 검증을 진행 중이다. "
        "reader와 optimizer 지원 여부는 미확정이다"
    ))
    flaws = create_eval._starr1_contract_flaws(output)
    assert not any(
        "reader/optimizer" in flaw or "reader와 optimizer" in flaw
        for flaw in flaws
    )


def test_starr1_validation_does_not_mix_different_actor_predicates():
    output = _starr1_payload(validation=(
        "reader 소비 검증은 진행 중이고 optimizer 지원 여부는 미확정"
    ))
    flaws = create_eval._starr1_contract_flaws(output)
    assert any("reader와 optimizer" in flaw and "누락" in flaw for flaw in flaws)


def test_starr1_negated_rollout_approval_is_a_valid_hold_not_a_reversal():
    output = _starr1_payload(rollout=(
        "운영 반영을 승인하지 않고 reader 검증 완료 전까지 보류"
    ))
    flaws = create_eval._starr1_contract_flaws(output)
    assert not any("운영 반영" in flaw for flaw in flaws)


def test_starr1_rollout_hold_composes_an_explicit_adjacent_antecedent():
    output = _starr1_payload(rollout=(
        "reader 검증은 아직 완료 전이다. 따라서 운영 반영을 보류한다"
    ))
    flaws = create_eval._starr1_contract_flaws(output)
    assert not any("운영 반영" in flaw for flaw in flaws)


def test_starr1_r28_raw_payload_replays_as_typed_fact_relations():
    """The r28 raw draft is semantically faithful despite DOM/prose morphology.

    This is intentionally a minimal replay of the actual payload, not a new product
    phrase fixture: completion is inflected, the two validation actors share predicates,
    and the rollout condition inherits its actor across clauses in one DOM leaf.
    """
    output = _starr1_payload(
        writer=(
            "DL-9201에서 5개 표본에 대한 Writer PoC가 완료되어 "
            "Puffin 파일 생성 결과가 확보되었다"
        ),
        validation=(
            "reader 경로와 optimizer 실행계획을 함께 확인 중입니다. / "
            "지원 여부는 아직 확정하지 않았습니다"
        ),
        rollout=(
            "StarRocks reader 소비 가능성을 단계적으로 검증한다. / "
            "검증 전 운영 반영은 금지한다"
        ),
    )

    assert create_eval._starr1_contract_flaws(output) == []


def test_typed_fact_relation_engine_is_product_neutral():
    producer = fact_relations.FactTerm("producer", r"\bproducer\b")
    consumer = fact_relations.FactTerm("consumer", r"\bconsumer\b")
    indexer = fact_relations.FactTerm("indexer", r"\bindexer\b")
    trial = fact_relations.FactTerm("trial", r"\btrial\b")
    samples = fact_relations.FactTerm("sample scope", r"\b3 samples\b")
    validation = fact_relations.FactTerm("validation", r"\bvalidation\b")
    deployment = fact_relations.FactTerm(
        "deployment", r"\bproduction deployment\b",
    )
    contracts = (
        fact_relations.FactRelationContract(
            "baseline", "single",
            fact_relations.RelationRef(
                (producer,), (trial,), (samples,), (consumer, indexer),
            ),
            ("completed",), ("incomplete", "in_progress"), "missing baseline", "reversed baseline",
        ),
        fact_relations.FactRelationContract(
            "shared state", "shared",
            fact_relations.RelationRef((consumer, indexer)),
            ("in_progress", "unconfirmed"), ("confirmed",),
            "missing shared state", "reversed shared state",
        ),
        fact_relations.FactRelationContract(
            "gate", "gate",
            fact_relations.RelationRef((consumer,), (validation,)),
            ("held", "before_boundary"), ("positive_action",),
            "missing gate", "reversed gate",
            fact_relations.RelationRef((), (deployment,)),
        ),
    )
    valid = [
        "<p>The producer trial completed for 3 samples.</p>"
        "<p>The consumer and indexer are together under validation. "
        "Support is unconfirmed.</p>"
        "<p>Consumer validation is incomplete before release. "
        "Therefore production deployment is on hold.</p>"
    ]
    assert fact_relations.fact_relation_flaws(valid, contracts) == []

    reversed_baseline = [valid[0].replace("completed", "is incomplete")]
    assert fact_relations.fact_relation_flaws(
        reversed_baseline, contracts[:1],
    ) == ["reversed baseline"]


def test_starr1_typed_relation_composes_adjacent_dom_leaves():
    output = _starr1_payload(
        writer=(
            "<span>writer PoC를 완료했습니다</span>"
            "<span>대상은 5개 표본입니다</span>"
        ),
        validation=(
            "<span>reader와 optimizer 소비 검증을 진행하고 있습니다</span>"
            "<span>지원 여부는 아직 확정하지 않았습니다</span>"
        ),
        rollout=(
            "<span>reader 검증은 완료 전입니다</span>"
            "<span>따라서 운영 반영을 보류합니다</span>"
        ),
    )

    assert create_eval._starr1_contract_flaws(output) == []


@pytest.mark.parametrize(("override", "expected"), [
    ({"writer": "5개 표본의 writer PoC는 미완료 상태임"}, "미완료·미수행"),
    ({"validation": "reader와 optimizer 소비 검증 완료, 지원은 확정됨"},
     "완료·확정 상태"),
    ({"rollout": "reader 검증 전 운영 반영을 승인해 진행함"},
     "검증 완료 전에 운영 반영"),
])
def test_starr1_checker_reports_each_reversed_internal_validation_fact(override, expected):
    flaws = create_eval._starr1_contract_flaws(_starr1_payload(**override))
    assert any(expected in flaw and "뒤집음" in flaw for flaw in flaws)


def test_create_checker_keeps_failed_turns_failed_after_a_later_valid_payload():
    final = _starr1_payload()
    failed = {
        "error": "provider-specific secret validation detail",
        "trace": [{"note": "업무 구체화 실패"}],
    }
    flaws = create_eval._all_contract_flaws(
        final, [], [failed, final], structure_ok=True, case_id="STARR1",
    )
    diagnostic = (
        "turn[0] Agent 실행 오류 또는 structured output 실패 기록 — "
        "후속 turn 성공과 무관하게 자동 계약 실패"
    )
    assert diagnostic in flaws
    assert "provider-specific" not in " ".join(flaws)

    trace_only = {"error": "", "trace": [{
        "note": "실패: structured output 실패 — private validation payload",
    }]}
    assert create_eval._turn_execution_flaws([trace_only]) == [diagnostic]


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


def test_meeting_create_maps_noisy_titles_by_stable_source_fields_not_first_substring():
    rows = [
        {"type": "Task", "summary": "writer reader 증빙 패키지", "epic": "DL-9200",
         "assignee": "skcc.i2011", "duedate": "2026-08-22",
         "description": "배경 회의 skcc.x1042\n작업 범위 writer\n완료 조건"},
        {"type": "Task", "summary": "writer reader 검증 결과", "epic": "DL-9200",
         "assignee": "skcc.x1402", "duedate": "2026-08-25",
         "description": "배경 회의 skcc.x1042\n작업 범위 reader\n완료 조건"},
        {"type": "Task", "summary": "writer reader 로그 마스킹", "epic": "DL-9200",
         "assignee": "", "duedate": "2026-08-27",
         "description": "배경 회의 skcc.x1042\n작업 범위 로그 마스킹\n완료 조건"},
    ]
    output = {"pending": {"action": "create_tickets", "items": rows}, "questions": []}

    assert meeting_eval._meeting_fragment_create_ok(output, [output])

    # An auditor-blocked raw turn with no executable payload remains a real failure.
    blocked = {"pending": None, "questions": [], "review": {"ok": False}}
    assert not meeting_eval._meeting_fragment_create_ok(blocked, [blocked])

    for index, row in enumerate(rows):
        row["outcome_refs"] = [f"outcome:opaque-{index}"]
    output["pending"]["items"] = list(reversed(rows))
    assert meeting_eval._meeting_fragment_create_ok(output, [output])

    for row, outcome_id in zip(rows, ("task_writer", "task_reader", "task_masking")):
        row["outcome_refs"] = [outcome_id]
    assert meeting_eval._meeting_fragment_create_ok(output, [output])

    rows[0]["outcome_refs"] = ["task_reader"]
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


def test_context_checker_replays_every_intermediate_exact_field_request():
    inputs = list(context_eval._CTX3_INPUTS)
    outputs = [
        {
            "reply": "DL-9203 priority P1-Critical 변경 승인 초안",
            "pending": {
                "action": "update_ticket", "key": "DL-9203",
                "changes": {"priority": "P1-Critical"},
            },
        },
        {
            "reply": "DL-9203 댓글 승인 초안",
            "pending": {
                "action": "add_ticket_comment", "key": "DL-9203", "changes": {},
                "comment": "회의 결정사항을 공유합니다",
            },
        },
        {
            "reply": "DL-9203 제목 [Catalog] Puffin NDV 결과 템플릿 정리 변경 승인 초안",
            "pending": {
                "action": "update_ticket", "key": "DL-9203",
                "changes": {"summary": "[Catalog] Puffin NDV 결과 템플릿 정리"},
                "comment": "",
            },
        },
    ]

    flaws = context_eval._intermediate_request_field_flaws(inputs, outputs)
    assert any("turn[0]" in flaw and "duedate" in flaw and "2026-08-31" in flaw
               for flaw in flaws)
    assert any("turn[0]" in flaw and "reply" in flaw and "2026-08-31" in flaw
               for flaw in flaws)
    checker = next(case[3] for case in context_eval.CASES if case[0] == "CTX3")
    assert not checker(outputs[-1], outputs)

    outputs[0]["pending"]["changes"]["duedate"] = "2026-08-31"
    outputs[0]["reply"] += " · due 2026-08-31"
    assert context_eval._intermediate_request_field_flaws(inputs, outputs) == []
    assert checker(outputs[-1], outputs)


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
