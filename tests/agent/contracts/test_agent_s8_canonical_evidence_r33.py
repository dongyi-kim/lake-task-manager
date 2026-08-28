from __future__ import annotations

from copy import deepcopy
import re


def _materialized_state() -> dict:
    tickets = [{
        "key": "ACME-501",
        "summary": "component:writer rollout root",
        "status": "In Progress",
        "done": False,
        "updated": "2026-08-11T09:00:00Z",
        "description": "component:writer rollout requires a staged decision",
        "comments": [],
    }, {
        "key": "ACME-502",
        "summary": "component:reader validation child",
        "status": "In Progress",
        "done": False,
        "epicKey": "ACME-501",
        "updated": "2026-08-12T09:00:00Z",
        "description": "component:reader validation remains open",
        "comments": [],
    }, {
        "key": "ACME-503",
        "summary": "component:writer rollout child",
        "status": "Resolved",
        "done": True,
        "epicKey": "ACME-501",
        "updated": "2026-08-15T09:00:00Z",
        "description": "component:writer rollout execution",
        "comments": [{
            "created": "2026-08-15T08:00:00Z",
            "body": "component:writer rollout completed",
        }],
    }]
    documents = [{
        "title": "Prior rollout note",
        "url": "https://docs.example.test/rollout/prior",
        "updated": "2026-08-01T09:00:00Z",
        "text": "component:writer rollout was not performed",
    }, {
        "title": "Decision record",
        "url": "https://docs.example.test/rollout/decision",
        "updated": "2026-08-14T09:00:00Z",
        "text": "rollout and validation evidence must remain separate",
    }]
    materialized = {
        "projectedTicketDetails": deepcopy(tickets),
        "projectedDocumentBodies": deepcopy(documents),
        "ticketKeys": [row["key"] for row in tickets],
        "entityCoverage": {
            "mode": "bounded_one_hop",
            "rootKeys": ["ACME-501"],
            "selectedKeys": ["ACME-502", "ACME-503"],
            "materializedKeys": ["ACME-502", "ACME-503"],
            "complete": False,
            "truncated": False,
        },
    }
    return {
        "query_plan": {"queries": [
            {"id": "internal", "source": "jira"},
            {"id": "documents", "source": "confluence"},
        ]},
        "query_results": [{
            "id": "internal",
            "source": "jira",
            "result": {
                "ticketDetails": deepcopy(tickets),
                "entityCoverage": deepcopy(materialized["entityCoverage"]),
                "complete": True,
            },
        }, {
            "id": "documents",
            "source": "confluence",
            "result": {"documentBodies": deepcopy(documents), "complete": True},
        }],
        "query_artifacts": {"evidence-materialization": materialized},
        "materialized_ticket_sources": {"ticketDetails": deepcopy(tickets)},
        "evidence": [{
            "key": "ACME-501",
            "title": "component:writer rollout root",
            "observations": [{
                "source": "description",
                "text": "component:writer rollout requires a staged decision",
                "observed_at": "2026-08-11T09:00:00Z",
            }],
        }, {
            "key": "ACME-503",
            "title": "component:writer rollout child",
            "observations": [{
                "source": "comment",
                "text": "component:writer rollout completed",
                "observed_at": "2026-08-15T08:00:00Z",
            }],
        }, {
            "key": "Prior rollout note",
            "title": "Prior rollout note",
            "url": "https://docs.example.test/rollout/prior",
            "observations": [{
                "source": "document",
                "text": "component:writer rollout was not performed",
                "observed_at": "2026-08-01T09:00:00Z",
            }],
        }],
        "related_docs": [{
            "title": row["title"], "url": row["url"],
        } for row in documents[:1]],
    }


def _source_number(rendered: str, source_literal: str) -> str:
    match = re.search(
        rf"(?m)^\[(\d+)\]\s+.*{re.escape(source_literal)}.*$",
        rendered.split("### 근거", 1)[1],
    )
    assert match, rendered
    return match.group(1)


def test_common_exact_date_normalizer_does_not_rewrite_source_title_identity():
    from app.agent.workflow.evidence_index import normalize_evidence_summaries

    evidence = [{
        "key": "ACME-490",
        "title": "2026-08-11부터 2026-08-25까지 1주 검토",
        "why": "2026-08-11부터 2026-08-25까지 1주 검토",
        "observations": [{
            "source": "comment",
            "text": "2026-08-11부터 2026-08-25까지 1주 검토",
        }],
    }]
    before = deepcopy(evidence)

    projected = normalize_evidence_summaries(evidence)

    assert projected[0]["title"] == evidence[0]["title"]
    assert "정확히 14일" in projected[0]["why"]
    assert "정확히 14일" in projected[0]["observations"][0]["text"]
    assert evidence == before


def test_canonical_evaluation_projection_covers_materialized_entities_and_documents_once():
    from app.agent.workflow.agents.result_integrator import canonical_evaluation_evidence

    state = _materialized_state()
    state["evidence"][0]["title"] = "2026-08-11부터 2026-08-25까지 1주 검토"
    before = deepcopy(state)

    projected = canonical_evaluation_evidence(state)

    source_ids = [row["_source_id"] for row in projected]
    assert len(source_ids) == len(set(source_ids)) == 5
    assert set(source_ids) == {
        "ticket:ACME-501",
        "ticket:ACME-502",
        "ticket:ACME-503",
        "url:https://docs.example.test/rollout/prior",
        "url:https://docs.example.test/rollout/decision",
    }
    by_source = {row["_source_id"]: row for row in projected}
    assert by_source["ticket:ACME-501"]["title"] == "component:writer rollout root"
    assert by_source["ticket:ACME-502"]["_selection"] == "inspected_not_selected"
    assert by_source["url:https://docs.example.test/rollout/decision"]["_selection"] \
        == "inspected_not_selected"
    prior = by_source["url:https://docs.example.test/rollout/prior"]
    assert prior["_authority"] == "materialized_document_sources"
    assert prior["observations"][0]["direct"] is True
    assert prior["observations"][0]["observed_at"] == "2026-08-01T09:00:00Z"
    assert state == before


def test_materialized_source_manifest_renders_one_canonical_index_without_side_list():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = _materialized_state()
    rendered = _merge_evidence_index(
        "The staged decision remains in force [1].",
        state,
    )

    assert rendered.count("### 근거") == 1
    assert "### 조회된 내부 출처 범위" not in rendered
    source_tail = rendered.split("### 근거", 1)[1]
    for key in ("ACME-501", "ACME-502", "ACME-503"):
        assert source_tail.count(f"{{{{ticket-detail:{key}}}}}") == 1
    for url in (
        "https://docs.example.test/rollout/prior",
        "https://docs.example.test/rollout/decision",
    ):
        assert source_tail.count(url) == 1
    assert len(re.findall(r"(?m)^\[\d+\]\s+", source_tail)) == 5


def test_workflow_done_does_not_rebind_completion_but_dates_sources():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = _materialized_state()
    rendered = _merge_evidence_index(
        "{{ticket-inline:ACME-503}} component:writer rollout completed [1][2].",
        state,
    )
    body, source_tail = rendered.split("### 근거", 1)

    assert "rollout completed [1][2]" in body
    assert "직접 완료 근거 확인 필요" in body
    assert "2026-08-01T09:00:00Z 기준" in source_tail
    assert "2026-08-15T08:00:00Z 기준" in source_tail


def test_compound_completion_claim_is_not_supplemented_from_workflow_state():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = _materialized_state()
    rendered = _merge_evidence_index(
        "{{ticket-inline:ACME-503}} component:writer rollout completed, while "
        "{{ticket-inline:ACME-502}} remains open. [1][3]",
        state,
    )
    root = _source_number(rendered, "ticket-detail:ACME-501")
    prior = _source_number(rendered, "https://docs.example.test/rollout/prior")
    first_line = rendered.splitlines()[0]

    assert {root, prior}.issubset(set(re.findall(r"\[(\d+)\]", first_line)))
    assert "직접 완료 근거 확인 필요" in first_line


def test_incomplete_or_ambiguous_typed_targets_do_not_gain_completion_authority():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = _materialized_state()
    not_done = _merge_evidence_index(
        "{{ticket-inline:ACME-502}} component:reader validation completed [1].",
        state,
    )
    compound = _merge_evidence_index(
        "{{ticket-inline:ACME-502}}와 {{ticket-inline:ACME-503}}가 completed [1].",
        state,
    )

    assert "직접 완료 근거 확인 필요" in not_done
    assert "직접 완료 근거 확인 필요" in compound


def test_done_workflow_state_does_not_prove_a_technical_success_claim():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    failed_text = "technical validation failed; no successful result"
    ticket = {
        "key": "ACME-509", "summary": "technical validation",
        "status": "Closed", "done": True,
        "updated": "2026-08-16T09:00:00Z",
        "description": failed_text, "comments": [],
    }
    state = {
        "query_results": [{
            "id": "tickets", "source": "jira",
            "result": {"ticketDetails": [deepcopy(ticket)], "complete": True},
        }],
        "query_artifacts": {"evidence-materialization": {
            "projectedTicketDetails": [deepcopy(ticket)],
            "projectedDocumentBodies": [],
        }},
        "materialized_ticket_sources": {"ticketDetails": [deepcopy(ticket)]},
        "evidence": [{
            "key": "ACME-509", "title": "technical validation",
            "observations": [{"source": "description", "text": failed_text}],
        }],
        "related_docs": [],
    }

    rendered = _merge_evidence_index(
        "{{ticket-inline:ACME-509}} technical validation succeeded [1].", state,
    )

    assert "직접 완료 근거 확인 필요" in rendered.split("### 근거", 1)[0]


def test_legacy_model_observation_timestamp_is_not_display_authority():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    rendered = _merge_evidence_index(
        "A bounded finding is recorded [1].",
        {"evidence": [{
            "key": "ACME-510", "title": "bounded finding",
            "observations": [{
                "source": "comment", "text": "bounded finding is recorded",
                "observed_at": "2099-12-31T23:59:59Z",
            }],
        }]},
    )

    assert "2099-12-31" not in rendered


def test_empty_materialization_keys_do_not_switch_the_existing_selection_path():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = _materialized_state()
    state["query_artifacts"] = {"evidence-materialization": {
        "projectedTicketDetails": [], "projectedDocumentBodies": [],
    }}

    rendered = _merge_evidence_index("The staged decision remains in force [1].", state)
    source_tail = rendered.split("### 근거", 1)[1]

    assert "### 조회된 내부 출처 범위" in rendered
    assert "{{ticket-inline:ACME-502}}" in rendered.split("### 근거", 1)[0]
    assert "{{ticket-detail:ACME-502}}" not in source_tail
    assert "https://docs.example.test/rollout/decision" not in source_tail


def test_inspected_ticket_and_document_sources_share_one_bounded_ledger():
    from app.agent.workflow.agents.result_integrator import (
        _merge_evidence_index,
        canonical_evaluation_evidence,
    )

    state = _materialized_state()
    extra = [{
        "key": f"ACME-{600 + index}",
        "summary": f"candidate {index}",
        "status": "Open", "done": False,
        "updated": f"2026-08-{index + 1:02d}T09:00:00Z",
    } for index in range(10)]
    jira = state["query_results"][0]["result"]["ticketDetails"]
    jira.extend(deepcopy(extra))
    state["materialized_ticket_sources"]["ticketDetails"].extend(deepcopy(extra))
    artifact = state["query_artifacts"]["evidence-materialization"]
    artifact["projectedTicketDetails"].extend(deepcopy(extra))

    projected = canonical_evaluation_evidence(state)
    inspected = [row for row in projected
                 if row["_selection"] == "inspected_not_selected"]
    rendered = _merge_evidence_index("The staged decision remains in force [1].", state)

    assert len(inspected) == 8
    coverage = [row["_coverage"] for row in inspected if row.get("_coverage")]
    assert coverage == [{
        "kind": "inspected_source_ledger", "limit": 8, "remainingCount": 4,
    }]
    assert "조회된 추가 출처 4건은 bounded 검토 ledger 상한으로 인덱스에서 생략" in rendered
    assert rendered.count("### 근거") == 1


def test_same_document_url_with_conflicting_canonical_payload_fails_closed():
    from app.agent.workflow.agents.result_integrator import (
        _merge_evidence_index,
        canonical_evaluation_evidence,
    )

    url = "https://docs.example.test/decision/conflict"
    documents = [{
        "title": "Decision log", "url": url,
        "updated": "2026-08-10T09:00:00Z", "text": "payload alpha",
    }, {
        "title": "Decision log", "url": url,
        "updated": "2026-08-10T09:00:00Z", "text": "payload beta",
    }]
    state = {
        "query_results": [{
            "id": "documents", "source": "confluence",
            "result": {"documentBodies": deepcopy(documents), "complete": True},
        }],
        "query_artifacts": {"evidence-materialization": {
            "projectedDocumentBodies": deepcopy(documents),
        }},
        "evidence": [{
            "key": "Decision log", "title": "Decision log", "url": url,
            "observations": [{"source": "document", "text": "payload alpha"}],
        }],
        "related_docs": [{"title": "Decision log", "url": url}],
    }

    projected = canonical_evaluation_evidence(state)
    rendered = _merge_evidence_index("Decision is under review [1].", state)

    assert len(projected) == 1
    assert projected[0]["_authority"] == "materialized_document_conflict"
    assert projected[0]["observations"] == []
    assert rendered.count(url) == 1
    assert "payload alpha" not in rendered and "payload beta" not in rendered
    assert "canonical payload가 상충해 본문 근거를 제외함" in rendered


def test_raw_document_body_does_not_conflict_with_its_bounded_projection():
    from app.agent.workflow.agents.result_integrator import (
        canonical_evaluation_evidence,
    )

    url = "https://docs.example.test/decision/projected"
    projected_text = "bounded canonical projection"
    raw_text = projected_text + " with a longer raw suffix that is not role-visible"
    state = {
        "query_results": [{
            "id": "documents", "source": "confluence",
            "result": {"documentBodies": [{
                "title": "Decision record", "url": url,
                "updated": "2026-08-10T09:00:00Z", "text": projected_text,
            }], "complete": True},
        }],
        "query_artifacts": {"evidence-materialization": {
            "projectedDocumentBodies": [{
                "title": "Decision record", "url": url,
                "updated": "2026-08-10T09:00:00Z", "text": projected_text,
            }],
            "documentBodies": [{
                "title": "Decision record", "url": url,
                "updated": "2026-08-10T09:00:00Z", "text": raw_text,
            }],
        }},
        "evidence": [],
        "related_docs": [{"title": "Decision record", "url": url}],
    }

    projected = canonical_evaluation_evidence(state)

    assert len(projected) == 1
    assert projected[0]["_authority"] == "materialized_document_sources"
    assert [row["text"] for row in projected[0]["observations"]] == [projected_text]


def test_typed_completion_subject_never_silently_swaps_between_done_tickets():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = _materialized_state()
    other = {
        "key": "ACME-504", "summary": "component:writer rollout mirror",
        "status": "Resolved", "done": True,
        "updated": "2026-08-16T09:00:00Z",
        "description": "component:writer rollout mirror completed",
        "comments": [],
    }
    state["query_results"][0]["result"]["ticketDetails"].append(deepcopy(other))
    state["query_artifacts"]["evidence-materialization"][
        "projectedTicketDetails"
    ].append(deepcopy(other))
    state["materialized_ticket_sources"]["ticketDetails"].append(deepcopy(other))
    state["evidence"].append({
        "key": "ACME-504", "title": other["summary"],
        "observations": [{
            "source": "description", "text": other["description"],
            "observed_at": other["updated"],
        }],
    })

    for claim in (
        "{{ticket-inline:ACME-503}} component:writer rollout completed [1][4].",
        "{{ticket-inline:ACME-503}} component:writer rollout을 완료했습니다 [1][4].",
    ):
        rendered = _merge_evidence_index(claim, state)
        body = rendered.split("### 근거", 1)[0]
        assert "직접 완료 근거 확인 필요" in body


def test_workflow_state_does_not_prove_other_positive_outcome_predicates():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = _materialized_state()
    for claim in (
        "{{ticket-detail:ACME-503}} 운영에 배포되었고 검증을 통과했다 [1].",
        "{{ticket-detail:ACME-503}} 운영에 배포했습니다 [1].",
        "{{ticket-detail:ACME-503}} 운영 환경에 반영함 [1].",
        "{{ticket-detail:ACME-503}} 새 정책을 적용했다 [1].",
        "{{ticket-detail:ACME-503}} 장애가 해결됐다 [1].",
        "{{ticket-detail:ACME-503}} was deployed and passed validation [1].",
    ):
        body = _merge_evidence_index(claim, state).split("### 근거", 1)[0]
        assert "직접 완료 근거 확인 필요" in body


def test_planned_or_negated_outcome_is_not_misclassified_as_completion():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = _materialized_state()
    for claim in (
        "{{ticket-detail:ACME-503}} 운영 배포 예정이며 검증을 통과하지 못했다 [1].",
        "{{ticket-detail:ACME-503}} is not yet deployed and remains planned [1].",
    ):
        body = _merge_evidence_index(claim, state).split("### 근거", 1)[0]
        assert "직접 완료 근거 확인 필요" not in body


def test_confluence_search_hit_with_failed_body_open_is_not_evidence_coverage():
    from app.agent.workflow.agents.result_integrator import (
        _ensure_requested_source_coverage,
        canonical_evaluation_evidence,
    )
    from app.agent.workflow.source_coverage import _requested_source_coverage

    state = {
        "intent": "ask",
        "request_text": "Confluence wiki 원문을 조사해줘",
        "query_plan": {"queries": [{"id": "wiki", "source": "confluence"}]},
        "query_results": [{"id": "wiki", "source": "confluence", "result": {
            "documents": [{
                "id": "guide-1", "title": "Operations guide",
                "url": "https://docs.test/guide-1",
            }],
            "documentBodies": [{
                "id": "guide-1", "title": "Operations guide",
                "url": "https://docs.test/guide-1", "error": "403 permission denied",
            }],
            "materializationErrors": ["403 permission denied"],
            "complete": True,
        }}],
        "query_artifacts": {"evidence-materialization": {
            "projectedDocumentBodies": [{
                "id": "guide-1", "title": "Operations guide",
                "url": "https://docs.test/guide-1", "error": "403 permission denied",
            }],
        }},
        "evidence": [],
        "related_docs": [],
    }

    row = _requested_source_coverage(state)[0]

    assert row["status"] == "provider_error"
    assert row["result_hits"] == 1
    assert row["materialized_hits"] == 0
    assert row["materialization_complete"] is False
    assert row["materialization_failed_identities"] == ["guide-1"]
    assert row["usable_as_evidence"] is False
    assert canonical_evaluation_evidence(state) == []
    rendered = _ensure_requested_source_coverage("조회 결과", state)
    assert "실물 열기 실패: guide-1" in rendered
    assert "결론 근거에 사용하지 않음" in rendered


def test_jira_search_hit_with_failed_detail_open_is_not_evidence_coverage():
    from app.agent.workflow.source_coverage import _requested_source_coverage

    state = {
        "intent": "ask",
        "request_text": "Jira 티켓 원본을 조사해줘",
        "query_plan": {"queries": [{"id": "jira", "source": "jira"}]},
        "query_results": [{"id": "jira", "source": "jira", "result": {
            "tickets": [{"key": "ACME-91", "summary": "Candidate"}],
            "ticketDetails": [{"key": "ACME-91", "error": "403 permission denied"}],
            "materializationErrors": ["403 permission denied"],
            "complete": True,
        }}],
    }

    row = _requested_source_coverage(state)[0]

    assert row["status"] == "provider_error"
    assert row["result_hits"] == 1
    assert row["materialized_hits"] == 0
    assert row["materialization_complete"] is False
    assert row["materialization_failed_identities"] == ["ACME-91"]
    assert row["usable_as_evidence"] is False
