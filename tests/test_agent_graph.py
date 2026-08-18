"""agent/workflow — 그래프 분기 · 되묻기 · HITL 중단/재개.

키 없이 돈다(`fake`). 여기서 검증하는 것은 **문장 품질이 아니라 구조**다 —
어떤 의도가 어떤 길로 가는지, 되묻기가 왜 멈추는지, 승인 전에 정말로 아무것도 안 만드는지.
그건 실 LLM 으로도 확인하기 어렵다(매번 다른 문장이 나오므로).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("langgraph", reason="requirements-agent.txt 미설치")

from app.agent import approval                                     # noqa: E402
from app.agent.workflow import graph as G                          # noqa: E402
from app.agent.workflow.state import (MAX_REVISIONS, Intent, Node)  # noqa: E402


@pytest.fixture(autouse=True)
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "fake")
    import app.infra.settings as S
    monkeypatch.setattr(S, "CACHE_DIR", tmp_path)
    approval.clear()
    G.reset()
    yield
    approval.clear()
    G.reset()


# ── 라우터: State 만 보고 결정한다 ──────────────────────────────────
def test_chitchat_skips_investigation():
    assert G.route_after_request_architect({"intent": Intent.CHITCHAT}) == "respond"


def test_memory_only_context_is_acknowledged_without_research():
    from langchain_core.messages import HumanMessage

    state = {
        "intent": Intent.ASK,
        "messages": [HumanMessage(content=(
            "참고로 다음 주 점검이 예정돼 있어. 지금은 답하지 말고 이 정보만 기억해줘."
        ))],
    }
    assert G.route_after_request_architect(state) == "respond"


def test_shared_context_with_an_actual_request_still_uses_the_normal_route():
    from langchain_core.messages import HumanMessage

    state = {
        "intent": Intent.ASK,
        "messages": [HumanMessage(content=(
            "참고로 다음 주 점검이 예정돼 있어. 이 정보만 기억하고 DL-9090 현황도 알려줘."
        ))],
    }
    assert G.route_after_request_architect(state) == "investigate"


def test_everything_else_investigates_first():
    """조사를 건너뛰고 티켓을 만들어 주는 어시스턴트는 중복 티켓 생성기다.
    plan_work도 충분성 분류와 무관하게 먼저 조사하고, 남은 blocker만 이후 인터뷰한다."""
    for intent in (Intent.ASK, Intent.MODIFY):
        assert G.route_after_request_architect({"intent": intent}) == "investigate"
    assert G.route_after_request_architect({"intent": Intent.PLAN_WORK,
                                  "sufficient": True}) == "investigate"
    assert G.route_after_request_architect({"intent": Intent.PLAN_WORK,
                                  "sufficient": False}) == "investigate"


def test_reviewer_keeps_editorial_advice_non_blocking():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import _partition_model_problems

    state = {
        "messages": [HumanMessage(content="단계별 Sub-Task로 나눠줘")],
        "draft": {"structure": "task_with_subtasks",
                  "structure_source": "user_specified",
                  "items": [{"type": "Task", "children": [{"summary": "구현"}]}]},
    }
    raw = [
        {"check": "request", "message": "과잉 분해로 Sub-Task가 불필요하게 나뉘었다"},
        {"check": "rule", "message": "제목이 동사로 끝나지 않는다"},
        {"check": "grounded", "message": "본문의 DL-9999는 조사 근거에 없다"},
        {"check": "rule", "message": "지정 담당자 skcc.x1402가 존재하지 않는다"},
    ]
    blocking, advice = _partition_model_problems(state, raw)
    assert [p["check"] for p in blocking] == ["grounded"]
    assert len(advice) == 3


def test_reviewer_discards_findings_contradicted_by_authoritative_draft_state():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import Auditor

    state = {
        "request_text": "AcmeDB DeltaSketch 통계 파이프라인을 만들어줘",
        "messages": [HumanMessage(content=(
            "Epic은 네가 골라줘. 마감은 2026-09-30으로 진행해"
        ))],
        "draft": {"mode": "task", "items": [{
            "summary": "[Catalog] AcmeDB DeltaSketch 파이프라인 구현",
            "type": "Task", "epic": "DL-101", "duedate": "2026-09-30",
            "components": ["Catalog"],
            "description": ("<h3>배경</h3><p>파이프라인 구현</p>"
                            "<h3>작업 범위</h3><ul><li>포함: 구현</li>"
                            "<li>제외: 요청 외 변경</li></ul>"
                            "<h3>완료 조건 (DoD)</h3><ul>"
                            "<li>테스트 결과 기록</li><li>리뷰 결과 기록</li></ul>"),
        }]},
    }
    model = {
        "grounded": True, "rule_compliant": True, "answers_request": True,
        "problems": [
            {"index": -1, "check": "request",
             "message": "사용자가 Epic 생성을 요청했으므로 Task 생성과 충돌합니다.",
             "fix": "새 Epic 생성 초안으로 변경해야 합니다."},
            {"index": 0, "check": "request",
             "message": "초안에는 마감 날짜가 명시되어 있지 않습니다.",
             "fix": "마감일을 2026-09-30으로 넣어야 합니다."},
        ],
        "summary": "Epic과 마감이 누락됨",
    }
    result = Auditor().apply(state, model)
    assert result["review"]["ok"] is True
    assert result["review"]["problems"] == []
    contract = Auditor().task(state)
    assert "select_existing" in contract
    assert "2026-09-30" in contract


def test_reviewer_negative_axes_without_projected_problems_fail_closed():
    from app.agent.workflow.agents.auditor import Auditor

    state = {"draft": {"mode": "task", "items": [{
        "summary": "NDV 통계 검증", "type": "Task",
        "description": (
            "<h3>배경</h3><p>NDV 통계 검증 요청됨</p>"
            "<h3>작업 범위</h3><ul><li>포함: 통계 검증</li>"
            "<li>제외: 요청 외 변경</li></ul>"
            "<h3>완료 조건 (DoD)</h3><ul><li>검증 결과 기록</li></ul>"
        ),
    }]}}
    model = {
        "grounded": False, "rule_compliant": False, "answers_request": False,
        "problems": [], "summary": "projection에서 problem 배열이 유실됨",
    }

    review = Auditor().apply(state, model)["review"]

    assert review["ok"] is False
    assert review["checks"] == {
        "grounded": False, "rule_compliant": False, "answers_request": False,
    }
    assert {row.get("check") for row in review["problems"]} == {
        "grounded", "rule", "request",
    }


def test_machine_review_fails_closed_when_material_evidence_obligations_are_omitted():
    """r24 auto-pass missed completed baseline, unconfirmed dependency, and rollout gate."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import _machine_check

    state = {
        "request_text": "AcmeDB DeltaSketch 통계 생성 파이프라인을 만들어줘",
        "messages": [HumanMessage(content="최소 기능 1차까지만. 알아서")],
        "turn_continuation": True,
        "keywords": ["AcmeDB", "DeltaSketch", "통계", "생성", "파이프라인"],
        "evidence": [
            {"key": "DL-9201", "title": "[ETL] DeltaSketch writer PoC",
             "why": "writer baseline", "observations": [{"source": "comment",
              "text": "AcmeWriter가 DeltaSketch 파일을 생성해 5개 표본 결과를 확보했습니다."}]},
            {"key": "DL-9202", "title": "[Runtime] DeltaSketch reader 검증",
             "why": "consumer dependency", "observations": [{"source": "comment",
              "text": ("AcmeReader와 Optimizer 소비 여부를 확인 중이며 지원 여부는 아직 "
                       "확정하지 않았습니다. 실제 소비 증거 전에는 운영 반영을 승인하지 않습니다.")}]},
        ],
        "materialized_ticket_sources": {"ticketDetails": [
            {"key": "DL-9201", "type": "Task", "done": True, "status": "Resolved",
             "summary": "[ETL] DeltaSketch writer PoC", "updated": "2026-08-15",
             "comments": [{"created": "2026-08-15", "body":
                           "AcmeWriter가 DeltaSketch 파일을 생성해 5개 표본 결과를 확보했습니다."}]},
            {"key": "DL-9202", "type": "Task", "done": False,
             "status": "In Progress", "summary": "[Runtime] DeltaSketch reader 검증",
             "updated": "2026-08-17", "description":
             ("AcmeReader와 Optimizer 소비 여부를 확인 중이며 지원 여부는 아직 "
              "확정하지 않았습니다. 실제 소비 증거 전에는 운영 반영을 승인하지 않습니다.")},
        ]},
        "draft": {"mode": "task", "items": [{
            "summary": "[ETL] AcmeDB DeltaSketch 통계 생성 파이프라인 1차 구현",
            "type": "Task", "components": ["ETL"],
            "description": (
                "<h3>배경</h3><p>통계 생성 파이프라인 요청됨</p>"
                "<h3>작업 범위</h3><ul><li>포함: 최소 기능 구현</li>"
                "<li>제외: 운영 반영</li></ul>"
                "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                "<li data-checked=\"false\">실행 로그를 기록한다</li>"
                "<li data-checked=\"false\">결과를 검토한다</li></ul>"
            ),
        }]},
    }

    review = _machine_check(state)

    assert review["ok"] is False
    obligation_errors = [row for row in review["errors"]
                         if row.get("field") == "evidence_obligation"]
    assert obligation_errors
    assert {row.get("obligation_kind") for row in obligation_errors} >= {
        "completed_baseline", "unconfirmed_dependency", "approval_gate",
    }


def test_evidence_audit_compares_html_unescaped_visible_text():
    """HTML escaping an ampersand must not turn a present canonical fact into 'missing'."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.work_architect import (
        WorkArchitect, _evidence_obligation_errors,
    )

    state = {
        "request_text": "AcmeDB R&D 검증 작업을 만들어줘",
        "messages": [HumanMessage(content="AcmeDB R&D 검증 작업을 만들어줘")],
        "keywords": ["AcmeDB", "R&D", "검증"],
        "evidence": [{"key": "DL-9405", "why": "요청과 직접 관련"}],
        "materialized_ticket_sources": {"ticketDetails": [{
            "key": "DL-9405", "type": "Task", "status": "Resolved", "done": True,
            "summary": "[Runtime] AcmeDB R&D 검증", "updated": "2026-08-18",
            "comments": [{"created": "2026-08-18", "body":
                          "AcmeDB R&D 호환성 검증을 완료했습니다."}],
        }]},
    }
    result = WorkArchitect().apply(state, {
        "questions": [], "mode": "task", "structure": "single_task",
        "structure_why": "단일 검증 산출물", "rationale": "",
        "items": [{
            "summary": "[Runtime] AcmeDB R&D 호환성 검증", "type": "Task",
            "background": "호환성 검증 요청됨", "scope_in": ["호환성 검증"],
            "scope_out": [], "dod": ["검증 결과를 기록한다"],
            "components": ["Runtime"],
        }],
    })
    draft = result["draft"]

    assert "R&amp;D" in draft["items"][0]["description"]
    assert _evidence_obligation_errors(state, draft) == []


def test_auditor_rejects_relation_and_state_scattered_outside_the_obligation_marker():
    """A marker on one fragment must not launder its required relation from another block."""
    from app.agent.workflow.agents.auditor import _machine_check

    oid = "evidence:DL-9501:unconfirmed_dependency"
    fact = "DeltaSketch consumption support is unconfirmed."
    relation = "AcmeReader consumes DeltaSketch while AcmeOptimizer validates the plan."
    obligation = {
        "id": oid,
        "kind": "unconfirmed_dependency",
        "source_key": "DL-9501",
        "source_subject": "[Runtime] DeltaSketch reader validation",
        "constraint_context": relation,
        "fact": fact,
        "relationship_facts": [relation],
        "fact_relation": {"fact": fact, "actors": [], "actions": ["support"],
                          "objects": ["deltasketch", "consumption"]},
        "relations": [{"fact": relation, "actors": ["AcmeReader", "AcmeOptimizer"],
                       "actions": ["consumes", "validates"],
                       "objects": ["deltasketch", "plan"]}],
        "item_indexes": [0],
    }
    description = (
        "<h3>배경</h3>"
        f'<p data-evidence-obligation="{oid}">DL-9501의 미확정 dependency: {fact}</p>'
        f"<p>{relation}</p>"
        "<h3>작업 범위</h3><ul><li>포함: reader 검증</li>"
        "<li>제외: 운영 반영</li></ul><h3>완료 조건 (DoD)</h3>"
        '<ul data-type="taskList"><li data-checked="false">검증 결과를 기록한다.</li>'
        '<li data-checked="false">리뷰 결과를 기록한다.</li></ul>'
    )
    state = {"draft": {"mode": "task", "evidence_obligations": [obligation], "items": [{
        "summary": "[Runtime] DeltaSketch reader validation", "type": "Task",
        "components": ["Runtime"], "description": description,
    }]}}

    review = _machine_check(state)
    obligation_errors = [row for row in review["errors"]
                         if row.get("field") == "evidence_obligation"]

    assert obligation_errors, "the complete typed relation must be inside its marked block"
    assert obligation_errors[0].get("obligation_kind") == "unconfirmed_dependency"


def test_auditor_rejects_unmarked_model_prose_that_reverses_a_canonical_actor_role():
    """Canonical consumer evidence cannot coexist with model prose recasting it as producer."""
    from app.agent.workflow.agents.auditor import _machine_check

    oid = "evidence:DL-9502:unconfirmed_dependency"
    fact = "DeltaSketch consumption support is unconfirmed."
    relation = "AcmeReader consumes DeltaSketch."
    obligation = {
        "id": oid,
        "kind": "unconfirmed_dependency",
        "source_key": "DL-9502",
        "source_subject": "[Runtime] DeltaSketch reader validation",
        "constraint_context": relation,
        "fact": fact,
        "relationship_facts": [relation],
        "fact_relation": {"fact": fact, "actors": [], "actions": ["support"],
                          "objects": ["deltasketch", "consumption"]},
        "relations": [{"fact": relation, "actors": ["AcmeReader"],
                       "actions": ["consumes"], "objects": ["deltasketch"]}],
        "item_indexes": [0],
    }
    atomic = (
        "[Runtime] DeltaSketch reader validation — DL-9502: "
        f"{relation} {fact}"
    )
    description = (
        "<h3>배경</h3><p>AcmeReader generates DeltaSketch.</p>"
        f'<p data-evidence-obligation="{oid}">{atomic}</p>'
        "<h3>작업 범위</h3><ul><li>포함: reader 검증</li>"
        "<li>제외: 운영 반영</li></ul><h3>완료 조건 (DoD)</h3>"
        '<ul data-type="taskList"><li data-checked="false">검증 결과를 기록한다.</li>'
        '<li data-checked="false">리뷰 결과를 기록한다.</li></ul>'
    )
    state = {"draft": {"mode": "task", "evidence_obligations": [obligation], "items": [{
        "summary": "[Runtime] DeltaSketch reader validation", "type": "Task",
        "components": ["Runtime"], "description": description,
    }]}}

    review = _machine_check(state)
    obligation_errors = [row for row in review["errors"]
                         if row.get("field") == "evidence_obligation"]

    assert obligation_errors, "unmarked actor/action reversal must fail closed"
    assert obligation_errors[0].get("obligation_kind") == "unconfirmed_dependency"


def test_auditor_role_reversal_check_is_independent_of_atomic_marker_presence():
    """A complete generated marker still fails if separate prose reverses the actor role."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.work_architect import (
        WorkArchitect, _evidence_obligation_errors,
    )

    state = {
        "request_text": "AcmeDB DeltaSketch consumer 검증 Task를 만들어줘",
        "messages": [HumanMessage(content="AcmeDB DeltaSketch consumer 검증 Task를 만들어줘")],
        "keywords": ["AcmeDB", "DeltaSketch", "consumer", "검증"],
        "evidence": [{"key": "DL-9601", "why": "consumer 관계가 직접 관련"}],
        "materialized_ticket_sources": {"ticketDetails": [{
            "key": "DL-9601", "type": "Task", "status": "In Progress", "done": False,
            "summary": "[Runtime] AcmeDB DeltaSketch consumer 검증",
            "comments": [{"created": "2026-08-18", "body":
                          ("AcmeReader consumes DeltaSketch but consumption support "
                           "is unconfirmed.")}],
        }]},
    }
    result = WorkArchitect().apply(state, {
        "questions": [], "mode": "task", "structure": "single_task",
        "structure_why": "단일 검증 산출물", "rationale": "",
        "items": [{
            "summary": "[Runtime] AcmeDB DeltaSketch consumer 검증", "type": "Task",
            "background": "consumer 검증 요청됨", "scope_in": ["소비 지원 검증"],
            "scope_out": ["운영 반영"],
            "dod": ["소비 결과를 기록한다", "검토 결과를 기록한다"],
            "components": ["Runtime"],
        }],
    })
    draft = result["draft"]
    assert _evidence_obligation_errors(state, draft) == []

    body = draft["items"][0]["description"]
    draft["items"][0]["description"] = body.replace(
        "<h3>배경</h3>",
        "<h3>배경</h3><p>AcmeReader generates DeltaSketch.</p>",
        1,
    )
    errors = _evidence_obligation_errors(state, draft)

    assert errors and "producer/consumer" in errors[0]["message"]


def test_reviewer_does_not_treat_selection_intent_as_proof_draft_avoids_epic_creation():
    """A select-existing request is the rule to audit, not proof that the draft obeyed it."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import _partition_model_problems

    state = {
        "request_text": "AcmeDB DeltaSketch 통계 파이프라인을 만들어줘",
        "messages": [HumanMessage(content="Epic은 네가 골라줘")],
        "draft": {"mode": "task", "items": [{
            "summary": "[Catalog] AcmeDB DeltaSketch 파이프라인 구현",
            "type": "Task", "epic": "DL-101",
            "description": (
                "<h3>배경</h3><p>승인 후 새 Epic을 생성해 작업을 배치한다.</p>"
                "<h3>작업 범위</h3><ul><li>새 Epic 생성</li></ul>"
                "<h3>완료 조건 (DoD)</h3><ul><li>Epic 생성 확인</li></ul>"
            ),
        }]},
    }
    finding = {
        "index": 0, "check": "request",
        "message": "기존 Epic 선택 요청과 달리 초안 본문이 새 Epic 생성을 지시합니다.",
        "fix": "새 Epic 생성 문구를 제거하고 검증된 기존 Epic에 연결합니다.",
    }

    blocking, advice = _partition_model_problems(state, [finding])

    assert blocking == [finding]
    assert advice == []


def test_reviewer_does_not_hide_global_missing_claim_when_only_one_root_has_field():
    from app.agent.workflow.agents.auditor import _partition_model_problems

    state = {"draft": {"mode": "task", "items": [
        {"summary": "AcmeDB 설계", "type": "Task", "duedate": "2026-09-30"},
        {"summary": "AcmeDB 배포", "type": "Task", "duedate": ""},
    ]}}
    finding = {"index": -1, "check": "request",
               "message": "요청한 마감일이 일부 초안에 누락되어 있습니다.",
               "fix": "누락된 초안에 마감일을 설정해야 합니다."}
    blocking, advice = _partition_model_problems(state, [finding])
    assert blocking == [finding]
    assert advice == []


def test_reviewer_preserves_explicit_fallback_epic_creation_contract():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import _request_parent_action

    state = {"messages": [HumanMessage(content=(
        "맞는 Epic을 골라줘. 적합한 Epic이 없으면 새로 만들어줘"
    ))]}
    assert _request_parent_action(state) == "create_new"


def test_bug_contract_does_not_require_task_dod():
    from app.agent.workflow.agents.auditor import _machine_check

    state = {"draft": {"mode": "task", "items": [{
        "summary": "[Workbench] 리니지 화면이 빈다", "type": "Bug",
        "components": ["Workbench"],
        "description": ("<h3>재현 경로</h3><p>2홉을 펼친다.</p>"
                        "<h3>기대 동작</h3><p>그래프가 보인다.</p>"
                        "<h3>실제 동작</h3><p>화면이 빈다.</p>")}]}}
    review = _machine_check(state)
    assert review["ok"]
    assert not any("완료 조건" in w.get("message", "") for w in review["warnings"])


def test_explicit_user_due_mismatch_is_a_machine_blocker():
    """A semantically wrong but schema-valid date cannot be approved by model opinion."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import _machine_check

    state = {
        "request_text": "AcmeDB DeltaSketch 파이프라인을 만들어줘",
        "turn_continuation": True,
        "messages": [HumanMessage(content="마감은 2026-09-30으로 진행해. 알아서")],
        "draft": {"mode": "task", "items": [{
            "summary": "[Catalog] AcmeDB DeltaSketch 파이프라인 구현",
            "type": "Task", "components": ["Catalog"], "duedate": "2026-09-25",
            "description": (
                "<h3>배경</h3><p>파이프라인 구현</p>"
                "<h3>작업 범위</h3><ul><li>포함: 구현</li>"
                "<li>제외: 요청 외 변경</li></ul>"
                "<h3>완료 조건 (DoD)</h3><ul><li>구현 결과 확인</li></ul>"
            ),
        }]},
    }

    review = _machine_check(state)

    assert review["ok"] is False
    assert any(error.get("field") == "duedate"
               and "2026-09-30" in error.get("message", "")
               and "2026-09-25" in error.get("message", "")
               for error in review["errors"])


def test_ordinal_type_and_materialized_parent_mismatches_are_machine_blockers():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import _machine_check

    description = (
        "<h3>배경</h3><p>Puffin NDV 검증</p>"
        "<h3>작업 범위</h3><ul><li>차 검증 수행</li></ul>"
        "<h3>완료 조건 (DoD)</h3><ul><li>검증 결과 확인</li></ul>"
    )
    state = {
        "request_text": "Puffin NDV 1차 검증 Bug를 만들어줘",
        "turn_continuation": True,
        "messages": [HumanMessage(content=(
            "기존 Epic은 네가 골라줘. 범위는 1차로 진행해"
        ))],
        "materialized_ticket_sources": {
            "parentCandidateKeys": ["DL-9200"],
            "ticketDetails": [{"key": "DL-9200", "type": "Epic"}],
        },
        "draft": {"mode": "task", "items": [{
            "summary": "[ETL] Puffin NDV 차 검증", "type": "Task",
            "epic": "DL-7001", "components": ["ETL"], "description": description,
            "children": [{
                "summary": "Puffin NDV 차 검증", "type": "Sub-Task",
                "description": description,
            }],
        }]},
    }

    review = _machine_check(state)

    assert review["ok"] is False
    fields = {row.get("field") for row in review["errors"]}
    assert {"ordinal", "type", "parent"}.issubset(fields)


def test_single_root_ordinal_scopes_children_without_repeated_title_noise():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import _deterministic_request_field_errors

    state = {
        "request_text": "StarRocks Puffin NDV 파이프라인 1차 구현 Task를 만들어줘",
        "messages": [HumanMessage(content=(
            "StarRocks Puffin NDV 파이프라인 1차 구현 Task를 만들어줘"
        ))],
    }
    root = {
        "summary": "[ETL] StarRocks Puffin NDV 파이프라인 1차 구현",
        "description": "<p>1차 구현 범위</p>",
        "type": "Task",
        "children": [
            {"summary": "파이프라인 코드 구현", "type": "Sub-Task"},
            {"summary": "회귀 테스트 수행", "type": "Sub-Task"},
        ],
    }

    assert not [row for row in _deterministic_request_field_errors(state, [root])
                if row.get("field") == "ordinal"]

    conflicting = {**root, "children": [
        {"summary": "파이프라인 2차 구현", "type": "Sub-Task"},
    ]}
    conflict_errors = [
        row for row in _deterministic_request_field_errors(state, [conflicting])
        if row.get("field") == "ordinal"
    ]
    assert len(conflict_errors) == 1
    assert "2차" in conflict_errors[0]["message"] and "1차" in conflict_errors[0]["message"]

    bare = {**root, "children": [
        {"summary": "파이프라인 차 구현", "type": "Sub-Task"},
    ]}
    bare_errors = [row for row in _deterministic_request_field_errors(state, [bare])
                   if row.get("field") == "ordinal"]
    assert len(bare_errors) == 1 and "bare '차'" in bare_errors[0]["message"]


def test_multi_phase_hierarchy_binds_each_ordinal_to_its_explicit_issue_tier():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import _deterministic_request_field_errors

    request = "StarRocks Puffin NDV 1차 구현 Task 아래 2차 검증 Sub-Task를 만들어줘"
    state = {"request_text": request, "messages": [HumanMessage(content=request)]}
    valid = [{
        "summary": "[ETL] StarRocks Puffin NDV 1차 구현", "type": "Task",
        "children": [{
            "summary": "[ETL] StarRocks Puffin NDV 2차 검증", "type": "Sub-Task",
        }],
    }]
    swapped = [{
        **valid[0],
        "summary": "[ETL] StarRocks Puffin NDV 2차 구현",
        "children": [{
            "summary": "[ETL] StarRocks Puffin NDV 1차 검증", "type": "Sub-Task",
        }],
    }]

    assert not [row for row in _deterministic_request_field_errors(state, valid)
                if row.get("field") == "ordinal"]
    errors = [row for row in _deterministic_request_field_errors(state, swapped)
              if row.get("field") == "ordinal"]
    assert {row["index"] for row in errors} == {0, 1}
    assert any("root" in row["message"] and "1차" in row["message"] for row in errors)
    assert any("child" in row["message"] and "2차" in row["message"] for row in errors)


def test_multi_outcome_issue_types_are_checked_against_each_bound_root():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import (
        _deterministic_request_field_errors, _expected_issue_types_by_root,
        _explicit_issue_type_mentions,
    )
    from app.agent.workflow.agents.work_architect import _current_request_boundary_text
    from app.agent.workflow.anchors import requested_outcome_contract

    state = {
        "request_text": "로그인 오류 Bug를 만들고 검색 개선 Story를 만들어줘",
        "messages": [HumanMessage(content=(
            "로그인 오류 Bug를 만들고 검색 개선 Story를 만들어줘"
        ))],
        "request_plan": {"tasks": [
            {"id": "login-bug", "kind": "ticket", "write_intent": True,
             "instruction": "로그인 오류 Bug 생성"},
            {"id": "search-story", "kind": "ticket", "write_intent": True,
             "instruction": "검색 개선 Story 생성"},
        ]},
    }
    bug_ref, story_ref = [
        row["id"] for row in requested_outcome_contract(state)["outcomes"]
    ]
    correct = [
        {"summary": "로그인 오류 수정", "type": "Bug", "outcome_refs": [bug_ref]},
        {"summary": "검색 경험 개선", "type": "Story", "outcome_refs": [story_ref]},
    ]
    wrong = [
        {**correct[0], "type": "Story"},
        {**correct[1], "type": "Bug"},
    ]

    assert [[row["type"] for row in _explicit_issue_type_mentions(outcome["instruction"])]
            for outcome in requested_outcome_contract(state)["outcomes"]] == [
        ["Bug"], ["Story"],
    ]
    assert _current_request_boundary_text(state) == state["request_text"]
    assert [row["type"] for row in _explicit_issue_type_mentions(
        _current_request_boundary_text(state))] == ["Bug", "Story"]
    assert _expected_issue_types_by_root(state, correct) == {0: "Bug", 1: "Story"}
    assert not [row for row in _deterministic_request_field_errors(state, correct)
                if row.get("field") == "type"]
    errors = [row for row in _deterministic_request_field_errors(state, wrong)
              if row.get("field") == "type"]
    assert len(errors) == 2
    assert all(expected in next(row["message"] for row in errors if row["index"] == index)
               for index, expected in ((0, "Bug"), (1, "Story")))


def test_visible_multi_type_roots_are_checked_only_with_a_literal_bijection():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import _deterministic_request_field_errors

    request = "로그인 오류 Bug 1건과 검색 개선 Story 1건 만들어줘"
    state = {"request_text": request, "messages": [HumanMessage(content=request)]}
    correct = [
        {"summary": "로그인 오류 수정", "type": "Bug"},
        {"summary": "검색 경험 개선", "type": "Story"},
    ]
    wrong = [{**correct[0], "type": "Story"}, {**correct[1], "type": "Bug"}]

    assert not [row for row in _deterministic_request_field_errors(state, correct)
                if row.get("field") == "type"]
    errors = [row for row in _deterministic_request_field_errors(state, wrong)
              if row.get("field") == "type"]
    assert {row["index"] for row in errors} == {0, 1}


def test_ambiguous_multi_type_request_does_not_apply_the_first_type_to_every_root():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import _deterministic_request_field_errors

    request = "Bug 1건과 Story 1건 만들어줘"
    state = {"request_text": request, "messages": [HumanMessage(content=request)]}
    roots = [
        {"summary": "첫 번째 작업", "type": "Task"},
        {"summary": "두 번째 작업", "type": "Task"},
    ]

    assert not [row for row in _deterministic_request_field_errors(state, roots)
                if row.get("field") == "type"]


def test_one_explicit_issue_type_still_applies_to_every_created_root():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import _deterministic_request_field_errors

    request = "로그인과 검색 오류 Bug 2건 만들어줘"
    state = {"request_text": request, "messages": [HumanMessage(content=request)]}
    roots = [
        {"summary": "로그인 오류", "type": "Bug"},
        {"summary": "검색 오류", "type": "Story"},
    ]

    errors = [row for row in _deterministic_request_field_errors(state, roots)
              if row.get("field") == "type"]
    assert len(errors) == 1 and errors[0]["index"] == 1
    assert "Bug" in errors[0]["message"]


def test_global_exact_due_is_enforced_for_every_root_but_unscoped_due_is_not():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import _machine_check

    body = ("<h3>배경</h3><p>요청됨</p><h3>작업 범위</h3>"
            "<ul><li>포함: 구현</li><li>제외: 요청 외 변경</li></ul>"
            "<h3>완료 조건 (DoD)</h3><ul><li>결과 확인</li></ul>")
    roots = [
        {"summary": "로그인 개선", "type": "Task", "description": body,
         "duedate": "2026-09-30"},
        {"summary": "검색 개선", "type": "Task", "description": body,
         "duedate": "2026-09-25"},
    ]
    scoped_request = "로그인과 검색 Task 둘 다 마감은 2026-09-30으로 해"
    scoped = {
        "request_text": scoped_request,
        "messages": [HumanMessage(content=scoped_request)],
        "draft": {"mode": "task", "items": roots},
    }

    review = _machine_check(scoped)

    due_errors = [row for row in review["errors"] if row.get("field") == "duedate"]
    assert len(due_errors) == 1 and due_errors[0]["index"] == 1
    unscoped_request = "로그인과 검색 Task를 만들어줘. 마감은 2026-09-30"
    unscoped = {
        **scoped,
        "request_text": unscoped_request,
        "messages": [HumanMessage(content=unscoped_request)],
    }
    assert not [row for row in _machine_check(unscoped)["errors"]
                if row.get("field") == "duedate"]


def test_delegated_parent_with_no_materialized_candidates_allows_only_top_level():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import _deterministic_request_field_errors

    request = "NDV Task를 만들고 기존 Epic은 네가 골라줘"
    base = {
        "request_text": request,
        "messages": [HumanMessage(content=request)],
        "materialized_ticket_sources": {
            "parentCandidateKeys": [], "ticketDetails": [],
        },
    }

    blank = [{"summary": "NDV 통계 검증", "type": "Task"}]
    opaque = [{"summary": "NDV 통계 검증", "type": "Task", "epic": "DL-9999"}]

    assert not [row for row in _deterministic_request_field_errors(base, blank)
                if row.get("field") == "parent"]
    errors = [row for row in _deterministic_request_field_errors(base, opaque)
              if row.get("field") == "parent"]
    assert len(errors) == 1 and "DL-9999" in errors[0]["message"]


def test_parent_candidate_requires_a_successfully_opened_epic_detail():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import _deterministic_request_field_errors

    request = "NDV Task를 만들고 기존 Epic은 네가 골라줘"
    state = {
        "request_text": request,
        "messages": [HumanMessage(content=request)],
        "materialized_ticket_sources": {
            "parentCandidateKeys": ["DL-9200", "DL-7001", "DL-9300"],
            "ticketDetails": [
                {"key": "DL-9200", "type": "Epic", "error": "permission denied"},
                {"key": "DL-7001", "type": "Task"},
                {"key": "DL-9300", "type": "Epic"},
            ],
        },
    }

    valid = [{"summary": "NDV 통계 검증", "type": "Task", "epic": "DL-9300"}]
    invalid = [{"summary": "NDV 통계 검증", "type": "Task", "epic": "DL-7001"}]

    assert not [row for row in _deterministic_request_field_errors(state, valid)
                if row.get("field") == "parent"]
    errors = [row for row in _deterministic_request_field_errors(state, invalid)
              if row.get("field") == "parent"]
    assert len(errors) == 1
    assert "DL-9300" in errors[0]["message"] and "DL-7001" in errors[0]["message"]


def test_auditor_never_repeats_unverified_parent_nonexistence_or_recommendation():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import Auditor

    request = "NDV Task를 만들고 기존 Epic은 네가 골라줘"
    state = {
        "request_text": request,
        "messages": [HumanMessage(content=request)],
        "materialized_ticket_sources": {"parentCandidateKeys": [], "ticketDetails": []},
        "draft": {"mode": "task", "items": [{
            "summary": "NDV 통계 검증", "type": "Task", "epic": "DL-9200",
            "description": (
                "<h3>배경</h3><p>NDV 검증</p><h3>작업 범위</h3>"
                "<ul><li>포함: 검증</li><li>제외: 요청 외 변경</li></ul>"
                "<h3>완료 조건 (DoD)</h3><ul><li>검증 결과 기록</li></ul>"
            ),
        }]},
    }
    model = {
        "grounded": False, "rule_compliant": True, "answers_request": True,
        "problems": [{
            "index": 0, "check": "grounded",
            "message": "DL-9200은 존재하지 않으며 검색 결과에는 DL-7001만 있습니다.",
            "fix": "DL-7001을 상위 Epic으로 연결하세요.",
        }],
        "summary": "DL-9200이 존재하지 않으므로 DL-7001을 연결해야 합니다.",
    }

    review = Auditor().apply(state, model)["review"]
    rendered = " ".join(
        [review.get("summary", "")]
        + [str(row.get("message") or "") + " " + str(row.get("fix") or "")
           for row in review.get("problems") or []]
        + [str(row.get("message") or "") for row in review.get("errors") or []]
    )
    assert "존재하지" not in rendered
    assert "DL-7001" not in rendered
    assert "상세 확인된 기존 Epic 후보" in rendered


def test_auditor_maps_parent_and_due_per_outcome_and_blocks_swaps(monkeypatch):
    from langchain_core.messages import HumanMessage
    import app.agent.workflow.agents.work_architect as work
    from app.agent.workflow.agents.auditor import (
        _deterministic_request_field_errors, _machine_check,
    )
    from app.agent.workflow.anchors import requested_outcome_contract

    request = (
        "Bug는 DL-100 아래에 마감 2026-09-10으로 만들고, "
        "Story는 DL-200 아래에 마감 2026-09-20으로 만들어줘"
    )
    state = {
        "request_text": request, "messages": [HumanMessage(content=request)],
        "request_plan": {"tasks": [
            {"id": "bug", "kind": "ticket", "write_intent": True,
             "instruction": "Bug를 DL-100 아래에 마감 2026-09-10으로 생성"},
            {"id": "story", "kind": "ticket", "write_intent": True,
             "instruction": "Story를 DL-200 아래에 마감 2026-09-20으로 생성"},
        ]},
    }
    contract = requested_outcome_contract(state)
    bug_ref, story_ref = [row["id"] for row in contract["outcomes"]]
    body = ("<h3>배경</h3><p>요청됨</p><h3>작업 범위</h3>"
            "<ul><li>포함: 구현</li><li>제외: 요청 외 변경</li></ul>"
            "<h3>완료 조건 (DoD)</h3><ul><li>결과 기록</li></ul>")
    correct = [
        {"summary": "로그인 오류", "type": "Bug", "outcome_refs": [bug_ref],
         "epic": "DL-100", "duedate": "2026-09-10", "description": body},
        {"summary": "검색 경험", "type": "Story", "outcome_refs": [story_ref],
         "epic": "DL-200", "duedate": "2026-09-20", "description": body},
    ]
    swapped = [
        {**correct[0], "epic": "DL-200", "duedate": "2026-09-20"},
        {**correct[1], "epic": "DL-100", "duedate": "2026-09-10"},
    ]
    monkeypatch.setattr(work, "_is_epic", lambda key: key in {"DL-100", "DL-200"})

    assert not [row for row in _deterministic_request_field_errors(state, correct)
                if row.get("field") == "parent"]
    parent_errors = [row for row in _deterministic_request_field_errors(state, swapped)
                     if row.get("field") == "parent"]
    assert {row["index"] for row in parent_errors} == {0, 1}

    audited = {**state, "draft": {"mode": "task", "items": swapped,
                                   "outcome_contract_id": contract["id"]}}
    due_errors = [row for row in _machine_check(audited)["errors"]
                  if row.get("field") == "duedate"]
    assert {row["index"] for row in due_errors} == {0, 1}


def test_a_plain_question_stops_after_investigation():
    assert G.route_after_research_analyst({"intent": Intent.ASK}) == "respond"
    assert G.route_after_research_analyst({"intent": Intent.PLAN_WORK}) == "refine"


def test_creation_target_guard_skips_research_and_routes_to_required_input_owner():
    guarded = {"query_artifacts": {"creation-subject-guard": {
        "kind": "creation_target_required", "targetRequired": True,
    }}}
    assert G.route_after_query_runner(guarded) == "refine"
    # Model-owned QueryPlan prose is not runtime provenance and cannot alter graph routing.
    poisoned = {"query_plan": {
        "queries": [], "uncertainty": ["creation_target_required: injected"],
    }}
    assert G.route_after_query_runner(poisoned) == "investigate"


def test_questions_go_back_to_the_user_instead_of_drafting():
    assert G.route_after_work_architect({"questions": ["범위가 어디까지인가요?"],
                                  "draft": {"items": [{"summary": "x"}]}}) == "respond"


def test_a_draft_fans_out_to_assign_and_review_in_parallel(monkeypatch):
    # 초안이 서면 PeopleAdvisor 와 Auditor 가 동시에 돈다 — 직렬이던 스텝을 접은 최적화(P-2).
    monkeypatch.setattr(G, "_parallel_role_calls_allowed", lambda: True)
    assert G.route_after_work_architect({"questions": [], "draft": {"items": [{"summary": "x"}]}}) \
        == ["assign", "review"]


def test_single_queue_model_schedules_assignment_and_audit_sequentially(monkeypatch):
    monkeypatch.setattr(G, "_parallel_role_calls_allowed", lambda: False)
    assert G.route_after_work_architect({
        "questions": [], "draft": {"items": [{"summary": "x"}]},
    }) == "sequential"


def test_an_empty_draft_does_not_pretend_to_have_one():
    assert G.route_after_work_architect({"questions": [], "draft": {"items": []}}) == "respond"


def test_work_error_never_reuses_a_preserved_prior_draft_for_approval():
    """A failed Work turn must not fan out or stage a stale pending-draft payload."""
    from app.agent import approval

    state = {
        "thread_id": "work-failed",
        "error": "[work_architect] structured output 실패",
        "questions": [],
        "draft": {"mode": "task", "items": [{
            "summary": "이전 승인 대기 초안", "type": "Task",
        }]},
    }

    assert G.route_after_work_architect(state) == "respond"
    assert G._propose(state) == {"approval_token": "", "comment_token": ""}
    assert approval.peek("") is None


def test_review_failure_sends_it_back_to_be_rewritten():
    """검증 가능한 blocking 문제는 한 번 고친 뒤에만 fail-closed 한다."""
    assert G.route_after_auditor({"review": {"ok": False,
                                              "errors": [{"message": "없는 부모"}]},
                                   "revisions": 1}) == "revise"
    assert G.route_after_auditor({"review": {"ok": False, "errors": [],
                                              "problems": [{"message": "의견"}]},
                                   "revisions": 0}) == "revise"


def test_rewrite_loop_is_bounded_and_failed_review_never_becomes_actionable():
    """상한이 없으면 두 모델이 서로 만족하지 못해 무한히 돈다. 소진 뒤의 갈래:

    기계 오류가 남았으면 respond(만들어 봤자 Jira 가 거부한다). **LLM 의견만** 남았으면
    respond — 검토 결과가 거짓이면 먼저 grounding에서 제거해야 한다. 남은 blocking
    problem을 승인 가능한 payload와 함께 노출하면 review 계약 자체가 무의미해진다.
    """
    exhausted = {"revisions": MAX_REVISIONS}
    assert G.route_after_auditor({**exhausted,
                                   "review": {"ok": False,
                                              "errors": [{"message": "없는 부모"}]}}) == "respond"
    assert G.route_after_auditor({**exhausted,
                                   "review": {"ok": False, "errors": [],
                                              "problems": [{"message": "의견"}]}}) == "respond"


def test_proposal_boundary_clears_any_actionable_token_when_review_failed():
    state = {
        "thread_id": "blocked-review",
        "review": {"ok": False, "problems": [{"message": "요청 누락"}]},
        "draft": {"mode": "task", "items": [{
            "summary": "[Catalog] 초안", "type": "Task",
        }]},
        "approval_token": "stale-token",
        "comment_token": "stale-comment-token",
    }
    assert G._propose(state) == {"approval_token": "", "comment_token": ""}


def test_comment_authority_projects_out_create_and_field_update_effects():
    """CTX4: a typed comment request may never inherit a stale create/update payload."""
    state = {
        "thread_id": "comment-authority",
        "request_text": "이전 주제에서 DL-9090에 '오래된 댓글'이라고 댓글 남겨줘",
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "DL-9090에 결정 내용만 댓글로 남겨줘",
            "intent": "modify",
            "action": "comment",
            "target_keys": ["DL-9090"],
            "outcome_ids": ["comment-decision"],
            "decisions": [],
        },
        "review": {"ok": True, "summary": "stale creation review"},
        "draft": {"mode": "task", "rationale": "새 Task 생성", "items": [{
            "summary": "요청하지 않은 신규 Task", "type": "Task",
        }]},
        "change_plan": {
            "key": "DL-9090",
            "changes": {"assignee": "skcc.x1210"},
            "comment": "회의에서 운영 반영을 보류하기로 결정했습니다.",
            "why": "담당자 변경과 신규 Task 생성",
        },
    }

    staged = G._propose(state)
    record = approval.peek(staged["approval_token"])

    assert record["action"] == "add_ticket_comment"
    assert record["payload"] == {
        "key": "DL-9090",
        "body": "회의에서 운영 반영을 보류하기로 결정했습니다.",
    }
    assert staged["draft"] == {}
    assert staged["change_plan"]["changes"] == {}
    assert "필드·상태 변경 없음" in staged["change_plan"]["why"]
    assert staged["review"]["ok"] is True
    assert "댓글" in staged["review"]["summary"]


def test_comment_authority_never_falls_through_to_stale_create_draft():
    state = {
        "thread_id": "comment-no-body",
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "DL-9090에 댓글 남겨줘",
            "intent": "modify", "action": "comment",
            "target_keys": ["DL-9090"], "outcome_ids": ["comment"],
            "decisions": [],
        },
        "review": {"ok": True},
        "draft": {"mode": "task", "items": [{
            "summary": "오래된 생성 초안", "type": "Task",
        }]},
        "change_plan": {},
    }

    blocked = G._propose(state)

    assert blocked["approval_token"] == ""
    assert blocked["comment_token"] == ""
    assert blocked["draft"] == {}
    assert blocked["review"]["ok"] is False
    assert any(row.get("field") == "effect" for row in blocked["review"]["errors"])


def test_comment_change_uses_deterministic_final_review_but_never_promotes_red_review():
    contract = {
        "version": "continuation.v1",
        "root_request": "DL-9090에 결정 댓글을 남겨줘",
        "intent": "modify", "action": "comment",
        "target_keys": ["DL-9090"], "outcome_ids": ["comment"], "decisions": [],
    }
    plan = {"key": "DL-9090", "changes": {}, "comment": "운영 반영 보류"}

    staged = G._propose({
        "thread_id": "comment-machine-review",
        "continuation_contract": contract,
        "draft": {}, "change_plan": plan,
    })
    assert approval.peek(staged["approval_token"])["action"] == "add_ticket_comment"
    assert staged["review"]["ok"] is True
    assert staged["review"]["approval_contract"] == "deterministic_final_effect.v1"

    blocked = G._propose({
        "thread_id": "comment-red-review",
        "continuation_contract": contract,
        "review": {"ok": False},
        "draft": {}, "change_plan": plan,
    })
    assert blocked["approval_token"] == "" and blocked["comment_token"] == ""
    assert blocked["review"]["ok"] is False


def test_mixed_create_and_change_requires_split_before_approval():
    """The current executor has no atomic fingerprint for create+change compound effects."""
    state = {
        "thread_id": "unsupported-mixed",
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "새 Task를 만들고 DL-9090에 보류 댓글도 남겨줘",
            "intent": "modify", "action": "mixed",
            "target_keys": ["DL-9090"], "outcome_ids": ["create", "comment"],
            "decisions": [],
        },
        "review": {"ok": True},
        "draft": {"mode": "task", "items": [{"summary": "새 Task", "type": "Task"}]},
        "change_plan": {"key": "DL-9090", "changes": {}, "comment": "보류"},
    }

    blocked = G._propose(state)

    assert blocked["approval_token"] == "" and blocked["comment_token"] == ""
    assert blocked["review"]["ok"] is False
    assert any("분할" in str(row.get("message") or "")
               for row in blocked["review"]["errors"])


def test_mixed_update_and_comment_keeps_existing_two_fingerprint_approval_contract():
    """A supported compound change keeps both reviewed effects on one approval card."""
    state = {
        "thread_id": "supported-mixed",
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "DL-9090 마감일을 바꾸고 결정 댓글도 남겨줘",
            "intent": "modify", "action": "mixed",
            "target_keys": ["DL-9090"], "outcome_ids": ["update", "comment"],
            "decisions": [],
        },
        "draft": {},
        "change_plan": {
            "key": "DL-9090",
            "changes": {"duedate": "2026-09-01"},
            "comment": "운영 반영은 9월 1일로 결정",
        },
    }

    staged = G._propose(state)

    update = approval.peek(staged["approval_token"])
    comment = approval.peek(staged["comment_token"])
    assert update["action"] == "update_ticket"
    assert update["payload"] == {
        "key": "DL-9090", "changes": {"duedate": "2026-09-01"},
    }
    assert comment["action"] == "add_ticket_comment"
    assert comment["payload"] == {
        "key": "DL-9090", "body": "운영 반영은 9월 1일로 결정",
    }
    assert staged["review"]["ok"] is True
    assert staged["review"]["final_authority"] == {
        "kind": "update",
        "actions": ["update_ticket", "add_ticket_comment"],
        "target_count": 1,
    }


def test_propose_rejects_change_target_outside_typed_contract():
    """A self-consistent approval fingerprint is not authority to change another ticket."""
    state = {
        "thread_id": "typed-target-mismatch",
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "DL-100에 결정 댓글을 남겨줘",
            "intent": "modify", "action": "comment",
            "target_keys": ["DL-100"], "outcome_ids": ["comment"],
            "decisions": [],
        },
        "draft": {},
        "change_plan": {"key": "DL-999", "changes": {}, "comment": "결정 승인"},
    }

    blocked = G._propose(state)

    assert blocked["approval_token"] == "" and blocked["comment_token"] == ""
    assert blocked["review"]["ok"] is False
    assert any(row.get("field") == "target" for row in blocked["review"]["errors"])
    assert approval.peek(blocked["approval_token"]) is None


def test_propose_rejects_ambiguous_singular_and_bulk_change_targets():
    state = {
        "thread_id": "typed-target-shape-conflict",
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "ACME-10을 In Progress로 옮겨줘",
            "intent": "modify", "action": "update",
            "target_keys": ["ACME-10"], "outcome_ids": ["transition"],
            "decisions": [],
        },
        "draft": {},
        "change_plan": {
            "key": "ACME-999", "keys": ["ACME-10"],
            "transition": {"id": "2", "name": "In Progress"},
        },
    }

    blocked = G._propose(state)

    assert blocked["approval_token"] == "" and blocked["comment_token"] == ""
    assert blocked["review"]["ok"] is False
    assert any(row.get("field") == "target" for row in blocked["review"]["errors"])


@pytest.mark.parametrize("extra", [
    {"transition": {"id": "2", "name": "In Progress"},
     "changes": {"priority": "P1-Critical"}},
    {"link": {"other": "ACME-20", "relation": "Relates"},
     "changes": {"priority": "P1-Critical"}},
    {"transition": {"id": "2", "name": "In Progress"},
     "link": {"other": "ACME-20", "relation": "Relates"}},
])
def test_propose_rejects_competing_primary_change_effects(extra):
    target_keys = ["ACME-10"] + (["ACME-20"] if extra.get("link") else [])
    state = {
        "thread_id": "typed-primary-conflict",
        "continuation_contract": {
            "version": "continuation.v1", "root_request": "복합 변경",
            "intent": "modify", "action": "update", "target_keys": target_keys,
            "outcome_ids": ["update"], "decisions": [],
        },
        "draft": {}, "change_plan": {"key": "ACME-10", **extra},
    }

    blocked = G._propose(state)

    assert blocked["approval_token"] == "" and blocked["comment_token"] == ""
    assert blocked["review"]["ok"] is False
    assert any(row.get("field") == "effect" for row in blocked["review"]["errors"])


def test_mixed_bulk_update_and_comments_stage_both_fingerprints():
    state = {
        "thread_id": "typed-bulk-mixed",
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "DL-100과 DL-200의 마감을 바꾸고 결정 댓글도 남겨줘",
            "intent": "modify", "action": "mixed",
            "target_keys": ["DL-100", "DL-200"],
            "outcome_ids": ["update", "comment"], "decisions": [],
        },
        "draft": {},
        "change_plan": {
            "keys": ["DL-100", "DL-200"],
            "changes": {"duedate": "2026-09-30"},
            "comment": "운영 반영일 확정",
        },
    }

    staged = G._propose(state)

    primary = approval.peek(staged["approval_token"])
    secondary = approval.peek(staged["comment_token"])
    assert primary["action"] == "update_tickets"
    assert [row["key"] for row in primary["payload"]["items"]] == ["DL-100", "DL-200"]
    assert secondary["action"] == "add_ticket_comments"
    assert secondary["payload"]["items"] == [
        {"key": "DL-100", "body": "운영 반영일 확정"},
        {"key": "DL-200", "body": "운영 반영일 확정"},
    ]


def test_mixed_bulk_update_rejects_partial_comment_preview():
    state = {
        "thread_id": "typed-bulk-partial-comment",
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "DL-100과 DL-200의 마감을 바꾸고 둘 다 댓글을 남겨줘",
            "intent": "modify", "action": "mixed",
            "target_keys": ["DL-100", "DL-200"],
            "outcome_ids": ["update", "comment"], "decisions": [],
        },
        "draft": {},
        "change_plan": {
            "keys": ["DL-100", "DL-200"],
            "changes": {"duedate": "2026-09-30"},
            "comment": "공통 결정",
            "comments": [{"key": "DL-100", "body": "공통 결정"}],
        },
    }

    blocked = G._propose(state)

    assert blocked["approval_token"] == "" and blocked["comment_token"] == ""
    assert blocked["review"]["ok"] is False
    assert any(row.get("field") == "comment_targets"
               for row in blocked["review"]["errors"])


def test_mixed_bulk_update_allows_an_explicit_single_ticket_comment_subset():
    state = {
        "thread_id": "typed-bulk-comment-subset",
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": ("ACME-10과 ACME-20 priority를 High로 바꾸고 "
                             "ACME-10에만 결정 댓글을 남겨줘"),
            "intent": "modify", "action": "mixed",
            "target_keys": ["ACME-10", "ACME-20"],
            "outcome_ids": ["update", "comment"], "decisions": [],
        },
        "draft": {},
        "change_plan": {
            "keys": ["ACME-10", "ACME-20"],
            "changes": {"priority": "High"},
            "comments": [{"key": "ACME-10", "body": "결정"}],
        },
    }

    staged = G._propose(state)

    assert staged["review"]["ok"] is True
    assert approval.peek(staged["approval_token"])["action"] == "update_tickets"
    secondary = approval.peek(staged["comment_token"])
    assert secondary["action"] == "add_ticket_comments"
    assert secondary["payload"]["items"] == [{"key": "ACME-10", "body": "결정"}]


def test_mixed_link_and_comment_stage_both_fingerprints():
    state = {
        "thread_id": "typed-link-mixed",
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "DL-100을 DL-200과 연결하고 DL-100에 결정 댓글을 남겨줘",
            "intent": "modify", "action": "mixed",
            "target_keys": ["DL-100", "DL-200"],
            "outcome_ids": ["link", "comment"], "decisions": [],
        },
        "draft": {},
        "change_plan": {
            "key": "DL-100", "changes": {},
            "link": {"other": "DL-200", "relation": "Relates"},
            "comment": "관련 결정 기록",
        },
    }

    staged = G._propose(state)

    assert approval.peek(staged["approval_token"])["action"] == "link_tickets"
    assert approval.peek(staged["comment_token"])["action"] == "add_ticket_comment"


def test_directional_link_and_comment_target_are_bound_to_the_literal_request():
    state = {
        "thread_id": "typed-link-direction",
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "DL-100이 DL-200을 막는 링크를 만들고 DL-100에 결정 댓글을 남겨줘",
            "intent": "modify", "action": "mixed",
            "target_keys": ["DL-100", "DL-200"],
            "outcome_ids": ["link", "comment"], "decisions": [],
        },
        "draft": {},
        "change_plan": {
            "key": "DL-200", "changes": {},
            "link": {"other": "DL-100", "relation": "Relates"},
            "comment": "관련 결정 기록",
        },
    }

    blocked = G._propose(state)

    assert blocked["approval_token"] == "" and blocked["comment_token"] == ""
    assert blocked["review"]["ok"] is False
    assert {row.get("field") for row in blocked["review"]["errors"]} >= {
        "link", "comment_targets",
    }


def test_failed_review_renderer_never_asks_for_approval():
    from app.agent.workflow.agents.result_integrator import _blocked_review_reply

    text = _blocked_review_reply({"review": {"ok": False, "problems": [{
        "message": "요청의 필수 대상이 빠짐", "fix": "대상을 복원"
    }]}})
    assert "실행 대기 카드 없음" in text
    assert "승인해" not in text and "승인 요청" not in text


def test_failed_review_full_result_does_not_append_unrelated_research_evidence():
    """Review-boundary replies contain only rejection facts, never the earlier research ledger."""
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    out = ResultIntegrator()._run({
        "review": {"ok": False, "problems": [{
            "message": "생성 대상의 담당자가 확정되지 않음", "fix": "담당자 확인",
        }]},
        "request_text": "회의 결정으로 티켓을 만들어줘",
        "evidence": [{
            "key": "DL-777", "title": "무관한 과거 티켓",
            "url": "https://jira.example/browse/DL-777",
            "observations": [{"source": "comment", "text": "과거 댓글"}],
        }],
        "related_docs": [{
            "title": "무관한 설계 문서", "url": "https://confluence.example/pages/777",
        }],
        "web_context": "https://example.com/unrelated external source",
        "trace": [],
    })

    text = out["reply"]
    assert "검토 보류" in text and "담당자 확인" in text
    assert "### 근거" not in text
    assert "DL-777" not in text and "무관한 설계 문서" not in text
    assert "jira.example" not in text and "confluence.example" not in text


def test_assignment_join_reseals_user_anchor_before_pending_boundary():
    from langchain_core.messages import HumanMessage

    state = {
        "request_text": "AcmeDB DeltaSketch pipeline을 개발해줘",
        "messages": [HumanMessage(content="범위는 1차 구현까지. 알아서")],
        "draft": {"mode": "task", "items": [{
            "summary": "[Runtime] AcmeDB DeltaSketch pipeline 1차 구현",
            "children": [{
                "summary": "[Runtime] AcmeDB DeltaSketch pipeline 차 — 구현",
                "description": ("<h3>작업 범위</h3><ul>"
                                "<li>포함: AcmeDB DeltaSketch pipeline 차 구현</li></ul>"),
            }],
        }]},
        "assignments": [],
    }

    draft = G._merge_assignments(state)["draft"]
    child = draft["items"][0]["children"][0]
    assert "1차" in child["summary"] and " pipeline 차" not in child["summary"]
    assert "1차" in child["description"] and " pipeline 차" not in child["description"]


def test_outcome_contract_mismatch_is_a_machine_blocker():
    from app.agent.workflow.agents.auditor import _machine_check

    state = {
        "request_plan": {"tasks": [{
            "id": "create-index", "kind": "ticket", "write_intent": True,
            "instruction": "AcmeDB DeltaSketch index 생성",
        }]},
        "draft": {"mode": "task", "outcome_contract_id": "requested-outcome:wrong",
                  "items": [{
                      "summary": "[Runtime] AcmeDB DeltaSketch index 추출", "type": "Task",
                      "outcome_refs": ["outcome:wrong"],
                      "description": ("<h3>배경</h3><p>index 추출</p>"
                                      "<h3>작업 범위</h3><ul><li>추출</li></ul>"
                                      "<h3>완료 조건 (DoD)</h3><ul><li>추출 결과 확인</li></ul>"),
                  }]},
    }

    result = _machine_check(state)

    assert result["ok"] is False
    assert any("outcome contract" in str(row.get("message") or "").lower()
               for row in result["errors"])


def test_small_draft_with_outcome_contract_still_runs_semantic_audit(monkeypatch):
    from app.agent.workflow.agents.auditor import Auditor
    from app.agent.workflow.agents.base import StructuredAgent
    from app.agent.workflow.anchors import requested_outcome_contract

    state = {
        "request_plan": {"tasks": [{
            "id": "create-index", "kind": "ticket", "write_intent": True,
            "instruction": "AcmeDB DeltaSketch index 생성",
        }]},
        "draft": {"mode": "task", "items": [{
            "summary": "[Runtime] AcmeDB DeltaSketch index 생성", "type": "Task",
            "description": ("<h3>배경</h3><p>index 생성</p>"
                            "<h3>작업 범위</h3><ul><li>index 생성</li></ul>"
                            "<h3>완료 조건 (DoD)</h3><ul><li>생성 결과 확인</li></ul>"),
        }]},
    }
    contract = requested_outcome_contract(state)
    state["draft"]["outcome_contract_id"] = contract["id"]
    state["draft"]["items"][0]["outcome_refs"] = [contract["outcomes"][0]["id"]]
    monkeypatch.setattr(
        StructuredAgent, "node",
        lambda self: (lambda _state: {"semantic_audit_ran": True}),
    )

    assert Auditor().node()(state) == {"semantic_audit_ran": True}


def test_small_draft_with_verified_evidence_obligations_runs_semantic_audit(monkeypatch):
    """Producer/consumer role reversal cannot use the machine-only small-draft shortcut."""
    from app.agent.workflow.agents.auditor import Auditor
    from app.agent.workflow.agents.base import StructuredAgent

    state = {
        "request_text": "AcmeDB DeltaSketch 통계 생성 파이프라인을 만들어줘",
        "keywords": ["AcmeDB", "DeltaSketch", "통계", "생성", "파이프라인"],
        "evidence": [{
            "key": "DL-9202", "title": "[Runtime] DeltaSketch reader 검증",
            "why": "진행 중 consumer dependency",
            "observations": [{
                "source": "description",
                "text": ("AcmeWriter가 만든 DeltaSketch를 AcmeReader가 실제 소비하는지 "
                         "확인 중이며 지원 여부는 아직 확정하지 않았습니다."),
            }],
        }],
        "materialized_ticket_sources": {"ticketDetails": [{
            "key": "DL-9202", "type": "Task", "status": "In Progress",
            "statusCategory": "indeterminate",
            "summary": "[Runtime] DeltaSketch reader 검증", "updated": "2026-08-17",
            "description": ("AcmeWriter가 만든 DeltaSketch를 AcmeReader가 실제 소비하는지 "
                            "확인 중이며 지원 여부는 아직 확정하지 않았습니다."),
        }]},
        "draft": {"mode": "task", "items": [{
            "summary": "[ETL] AcmeDB DeltaSketch 통계 생성 파이프라인 구현",
            "type": "Task",
            "description": (
                "<h3>배경</h3><p>파이프라인 요청됨</p>"
                "<h3>작업 범위</h3><ul><li>최소 기능 구현</li></ul>"
                "<h3>완료 조건 (DoD)</h3><ul><li>결과 확인</li></ul>"
            ),
        }]},
    }
    monkeypatch.setattr(
        StructuredAgent, "node",
        lambda self: (lambda _state: {"semantic_audit_ran": True}),
    )

    assert Auditor().node()(state) == {"semantic_audit_ran": True}


def test_opposite_requested_action_remains_blocking_and_gets_one_revision():
    from app.agent.workflow.agents.auditor import Auditor
    from app.agent.workflow.anchors import requested_outcome_contract

    state = {
        "revisions": 0,
        "request_plan": {"tasks": [{
            "id": "create-index", "kind": "ticket", "write_intent": True,
            "instruction": "AcmeDB DeltaSketch index 생성",
        }]},
        "draft": {"mode": "task", "items": [{
            "summary": "[Runtime] AcmeDB DeltaSketch index 추출", "type": "Task",
            "description": ("<h3>배경</h3><p>index 추출</p>"
                            "<h3>작업 범위</h3><ul><li>index 추출</li></ul>"
                            "<h3>완료 조건 (DoD)</h3><ul><li>추출 결과 확인</li></ul>"),
        }]},
    }
    contract = requested_outcome_contract(state)
    state["draft"]["outcome_contract_id"] = contract["id"]
    state["draft"]["items"][0]["outcome_refs"] = [contract["outcomes"][0]["id"]]
    out = {"grounded": True, "rule_compliant": True, "answers_request": False,
           "problems": [{
               "index": 0, "check": "request",
               "message": "요청한 index 생성이 초안에서 index 추출로 반대로 바뀌었습니다.",
               "fix": "scope와 DoD를 요청한 index 생성 결과에 맞춥니다.",
           }]}

    prompt = Auditor().task(state)
    reviewed = Auditor().apply(state, out)

    assert contract["id"] in prompt
    assert "AcmeDB DeltaSketch index 생성" in prompt
    assert reviewed["review"]["ok"] is False
    assert reviewed["review"]["problems"]
    assert G.route_after_auditor({**state, **reviewed}) == "revise"


def test_normal_stage_child_inherits_parent_requested_outcome_binding():
    from app.agent.workflow.agents.auditor import _audit_grounding_contract
    from app.agent.workflow.anchors import (
        requested_outcome_contract, validate_draft_outcome_contract,
    )

    state = {
        "request_plan": {"tasks": [{
            "id": "create-index", "kind": "ticket", "write_intent": True,
            "instruction": "AcmeDB DeltaSketch index 생성",
        }]},
        "draft": {"mode": "task", "items": [{
            "summary": "[Runtime] AcmeDB DeltaSketch index 생성", "type": "Task",
            "description": "<h3>작업 범위</h3><p>index 생성</p>",
            "children": [{
                "summary": "[Runtime] AcmeDB DeltaSketch index 설계", "type": "Sub-Task",
                "description": ("<h3>작업 범위</h3><p>index 구조 설계</p>"
                                "<h3>완료 조건 (DoD)</h3><p>설계 리뷰</p>"),
            }],
        }]},
    }
    contract = requested_outcome_contract(state)
    ref = contract["outcomes"][0]["id"]
    state["draft"]["outcome_contract_id"] = contract["id"]
    state["draft"]["items"][0]["outcome_refs"] = [ref]

    assert validate_draft_outcome_contract(state, state["draft"]) == []
    child = _audit_grounding_contract(state)["items"][0]["children"][0]
    assert child["outcome_refs"] == []
    assert child["applicable_outcome_refs"] == [ref]
    assert child["outcome_binding_source"] == "inherited_from_parent"


def test_opposite_action_child_is_a_blocking_request_finding():
    from app.agent.workflow.agents.auditor import Auditor, _audit_grounding_contract
    from app.agent.workflow.anchors import requested_outcome_contract

    state = {
        "revisions": 0,
        "request_text": "AcmeDB DeltaSketch index를 생성해줘",
        "request_plan": {"tasks": [{
            "id": "create-index", "kind": "ticket", "write_intent": True,
            "instruction": "AcmeDB DeltaSketch index 생성",
        }]},
        "draft": {"mode": "task", "items": [{
            "summary": "[Runtime] AcmeDB DeltaSketch index 생성", "type": "Task",
            "description": "<h3>작업 범위</h3><p>index 생성</p>",
            "children": [{
                "summary": "[Runtime] AcmeDB DeltaSketch 원천값 추출", "type": "Sub-Task",
                "description": ("<h3>작업 범위</h3><p>원천값 추출로 대체</p>"
                                "<h3>완료 조건 (DoD)</h3><p>추출 파일 확인</p>"),
            }],
        }]},
    }
    contract = requested_outcome_contract(state)
    ref = contract["outcomes"][0]["id"]
    state["draft"]["outcome_contract_id"] = contract["id"]
    state["draft"]["items"][0]["outcome_refs"] = [ref]
    out = {
        "grounded": True, "rule_compliant": True, "answers_request": False,
        "problems": [{
            "index": 0, "check": "request",
            "message": "하위 작업이 요청한 index 생성을 원천값 추출로 대체합니다.",
            "fix": "하위 작업을 index 생성에 기여하는 단계로 수정합니다.",
        }],
    }

    child = _audit_grounding_contract(state)["items"][0]["children"][0]
    prompt = Auditor().task(state)
    reviewed = Auditor().apply(state, out)

    assert child["applicable_outcome_refs"] == [ref]
    assert ref in prompt and "원천값 추출" in prompt
    assert reviewed["review"]["ok"] is False
    assert out["problems"][0] in reviewed["review"]["problems"]
    assert G.route_after_auditor({**state, **reviewed}) == "revise"


def test_passing_review_goes_to_approval_not_straight_to_execution():
    assert G.route_after_auditor({"review": {"ok": True}}) == "propose"


def test_responder_waits_for_approval_when_a_token_is_pending():
    assert G.route_after_result_integrator({"approval_token": "t"}) == "execute"


def test_responder_ends_after_execution_instead_of_looping():
    assert G.route_after_result_integrator({"approval_token": "t", "result": {"created": []}}) == "end"


def test_responder_ends_when_there_is_nothing_to_approve():
    assert G.route_after_result_integrator({}) == "end"


# ── 조립 ───────────────────────────────────────────────────────────
def test_graph_has_all_six_roles():
    nodes = set(G.build().get_graph().nodes)
    for n in (Node.REQUEST_ARCHITECT, Node.RESEARCH_ANALYST, Node.WORK_ARCHITECT, Node.PEOPLE_ADVISOR,
              Node.AUDITOR, Node.ACTION_EXECUTOR, Node.RESULT_INTEGRATOR):
        assert n in nodes


def test_tool_using_roles_really_are_subgraphs():
    """서브그래프가 아니면 stream(subgraphs=True) 가 '도구 부르는 중'을 못 보여 준다.

    ActionExecutor(결정적 modify)·ResearchAnalyst(결정적 진척률 보강)·PeopleAdvisor(유사 이력 사전 취합)는
    node() 를 한 겹 더 감싸면서 xray 가 클로저 속 서브그래프를 못 본다 — 세 역할의 ReAct 는
    build() 로 따로 지킨다.
    """
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    assert {"think", "act"} <= set(ResearchAnalyst().build().get_graph().nodes)


def test_draft_roles_do_not_use_tools():
    """WorkArchitect·PeopleAdvisor 는 **도구를 쓰지 않는다** — 필요한 재료(허용값·Epic 후보·규칙·
    유사 이력·로스터 부하)를 전부 코드가 미리 조회해 자료로 준다. 도구로 두면 모델이
    매 턴 다시 부르고, 도구 호출 한 번이 곧 LLM 왕복 한 번이라 생성 턴 하나에
    work_architect 12회·people_advisor 5회까지 불어났다(실측 기준선)."""
    from app.agent.workflow.agents.people_advisor import PeopleAdvisor
    from app.agent.workflow.agents.base import StructuredAgent
    from app.agent.workflow.agents.work_architect import WorkArchitect
    for role in (WorkArchitect(), PeopleAdvisor()):
        assert isinstance(role, StructuredAgent)
        assert not getattr(role, "tools", None), f"{role.name} 이 도구를 갖고 있다"


def test_action_executor_is_deterministic_and_exposes_no_review_tools_to_a_model():
    """승인 뒤에는 tool 선택 판단이 없으며 review catalog를 LLM에 보낼 이유도 없다."""
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent import tools as T

    executor = ActionExecutor()
    assert not isinstance(executor, ToolAgent)
    assert {tool.name for tool in executor.tools} == {tool.name for tool in T.WRITE_TOOLS}
    assert not ({tool.name for tool in executor.tools} &
                {tool.name for tool in T.REVIEW_TOOLS})


def test_diagram_renders():
    assert G.build().get_graph(xray=1).draw_mermaid().strip()


def test_interrupt_needs_a_checkpointer():
    """체크포인터가 없으면 멈출 자리가 없다 — 그때는 interrupt 도 걸지 않는다."""
    assert not getattr(G.build(), "interrupt_before_nodes", None)


# ── 승인 게이트 ────────────────────────────────────────────────────
def _staged(thread="t1", items=None):
    items = items or [{"summary": "CDC 도입 검토", "type": "Task", "epic": None}]
    state = {"thread_id": thread, "review": {"ok": True},
             "draft": {"mode": "task", "items": items}}
    return G._propose(state)["approval_token"], items


def test_propose_issues_a_token_bound_to_the_draft():
    tok, items = _staged()
    rec = approval.peek(tok)
    assert rec["action"] == "create_tickets" and rec["approved"] is False
    assert rec["fp"] == approval.fingerprint({"mode": "task", "items": items})


@pytest.mark.parametrize(("draft", "action"), [
    ({"mode": "task", "items": [{"summary": "검토 Task", "type": "Task"}]},
     "create_tickets"),
    ({"mode": "epic", "items": [{"summary": "검토 Epic", "type": "Epic",
                                    "epic_name": "검토"}]},
     "create_epic"),
])
def test_create_proposal_requires_an_explicit_passing_review(draft, action):
    blocked = G._propose({"thread_id": "review-gate", "review": {}, "draft": draft})
    assert blocked == {"approval_token": "", "comment_token": ""}

    staged = G._propose({
        "thread_id": "review-gate", "review": {"ok": True}, "draft": draft,
    })
    assert approval.peek(staged["approval_token"])["action"] == action


def test_propose_issues_nothing_for_an_empty_draft():
    assert G._propose({"thread_id": "t1", "draft": {"mode": "task", "items": []}}) == {}


def test_proposed_token_alone_cannot_create_anything():
    """제안 단계의 토큰은 아직 '승인'이 아니다."""
    from app.agent import tools as T
    tok, items = _staged()
    r = T.BY_NAME["create_tickets"].invoke({"mode": "task", "items": items, "approval_token": tok})
    assert r["ok"] is False and "승인" in r["error"]


def test_a_token_from_another_conversation_is_refused():
    from app.agent.workflow import session
    tok, _ = _staged(thread="t1")
    out = session.resume("t2-남의대화", tok)
    assert out["ok"] is False


# ── 전체 왕복 (fake) ───────────────────────────────────────────────
def test_a_full_turn_runs_and_produces_a_reply():
    from app.agent.workflow import session
    out = session.ask("실시간 수집 파이프라인에 CDC 방식을 도입해야 한다")
    assert out["thread_id"]
    assert out["reply"], out
    assert [t["node"] for t in out["trace"]], "어느 에이전트가 무엇을 했는지 남아야 한다"


def test_the_same_thread_keeps_its_history():
    """Checkpointer 가 없으면 되묻기가 불가능하다 — 답만 듣고 앞을 다 잊는다."""
    from app.agent.workflow import session
    first = session.ask("CDC 도입 검토가 필요하다")
    second = session.ask("범위는 수집까지야", thread_id=first["thread_id"])
    assert second["thread_id"] == first["thread_id"]
    snap = G.get_graph().get_state({"configurable": {"thread_id": first["thread_id"]}})
    humans = [m for m in (snap.values.get("messages") or []) if getattr(m, "type", "") == "human"]
    assert len(humans) >= 2, "두 번째 발화가 같은 대화에 쌓이지 않았다"


def test_nothing_is_created_before_approval():
    """★ HITL 의 본질. 그래프가 어디서 멈추든 티켓이 만들어져 있으면 안 된다."""
    from app.agent.tools import _ctx
    from app.agent.workflow import session
    before = len(_ctx.client().search_issues("ORDER BY created DESC", max_results=200))
    session.ask("신규 카탈로그 품질 규칙을 만들어야 한다")
    after = len(_ctx.client().search_issues("ORDER BY created DESC", max_results=200))
    assert after == before, "승인 전에 티켓이 만들어졌다"


# ── 승인 대기 → 재개 → 실제 생성 (가장 중요한 이음매) ──────────────
ITEMS = [{"summary": "CDC 도입 방식 검토 및 결정", "type": "Task", "epic": None}]


@pytest.fixture
def real_draft(monkeypatch):
    """fake 로는 못 넘는 두 곳만 고정하고, 시험하려는 이음매는 전부 진짜로 굴린다.

    고정하는 것:
      · WorkArchitect — fake 는 배열을 비워 두므로 초안이 아예 서지 않는다.
      · Auditor 의 **LLM 의견** — fake 는 boolean 을 해시로 정해 매번 갈린다.
        단 **기계 판정(`validate_bulk`)은 진짜로 돌린다.** 규칙에 어긋난 초안이 통과하면
        이 테스트가 무의미해지기 때문이다.
      · ActionExecutor 의 **문장** — 시험하려는 건 말이 아니라 토큰이 도구까지 닿는가다.

    진짜로 도는 것: PeopleAdvisor · 기계 검증 · propose(토큰 발급) · interrupt · 재개 ·
    승인 토큰 대조 · 실제 티켓 생성.
    """
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.agents.work_architect import WorkArchitect, as_bulk_items
    from app.agent.workflow.agents.auditor import Auditor, _machine_check
    from app.agent.workflow.state import Intent

    # RequestArchitect 도 고정한다 — fake 의 enum 선택은 해시라 **의도 갈래가 늘어날 때마다** 어디로
    # 떨어질지 바뀐다(실제로 pmo 갈래가 생기자 이 시나리오가 그쪽으로 새서 깨졌다).
    # 이 테스트의 관심사는 분류가 아니라 승인 이음매다.
    monkeypatch.setattr(RequestArchitect, "node", lambda self: (lambda state: {
        "intent": Intent.PLAN_WORK, "keywords": ["CDC"], "sufficient": True}))

    monkeypatch.setattr(WorkArchitect, "node", lambda self: (lambda state: {
        "questions": [], "turns": 1,
        "draft": {"mode": "task", "items": [dict(ITEMS[0])], "rationale": "방식이 정해지기 전엔 검토만"}}))

    def rv_node(self):
        def run(state):
            auto = _machine_check(state)        # ← 여기는 진짜다
            return {"review": {"ok": auto["ok"], "problems": [],
                               "checks": {"grounded": True, "rule_compliant": auto["ok"],
                                          "answers_request": True},
                               "errors": auto["errors"], "warnings": auto["warnings"],
                               "summary": "기계 판정만 사용(fake 환경)"},
                    "revisions": (state.get("revisions") or 0) + 1}
        return run

    monkeypatch.setattr(Auditor, "node", rv_node)

    # ActionExecutor 의 LLM 만 건너뛴다 — 시험하려는 것은 문장이 아니라 **토큰이 도구까지 닿는가**다.
    def op_node(self):
        from app.agent import tools as T

        def run(state):
            r = T.BY_NAME["create_tickets"].invoke({
                "mode": (state.get("draft") or {}).get("mode") or "task",
                "items": as_bulk_items(state.get("draft")),
                "approval_token": state.get("approval_token") or ""})
            return {"result": {"created": r.get("created") or [], "failed": r.get("failed") or [],
                               "error": r.get("error") or ""}}
        return run

    monkeypatch.setattr(ActionExecutor, "node", op_node)
    G.reset()
    yield
    G.reset()


def test_graph_stops_and_asks_for_approval(real_draft):
    from app.agent.workflow import session
    out = session.ask("실시간 수집에 CDC 를 도입해야 한다")
    assert out.get("pending"), f"승인 카드가 안 떴다: {out.get('review')}"
    assert out["pending"]["items"][0]["summary"] == ITEMS[0]["summary"]
    assert not out["result"], "승인 전인데 실행 결과가 있다"


def test_resume_after_approval_actually_creates_the_ticket(real_draft):
    from app.agent.tools import _ctx
    from app.agent.workflow import session
    out = session.ask("실시간 수집에 CDC 를 도입해야 한다")
    tok = out["pending"]["token"]

    done = session.resume(out["thread_id"], tok)
    created = (done.get("result") or {}).get("created") or []
    assert created, done
    key = created[0]["key"]
    assert _ctx.client().get_issue(key), "생성됐다는데 실물이 없다"


def test_cancelling_leaves_nothing_behind(real_draft):
    from app.agent.workflow import session
    out = session.ask("실시간 수집에 CDC 를 도입해야 한다")
    tok = out["pending"]["token"]
    assert session.cancel(out["thread_id"], tok)["cancelled"] is True
    # 거절한 토큰으로는 재개해도 아무것도 만들어지지 않는다
    again = session.resume(out["thread_id"], tok)
    assert again["ok"] is False


def test_trace_labels_are_human_readable():
    from app.agent.workflow import session
    out = session.ask("DL-1 어떻게 되어 가나요")
    assert all(t.get("label") for t in out["trace"])


def test_snapshot_restores_a_conversation():
    from app.agent.workflow import session
    tid = session.ask("데이터 카탈로그 관련 이력 알려줘")["thread_id"]
    assert session.snapshot(tid)["thread_id"] == tid


def test_evaluation_snapshot_exposes_retrieval_evidence_without_secrets():
    from app.agent.workflow import session
    tid = session.ask("데이터 카탈로그 관련 이력 알려줘")["thread_id"]
    evidence = session.evaluation_snapshot(tid)
    assert evidence
    assert set(evidence).issubset({
        "requestPlan", "queryPlan", "queryResults", "queryArtifacts", "preSurvey",
        "seedMap", "webContext", "topicDossier", "evidence", "relatedDocs",
        "knowledgeBrief", "trace",
    })
    assert not ({"messages", "token", "apiKey", "providerConfig"} & set(evidence))


def test_evaluation_snapshot_uses_canonical_evidence_without_mutating_state(monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace

    from app.agent.workflow import session

    inconsistent = "기간은 2026-08-01부터 2026-08-20까지 1주 기간입니다."
    state = {
        "evidence": [{
            "key": "ACME-901",
            "title": "Atlas 일정 검토",
            "observations": [{
                "source": "description", "text": inconsistent, "direct": True,
            }],
        }],
        "materialized_ticket_sources": {"ticketDetails": [{
            "key": "ACME-901", "summary": "Atlas 일정 검토",
            "description": inconsistent,
        }]},
    }
    before = deepcopy(state)
    graph = SimpleNamespace(get_state=lambda _config: SimpleNamespace(values=state))
    monkeypatch.setattr(session, "get_graph", lambda: graph)
    monkeypatch.setattr(session, "_config", lambda _thread_id: {})

    evidence = session.evaluation_snapshot("eval-thread")["evidence"]

    assert state == before
    assert evidence[0]["_source_id"] == "ticket:ACME-901"
    observation = evidence[0]["observations"][0]
    assert "정확히 19일" in observation["text"]
    assert observation["authority"] == "research_projection"
    assert observation["direct"] is False


def test_evaluation_snapshot_does_not_fall_back_to_rejected_raw_evidence(monkeypatch):
    from types import SimpleNamespace

    from app.agent.workflow import session
    from app.agent.workflow.agents import result_integrator

    state = {"evidence": [{"key": "ACME-902", "title": "model-only claim"}]}
    graph = SimpleNamespace(get_state=lambda _config: SimpleNamespace(values=state))
    monkeypatch.setattr(session, "get_graph", lambda: graph)
    monkeypatch.setattr(session, "_config", lambda _thread_id: {})
    monkeypatch.setattr(result_integrator, "canonical_evaluation_evidence", lambda _state: [])

    snapshot = session.evaluation_snapshot("eval-thread")

    assert "evidence" not in snapshot


def test_evaluation_snapshot_hydrates_selected_docs_only_from_query_authority(monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace

    from app.agent.workflow import session

    state = {
        "query_results": [{
            "id": "docs", "source": "confluence",
            "result": {"documentBodies": [{
                "title": "Atlas plan", "url": "https://docs.example.test/atlas",
                "text": "canonical decision", "updated": "2026-08-18",
            }]},
        }],
        "related_docs": [{
            "title": "Atlas plan", "url": "https://docs.example.test/atlas",
            "text": "model-authored replacement", "updated": "2099-01-01",
        }],
    }
    before = deepcopy(state)
    graph = SimpleNamespace(get_state=lambda _config: SimpleNamespace(values=state))
    monkeypatch.setattr(session, "get_graph", lambda: graph)
    monkeypatch.setattr(session, "_config", lambda _thread_id: {})

    related = session.evaluation_snapshot("eval-thread")["relatedDocs"]

    assert state == before
    assert related == [{
        "title": "Atlas plan", "url": "https://docs.example.test/atlas",
        "text": "canonical decision", "updated": "2026-08-18",
    }]


def test_evaluation_case_reset_drops_the_previous_graph_thread():
    from app.agent.workflow import session
    from tools.agent_eval_isolation import begin_case, finish_case

    tid = session.ask("DL-9090 진행 상황 알려줘")["thread_id"]
    assert session.snapshot(tid)
    isolated = begin_case("next-case")
    assert session.evaluation_snapshot(tid) == {}
    assert finish_case(isolated)["worldUnchanged"] is True


def test_knowledge_question_routes_through_curator():
    """지식형 ask("X가 뭐야/정리해줘")는 ResearchAnalyst → KnowledgeCurator → ResultIntegrator 로 흐른다.

    KnowledgeCurator 는 신설 역할(사용자 요청) — 조사 결과를 개념/우리 상황/참고/공백 스키마로
    정리한다. 도구는 없다(새 조사 금지). fake 로 경로와 State 필드만 검증한다.
    """
    from app.agent.workflow.graph import route_after_research_analyst
    from app.agent.workflow.state import Intent
    from langchain_core.messages import HumanMessage
    st = {"intent": Intent.ASK, "messages": [HumanMessage(content="CDC가 뭐야? 정리해줘")]}
    assert route_after_research_analyst(st) == "curate"
    st2 = {"intent": Intent.ASK, "messages": [HumanMessage(content="DL-101 왜 멈췄었지?")]}
    assert route_after_research_analyst(st2) == "respond"
    st3 = {"intent": Intent.PLAN_WORK, "messages": [HumanMessage(content="CDC가 뭐야 정리")]}
    assert route_after_research_analyst(st3) == "refine"
    assert "knowledge_curator" in set(G.build().get_graph().nodes)


def test_curator_produces_brief_from_materials():
    import os
    os.environ["LAKE_AGENT_PROVIDER"] = "fake"
    from app.agent.workflow.agents.knowledge_curator import KnowledgeCurator
    from langchain_core.messages import HumanMessage
    c = KnowledgeCurator()
    txt = c.task({"messages": [HumanMessage(content="CDC가 뭐야?")],
                  "situation": "DL-118 에서 검토", "evidence": [],
                  "web_context": "- CDC 는 변경 데이터 캡처"})
    assert "External Technology Research" in txt and "DL-118" in txt
    out = c.apply({}, {"concepts": [{"term": "CDC", "explanation": "x"}],
                       "our_context": "사내 이력 없음", "references": [], "gaps": ["도입 여부"]})
    kb = out["knowledge_brief"]
    assert kb["concepts"] and kb["gaps"] == ["도입 여부"]


def test_operator_create_is_deterministic_tool_truth():
    """생성 실행도 LLM 없이 — 도구 결과만이 사실이다.

    실측: ReAct 생성이 검증 **경고**(Epic 미연결 안내)를 '실패한 항목·후속 조치'로
    각색해 보고했다. 사용자가 방금 '최상위로 두겠다'고 결정했는데 다시 경고한 셈.
    결정적 실행은 created/failed 를 도구가 준 그대로 옮긴다.
    """
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.agents.work_architect import as_bulk_items
    draft = {"mode": "task", "items": [{"summary": "최상위로 두는 티켓", "type": "Task",
                                        "epic": ""}]}
    tok = approval.stage("t-det", "create_tickets",
                         {"mode": "task", "items": as_bulk_items(draft)})
    approval.approve(tok, "t-det")
    out = ActionExecutor().node()({"thread_id": "t-det", "draft": draft, "approval_token": tok,
                             "change_plan": {}, "trace": []})
    r = out["result"]
    assert r["created"] and r["created"][0]["key"], r
    assert r["failed"] == [], "경고가 실패로 각색되면 안 된다"
    assert r["note"] == ""


def test_action_executor_runs_approved_bulk_comments_without_an_llm():
    """댓글 일괄 승인 payload를 모델의 tool 선택 없이 그대로 실행한다."""
    from app.agent.tools import _ctx
    from app.agent.workflow.agents.action_executor import ActionExecutor

    keys = [row["key"] for row in _ctx.client().search_issues(
        "ORDER BY created DESC", max_results=2,
    )]
    rows = [{"key": key, "body": f"{key} 결정사항 공유"} for key in keys]
    tok = approval.stage("t-comments", "add_ticket_comments", {"items": rows})
    approval.approve(tok, "t-comments")

    out = ActionExecutor().node()({
        "thread_id": "t-comments", "approval_token": tok,
        # A misleading stale plan must not override the approved record.
        "change_plan": {"key": keys[0], "changes": {"priority": "P1-Critical"}},
        "trace": [],
    })

    assert not out["result"]["failed"], out
    assert {row["key"] for row in out["result"]["updated"]} == set(keys)
    assert all(row["fields"] == ["comment"] for row in out["result"]["updated"])
    assert approval.peek(tok) is None, "승인 토큰은 정확히 한 번 소비되어야 한다"


def test_action_executor_runs_an_explicitly_approved_document_attachment():
    """일반 ReAct가 없어도 registry의 승인된 attach action은 deterministic하게 실행된다."""
    from app.agent.tools import _ctx
    from app.agent.workflow.agents.action_executor import ActionExecutor

    key = _ctx.client().search_issues("ORDER BY created DESC", max_results=1)[0]["key"]
    payload = {"key": key, "url": "https://docs.example.test/decision/1",
               "title": "설계 결정 기록"}
    tok = approval.stage("t-attach", "attach_document", payload)
    approval.approve(tok, "t-attach")

    out = ActionExecutor().node()({
        "thread_id": "t-attach", "approval_token": tok, "trace": [],
    })

    assert out["result"]["updated"] == [{"key": key, "fields": ["document"]}]
    assert out["result"]["failed"] == []
    assert approval.peek(tok) is None


def test_action_executor_fails_closed_without_a_supported_approved_action():
    from app.agent.workflow.agents.action_executor import ActionExecutor

    missing = ActionExecutor().node()({"thread_id": "t-none", "trace": []})
    assert missing["result"]["failed"]
    assert not missing["result"]["created"] and not missing["result"]["updated"]

    tok = approval.stage("t-unknown", "delete_ticket", {"key": "DL-1"})
    approval.approve(tok, "t-unknown")
    unknown = ActionExecutor().node()({
        "thread_id": "t-unknown", "approval_token": tok, "trace": [],
    })
    assert "지원하지 않는" in unknown["result"]["failed"][0]["error"]
    assert approval.peek(tok) is None, "지원하지 않는 승인 capability도 폐기해야 한다"


def test_action_executor_requires_the_approval_record_thread_on_every_attempt():
    """A token alone is never authority; checkpoint state must carry the same thread id."""
    from app.agent.workflow.agents.action_executor import ActionExecutor

    payload = {"key": "DL-1", "body": "결정사항"}
    tok = approval.stage("t-owner", "add_ticket_comment", payload)
    approval.approve(tok, "t-owner")

    missing = ActionExecutor().node()({"approval_token": tok, "trace": []})
    assert "대화 식별자" in missing["result"]["failed"][0]["error"]
    assert approval.peek(tok) is not None, "실행하지 않은 정상 capability는 임의 폐기하지 않는다"

    foreign = ActionExecutor().node()({
        "thread_id": "t-foreign", "approval_token": tok, "trace": [],
    })
    assert "이 대화" in foreign["result"]["failed"][0]["error"]
    assert approval.peek(tok) is not None, "다른 대화가 소유자의 capability를 폐기하면 안 된다"


def test_action_executor_discards_primary_and_secondary_tokens_after_update_failure(monkeypatch):
    """One rejected approval-card attempt cannot be replayed later as a field update or comment."""
    from app.agent import tools as T
    from app.agent.workflow.agents.action_executor import ActionExecutor

    primary_payload = {"key": "DL-1", "changes": {"summary": "바뀐 제목"}}
    comment_payload = {"key": "DL-1", "body": "변경 사유"}
    primary, comment = approval.stage_pair(
        "t-update-fail", "update_ticket", primary_payload,
        "add_ticket_comment", comment_payload,
    )
    approval.approve(primary, "t-update-fail")
    approval.approve(comment, "t-update-fail")

    class FailingUpdate:
        name = "update_ticket"

        @staticmethod
        def invoke(_args):
            # Mirrors a write-tool pre-validation failure: it returns before consume().
            return {"ok": False, "error": "Done 티켓은 필드를 변경할 수 없습니다."}

    class ForbiddenComment:
        name = "add_ticket_comment"

        @staticmethod
        def invoke(_args):
            raise AssertionError("primary failure must not post the secondary comment")

    monkeypatch.setitem(T.BY_NAME, "update_ticket", FailingUpdate())
    monkeypatch.setitem(T.BY_NAME, "add_ticket_comment", ForbiddenComment())
    out = ActionExecutor().node()({
        "thread_id": "t-update-fail", "approval_token": primary,
        "comment_token": comment, "trace": [],
    })

    assert out["result"]["failed"]
    assert approval.peek(primary) is None, "실패한 primary capability도 한 번 시도 후 폐기"
    assert approval.peek(comment) is None, "실행하지 않은 secondary comment도 함께 폐기"


def test_action_executor_rejects_an_unbound_secondary_before_the_primary(monkeypatch):
    """Two unrelated valid tokens cannot be spliced into one compound card."""
    from app.agent import tools as T
    from app.agent.workflow.agents.action_executor import ActionExecutor

    primary_payload = {"key": "DL-1", "changes": {"summary": "바뀐 제목"}}
    primary = approval.stage("t-update-partial", "update_ticket", primary_payload)
    invalid = approval.stage("t-update-partial", "link_tickets", {
        "key": "DL-1", "other": "DL-2", "relation": "Relates",
    })
    approval.approve(primary, "t-update-partial")
    approval.approve(invalid, "t-update-partial")

    class SuccessfulUpdate:
        name = "update_ticket"

        @staticmethod
        def invoke(args):
            ok, why = approval.consume(
                args["approval_token"], "update_ticket", primary_payload,
            )
            return ({"ok": True, "updated": ["summary"]} if ok
                    else {"ok": False, "error": why})

    monkeypatch.setitem(T.BY_NAME, "update_ticket", SuccessfulUpdate())
    out = ActionExecutor().node()({
        "thread_id": "t-update-partial", "approval_token": primary,
        "comment_token": invalid, "trace": [],
    })

    assert out["result"]["updated"] == []
    assert out["result"]["failed"]
    assert "결속되지 않은" in out["result"]["failed"][0]["error"]
    assert approval.peek(primary) is None
    assert approval.peek(invalid) is None, "유효하지 않은 same-thread secondary capability 폐기"


@pytest.mark.parametrize(("primary_action", "secondary_action"), [
    ("update_tickets", "add_ticket_comments"),
    ("link_tickets", "add_ticket_comment"),
])
def test_action_executor_dispatches_supported_compound_change_effects(
        monkeypatch, primary_action, secondary_action):
    """Every reviewed effect on a mixed approval card executes exactly once."""
    from app.agent.workflow.agents.action_executor import ActionExecutor

    primary_payload = ({"items": [{"key": "DL-1", "changes": {"priority": "High"}}]}
                       if primary_action == "update_tickets" else
                       {"key": "DL-1", "other": "DL-2", "relation": "Relates"})
    secondary_payload = ({"items": [{"key": "DL-1", "body": "결정"}]}
                         if secondary_action == "add_ticket_comments" else
                         {"key": "DL-1", "body": "결정"})
    primary, secondary = approval.stage_pair(
        "t-compound", primary_action, primary_payload, secondary_action, secondary_payload,
    )
    approval.approve(primary, "t-compound")
    approval.approve(secondary, "t-compound")
    calls = []

    def dispatch(self, action, payload, token):
        calls.append((action, payload, token))
        return ({"created": [], "updated": [{"key": "DL-1", "fields": [action]}],
                 "failed": [], "note": ""}, action)

    monkeypatch.setattr(ActionExecutor, "_dispatch", dispatch)
    out = ActionExecutor().node()({
        "thread_id": "t-compound", "approval_token": primary,
        "comment_token": secondary, "trace": [],
    })

    assert [call[0] for call in calls] == [primary_action, secondary_action]
    assert out["result"]["failed"] == []
    assert out["result"]["updated"][0]["fields"] == [primary_action, secondary_action]


def test_partial_bulk_update_explicitly_reports_that_comments_were_not_posted(monkeypatch):
    from app.agent.workflow.agents.action_executor import ActionExecutor

    primary_payload = {"items": [
        {"key": "DL-1", "changes": {"priority": "High"}},
        {"key": "DL-2", "changes": {"priority": "High"}},
    ]}
    secondary_payload = {"items": [
        {"key": "DL-1", "body": "결정"}, {"key": "DL-2", "body": "결정"},
    ]}
    primary, secondary = approval.stage_pair(
        "t-partial", "update_tickets", primary_payload,
        "add_ticket_comments", secondary_payload,
    )
    approval.approve(primary, "t-partial")
    approval.approve(secondary, "t-partial")
    calls = []

    def dispatch(self, action, payload, token):
        calls.append(action)
        if action == "update_tickets":
            return ({"created": [], "updated": [{"key": "DL-1", "fields": ["priority"]}],
                     "failed": [{"summary": "DL-2", "error": "provider failure"}],
                     "note": ""}, "partial update")
        raise AssertionError("secondary comments must not execute after partial primary failure")

    monkeypatch.setattr(ActionExecutor, "_dispatch", dispatch)
    out = ActionExecutor().node()({
        "thread_id": "t-partial", "approval_token": primary,
        "comment_token": secondary, "trace": [],
    })

    assert calls == ["update_tickets"]
    assert "코멘트" in out["result"]["note"] and "게시하지 않았" in out["result"]["note"]
    assert approval.peek(secondary) is None


def test_partial_secondary_comments_preserve_successes_and_failures(monkeypatch):
    from app.agent.workflow.agents.action_executor import ActionExecutor

    primary_payload = {"items": [
        {"key": "DL-1", "changes": {"priority": "High"}},
        {"key": "DL-2", "changes": {"priority": "High"}},
    ]}
    secondary_payload = {"items": [
        {"key": "DL-1", "body": "결정"}, {"key": "DL-2", "body": "결정"},
    ]}
    primary, secondary = approval.stage_pair(
        "t-secondary-partial", "update_tickets", primary_payload,
        "add_ticket_comments", secondary_payload,
    )
    approval.approve(primary, "t-secondary-partial")
    approval.approve(secondary, "t-secondary-partial")

    def dispatch(self, action, payload, token):
        if action == "update_tickets":
            return ({"created": [], "updated": [
                {"key": "DL-1", "fields": ["priority"]},
                {"key": "DL-2", "fields": ["priority"]},
            ], "failed": [], "note": ""}, "updated")
        return ({"created": [], "updated": [{"key": "DL-1", "fields": ["comment"]}],
                 "failed": [{"summary": "DL-2", "error": "comment provider failure"}],
                 "note": ""}, "partial comments")

    monkeypatch.setattr(ActionExecutor, "_dispatch", dispatch)
    out = ActionExecutor().node()({
        "thread_id": "t-secondary-partial", "approval_token": primary,
        "comment_token": secondary, "trace": [],
    })

    by_key = {row["key"]: row["fields"] for row in out["result"]["updated"]}
    assert by_key["DL-1"] == ["priority", "comment"]
    assert by_key["DL-2"] == ["priority"]
    assert out["result"]["failed"] == [
        {"summary": "DL-2", "error": "comment provider failure"},
    ]


def test_compound_primary_missing_its_bound_comment_token_executes_nothing(monkeypatch):
    from app.agent.workflow.agents.action_executor import ActionExecutor

    primary, secondary = approval.stage_pair(
        "t-bound-missing", "update_ticket",
        {"key": "DL-1", "changes": {"priority": "High"}},
        "add_ticket_comment", {"key": "DL-1", "body": "결정"},
    )
    approval.approve(primary, "t-bound-missing")
    approval.approve(secondary, "t-bound-missing")
    calls = []
    monkeypatch.setattr(
        ActionExecutor, "_dispatch",
        lambda *args: calls.append(args) or ({"updated": [], "failed": []}, "unexpected"),
    )

    out = ActionExecutor().node()({
        "thread_id": "t-bound-missing", "approval_token": primary,
        "comment_token": "", "trace": [],
    })

    assert calls == []
    assert out["result"]["failed"]
    assert approval.peek(primary) is None and approval.peek(secondary) is None


def test_compound_tokens_from_different_cards_cannot_be_spliced(monkeypatch):
    from app.agent.workflow.agents.action_executor import ActionExecutor

    primary, proper = approval.stage_pair(
        "t-pair", "update_ticket", {"key": "DL-1", "changes": {"priority": "High"}},
        "add_ticket_comment", {"key": "DL-1", "body": "정상"},
    )
    other_primary, foreign = approval.stage_pair(
        "t-pair", "update_ticket", {"key": "DL-9", "changes": {"priority": "Low"}},
        "add_ticket_comment", {"key": "DL-999", "body": "다른 카드"},
    )
    for token in (primary, proper, other_primary, foreign):
        approval.approve(token, "t-pair")
    calls = []
    monkeypatch.setattr(
        ActionExecutor, "_dispatch",
        lambda *args: calls.append(args) or ({"updated": [], "failed": []}, "unexpected"),
    )

    out = ActionExecutor().node()({
        "thread_id": "t-pair", "approval_token": primary,
        "comment_token": foreign, "trace": [],
    })

    assert calls == []
    assert out["result"]["failed"]
    assert approval.peek(primary) is None and approval.peek(foreign) is None


@pytest.mark.parametrize("keys", [
    ["ACME-10", "ACME-10", "ACME-20"],
    ["ACME-10", "acme-10", "ACME-20"],
])
def test_propose_rejects_duplicate_bulk_targets(keys):
    state = {
        "thread_id": "typed-duplicate-targets",
        "continuation_contract": {
            "version": "continuation.v1", "root_request": "두 티켓 변경",
            "intent": "modify", "action": "mixed",
            "target_keys": ["ACME-10", "ACME-20"],
            "outcome_ids": ["update", "comment"], "decisions": [],
        },
        "draft": {}, "change_plan": {
            "keys": keys, "changes": {"priority": "P1-Critical"},
            "comment": "결정",
        },
    }

    blocked = G._propose(state)

    assert blocked["approval_token"] == "" and blocked["comment_token"] == ""
    assert any(row.get("field") == "target" for row in blocked["review"]["errors"])


def test_propose_rejects_invalid_or_duplicate_bulk_comment_previews():
    state = {
        "thread_id": "typed-invalid-comment-preview",
        "continuation_contract": {
            "version": "continuation.v1", "root_request": "두 티켓 변경과 댓글",
            "intent": "modify", "action": "mixed",
            "target_keys": ["ACME-10", "ACME-20"],
            "outcome_ids": ["update", "comment"], "decisions": [],
        },
        "draft": {}, "change_plan": {
            "keys": ["ACME-10", "ACME-20"],
            "changes": {"priority": "P1-Critical"}, "comment": "결정",
            "comments": [
                {"key": "ACME-10", "body": "결정"},
                {"key": "ACME-20", "body": "결정"},
                {"key": "NOT_A_KEY", "body": "유출"},
            ],
        },
    }

    blocked = G._propose(state)

    assert blocked["approval_token"] == "" and blocked["comment_token"] == ""
    assert any(row.get("field") == "comment_targets"
               for row in blocked["review"]["errors"])


def test_action_executor_write_adapter_allowlist_matches_registry_and_fails_on_drift():
    """A new WRITE_TOOL needs an explicit reviewed executor adapter; registry growth fails closed."""
    from types import SimpleNamespace
    from app.agent import tools as T
    from app.agent.workflow.agents.action_executor import (
        ActionExecutor, SUPPORTED_WRITE_ACTIONS,
    )

    registered = {tool.name for tool in T.WRITE_TOOLS}
    assert registered == SUPPORTED_WRITE_ACTIONS
    ActionExecutor._validate_action_registry(T)

    with pytest.raises(RuntimeError, match="unreviewed write tools: delete_ticket"):
        ActionExecutor._validate_action_registry(SimpleNamespace(
            WRITE_TOOLS=[*T.WRITE_TOOLS, SimpleNamespace(name="delete_ticket")],
        ))


def test_fast_paths_skip_historian_when_safe():
    """빠른 경로 2종 — 후속 턴(조사 결과 보유)과 키 명시 modify 는 재조사 없이 WorkArchitect 직행.

    인터뷰 답변 턴마다 ResearchAnalyst 이 통째로 다시 돌던 것이 턴 시간의 최대 낭비였다
    (턴당 LLM 3~5회). 첫 턴·새 대화는 여전히 조사부터.
    """
    # 첫 턴(구체적 요청) — 조사부터
    assert G.route_after_request_architect({"intent": Intent.PLAN_WORK, "turns": 0,
                                  "sufficient": True}) == "investigate"
    # 첫 턴(막연한 요청) — 내부 이력으로 해소 가능한지 조사한 뒤 필요한 것만 인터뷰
    assert G.route_after_request_architect({"intent": Intent.PLAN_WORK, "turns": 0,
                                  "sufficient": False}) == "investigate"
    # 위임("알아서")도 동일하게 조사부터; 필수 blocker는 조사 뒤에만 질문
    from langchain_core.messages import HumanMessage
    assert G.route_after_request_architect({"intent": Intent.PLAN_WORK, "turns": 0, "sufficient": False,
                                  "messages": [HumanMessage(content="알아서 해줘")]}) == "investigate"
    # 후속 턴 — situation 보유 시 직행
    assert G.route_after_request_architect({"intent": Intent.PLAN_WORK, "turns": 1,
                                  "situation": "DL-118 에서 검토"}) == "refine"
    # Research/term interviews can happen before WorkArchitect increments turns. The explicit
    # session boundary, not the counter, proves this is the same researched work.
    assert G.route_after_request_architect({
        "intent": Intent.PLAN_WORK, "turns": 0, "turn_continuation": True,
        "situation": "DL-118 상세와 기술 근거 조사 완료",
    }) == "refine"
    # modify + 키 명시 — 직행 (키 확인은 WorkArchitect 의 get_ticket 몫)
    assert G.route_after_request_architect({"intent": Intent.MODIFY,
                                  "mentioned_keys": ["DL-101"]}) == "refine"
    # modify 인데 키가 없으면 여전히 조사(어느 티켓인지 찾아야 한다)
    assert G.route_after_request_architect({"intent": Intent.MODIFY}) == "investigate"


def test_continuation_refreshes_retrieval_before_delegated_parent_selection():
    from langchain_core.messages import HumanMessage

    base = {
        "intent": Intent.PLAN_WORK,
        "turns": 0,
        "turn_continuation": True,
        "request_text": "StarRocks Puffin NDV 구현 Task를 구성해줘",
        "messages": [HumanMessage(content=(
            "기존 Epic은 네가 골라서 연결하고 범위는 1차, 마감은 2026-09-30"
        ))],
        "situation": "관련 PoC와 구현 이력 조사 완료",
    }
    missing = {
        **base,
        "materialized_ticket_sources": {
            "parentCandidateKeys": [],
            "ticketDetails": [{"key": "DL-9200", "type": "Epic"}],
        },
    }
    opened = {
        **base,
        "materialized_ticket_sources": {
            "parentCandidateKeys": ["DL-9200"],
            "ticketDetails": [{"key": "DL-9200", "type": "Epic"}],
        },
    }
    completed_zero_hit = {
        **base,
        "materialized_ticket_sources": {
            "parentCandidateSearchAttempted": True,
            "parentCandidateKeys": [],
            "ticketDetails": [],
        },
    }

    assert G.route_after_request_architect(missing) == "investigate"
    assert G.route_after_request_architect(opened) == "refine"
    assert G.route_after_request_architect(completed_zero_hit) == "refine"


def test_trace_reducer_appends_deltas_and_resets_on_sentinel():
    """병렬 fan-out(PeopleAdvisor∥Auditor)에서 두 노드가 같은 스텝에 trace 를 써도
    리듀서가 이어 붙인다. 턴 시작 리셋은 sentinel 로만 가능하다(리듀서엔 대입이 없다)."""
    from app.agent.workflow.state import TRACE_RESET, merge_trace, note
    a = note({}, "people_advisor", "제안 2건")
    b = note({}, "auditor", "통과")
    merged = merge_trace(merge_trace([{"node": "old"}], a), b)
    assert [t["node"] for t in merged] == ["old", "people_advisor", "auditor"]
    assert merge_trace(merged, [TRACE_RESET]) == []
    assert merge_trace(merged, [TRACE_RESET, a[0]]) == a


def test_merge_join_drops_ghost_assignees():
    """Auditor 가 배정 '전' 초안을 검증하므로(병렬), 배정 사용자 실재는 join 코드가 보장한다."""
    real = _any_real_user()
    draft = {"mode": "task", "items": [{"summary": "a"}, {"summary": "b"}]}
    assignments = [{"index": 0, "user": real, "reasons": ["유사 이력 DL-1"]},
                   {"index": 1, "user": "ghost.x9999", "reasons": ["임의"]}]
    out = G._merge_assignments({"draft": draft, "assignments": assignments})
    items = out["draft"]["items"]
    assert items[0].get("assignee") == real
    assert not items[1].get("assignee"), "실재하지 않는 사용자 배정은 join 에서 걸러져야 한다"
    assert not out["assignments"][1].get("user"), "ResultIntegrator 상태에도 유령 추천을 남기지 않는다"


def test_merge_join_drops_a_fabricated_child_recommendation():
    """PeopleAdvisor has no authority to introduce an unknown child account id."""
    real = _any_real_user()
    draft = {"mode": "task", "items": [{
        "summary": "a", "assignee": real,
        "children": [{"summary": "child"}],
    }]}
    assignments = [{
        "index": 0, "user": real, "reasons": ["유사 이력 DL-1"],
        "children": [{"index": 0, "user": "ghost.x9999", "why": "임의 추천"}],
    }]

    out = G._merge_assignments({"draft": draft, "assignments": assignments})

    child = out["draft"]["items"][0]["children"][0]
    assert not child.get("assignee")
    assert not out["assignments"][0]["children"][0].get("user")


def test_merge_join_fails_closed_for_an_explicit_unknown_child_assignee():
    """An invalid literal user assignment stays visible and blocks approval for correction."""
    real = _any_real_user()
    draft = {"mode": "task", "items": [{
        "summary": "a", "assignee": real,
        "children": [{
            "summary": "child", "assignee": "ghost.x9999", "assignee_source": "user",
        }],
    }]}
    assignments = [{"index": 0, "user": real, "reasons": ["유사 이력 DL-1"]}]

    out = G._merge_assignments({"draft": draft, "assignments": assignments})

    assert out["draft"]["items"][0]["children"][0]["assignee"] == "ghost.x9999"
    assert out["review"]["ok"] is False
    assert any(row.get("field") == "assignee" and row.get("child_index") == 0
               for row in out["review"]["errors"])


def test_merge_join_fails_closed_for_an_explicit_unknown_root_assignee():
    """Root and child ownership use the same final identity-authority rule."""
    draft = {"mode": "task", "items": [{
        "summary": "a", "assignee": "ghost.x9999", "assignee_source": "user",
    }]}

    out = G._merge_assignments({"draft": draft, "assignments": []})

    assert out["draft"]["items"][0]["assignee"] == "ghost.x9999"
    assert out["review"]["ok"] is False
    assert any(row.get("field") == "assignee" and "child_index" not in row
               for row in out["review"]["errors"])


def test_merge_join_fails_closed_when_identity_authority_is_unavailable(monkeypatch):
    """A lookup outage preserves the visible draft but cannot authorize its owners."""
    from app.agent.tools import _ctx

    monkeypatch.setattr(_ctx, "client", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    draft = {"mode": "task", "items": [{
        "summary": "a", "assignee": "skcc.x1042",
        "children": [{"summary": "child", "assignee": "skcc.x1045"}],
    }]}

    out = G._merge_assignments({"draft": draft, "assignments": []})

    assert out["draft"]["items"][0]["assignee"] == "skcc.x1042"
    assert out["draft"]["items"][0]["children"][0]["assignee"] == "skcc.x1045"
    assert out["review"]["ok"] is False
    assert len([row for row in out["review"]["errors"]
                if row.get("field") == "assignee"]) == 2


def test_merge_join_resolves_suffix_only_ids():
    """사용자가 "x1103"처럼 접미만 대면 로스터 유일 일치로 풀 아이디로 해소한다 —
    직렬 시절 Auditor 재작성 루프가 하던 교정이 병렬화로 사라져 배정이 통째로 빠졌다(실측)."""
    real = _any_real_user()
    suffix = real.split(".", 1)[1]
    draft = {"mode": "task", "items": [{"summary": "a", "assignee": suffix}]}
    out = G._merge_assignments({"draft": draft, "assignments": []})
    assert out["draft"]["items"][0].get("assignee") == real


def test_merge_join_reapplies_user_named_assignees_after_recommendations():
    from langchain_core.messages import HumanMessage
    draft = {"mode": "subtask", "items": [
        {"summary": "성능을 측정하는 작업", "type": "Sub-Task"},
        {"summary": "가이드를 작성하는 작업", "type": "Sub-Task"}]}
    state = {"messages": [HumanMessage(
        content="성능 측정은 x1402, 가이드 작성은 x1450. 알아서")],
        "draft": draft,
        "assignments": [{"index": 0, "user": "skcc.x1042", "reasons": ["부하 낮음"]},
                        {"index": 1, "user": "skcc.x1045", "reasons": ["부하 낮음"],
                         "alternates": [{"user": "skcc.x1450", "why": "대안"}]}]}
    merged = G._merge_assignments(state)
    out = merged["draft"]["items"]
    assert [i.get("assignee") for i in out] == ["skcc.x1402", "skcc.x1450"]
    assert all(i.get("assignee_source") == "user" for i in out)
    assert [a.get("user") for a in merged["assignments"]] == ["skcc.x1402", "skcc.x1450"]
    assert all("payload" in a["reasons"][0] for a in merged["assignments"])
    assert not merged["assignments"][1].get("alternates"), "1순위와 같은 사용자는 대안이 아니다"


def test_final_join_preserves_explicit_owner_unassigned_and_parent(monkeypatch):
    """MTG5/MTG8: recommendations cannot override literal meeting decisions."""
    from app.agent.workflow.agents import auditor

    monkeypatch.setattr(auditor, "_machine_check", lambda _state: {
        "ok": True, "errors": [], "warnings": [], "text": "ok",
    })
    state = {
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "Epic DL-9200 아래에 Writer와 Reader 후속 작업을 만든다",
            "intent": "plan_work", "action": "create",
            "target_keys": ["DL-9200"], "outcome_ids": ["writer", "reader"],
            "decisions": [{
                "field": "parent", "value": "Epic DL-9200 아래", "source": "explicit_refinement",
            }],
        },
        "review": {"ok": True, "errors": [], "problems": []},
        "draft": {"mode": "task", "items": [{
            "summary": "Writer 호환성 확인", "type": "Task", "epic": "DL-9200",
            "assignee": "skcc.i2011", "assignee_source": "user",
        }, {
            "summary": "Reader 호환성 확인", "type": "Task", "epic": "DL-9200",
            "assignee_source": "user_unassigned",
        }]},
        "assignments": [
            {"index": 0, "user": "skcc.x1210", "reasons": ["부하가 낮음"]},
            {"index": 1, "user": "skcc.x1103", "reasons": ["유사 이력"]},
        ],
    }

    merged = G._merge_assignments(state)
    writer, reader = merged["draft"]["items"]

    assert writer["assignee"] == "skcc.i2011"
    assert reader.get("assignee") in (None, "")
    assert writer["epic"] == reader["epic"] == "DL-9200"
    assert merged["review"]["ok"] is True
    assert merged["review"]["final_authority"]["kind"] == "create"


def test_final_join_rechecks_parent_after_premerge_auditor_passed(monkeypatch):
    """The semantic Auditor verdict is not authority over the post-merge payload."""
    from app.agent.workflow.agents import auditor

    monkeypatch.setattr(auditor, "_machine_check", lambda _state: {
        "ok": True, "errors": [], "warnings": [], "text": "ok",
    })
    state = {
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "Epic DL-9200 아래에 Writer 확인 Task를 만든다",
            "intent": "plan_work", "action": "create",
            "target_keys": ["DL-9200"], "outcome_ids": ["writer"],
            "decisions": [{
                "field": "parent", "value": "DL-9200", "source": "interview_answer",
            }],
        },
        "review": {"ok": True, "errors": [], "problems": []},
        "draft": {"mode": "task", "items": [{
            "summary": "Writer 확인", "type": "Task", "epic": "DL-7001",
        }]},
        "assignments": [],
    }

    merged = G._merge_assignments(state)

    assert merged["review"]["ok"] is False
    assert any(row.get("field") == "parent" for row in merged["review"]["errors"])
    assert G.route_after_auditor({**state, **merged, "revisions": MAX_REVISIONS}) == "respond"


def test_scoped_parent_decisions_are_not_reinterpreted_as_one_global_parent(monkeypatch):
    from app.agent.workflow.agents import auditor

    monkeypatch.setattr(auditor, "_machine_check", lambda _state: {
        "ok": True, "errors": [], "warnings": [], "text": "ok",
    })
    state = {
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "Bug와 Story를 각각 지정된 상위에 생성",
            "intent": "plan_work", "action": "create",
            "target_keys": ["DL-100", "DL-200"], "outcome_ids": ["bug", "story"],
            "decisions": [
                {"field": "parent:bug", "value": "DL-100", "source": "interview_answer"},
                {"field": "parent:story", "value": "DL-200", "source": "interview_answer"},
            ],
        },
        "review": {"ok": True, "errors": [], "problems": []},
        "draft": {"mode": "task", "items": [
            {"summary": "Bug 후속", "type": "Bug", "epic": "DL-100",
             "outcome_refs": ["bug"]},
            {"summary": "Story 후속", "type": "Story", "epic": "DL-200",
             "outcome_refs": ["story"]},
        ]},
    }

    review = auditor.final_authority_review(state, require_effect=True)

    assert review["ok"] is True
    assert not [row for row in review["errors"] if row.get("field") == "parent"]


def test_final_join_blocks_explicit_single_subtask_cardinality_expansion(monkeypatch):
    """ASKD2: one requested Sub-Task cannot become four approval payload items."""
    from app.agent.workflow.agents import auditor

    monkeypatch.setattr(auditor, "_machine_check", lambda _state: {
        "ok": True, "errors": [], "warnings": [], "text": "ok",
    })
    state = {
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "DL-9090 아래에 회귀 검증 Sub-Task 하나 만들어줘",
            "intent": "plan_work", "action": "create",
            "target_keys": ["DL-9090"], "outcome_ids": ["regression"],
            "decisions": [],
        },
        "request_plan": {"tasks": [{
            "id": "regression", "kind": "ticket", "write_intent": True,
            "instruction": "DL-9090 아래에 회귀 검증 Sub-Task 하나 생성",
        }]},
        "review": {"ok": True, "errors": [], "problems": []},
        "draft": {"mode": "subtask", "items": [
            {"summary": f"회귀 검증 {index}", "type": "Sub-Task", "parent": "DL-9090"}
            for index in range(1, 5)
        ]},
        "assignments": [],
    }

    merged = G._merge_assignments(state)

    assert merged["review"]["ok"] is False
    assert any(row.get("field") == "cardinality" for row in merged["review"]["errors"])


def _any_real_user():
    from app.agent.tools._ctx import client
    lk = client().bulk_lookup()
    for u in ("skcc.x1042", "skcc.x1001", "etl.x1001"):
        if lk.user_exists(u):
            return u
    import pytest
    pytest.skip("mock 사용자 확인 불가")


def test_card_edits_are_applied_and_survive_the_fingerprint(real_draft):
    """카드 인라인 편집(제목·라벨·마감) — State draft 를 고치고 지문을 재생성하므로
    승인·실행이 수정본으로 이루어진다. 두 벌 patch 시절의 지문 어긋남 회귀 방지."""
    from app.agent.tools import _ctx
    from app.agent.workflow import session
    out = session.ask("실시간 수집에 CDC 를 도입해야 한다")
    tok = out["pending"]["token"]
    done = session.resume(out["thread_id"], tok, {
        "items": {"0": {"summary": "[ETL] CDC 파이프라인 도입 — 수정본",
                        "labels": "cdc, q3-2026", "duedate": "2026-09-30"}}})
    created = (done.get("result") or {}).get("created") or []
    assert created, done
    got = _ctx.client().get_issue(created[0]["key"])
    f = got.get("fields") or {}
    assert f.get("summary") == "[ETL] CDC 파이프라인 도입 — 수정본"
    assert "cdc" in (f.get("labels") or [])


def test_card_edit_with_a_bad_duedate_keeps_the_card_alive(real_draft):
    """형식이 틀리면 실행하지 않고 오류만 — 카드는 살아 있어 다시 고칠 수 있다."""
    from app.agent.workflow import session
    out = session.ask("실시간 수집에 CDC 를 도입해야 한다")
    tok = out["pending"]["token"]
    r = session.resume(out["thread_id"], tok, {"items": {"0": {"duedate": "다음주"}}})
    assert r["ok"] is False and "YYYY-MM-DD" in r["error"]
    # 올바른 값으로 다시 — 같은 토큰이 그대로 쓰인다
    done = session.resume(out["thread_id"], tok, {"items": {"0": {"duedate": "2026-10-01"}}})
    assert (done.get("result") or {}).get("created"), done


def test_bulk_change_plan_stages_the_update_tickets_fingerprint():
    """조건 일괄 수정 — 승인 지문이 update_tickets(bulk) 도구의 payload 와 같은 모양이어야
    consume 이 통과한다(E2 실측 갭의 회귀 방지)."""
    from app.agent import approval
    approval.clear()
    plan = {"keys": ["DL-1", "DL-2"], "changes": {"priority": "P1-Critical"}}
    tok = G._propose({"thread_id": "tb", "change_plan": plan})["approval_token"]
    rec = approval.peek(tok)
    assert rec["action"] == "update_tickets"
    rows = [{"key": "DL-1", "changes": {"priority": "P1-Critical"}},
            {"key": "DL-2", "changes": {"priority": "P1-Critical"}}]
    assert rec["fp"] == approval.fingerprint({"items": rows})
    # 라우터도 keys 만으로 propose 로 간다
    assert G.route_after_work_architect({"questions": [], "change_plan": plan, "draft": {}}) == "propose"


# ── 답변 깊이는 대화 단위로 잇는다 (실측: 배터리 DATA13) ────────────────────
def test_answer_depth_is_carried_forward_across_a_clarifying_turn():
    """확인 질문에 답한 턴은 **새 질문이 아니다.** 사용자가 보기 하나를 고르면 그 발화는
    값 질문처럼 보이는데, 거기서 brief 로 떨어지면 ResultIntegrator 의 '물어본 것만 답하라'가
    원 요청(히스토리)을 눌러 버린다 — 실측: 티켓 8건 중 2건만 남았다."""
    from app.agent.workflow.agents.request_architect import _carry_depth
    assert _carry_depth({"answer_depth": "explain"}, {"answer_depth": "brief"}) == "explain"
    assert _carry_depth({"answer_depth": "explain"}, {}) == "explain"
    # 올리는 쪽으로만 붙인다 — 새 대화가 설명형이면 그대로 설명형이다
    assert _carry_depth({"answer_depth": "brief"}, {"answer_depth": "explain"}) == "explain"
    # 처음부터 값 질문이면 brief 다(과하게 붙지 않는다)
    assert _carry_depth({}, {"answer_depth": "brief"}) == "brief"
    assert _carry_depth({}, {}) == "brief"


def test_the_original_request_is_pinned_for_lookup_flows_too():
    """원 요청 고정은 **생성 갈래에만** 걸려 있었다 — 조회도 답의 성격은 원 요청이 정한다.
    실측(DATA11): "…데이터의 히스토리" 로 시작해 표기 확인 질문에 답하자, 그 턴의 발화가
    "fdc.… 말한거야" 뿐이라 request_text 가 거기로 폴백되며 '히스토리'가 사라졌고,
    연표 대신 현재 값 표가 나왔다."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.state import Intent

    def _m(t):
        return {"messages": [HumanMessage(content=t)]}
    st = _m("fdc_flat_summary_ic 데이터의 히스토리")
    got = RequestArchitect().apply(st, {"intent": Intent.ASK, "keywords": ["fdc_flat_summary_ic"]})
    assert got.get("request_text") == "fdc_flat_summary_ic 데이터의 히스토리", got
    # 이미 고정돼 있으면 후속 턴이 덮지 않는다
    st2 = {**_m("fdc.fdc_trace_summary_ic 말한거야"),
           "request_text": "fdc_flat_summary_ic 데이터의 히스토리"}
    got2 = RequestArchitect().apply(st2, {"intent": Intent.ASK, "keywords": []})
    assert "request_text" not in got2, got2


def test_my_own_work_request_is_pinned_to_my_day_by_code():
    """"내가 할 만한 일" 은 **내 일감**이지 진척 집계가 아니다.

    실측(REC9): 같은 문장이 실행마다 my_day / progress 로 갈렸다. 두 갈래는 지나는 노드와
    재료가 통째로 달라(내 일감 사전취합 vs 진척률) 갈리는 순간 답의 성격이 바뀐다.
    낱말로 하는 판정은 흔들릴 이유가 없으므로 코드가 확정한다.
    """
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.state import Intent

    def _intent(text, model_said):
        st = {"messages": [HumanMessage(content=text)]}
        return RequestArchitect().apply(st, {"intent": model_said, "keywords": []})["intent"]

    assert _intent("지금 내가 할 만한 일 추천해줘", Intent.PROGRESS) == Intent.MY_DAY
    assert _intent("나 오늘 뭐부터 하지?", Intent.ASK) == Intent.MY_DAY
    # ★ '추천' 하나로는 판정하지 않는다 — 생성 요청을 빼앗으면 안 된다
    assert _intent("내가 만들 티켓 추천해줘", Intent.PLAN_WORK) == Intent.PLAN_WORK
    # 남의 진척을 묻는 것은 그대로 progress
    assert _intent("ETL 모듈 진척 어때?", Intent.PROGRESS) == Intent.PROGRESS


def test_structured_agent_survives_a_server_without_function_calling(monkeypatch):
    """구조화 출력이 안 되는 서버에서도 역할이 죽지 않는다.

    실사용 사고: 자체 LLM 으로 붙이면 "Invalid json output" 이 반복됐다. langchain 의
    `with_structured_output` 은 기본적으로 OpenAI 의 **함수 호출**로 스키마를 강제하는데,
    사내 게이트웨이나 자체 서빙(vLLM·TGI 등)은 그 기능이 없거나 반쪽일 수 있다.

    우리가 원하는 건 '함수 호출'이 아니라 **JSON 한 덩이**다. 한 번 더 묻되 스키마를 말로
    주고 정확한 JSON object를 받는다. code fence나 prefix를 잘라 성공 처리하지 않는다.
    """
    from langchain_core.messages import AIMessage

    from app.agent.workflow.agents.base import StructuredAgent

    class _Broken:                      # with_structured_output 이 죽는 서버 흉내
        def with_structured_output(self, *a, **k):
            class _S:
                def invoke(self, *a, **k):
                    raise ValueError("Invalid json output: 여기 결과입니다 ...")
            return _S()

        def invoke(self, *a, **k):      # prompt JSON 계약은 정확한 객체 한 개로 답한다
            return AIMessage(content='{"picked": "ok"}')

    class _A(StructuredAgent):
        # Runtime test doubles still use a canonical manifest id; aliases fail closed.
        name = "request_architect"

        def system(self, state):
            return "sys"

        def task(self, state):
            return "task"

        def schema(self):
            return {"type": "object", "properties": {"picked": {"type": "string"}}}

        def apply(self, state, out):
            return {"got": out}

    a = _A()
    monkeypatch.setattr(a, "llm", lambda **kw: _Broken())
    r = a.node()({})
    assert r.get("got") == {"picked": "ok"}, r
    assert "error" not in r, "폴백이 있는데도 실패로 떨어졌다"
