"""Request draft envelope layout stays strict through the assembler and guards."""
import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.agent.maxis import ContextGuard, ContextPolicy, ResponseGuard
from app.agent.maxis.contracts import canonical_payload_schema
from app.agent.workflow.agents.request_architect import SCHEMA

ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = ROOT / "deploy/maxis-dify"
TRANSPORT = DIRECTORY / "prompts/core_request.md"
VERSION = "11111111-1111-4111-8111-111111111111"
PROOF = "LTM_RELEASE_SENTINEL_V1=" + "a" * 64


def load(path):
    spec = importlib.util.spec_from_file_location("request_envelope_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.fixture(scope="module")
def contract():
    return load(DIRECTORY / "core_request_contract.py")


@pytest.fixture(scope="module")
def compiled(contract):
    schema, digest = canonical_payload_schema(SCHEMA)
    native = contract.build_draft_schema(json.loads(schema), lambda row: row)
    source = "\n\n".join((DIRECTORY / "code" / name).read_text(encoding="utf-8").rstrip()
        for name in ("core_draft_validation.py", "core_draft_context.py", "core_request_assembler.py"))
    namespace = {"__name__": "request_envelope_test"}
    exec(compile(source + f"\n_REQUEST_SCHEMA_JSON = {schema!r}\n_REQUEST_SCHEMA_SHA256 = {digest!r}\n_DRAFT_SCHEMA = {native!r}\n",
        "<request-envelope-assembler>", "exec"), namespace)
    return namespace["main"], native


@pytest.fixture
def case():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    pack = ContextGuard(ContextPolicy(allowed_scopes={}, allowed_source_hosts={}),
        b"synthetic-envelope-test-key-32bytes").build(
        request_id="request-envelope", use_case="request_architect", query="변경 초안을 작성해줘",
        payload_schema=SCHEMA, knowledge_manifest_sha256="a" * 64,
        user_identity="synthetic-envelope-user", requested_at=now,
        sources=[{"source_id": "src-intent-envelope", "kind": "ltm",
            "canonical_id": "ltm:user-intent:envelope", "title": "Synthetic request", "scope": "",
            "content": "변경 초안을 작성해줘", "retrieved_at": now}])
    incoming = {"contract": "ltm-model-input.v1", "context_pack": pack.model_dump(mode="json"),
        "role_id": "request_architect", "correlation_id": pack.request_id,
        "output_contract": "remote-role-result.v1", "workflow_version": VERSION,
        "knowledge_version": "kb-test", "knowledge_proof": PROOF, "knowledge_context": []}
    leaf = lambda value: {"value": value, "kind": "recommendation", "source_ids": ["src-intent-envelope"]}
    draft = {"contract": "request-draft.v2", "status": "ok", "payload": {
        "intent": leaf("plan_work"), "keywords": [], "research_readiness": "ready",
        "request_questions": [], "requested_effects": [], "target_selectors": []}, "missing_inputs": []}
    return pack, incoming, draft, leaf


def test_transport_places_missing_inputs_at_top_level_without_changing_status_contract(contract):
    transport = TRANSPORT.read_text(encoding="utf-8").strip()
    role = (ROOT / "app/agent/prompts/roles/request_architect.md").read_text(encoding="utf-8")
    rule = ("Return only one request-draft.v2 JSON object with top-level contract, status, payload and "
        "missing_inputs; missing_inputs is never inside payload.")
    prompt = contract.build_request_system_prompt(SCHEMA, role, transport)
    assert transport.count(rule) == prompt.count(rule) == 1
    for unchanged in ("It requires missing_inputs: [].", "needs_input requires payload: {} and concrete missing_inputs.",
                      "blocked with payload: {} and missing_inputs: [].", "payload contains request_questions"):
        assert unchanged in transport
    assert prompt.startswith(transport + "\n\n") and len(prompt.encode("utf-8")) <= 8192


def test_native_envelope_stays_exactly_four_required_fields(compiled):
    native = compiled[1]
    assert set(native["properties"]) == set(native["required"]) == {"contract", "status", "payload", "missing_inputs"}
    assert native["additionalProperties"] is False
    assert native["properties"]["status"]["enum"] == ["ok", "needs_input", "blocked"]
    assert "missing_inputs" not in native["properties"]["payload"]["anyOf"][1]["properties"]


@pytest.mark.parametrize("status", ["ok", "needs_input", "blocked"])
@pytest.mark.parametrize("retain_top_level", [False, True])
def test_missing_inputs_inside_payload_is_rejected_without_repair(compiled, case, status, retain_top_level):
    _, incoming, draft, _ = case
    draft["status"] = status
    if status != "ok":
        draft["payload"] = {}
    draft["missing_inputs"] = ["적용 범위를 알려주세요"] if status == "needs_input" else []
    draft["payload"]["missing_inputs"] = copy.deepcopy(draft["missing_inputs"])
    if not retain_top_level:
        del draft["missing_inputs"]
    before = copy.deepcopy(draft)
    assert not Draft202012Validator(compiled[1]).is_valid(draft)
    with pytest.raises(ValueError, match="^invalid_core_request_draft(?: native_draft_(?:json_syntax|json_structure|schema|source_id|fact_selection))?$"):
        compiled[0](encoded(incoming), encoded(draft))
    assert draft == before


@pytest.mark.parametrize("outcome", ["ok", "ok_with_questions", "needs_input", "blocked"])
def test_correct_envelope_and_request_questions_survive_assembler_and_both_guards(compiled, case, outcome):
    pack, incoming, draft, leaf = case
    if outcome in {"needs_input", "blocked"}:
        draft.update(status=outcome, payload={},
            missing_inputs=["적용 범위를 알려주세요"] if outcome == "needs_input" else [])
    elif outcome == "ok_with_questions":
        draft["payload"].update(research_readiness="needs_clarification",
            request_questions=[{"field": "scope", "question": leaf("적용 범위를 알려주세요")}])
    before = copy.deepcopy(draft)
    Draft202012Validator(compiled[1]).validate(draft)
    result = json.loads(compiled[0](encoded(incoming), encoded(draft))["candidate"])
    remote = load(DIRECTORY / "code/contract_guard.py").main(encoded(result), encoded(pack.model_dump(mode="json")),
        "request_architect", pack.request_id, "remote-role-result.v1", VERSION, VERSION, "kb-test", [{"content": PROOF}])
    local, payload = ResponseGuard().validate_remote_role(encoded(result), expected_role_id="request_architect",
        payload_model=SCHEMA, evidence=pack, expected_knowledge_manifest_sha256="a" * 64)
    assert json.loads(remote["result"]) == result and local.status == draft["status"]
    assert result["missing_inputs"] == local.model_dump(mode="json")["missing_inputs"] == draft["missing_inputs"]
    assert "missing_inputs" not in result["payload"] and draft == before
    if outcome == "ok_with_questions":
        assert result["status"] == "ok" and result["missing_inputs"] == []
        assert result["payload"]["sufficient"] is False
        assert result["payload"]["request_questions"] == payload["request_questions"] == [
            {"field": "scope", "question": "적용 범위를 알려주세요"}]
    if outcome in {"needs_input", "blocked"}:
        assert result["payload"] == {} and result["claims"] == result["citations"] == result["payload_claims"] == []
