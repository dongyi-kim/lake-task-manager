from __future__ import annotations

import hashlib
import json

import pytest

from app.agent.maxis import (
    EditorDraftV1,
    EvidenceSourceV1,
    PortfolioResultV1,
    ResponseGuard,
    ResponseGuardError,
    WorkDraftV1,
)


MANIFEST_SHA = "a" * 64


def _remote(
    *, source_id="src-1", payload=None, text="진척률은 50%다.", payload_claims=None,
    manifest_sha=MANIFEST_SHA, kind="fact",
):
    return {
        "contract": "remote-role-result.v1",
        "role_id": "work_architect",
        "status": "ok",
        "payload": payload or {},
        "claims": [{
            "claim_id": "claim-1",
            "kind": kind,
            "text": text,
            "citation_ids": ["cite-1"],
        }],
        "payload_claims": payload_claims or [],
        "citations": [{"citation_id": "cite-1", "source_id": source_id}],
        "missing_inputs": [],
        "warnings": [],
        "workflow_version": "wf-1",
        "knowledge_version": "kb-1",
        "knowledge_manifest_sha256": manifest_sha,
    }


def test_response_guard_accepts_exact_grounded_json(evidence):
    result = ResponseGuard().validate(
        json.dumps(_remote(), ensure_ascii=False),
        __import__("app.agent.maxis", fromlist=["RemoteRoleResultV1"]).RemoteRoleResultV1,
        evidence,
    )
    assert result.role_id == "work_architect"


def test_response_guard_rejects_wrong_expected_knowledge_manifest(evidence):
    with pytest.raises(ResponseGuardError) as caught:
        ResponseGuard().validate_remote_role(
            json.dumps(_remote(), ensure_ascii=False),
            expected_role_id="work_architect",
            evidence=evidence,
            expected_knowledge_manifest_sha256="b" * 64,
        )
    assert caught.value.code == "knowledge_manifest_mismatch"
    assert caught.value.path == "knowledge_manifest_sha256"


def test_response_guard_accepts_needs_input_without_validating_partial_role_payload(evidence):
    answer = _remote()
    answer.update(
        status="needs_input", payload={}, claims=[], payload_claims=[], citations=[],
        missing_inputs=["완료 기준을 알려 주세요"],
    )
    remote, payload = ResponseGuard().validate_remote_role(
        json.dumps(answer, ensure_ascii=False),
        expected_role_id="work_architect",
        payload_model={
            "type": "object", "additionalProperties": False,
            "properties": {"required_value": {"type": "string"}},
            "required": ["required_value"],
        },
        evidence=evidence,
        expected_knowledge_manifest_sha256=MANIFEST_SHA,
    )
    assert remote.status == "needs_input"
    assert remote.missing_inputs == ("완료 기준을 알려 주세요",)
    assert payload is None


def test_response_guard_rejects_partial_payload_beside_needs_input(evidence):
    answer = _remote()
    answer.update(
        status="needs_input", payload={"partial": "draft"}, claims=[],
        payload_claims=[], citations=[], missing_inputs=["범위를 알려 주세요"],
    )
    with pytest.raises(ResponseGuardError) as caught:
        ResponseGuard().validate_remote_role(
            json.dumps(answer, ensure_ascii=False),
            expected_role_id="work_architect", evidence=evidence,
        )
    assert caught.value.code == "contract_validation_failed"


def test_response_guard_does_not_extract_markdown_code_fence(evidence):
    answer = "```json\n" + json.dumps(_remote(), ensure_ascii=False) + "\n```"
    with pytest.raises(ResponseGuardError) as caught:
        ResponseGuard().validate_remote_role(
            answer, expected_role_id="work_architect", evidence=evidence
        )
    assert caught.value.code == "invalid_json"


def test_response_guard_rejects_duplicate_keys(evidence):
    answer = json.dumps(_remote(), ensure_ascii=False)
    answer = answer.replace(
        '"role_id": "work_architect"',
        '"role_id": "request_architect", "role_id": "work_architect"',
        1,
    )
    with pytest.raises(ResponseGuardError) as caught:
        ResponseGuard().validate_remote_role(
            answer, expected_role_id="work_architect", evidence=evidence
        )
    assert caught.value.code == "invalid_json"


def test_response_guard_rejects_unknown_citation(evidence):
    with pytest.raises(ResponseGuardError) as caught:
        ResponseGuard().validate_remote_role(
            json.dumps(_remote(source_id="src-unknown"), ensure_ascii=False),
            expected_role_id="work_architect",
            evidence=evidence,
        )
    assert caught.value.code == "unknown_source_reference"


def test_response_guard_rejects_unverified_numeric_claim(evidence):
    with pytest.raises(ResponseGuardError) as caught:
        ResponseGuard().validate_remote_role(
            json.dumps(_remote(text="진척률은 77%다."), ensure_ascii=False),
            expected_role_id="work_architect",
            evidence=evidence,
        )
    assert caught.value.code == "unverified_numeric_claim"


def test_response_guard_rejects_value_supported_only_by_a_different_source(evidence, now):
    second_content = "DL-9002의 오류는 1건이다."
    second = EvidenceSourceV1(
        source_id="src-2",
        kind="jira",
        canonical_id="DL-9002",
        title="오류 현황",
        scope="DL",
        url="https://jira.example.local/browse/DL-9002",
        content=second_content,
        retrieved_at=now,
        content_sha256=hashlib.sha256(second_content.encode("utf-8")).hexdigest(),
    )
    two_sources = evidence.model_copy(update={"sources": (*evidence.sources, second)})
    wrong = _remote(source_id="src-2", text="DL-9001 진척률은 50%다.")
    with pytest.raises(ResponseGuardError) as caught:
        ResponseGuard().validate_remote_role(
            json.dumps(wrong, ensure_ascii=False),
            expected_role_id="work_architect",
            evidence=two_sources,
        )
    assert caught.value.code == "citation_assertion_mismatch"


def test_response_guard_requires_claims_for_evidence_backed_payload(evidence):
    answer = _remote(payload={"summary": "배포 완료"})
    answer["claims"] = []
    answer["citations"] = []
    with pytest.raises(ResponseGuardError) as caught:
        ResponseGuard().validate_remote_role(
            json.dumps(answer, ensure_ascii=False),
            expected_role_id="work_architect",
            evidence=evidence,
        )
    assert caught.value.code == "payload_claims_required"


def test_response_guard_rejects_qualitative_fact_unrelated_to_cited_source(evidence):
    answer = _remote(
        payload={"summary": "근거와 무관하게 배포 완료"},
        text="근거와 무관하게 배포 완료",
        payload_claims=[{"path": "/summary", "claim_ids": ["claim-1"]}],
    )
    with pytest.raises(ResponseGuardError) as caught:
        ResponseGuard().validate_remote_role(
            json.dumps(answer, ensure_ascii=False),
            expected_role_id="work_architect",
            evidence=evidence,
        )
    assert caught.value.code == "citation_assertion_mismatch"
    assert caught.value.path == "claims[0].text"


def test_response_guard_requires_new_free_text_fields_to_be_claim_bound(evidence):
    for field in ("goal", "instruction", "completion_criteria", "our_context",
                  "answer_template", "pending_actions", "explanation"):
        with pytest.raises(ResponseGuardError) as caught:
            ResponseGuard().validate_remote_role(
                json.dumps(_remote(payload={field: "진척률은 50%다."}), ensure_ascii=False),
                expected_role_id="work_architect",
                evidence=evidence,
            )
        assert caught.value.code == "payload_claim_coverage_missing"


def test_response_guard_rejects_xss_and_unverified_url(evidence):
    guard = ResponseGuard()
    with pytest.raises(ResponseGuardError) as xss:
        guard.validate(
            json.dumps({
                "contract": "editor-draft.v1",
                "mode": "comment",
                "content_html": '<p><img src=x onerror="alert(1)"></p>',
            }),
            EditorDraftV1,
            evidence,
        )
    assert xss.value.code == "unsafe_event_handler"
    with pytest.raises(ResponseGuardError) as url:
        guard.validate(
            json.dumps({
                "contract": "editor-draft.v1",
                "mode": "comment",
                "content_html": '<p><a href="https://evil.example/x">근거</a></p>',
            }),
            EditorDraftV1,
            evidence,
        )
    assert url.value.code == "unverified_url"


def test_response_guard_returns_typed_payload(evidence):
    payload = {
        "contract": "work-draft.v1",
        "mode": "task",
        "structure": "single_task",
        "items": [{
            "summary": "진척 점검",
            "tier": "task",
            "issue_type": "Task",
            "scope_in": ["현황 확인"],
            "dod": ["검토 완료"],
            "source_ids": ["src-1"],
        }],
    }
    remote, typed = ResponseGuard().validate_remote_role(
        json.dumps(_remote(
            payload=payload,
            text="진척 점검 현황 확인 검토 완료",
            kind="inference",
            payload_claims=[
                {"path": "/items/0/summary", "claim_ids": ["claim-1"]},
                {"path": "/items/0/scope_in/0", "claim_ids": ["claim-1"]},
                {"path": "/items/0/dod/0", "claim_ids": ["claim-1"]},
            ],
        ), ensure_ascii=False),
        expected_role_id="work_architect",
        payload_model=WorkDraftV1,
        evidence=evidence,
    )
    assert remote.status == "ok"
    assert isinstance(typed, WorkDraftV1)
    assert typed.items[0].summary == "진척 점검"


def test_response_guard_binds_editor_visible_html_to_cited_claim(evidence):
    payload = {
        "contract": "editor-draft.v1",
        "mode": "comment",
        "content_html": "<p><strong>진척률은 50%다.</strong></p>",
    }
    _, typed = ResponseGuard().validate_remote_role(
        json.dumps(_remote(
            payload=payload,
            payload_claims=[{
                "path": "/content_html", "claim_ids": ["claim-1"],
            }],
        ), ensure_ascii=False),
        expected_role_id="work_architect",
        payload_model=EditorDraftV1,
        evidence=evidence,
    )
    assert typed.content_html.startswith("<p>")


def test_response_guard_accepts_existing_json_schema_contract(evidence):
    payload = {"summary": "근거 기반 초안", "count": 2}
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "count": {"type": "integer"},
        },
        "required": ["summary", "count"],
    }
    _, typed = ResponseGuard().validate_remote_role(
        json.dumps(_remote(
            payload=payload,
            text="근거 기반 초안",
            kind="inference",
            payload_claims=[{
                "path": "/summary", "claim_ids": ["claim-1"],
            }],
        ), ensure_ascii=False),
        expected_role_id="work_architect",
        payload_model=schema,
        evidence=evidence,
    )
    assert typed == payload


def _portfolio_payload(*, headline="진척률은 50%다.", point="진척률은 50%다."):
    return {
        "contract": "portfolio-result.v1",
        "headline": headline,
        "findings": [{
            "finding_id": "finding-1",
            "kind": "change",
            "point": point,
            "source_ids": ["src-1"],
        }],
    }


def test_response_guard_accepts_typed_payload_with_field_claim_coverage(evidence):
    payload = _portfolio_payload()
    _, typed = ResponseGuard().validate_remote_role(
        json.dumps(_remote(
            payload=payload,
            payload_claims=[
                {"path": "/headline", "claim_ids": ["claim-1"]},
                {"path": "/findings/0/point", "claim_ids": ["claim-1"]},
            ],
        ), ensure_ascii=False),
        expected_role_id="work_architect",
        payload_model=PortfolioResultV1,
        evidence=evidence,
    )
    assert typed.headline == "진척률은 50%다."


@pytest.mark.parametrize(
    ("headline", "point", "bad_path"),
    [
        ("근거와 무관하게 배포 완료", "진척률은 50%다.", "/headline"),
        ("진척률은 50%다.", "근거와 무관하게 장애 위험 없음", "/findings/0/point"),
    ],
)
def test_response_guard_rejects_unrelated_qualitative_payload_assertion(
    evidence, headline, point, bad_path,
):
    answer = _remote(
        payload=_portfolio_payload(headline=headline, point=point),
        payload_claims=[
            {"path": "/headline", "claim_ids": ["claim-1"]},
            {"path": "/findings/0/point", "claim_ids": ["claim-1"]},
        ],
    )
    with pytest.raises(ResponseGuardError) as caught:
        ResponseGuard().validate_remote_role(
            json.dumps(answer, ensure_ascii=False),
            expected_role_id="work_architect",
            payload_model=PortfolioResultV1,
            evidence=evidence,
        )
    assert caught.value.code == "payload_claim_text_mismatch"
    assert caught.value.path == bad_path


def test_response_guard_rejects_missing_payload_claim_coverage(evidence):
    with pytest.raises(ResponseGuardError) as caught:
        ResponseGuard().validate_remote_role(
            json.dumps(_remote(payload=_portfolio_payload()), ensure_ascii=False),
            expected_role_id="work_architect",
            payload_model=PortfolioResultV1,
            evidence=evidence,
        )
    assert caught.value.code == "payload_claim_coverage_missing"


def test_response_guard_rejects_duplicate_claim_identity(evidence):
    answer = _remote()
    answer["claims"].append({**answer["claims"][0], "text": "다른 주장"})
    with pytest.raises(ResponseGuardError) as caught:
        ResponseGuard().validate_remote_role(
            json.dumps(answer, ensure_ascii=False),
            expected_role_id="work_architect",
            evidence=evidence,
        )
    assert caught.value.code == "contract_validation_failed"


def test_response_guard_rejects_remote_authority_and_identity_fields(evidence):
    guard = ResponseGuard()
    with pytest.raises(ResponseGuardError) as authority:
        guard.validate_remote_role(
            json.dumps(_remote(payload={"approval_token": "forged"}), ensure_ascii=False),
            expected_role_id="work_architect",
            evidence=evidence,
        )
    assert authority.value.code == "forbidden_authority_field"
    with pytest.raises(ResponseGuardError) as identity:
        guard.validate_remote_role(
            json.dumps(_remote(payload={"assignee": "skcc.x9999"}), ensure_ascii=False),
            expected_role_id="work_architect",
            evidence=evidence,
        )
    assert identity.value.code == "sensitive_or_identity_output"
    with pytest.raises(ResponseGuardError) as group_identity:
        guard.validate_remote_role(
            json.dumps(_remote(payload={"assignee": "skhy.x1234"}), ensure_ascii=False),
            expected_role_id="work_architect",
            evidence=evidence,
        )
    assert group_identity.value.code == "sensitive_or_identity_output"


@pytest.mark.parametrize("field", ["db_password", "authToken", "private_key_data"])
def test_response_guard_rejects_disguised_remote_authority_fields(evidence, field):
    with pytest.raises(ResponseGuardError) as caught:
        ResponseGuard().validate_remote_role(
            json.dumps(_remote(payload={field: "opaque-value"}), ensure_ascii=False),
            expected_role_id="work_architect",
            evidence=evidence,
        )
    assert caught.value.code == "forbidden_authority_field"
