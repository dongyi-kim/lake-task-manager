"""Request native draft through the actual Core graph and both unchanged guards."""
import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from app.agent.maxis import ResponseGuard, ResponseGuardError
from app.agent.workflow.agents.request_architect import SCHEMA

ROOT = Path(__file__).resolve().parents[3]
REQUEST_ID = "1909000000001031"
ASSEMBLER_ID = "1909000000001032"
VERSION = "11111111-1111-4111-8111-111111111111"
PROOF = "LTM_RELEASE_SENTINEL_V1=" + "a" * 64


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def code(node):
    namespace = {"__name__": "request_graph_test"}
    exec(compile(node["code"], "<request-graph>", "exec"), namespace)
    return namespace


@pytest.fixture(scope="module")
def nodes():
    dsl = yaml.safe_load((ROOT / "deploy/maxis-dify/dsl/ltm-agent-core.yml").read_text(encoding="utf-8"))
    return {row["id"]: row["data"] for row in dsl["workflow"]["graph"]["nodes"]}


@pytest.fixture
def request_case(context_guard, now, nodes):
    intent = "신규 배치 오류를 Bug 초안으로 정리해줘."
    pack = context_guard.build(request_id="request-business", use_case="request_architect",
        query=intent, payload_schema=SCHEMA, knowledge_manifest_sha256="a" * 64,
        user_identity="synthetic-user", requested_at=now,
        sources=[{"source_id": "src_intent_request", "kind": "ltm",
            "canonical_id": "ltm:user-intent:request", "title": "Guarded intent", "scope": "",
            "content": intent, "retrieved_at": now}])
    guard = code(nodes["1909000000001006"])
    name = guard["_ALLOWED_DOCUMENT_NAMES_BY_ROLE"]["request_architect"][0]
    policy = {"content": "Synthetic authoring policy.", "metadata": {
        "dataset_id": guard["_DATASET_ID"], "document_id": guard["_DOCUMENT_IDS_BY_NAME"][name],
        "document_name": name}}
    sentinel = {"content": PROOF, "metadata": {"dataset_id": guard["_SENTINEL_DATASET_ID"],
        "document_id": guard["_SENTINEL_DOCUMENT_ID"], "document_name": guard["_SENTINEL_DOCUMENT_NAME"]}}
    guarded = guard["main"]([policy], [sentinel], encoded(pack.model_dump(mode="json")), pack.query,
        "request_architect", pack.request_id, "remote-role-result.v1", VERSION, VERSION, "kb-test")
    assert guarded["policy_decision"] == "allow"
    wrap = lambda text: {"value": text, "kind": "recommendation", "source_ids": ["src_intent_request"]}
    draft = {"contract": "request-draft.v1", "status": "ok", "missing_inputs": [], "payload": {
        "intent": wrap("plan_work"), "keywords": [wrap("배치 오류")], "module": "",
        "mentioned_keys": [], "research_readiness": "ready", "playbook": "bug_report",
        "goal": wrap("신규 배치 오류의 Bug 초안을 작성한다"), "tasks": [{
            "id": "draft-bug", "kind": "plan", "instruction": wrap("배치 오류의 Bug 초안을 작성한다"),
            "depends_on": [], "effect": "no_write_requested",
            "completion_criteria": [wrap("배치 오류의 Bug 초안 검토")]}],
        "request_questions": [], "requested_effects": [], "target_selectors": []}}
    return pack, guarded["model_input"], draft


def assembled(nodes, incoming, draft):
    return json.loads(code(nodes[ASSEMBLER_ID])["main"](incoming, encoded(draft))["candidate"])


def remote(nodes, pack, candidate):
    return json.loads(code(nodes["1909000000001005"])["main"](encoded(candidate),
        encoded(pack.model_dump(mode="json")), "request_architect", pack.request_id,
        "remote-role-result.v1", VERSION, VERSION, "kb-test", [{"content": PROOF}])["result"])


def local(pack, candidate):
    return ResponseGuard().validate_remote_role(encoded(candidate), expected_role_id="request_architect",
        payload_model=SCHEMA, evidence=pack, expected_knowledge_manifest_sha256="a" * 64)


def test_request_native_route_uses_real_schema_and_both_final_guards(nodes, request_case):
    pack, incoming, draft = request_case
    before = copy.deepcopy((incoming, draft))
    assert code(nodes["1909000000001024"])["main"](incoming) == {"route": "request"}
    schema = nodes[REQUEST_ID]["structured_output"]["schema"]
    Draft202012Validator(schema).validate(draft)
    result = assembled(nodes, incoming, draft)
    assert result["payload"]["intent"] == "plan_work"
    assert result["payload"]["sufficient"] is True
    assert result["payload"]["tasks"][0]["write_intent"] is False
    assert result["payload"]["tasks"][0]["instruction"] == draft["payload"]["tasks"][0]["instruction"]["value"]
    assert "assumptions" not in result["payload"]
    assert remote(nodes, pack, result) == result
    accepted, payload = local(pack, result)
    assert accepted.status == "ok" and payload == result["payload"]
    assert (incoming, draft) == before


@pytest.mark.parametrize("status", ["needs_input", "blocked"])
def test_request_empty_control_outcomes_preserve_existing_guard_contract(nodes, request_case, status):
    pack, incoming, draft = request_case
    draft.update(status=status, payload={}, missing_inputs=["어떤 업무의 초안이 필요한가요?"] if status == "needs_input" else [])
    result = assembled(nodes, incoming, draft)
    assert result["status"] == status
    assert result["payload"] == {} and result["claims"] == result["citations"] == result["payload_claims"] == []
    assert remote(nodes, pack, result) == result
    accepted, payload = local(pack, result)
    assert accepted.status == status and payload is None


def test_request_user_intent_cannot_be_relabelled_as_external_fact(nodes, request_case):
    pack, incoming, draft = request_case
    draft["payload"]["goal"]["kind"] = "fact"
    result = assembled(nodes, incoming, draft)
    assert any(row["kind"] == "fact" for row in result["claims"])
    assert remote(nodes, pack, result)["warnings"] == ["contract_guard:intent_source_fact_forbidden"]
    with pytest.raises(ResponseGuardError):
        local(pack, result)


@pytest.mark.parametrize("mutation", ["wrong_role", "wrong_schema", "unknown_source", "string_boolean"])
def test_request_assembly_rejects_unbound_inputs_without_a_partial_candidate(nodes, request_case, mutation):
    _, incoming, draft = request_case
    value = json.loads(incoming)
    if mutation == "wrong_role":
        value["role_id"] = "query_specialist"
    elif mutation == "wrong_schema":
        value["context_pack"]["payload_schema_sha256"] = "b" * 64
    elif mutation == "unknown_source":
        draft["payload"]["intent"]["source_ids"] = ["not-supplied"]
    else:
        draft["payload"]["research_readiness"] = "true"
    with pytest.raises(ValueError, match="^invalid_core_request_draft$"):
        assembled(nodes, encoded(value), draft)
