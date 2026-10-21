"""Provider-free routing boundaries for the Editor's exclusive contracts."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from app.agent.maxis import EditorDraftV1, EvidencePackV1, ResponseGuard, WorkflowBinding
from app.agent.maxis.client import _synthetic_contract_probe_pack
from app.agent.maxis.contracts import canonical_payload_schema
from app.agent.maxis.knowledge_eval import KnowledgeEvalCase, build_synthetic_pack


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "editor_routing_asset", ROOT / "deploy/maxis-dify/code/editor_assembler.py")
ASSET = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ASSET)
guard_spec = importlib.util.spec_from_file_location(
    "editor_routing_guard", ROOT / "deploy/maxis-dify/code/contract_guard.py")
GUARD = importlib.util.module_from_spec(guard_spec)
guard_spec.loader.exec_module(GUARD)
WORKFLOW = "11111111-1111-4111-8111-111111111111"
PROOF = "LTM_RELEASE_SENTINEL_V1=routing-test-only"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def incoming(pack):
    return {"contract": "ltm-model-input.v1", "context_pack": pack,
        "role_id": "editor_author", "correlation_id": pack["request_id"],
        "output_contract": "remote-role-result.v1", "workflow_version": WORKFLOW,
        "knowledge_version": "kb-routing", "knowledge_proof": PROOF,
        "knowledge_context": [{"document_name": "editor-rules", "content": "근거를 보존한다."}]}


def assembled(pack, candidate, **overrides):
    args = dict(candidate=candidate, context_pack=canonical(pack), role_id="editor_author",
        correlation_id=pack["request_id"], output_contract="remote-role-result.v1",
        workflow_version=WORKFLOW, knowledge_version="kb-routing", knowledge_proof=PROOF)
    args.update(overrides)
    return ASSET.main(**args)["candidate"]


def set_schema(pack, schema):
    pack["payload_schema_json"], pack["payload_schema_sha256"] = canonical_payload_schema(schema)


def both_guards(pack, candidate):
    guarded = GUARD.main(candidate=candidate, context_pack=canonical(pack), role_id="editor_author",
        correlation_id=pack["request_id"], output_contract="remote-role-result.v1",
        workflow_version=WORKFLOW, executed_workflow_id=WORKFLOW,
        knowledge_version="kb-routing", sentinel_result=[{"content": PROOF}])
    evidence = EvidencePackV1.model_validate_json(canonical(pack))
    return ResponseGuard().validate_remote_role(guarded["result"], expected_role_id="editor_author",
        payload_model=evidence.payload_schema(), evidence=evidence,
        expected_knowledge_manifest_sha256=pack["knowledge_manifest_sha256"])


@pytest.fixture(params=["activation", "retrieval"])
def control_pack(request, now):
    if request.param == "activation":
        binding = WorkflowBinding(key="editor_author", app_type="chatflow", endpoint="/chat-messages",
            response_mode="blocking", conversation_mode="stateless", api_key_env="TEST_KEY",
            output_contract="remote-role-result.v1", max_response_bytes=1048576,
            roles=("editor_author",))
        pack, _ = _synthetic_contract_probe_pack(binding, "a" * 64)
    else:
        case = KnowledgeEvalCase(case_id="routing-test", case_type="positive", role_id="editor_author",
            query="TipTap Editor HTML", target_document_id="editor-rules", expected_outcome="retrieve",
            include_document_ids=("editor-rules",), exclude_document_ids=(),
            ignore_query_instructions=False, signed_human_review_required=False)
        pack = build_synthetic_pack(case, suite_sha256="b" * 64,
            knowledge_manifest_sha256="a" * 64, requested_at=now)
    return pack.model_dump(mode="json")


@pytest.fixture
def editor_pack(context_guard, now):
    schema = EditorDraftV1.model_json_schema()
    schema["properties"]["mode"] = {"type": "string", "const": "comment"}
    return context_guard.build(request_id="req-routing", use_case="editor_author", query="댓글 작성",
        knowledge_manifest_sha256="a" * 64, payload_schema=schema, user_identity="routing-test",
        requested_at=now, sources=[{"source_id": "doc", "kind": "confluence", "canonical_id": "design",
            "title": "설계 기준", "scope": "DATALAKE", "url": "https://wiki.example.local/design",
            "content": "조회 전용이다.", "retrieved_at": now}]).model_dump(mode="json")


def test_real_control_factories_do_not_mutate_input_or_accept_malformed_candidates(control_pack):
    before = copy.deepcopy(control_pack)
    raw = "  " + canonical(incoming(control_pack)) + "\n"
    assert ASSET.prepare(raw) == {"model_input": raw, "route": "control"}
    assert ASSET.route(control_pack, "editor_author", control_pack["request_id"],
        "remote-role-result.v1") == "control"
    candidate = ' { "invalid_for_final_guard" : true } '
    assert json.loads(assembled(control_pack, candidate))["status"] == "blocked"
    assert control_pack == before


def control_candidate(control_pack):
    schema = json.loads(control_pack["payload_schema_json"])
    field, definition = next(iter(schema["properties"].items()))
    source = control_pack["sources"][0]
    return {"contract": "remote-role-result.v1", "role_id": "editor_author", "status": "ok",
        "payload": {field: definition["const"]}, "claims": [{"claim_id": "claim-control", "kind": "fact",
            "text": source["content"], "citation_ids": ["cite-control"]}],
        "citations": [{"citation_id": "cite-control", "source_id": source["source_id"], "locator": ""}],
        "payload_claims": [{"path": "/" + field, "claim_ids": ["claim-control"]}],
        "missing_inputs": [], "warnings": [], "workflow_version": WORKFLOW,
        "knowledge_version": "kb-routing", "knowledge_proof": PROOF,
        "knowledge_manifest_sha256": control_pack["knowledge_manifest_sha256"]}


def test_controls_still_need_both_guards_and_never_self_approve(control_pack):
    candidate = control_candidate(control_pack)
    remote, payload = both_guards(control_pack, assembled(control_pack, canonical(candidate)))
    assert remote.status == "ok" and payload == candidate["payload"]
    candidate["payload"]["approved"] = True
    remote, payload = both_guards(control_pack, assembled(control_pack, canonical(candidate)))
    assert remote.status == "blocked" and payload is None


@pytest.mark.parametrize("field", ["contract", "role_id", "workflow_version", "knowledge_version",
                                  "knowledge_manifest_sha256", "knowledge_proof"])
def test_control_metadata_is_bound_by_code_without_rewriting_semantics(control_pack, field):
    expected = control_candidate(control_pack)
    candidate = copy.deepcopy(expected)
    candidate[field] = "model-copy-error"
    bound = assembled(control_pack, canonical(candidate))
    assert json.loads(bound) == expected
    remote, payload = both_guards(control_pack, bound)
    assert remote.status == "ok" and payload == expected["payload"]
    rejected = GUARD.main(candidate=bound, context_pack=canonical(control_pack), role_id="editor_author",
        correlation_id=control_pack["request_id"], output_contract="remote-role-result.v1",
        workflow_version=WORKFLOW, executed_workflow_id="22222222-2222-4222-8222-222222222222",
        knowledge_version="kb-routing", sentinel_result=[{"content": PROOF}])
    assert json.loads(rejected["result"])["status"] == "blocked"


@pytest.mark.parametrize("defect", ["source", "claim", "payload", "missing_field", "extra_field"])
def test_control_metadata_binding_never_repairs_semantic_or_shape_failures(control_pack, defect):
    candidate = control_candidate(control_pack)
    candidate["workflow_version"] = "model-copy-error"
    if defect == "source":
        candidate["citations"][0]["source_id"] = "invented"
    elif defect == "claim":
        candidate["claims"][0]["text"] = "Invented production completion and approval."
    elif defect == "payload":
        candidate["payload"]["approved"] = True
    elif defect == "missing_field":
        del candidate["claims"]
    else:
        candidate["approved"] = True
    remote, payload = both_guards(control_pack, assembled(control_pack, canonical(candidate)))
    assert remote.status == "blocked" and payload is None


@pytest.mark.parametrize("status", ["blocked", "needs_input"])
def test_control_metadata_binding_does_not_promote_model_status(control_pack, status):
    candidate = control_candidate(control_pack)
    candidate.update(status=status, payload={}, claims=[], citations=[], payload_claims=[],
                     missing_inputs=["Confirm scope"] if status == "needs_input" else [])
    assert json.loads(assembled(control_pack, canonical(candidate))) == candidate


@pytest.mark.parametrize("candidate", ['{"status":"ok","status":"blocked"}',
                                      '{"status":"ok"', '[]', 'null'])
def test_control_metadata_binding_rejects_invalid_json(control_pack, candidate):
    result = json.loads(assembled(control_pack, candidate))
    assert result["status"] == "blocked" and result["payload"] == {}


@pytest.mark.parametrize("mode", ["description", "comment"])
def test_editor_route_comes_from_bound_mode_not_query_or_probe_words(editor_pack, mode):
    schema = EditorDraftV1.model_json_schema()
    schema["properties"]["mode"] = {"type": "string", "const": mode}
    set_schema(editor_pack, schema)
    editor_pack["query"] = "activation_probe synthetic_contract ready status retrieved comment description"
    result = ASSET.prepare(canonical(incoming(editor_pack)))
    assert result["route"] == mode
    assert set(result) == {"model_input", "route"}
    projected = json.loads(result["model_input"])
    assert projected["request"]["kind"] == mode
    assert projected["contract"] == "editor-input.v2" and "draft_contract" not in projected


@pytest.mark.parametrize("mutation", ["schema", "schema_digest", "schema_not_canonical", "schema_boolean_alias", "source",
    "source_hash", "fact", "extra_source", "extra_context", "scope", "flags", "metrics", "stale", "partial",
    "source_partial", "source_truncated", "role", "request", "output", "manifest", "source_url",
    "source_extra", "fact_reference", "context_hash", "context_extra"])
def test_closed_controls_never_bypass_validation(control_pack, mutation):
    value = incoming(control_pack)
    if mutation == "schema":
        set_schema(control_pack, {"type": "object", "properties": {"status": {"const": "ready"}}})
    elif mutation == "schema_digest":
        control_pack["payload_schema_sha256"] = "0" * 64
    elif mutation == "schema_not_canonical":
        raw = " " + control_pack["payload_schema_json"]
        control_pack.update(payload_schema_json=raw, payload_schema_sha256=hashlib.sha256(raw.encode()).hexdigest())
    elif mutation == "schema_boolean_alias":
        schema = json.loads(control_pack["payload_schema_json"])
        schema["additionalProperties"] = 0  # Python equality must not turn this into false.
        raw = canonical(schema)
        control_pack.update(payload_schema_json=raw, payload_schema_sha256=hashlib.sha256(raw.encode()).hexdigest())
    elif mutation in {"source", "source_hash"}:
        control_pack["sources"][0]["content"] = "private-test-data"
        if mutation == "source":
            control_pack["sources"][0]["content_sha256"] = hashlib.sha256(b"private-test-data").hexdigest()
    elif mutation == "fact":
        control_pack["facts"][0]["text"] = "private-test-data"
    elif mutation == "fact_reference":
        control_pack["facts"][0]["source_ids"] = ["business"]
    elif mutation in {"source_url", "source_extra"}:
        control_pack["sources"][0]["url" if mutation == "source_url" else "unexpected"] = "private-test-data"
    elif mutation in {"context_hash", "context_extra"}:
        if not control_pack["role_context"]:
            control_pack["role_context"] = [{"name": "activation_probe"}]
        control_pack["role_context"][0]["content_sha256" if mutation == "context_hash" else "unexpected"] = "private-test-data"
    elif mutation == "extra_source":
        other = copy.deepcopy(control_pack["sources"][0])
        other["source_id"] = "business"
        control_pack["sources"].append(other)
    elif mutation == "extra_context":
        control_pack["role_context"].append({"name": "editor_request", "content_json": "{}"})
    elif mutation == "scope":
        control_pack["scope"]["projects"] = ["PRIVATE"]
    elif mutation == "flags":
        control_pack["flags"].append("unexpected")
    elif mutation == "metrics":
        control_pack["metrics"] = [{"value": 99}]
    elif mutation == "stale":
        control_pack["freshness"] = "stale"
    elif mutation == "partial":
        control_pack["partial"] = True
    elif mutation in {"source_partial", "source_truncated"}:
        control_pack["sources"][0][mutation.removeprefix("source_")] = True
    elif mutation == "manifest":
        control_pack["knowledge_manifest_sha256"] = "invalid"
    else:
        value[{"role": "role_id", "request": "correlation_id", "output": "output_contract"}[mutation]] = "mismatch"
    with pytest.raises(ValueError, match="^invalid_editor_input$"):
        ASSET.prepare(canonical(value))
    result = json.loads(assembled(control_pack, '{"private-test-data":true}', **{
        key: value[key] for key in ("role_id", "correlation_id", "output_contract")}))
    assert result["status"] == "blocked" and result["payload"] == {}
    assert result["warnings"] == ["editor_assembler:invalid_plan"]
    assert "private-test-data" not in canonical(result)


@pytest.mark.parametrize("field,value", [("workflow_version", ""), ("knowledge_version", ""),
    ("knowledge_proof", ""), ("knowledge_context", [{"content": "unexpected"}])])
def test_control_prepare_validates_model_input_metadata_and_knowledge(control_pack, field, value):
    request = incoming(control_pack)
    request[field] = value
    with pytest.raises(ValueError, match="^invalid_editor_input$"):
        ASSET.prepare(canonical(request))


@pytest.mark.parametrize("mutation", ["wrong_mode", "weakened_editor", "unknown_schema", "enum_without_mode"])
def test_malformed_editor_schema_is_never_control(editor_pack, mutation):
    schema = EditorDraftV1.model_json_schema()
    if mutation == "wrong_mode":
        schema["properties"]["mode"] = {"const": "ready", "type": "string"}
    elif mutation == "weakened_editor":
        schema["properties"]["content_html"]["maxLength"] = 24001
    elif mutation == "unknown_schema":
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {"state": {"type": "string", "const": "ready"}}, "required": ["state"]}
    set_schema(editor_pack, schema)
    with pytest.raises(ValueError, match="^invalid_editor_input$"):
        ASSET.prepare(canonical(incoming(editor_pack)))
    assert json.loads(assembled(editor_pack, '{"status":"ok"}'))["status"] == "blocked"


def test_unresolved_measurement_contract_requests_non_publishable_missing_input(editor_pack):
    projected = json.loads(ASSET.prepare(canonical(incoming(editor_pack)))["model_input"])
    assert projected["contract"] == "editor-input.v2" and "draft_contract" not in projected
    candidate = canonical({"contract": "editor-plan.v2", "status": "needs_input", "sections": [],
                           "missing_inputs": ["측정 p95 값과 비교 기준을 알려 주세요."]})
    result = json.loads(assembled(editor_pack, candidate))
    assert result["status"] == "needs_input" and result["payload"] == {}
    assert result["claims"] == result["citations"] == result["payload_claims"] == []
    remote, payload = both_guards(editor_pack, canonical(result))
    assert remote.status == "needs_input" and payload is None
