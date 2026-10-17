"""Query input -> native typed branch -> unchanged remote/local guards; no provider."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from app.agent.maxis import ResponseGuard, ResponseGuardError
from app.agent.maxis.contracts import canonical_payload_schema
from app.agent.workflow.contracts import CompactQueryPlan, role_output_schema

ROOT = Path(__file__).resolve().parents[3]
QUERY_ID = "1909000000001028"
VERSION = "11111111-1111-4111-8111-111111111111"
PROOF = "LTM_RELEASE_SENTINEL_V1=" + "a" * 64


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.fixture(scope="module")
def nodes():
    dsl = yaml.safe_load((ROOT / "deploy/maxis-dify/dsl/ltm-agent-core.yml").read_text(encoding="utf-8"))
    return {row["id"]: row["data"] for row in dsl["workflow"]["graph"]["nodes"]}


def code(node):
    namespace = {"__name__": "core_query_integration"}
    exec(compile(node["code"], "<core-query-integration>", "exec"), namespace)
    return namespace


@pytest.fixture
def query_case(context_guard, now, nodes):
    pack = context_guard.build(request_id="query-business", use_case="query_specialist",
        query="데이터 자산 이력을 찾아줘", payload_schema=role_output_schema("query_specialist"),
        knowledge_manifest_sha256="a" * 64, user_identity="synthetic-user", requested_at=now,
        sources=[{"source_id": "src_intent_query", "kind": "ltm",
            "canonical_id": "ltm:user-intent:query", "title": "Guarded intent", "scope": "",
            "content": "데이터 자산 이력을 찾아줘", "retrieved_at": now}])
    guard = code(nodes["1909000000001006"])
    name = guard["_ALLOWED_DOCUMENT_NAMES_BY_ROLE"]["query_specialist"][0]
    policy = {"content": "Synthetic policy.", "metadata": {"dataset_id": guard["_DATASET_ID"],
        "document_id": guard["_DOCUMENT_IDS_BY_NAME"][name], "document_name": name}}
    sentinel = {"content": PROOF, "metadata": {"dataset_id": guard["_SENTINEL_DATASET_ID"],
        "document_id": guard["_SENTINEL_DOCUMENT_ID"], "document_name": guard["_SENTINEL_DOCUMENT_NAME"]}}
    # The transport uses canonical JSON, not Pydantic's field order.
    guarded = guard["main"]([policy], [sentinel], encoded(pack.model_dump(mode="json")), pack.query,
        "query_specialist", pack.request_id, "remote-role-result.v1", VERSION, VERSION, "kb-test")
    assert guarded["policy_decision"] == "allow"
    texts = [("/reads/0/source", "confluence"), ("/reads/0/subject", "데이터 자산"),
             ("/reads/0/where", "변경 이력")]
    candidate = {"contract": "remote-role-result.v1", "role_id": "query_specialist", "status": "ok",
        "payload": {"reads": [{"source": "confluence", "subject": "데이터 자산",
                               "where": "변경 이력", "exhaustive": True}], "uncertainty": []},
        "claims": [{"claim_id": f"c{i}", "kind": "recommendation", "text": text,
                    "citation_ids": ["cite"]} for i, (_, text) in enumerate(texts)],
        "payload_claims": [{"path": path, "claim_ids": [f"c{i}"]} for i, (path, _) in enumerate(texts)],
        "citations": [{"citation_id": "cite", "source_id": "src_intent_query", "locator": ""}],
        "missing_inputs": [], "warnings": [], "workflow_version": VERSION,
        "knowledge_version": "kb-test", "knowledge_manifest_sha256": "a" * 64, "knowledge_proof": PROOF}
    return pack, guarded["model_input"], candidate


def remote(nodes, pack, candidate):
    return json.loads(code(nodes["1909000000001005"])["main"](encoded(candidate),
        encoded(pack.model_dump(mode="json")), "query_specialist", pack.request_id,
        "remote-role-result.v1", VERSION, VERSION, "kb-test", [{"content": PROOF}])["result"])


def local(pack, candidate):
    return ResponseGuard().validate_remote_role(encoded(candidate), expected_role_id="query_specialist",
        payload_model=CompactQueryPlan, evidence=pack, expected_knowledge_manifest_sha256="a" * 64)


def test_query_native_contract_is_resolved_and_both_guards_accept_complete_candidate(nodes, query_case):
    pack, model_input, candidate = query_case
    assert code(nodes["1909000000001024"])["main"](model_input) == {"route": "query"}
    schema = nodes[QUERY_ID]["structured_output"]["schema"]
    assert "$ref" not in encoded(schema) and "$defs" not in encoded(schema)
    assert schema["properties"]["role_id"] == {"enum": ["query_specialist"]}
    assert schema["properties"]["payload"]["anyOf"][0] == {
        "type": "object", "additionalProperties": False, "maxProperties": 0}
    Draft202012Validator(schema).validate(candidate)
    assert remote(nodes, pack, candidate) == candidate
    result, payload = local(pack, candidate)
    assert result.status == "ok" and payload.reads[0].exhaustive is True


@pytest.mark.parametrize("bad", ["true", "false", 1, None])
def test_query_native_schema_and_both_guards_reject_non_boolean_without_coercion(nodes, query_case, bad):
    pack, _, candidate = query_case
    candidate["payload"]["reads"][0]["exhaustive"] = bad
    before = copy.deepcopy(candidate)
    assert not Draft202012Validator(nodes[QUERY_ID]["structured_output"]["schema"]).is_valid(candidate)
    assert remote(nodes, pack, candidate)["warnings"] == ["contract_guard:payload_type_mismatch"]
    with pytest.raises(ResponseGuardError):
        local(pack, candidate)
    assert candidate == before


@pytest.mark.parametrize("status", ["blocked", "needs_input"])
def test_query_native_contract_preserves_empty_control_outcomes(nodes, query_case, status):
    pack, _, candidate = query_case
    candidate.update(status=status, payload={}, claims=[], payload_claims=[], citations=[],
                     missing_inputs=["조회 대상을 알려주세요"] if status == "needs_input" else [])
    Draft202012Validator(nodes[QUERY_ID]["structured_output"]["schema"]).validate(candidate)
    assert remote(nodes, pack, candidate) == candidate
    assert local(pack, candidate)[0].status == status


@pytest.mark.parametrize("mutation", ["missing_leaf", "array_binding", "unknown_source", "intent_fact", "different_text"])
def test_query_native_types_never_replace_existing_grounding_and_binding_guards(nodes, query_case, mutation):
    pack, _, candidate = query_case
    if mutation == "missing_leaf":
        candidate["payload_claims"].pop()
    elif mutation == "array_binding":
        candidate["payload_claims"][0]["path"] = "/reads"
    elif mutation == "unknown_source":
        candidate["citations"][0]["source_id"] = "invented_source"
    elif mutation == "intent_fact":
        candidate["claims"][0]["kind"] = "fact"
    else:
        candidate["claims"][0]["text"] = "Confluence"
    assert Draft202012Validator(nodes[QUERY_ID]["structured_output"]["schema"]).is_valid(candidate)
    assert remote(nodes, pack, candidate)["status"] == "blocked"
    with pytest.raises(ResponseGuardError):
        local(pack, candidate)


@pytest.mark.parametrize("mutation", ["digest", "schema", "rehashed_schema", "noncanonical", "use_case"])
def test_query_schema_mismatch_never_falls_back_to_generic_semantic(nodes, query_case, mutation):
    _, model_input, _ = query_case
    incoming = json.loads(model_input)
    pack = incoming["context_pack"]
    schema, digest = canonical_payload_schema(role_output_schema("query_specialist"))
    assert (pack["payload_schema_json"], pack["payload_schema_sha256"]) == (schema, digest)
    if mutation == "digest":
        pack["payload_schema_sha256"] = "0" * 64
    elif mutation == "use_case":
        pack["use_case"] = "work_architect"
    else:
        pack["payload_schema_json"] = schema + " " if mutation == "noncanonical" else '{"type":"object"}'
        if mutation in {"rehashed_schema", "noncanonical"}:
            pack["payload_schema_sha256"] = hashlib.sha256(pack["payload_schema_json"].encode()).hexdigest()
    with pytest.raises(ValueError, match="invalid_core_control_input"):
        code(nodes["1909000000001024"])["main"](encoded(incoming))
