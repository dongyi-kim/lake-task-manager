from __future__ import annotations

from copy import deepcopy


def test_model_evidence_date_summary_is_normalized_without_mutating_input():
    from app.agent.workflow.evidence_index import canonicalize_evidence_index

    evidence = [{
        "key": "ACME-301",
        "title": "Atlas rollout decision",
        "observations": [{
            "source": "comment",
            "text": "기간은 2026-08-01부터 2026-08-20까지 1주 기간입니다.",
        }],
    }]
    before = deepcopy(evidence)

    rendered = canonicalize_evidence_index(
        "결정 메모 [1].", evidence=evidence,
    )

    assert "정확히 19일" in rendered and "1주" in rendered and "불일치" in rendered
    assert evidence == before


def test_canonical_document_observation_is_not_rewritten_as_a_model_summary():
    from app.agent.workflow.evidence_index import canonicalize_evidence_index

    raw_observation = "기간은 2026-08-01부터 2026-08-20까지 1주 기간입니다."
    related_docs = [{
        "title": "Atlas decision log",
        "url": "https://docs.example.test/atlas/decision",
        "text": raw_observation,
    }]
    before = deepcopy(related_docs)

    rendered = canonicalize_evidence_index(
        "Atlas decision log의 결정 원문 [Atlas decision log].",
        related_docs=related_docs,
    )

    assert raw_observation in rendered
    assert "정확히 19일" not in rendered
    assert related_docs == before


def test_materialized_internal_source_identity_is_disclosed_when_not_selected():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    details = [{
        "key": "ACME-311", "summary": "Atlas current decision",
        "description": "운영 반영은 검증 뒤 결정", "status": "Open",
    }, {
        "key": "ACME-312", "summary": "Atlas prior experiment",
        "description": "이전 실험 결과와 한계", "status": "Closed",
    }]
    state = {
        "query_plan": {"queries": [{"id": "internal", "source": "jira"}]},
        "query_results": [{
            "id": "internal", "source": "jira",
            "result": {"ticketDetails": details, "complete": True},
        }],
        "materialized_ticket_sources": {"ticketDetails": details},
        "evidence": [{
            "key": "ACME-311", "title": "Atlas current decision",
            "observations": [{"source": "description", "text": "운영 반영은 검증 뒤 결정"}],
        }],
        "related_docs": [],
    }
    before = deepcopy(state)

    rendered = _merge_evidence_index("현재 결정 [1].", state)

    body, source_tail = rendered.split("### 근거", 1)
    assert "### 조회된 내부 출처 범위" in body
    assert "ACME-312" in body and "최종 결론 근거로 선택하지 않음" in body
    assert "{{ticket-detail:ACME-311}}" in source_tail
    assert "{{ticket-detail:ACME-312}}" not in source_tail
    assert state == before


def test_query_only_ticket_projection_is_disclosed_instead_of_silently_selected():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    detail = {
        "key": "ACME-313", "summary": "Atlas inspected candidate",
        "description": "검토 결과는 원천에 보존", "status": "Open",
    }
    state = {
        "query_plan": {"queries": [{"id": "internal", "source": "jira"}]},
        "query_results": [{
            "id": "internal", "source": "jira",
            "result": {"ticketDetails": [detail], "complete": True},
        }],
        "evidence": [{
            "key": "ACME-313", "title": "Atlas inspected candidate",
            "why": "QueryPlan canonicalJql 실행 기록",
            "observations": [{
                "source": "query", "text": "canonicalJql = key = ACME-313",
            }],
        }],
        "related_docs": [],
    }

    rendered = _merge_evidence_index("후보 원천을 조회했습니다.", state)

    assert "### 조회된 내부 출처 범위" in rendered
    assert "{{ticket-inline:ACME-313}}" in rendered
    assert "최종 결론 근거로 선택하지 않음" in rendered
    assert "{{ticket-detail:ACME-313}}" not in rendered


def test_canonical_evaluation_evidence_is_pure_and_never_promotes_rewritten_prose():
    from app.agent.workflow.agents.result_integrator import canonical_evaluation_evidence

    inconsistent = "기간은 2026-08-01부터 2026-08-20까지 1주 기간입니다."
    exact_comment = "검토 창구는 Atlas 운영위원회입니다."
    details = [{
        "key": "ACME-314", "summary": "Atlas selected decision",
        "description": inconsistent, "status": "Open", "updated": "2026-08-18",
        "comments": [{"body": exact_comment, "created": "2026-08-17"}],
    }, {
        "key": "ACME-315", "summary": "Atlas inspected alternative",
        "description": "대안의 제약 조건", "status": "Closed",
    }]
    state = {
        "query_plan": {"queries": [{"id": "internal", "source": "jira"}]},
        "query_results": [{
            "id": "internal", "source": "jira",
            # The current compact result omits comments; the durable materialization below
            # remains the canonical authority for that exact source cell.
            "result": {"ticketDetails": [
                {key: value for key, value in row.items() if key != "comments"}
                for row in details
            ], "complete": True},
        }],
        "materialized_ticket_sources": {"ticketDetails": details},
        "evidence": [{
            "key": "ACME-314", "title": "Atlas selected decision",
            "observations": [
                {"source": "description", "text": inconsistent, "direct": True},
                {"source": "comment", "text": exact_comment, "direct": False},
            ],
        }],
        "related_docs": [],
    }
    before = deepcopy(state)

    projected = canonical_evaluation_evidence(state)

    assert state == before
    by_source = {row["_source_id"]: row for row in projected}
    selected = by_source["ticket:ACME-314"]
    assert selected["_selection"] == "selected"
    assert selected["_authority"] == "materialized_ticket_sources"
    rewritten, exact = selected["observations"]
    assert "정확히 19일" in rewritten["text"] and "불일치" in rewritten["text"]
    assert rewritten["authority"] == "research_projection"
    assert rewritten["direct"] is False
    assert exact["text"] == exact_comment
    assert exact["authority"] == "materialized_match"
    assert exact["direct"] is True
    omitted = by_source["ticket:ACME-315"]
    assert omitted["_selection"] == "inspected_not_selected"
    assert omitted["_authority"] == "materialized_ticket_sources"
    assert omitted["observations"] == []


def test_atomic_claim_rebinds_an_unrelated_source_cluster_to_exact_typed_source():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = {
        "materialized_ticket_sources": {"ticketDetails": [{
            "key": "ACME-321", "status": "Ready", "updated": "2026-08-18",
        }]},
        "evidence": [{
            "key": "ACME-399", "observations": [{
                "source": "description", "text": "Unrelated migration context",
            }],
        }, {
            "key": "ACME-321", "observations": [{
                "source": "field", "text": "status Ready",
                "subject_id": "ACME-321", "predicate": "status", "value": "Ready",
            }],
        }],
        "related_docs": [],
    }

    rendered = _merge_evidence_index(
        "ACME-321의 status는 Ready입니다 [1][2].", state,
    )
    body = rendered.split("### 근거", 1)[0]

    assert "Ready입니다 [2]." in body
    assert "Ready입니다 [1][2]." not in body
    assert "직접 근거 확인 필요" not in body


def test_atomic_claim_fails_closed_when_exact_typed_source_is_not_rendered():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = {
        "materialized_ticket_sources": {"ticketDetails": [{
            "key": "ACME-331", "status": "Ready", "updated": "2026-08-18",
        }]},
        "evidence": [{
            "key": "ACME-399", "observations": [{
                "source": "description", "text": "Unrelated migration context",
            }],
        }],
        "related_docs": [],
    }

    rendered = _merge_evidence_index(
        "ACME-331의 status는 Ready입니다 [1].", state,
    )
    body = rendered.split("### 근거", 1)[0]

    assert "Ready입니다 (직접 근거 확인 필요)." in body
    assert "Ready입니다 [1]." not in body


def test_generic_same_subject_predicate_history_is_reconciled_in_decision_memo():
    from app.agent.workflow.evidence_index import (
        build_atomic_fact_ledger,
        enforce_atomic_fact_boundaries,
    )

    facts = build_atomic_fact_ledger({}, extra_facts=[{
        "subject_id": "component:atlas-writer", "predicate": "rollout_state",
        "value": "planned", "observed_at": "2026-08-01T09:00:00Z",
        "source_id": "decision:old", "provenance": "decision[old]",
        "direct": True, "authority": "decision_ledger",
    }, {
        "subject_id": "component:atlas-writer", "predicate": "rollout_state",
        "value": "completed", "observed_at": "2026-08-10T09:00:00Z",
        "source_id": "decision:new", "provenance": "decision[new]",
        "direct": True, "authority": "decision_ledger",
    }])

    rendered = enforce_atomic_fact_boundaries(
        "### 결정 메모\n\ncomponent:atlas-writer rollout_state는 planned입니다.",
        facts,
    )

    assert "이전 기록(2026-08-01T09:00:00Z) [과거·보조 근거]" in rendered
    assert "현재 기록(2026-08-10T09:00:00Z)" in rendered
    assert "`component:atlas-writer` — completed" in rendered
