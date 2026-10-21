"""Auditor native transport keeps negative judgments and both final guards."""
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from app.agent.maxis import ContextGuard, ContextPolicy, ResponseGuard, ResponseGuardError
from app.agent.workflow.agents.auditor import SCHEMA
from test_core_request_graph import code

ROOT = Path(__file__).resolve().parents[3]
LLM, ASSEMBLER, INPUT = "1909000000001044", "1909000000001045", "1909000000001046"
VERSION = "11111111-1111-4111-8111-111111111111"
PROOF = "LTM_RELEASE_SENTINEL_V1=" + "a" * 64


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@pytest.fixture(scope="module")
def nodes():
    dsl = yaml.safe_load((ROOT / "deploy/maxis-dify/dsl/ltm-agent-core.yml").read_text(encoding="utf-8"))
    return {row["id"]: row["data"] for row in dsl["workflow"]["graph"]["nodes"]}


@pytest.fixture
def audit_case(now):
    guard = ContextGuard(ContextPolicy(allowed_scopes={}, allowed_context_keys=frozenset({"draft", "structure_ok"})),
                         b"test-pseudonym-key-32-bytes-long")
    pack = guard.build(request_id="audit-native", use_case="auditor", query="검토할 초안",
        payload_schema=SCHEMA, knowledge_manifest_sha256="a" * 64, user_identity="synthetic", requested_at=now,
        sources=[{"source_id": "intent-a", "kind": "ltm", "canonical_id": "ltm:user-intent:fixture",
                  "title": "User intent", "content": "검증 작업 초안 요청", "retrieved_at": now}],
        role_context=[{"context_id": "draft-a", "name": "draft", "value": {"items": [{"summary": "검증 작업"}]}, "observed_at": now},
                      {"context_id": "structure-a", "name": "structure_ok", "value": False, "observed_at": now}])
    incoming = encoded({"contract": "ltm-model-input.v1", "context_pack": pack.model_dump(mode="json"),
        "role_id": "auditor", "correlation_id": pack.request_id, "output_contract": "remote-role-result.v1",
        "workflow_version": VERSION, "knowledge_version": "kb-test", "knowledge_proof": PROOF, "knowledge_context": []})
    draft = {"contract": "audit-draft.v1", "status": "ok", "payload": {
        "grounded": "pass", "rule_compliant": "fail", "answers_request": "fail", "problems": [],
        "summary": {"value": "요청과 초안의 검토가 필요합니다", "kind": "inference", "source_ids": ["intent-a"]}}, "missing_inputs": []}
    return pack, incoming, draft


def assemble(nodes, incoming, draft):
    return json.loads(code(nodes[ASSEMBLER])["main"](incoming, encoded(draft))["candidate"])


def test_auditor_uses_one_native_model_and_original_input_binding(nodes, audit_case):
    _, incoming, _ = audit_case
    assert code(nodes["1909000000001024"])["main"](incoming) == {"route": "audit"}
    assert nodes[LLM]["model"] == nodes["1909000000001004"]["model"]
    assert nodes[LLM]["memory"] is None
    assert nodes[ASSEMBLER]["variables"] == nodes[INPUT]["variables"] + [{"variable": "candidate", "value_selector": [LLM, "text"]}]
    assert [ASSEMBLER, "candidate"] in nodes["1909000000001027"]["variables"]


def test_audit_view_keeps_draft_and_sources_not_envelope_metadata(nodes, audit_case):
    pack, incoming, _ = audit_case
    view = json.loads(code(nodes[INPUT])["main"](incoming)["audit_input"])
    assert view["contract"] == "audit-input.v1"
    assert view["context"]["role_context"] == pack.model_dump(mode="json")["role_context"]
    assert view["context"]["sources"][0]["source_id"] == "intent-a"
    assert view["context"]["sources"][0]["content"] == pack.sources[0].content
    assert view["context"]["sources"][0]["allowed_claim_kinds"] == ["inference", "recommendation"]
    assert not {"payload_schema_json", "payload_schema_sha256", "knowledge_manifest_sha256"} & view["context"].keys()
    assert VERSION not in encoded(view) and PROOF not in encoded(view)


def test_completed_negative_audit_is_not_transport_failure_or_approval(nodes, audit_case):
    pack, incoming, draft = audit_case
    result = assemble(nodes, incoming, draft)
    assert result["status"] == "ok"
    assert result["payload"]["grounded"] is True
    assert result["payload"]["rule_compliant"] is False
    assert result["payload"]["answers_request"] is False
    remote = json.loads(code(nodes["1909000000001005"])["main"](encoded(result), encoded(pack.model_dump(mode="json")),
        "auditor", pack.request_id, "remote-role-result.v1", VERSION, VERSION, "kb-test", PROOF)["result"])
    assert remote["status"] == "ok"
    assert ResponseGuard().validate_remote_role(encoded(result), expected_role_id="auditor", payload_model=SCHEMA,
        evidence=pack, expected_knowledge_manifest_sha256="a" * 64)[1] == result["payload"]


@pytest.mark.parametrize("index", [-1, 0, 2])
def test_concrete_negative_finding_survives_assembly_and_both_guards(nodes, audit_case, index, monkeypatch):
    pack, incoming, draft = audit_case
    def text(value):
        return {"value": value, "kind": "inference", "source_ids": ["intent-a"]}
    draft["payload"]["problems"] = [{"index": index, "check": text("request"),
        "finding_kind": "missing_requirement", "field": text("scope_in"),
        "expected": text("검증 작업"), "actual": text(""),
        "message": text("요청한 검증 작업이 초안에 누락됐습니다"),
        "fix": text("초안 범위에 요청한 검증 작업을 포함하세요")}]
    result = assemble(nodes, incoming, draft)
    assert result["payload"]["problems"][0] == {"index": index, "check": "request",
        "finding_kind": "missing_requirement", "field": "scope_in", "expected": "검증 작업",
        "actual": "", "message": "요청한 검증 작업이 초안에 누락됐습니다",
        "fix": "초안 범위에 요청한 검증 작업을 포함하세요"}
    assert result["payload"]["answers_request"] is False
    remote = json.loads(code(nodes["1909000000001005"])["main"](encoded(result), encoded(pack.model_dump(mode="json")),
        "auditor", pack.request_id, "remote-role-result.v1", VERSION, VERSION, "kb-test", PROOF)["result"])
    assert remote["status"] == "ok"
    assert ResponseGuard().validate_remote_role(encoded(result), expected_role_id="auditor", payload_model=SCHEMA,
        evidence=pack, expected_knowledge_manifest_sha256="a" * 64)[1] == result["payload"]
    from app.agent.workflow.agents import auditor
    monkeypatch.setattr(auditor, "_machine_result", lambda _: {"ok": True, "errors": [], "warnings": []})
    applied = auditor.Auditor().apply({"request_text": "검증 작업 요청", "trace": []}, result["payload"])
    assert applied["review"]["ok"] is False
    assert result["payload"]["problems"][0] in applied["review"]["problems"]


@pytest.mark.parametrize("bad", [True, False, 0.0, "0", None])
def test_audit_problem_index_is_never_coerced(nodes, audit_case, bad):
    _, incoming, draft = audit_case
    draft["payload"]["problems"] = [{"index": bad}]
    with pytest.raises(ValueError, match="invalid_core_audit_draft"):
        assemble(nodes, incoming, draft)


@pytest.mark.parametrize("bad", [True, "true", "string", "PASS", 1])
def test_auditor_rejects_boolean_coercion_and_schema_placeholder(nodes, audit_case, bad):
    _, incoming, draft = audit_case
    draft["payload"]["grounded"] = bad
    with pytest.raises(ValueError, match="invalid_core_audit_draft"):
        assemble(nodes, incoming, draft)


@pytest.mark.parametrize("source", ["src_knowledge", "src_schema", "context-draft"])
def test_auditor_rejects_policy_and_schema_citations(nodes, audit_case, source):
    _, incoming, draft = audit_case
    draft["payload"]["summary"]["source_ids"] = [source]
    with pytest.raises(ValueError, match="invalid_core_audit_draft native_draft_source_id"):
        assemble(nodes, incoming, draft)


def test_intent_fact_remains_blocked_by_both_guards(nodes, audit_case):
    pack, incoming, draft = audit_case
    draft["payload"]["summary"]["kind"] = "fact"
    result = assemble(nodes, incoming, draft)
    remote = json.loads(code(nodes["1909000000001005"])["main"](encoded(result), encoded(pack.model_dump(mode="json")),
        "auditor", pack.request_id, "remote-role-result.v1", VERSION, VERSION, "kb-test", PROOF)["result"])
    assert remote["status"] == "blocked"
    assert remote["warnings"] == ["contract_guard:intent_source_fact_forbidden"]
    with pytest.raises(ResponseGuardError, match="intent_source_fact_forbidden"):
        ResponseGuard().validate_remote_role(encoded(result), expected_role_id="auditor", payload_model=SCHEMA, evidence=pack)


@pytest.mark.parametrize("status,missing", [("needs_input", ["검토할 초안을 제공해 주세요"]), ("blocked", [])])
def test_control_outcomes_remain_empty(nodes, audit_case, status, missing):
    _, incoming, draft = audit_case
    draft.update(status=status, payload={}, missing_inputs=missing)
    result = assemble(nodes, incoming, draft)
    assert result["status"] == status
    assert not any(result[key] for key in ("payload", "claims", "citations", "payload_claims"))
    draft["payload"] = {"grounded": "pass"}
    with pytest.raises(ValueError, match="invalid_core_audit_draft"):
        assemble(nodes, incoming, draft)


@pytest.mark.parametrize("field", ["role_id", "use_case", "schema", "source_hash"])
def test_audit_binding_tamper_never_falls_back(nodes, audit_case, field):
    _, incoming, draft = audit_case
    value = json.loads(incoming)
    if field == "role_id": value[field] = "work_architect"
    elif field == "use_case": value["context_pack"][field] = "work_architect"
    elif field == "schema":
        value["context_pack"]["payload_schema_json"] = "{}"
        value["context_pack"]["payload_schema_sha256"] = hashlib.sha256(b"{}").hexdigest()
    else: value["context_pack"]["sources"][0]["content"] += "changed"
    with pytest.raises(ValueError): assemble(nodes, encoded(value), draft)
    if field != "source_hash":
        with pytest.raises(ValueError): code(nodes["1909000000001024"])["main"](encoded(value))
