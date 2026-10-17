"""Query draft assembly preserves meaning; existing guards decide acceptance."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.agent.maxis import ResponseGuard, ResponseGuardError
from app.agent.maxis.contracts import canonical_payload_schema
from app.agent.maxis.context_guard import MAX_CONTEXT_PACK_UTF8_BYTES
from app.agent.workflow.contracts import CompactQueryPlan, role_output_schema

ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = ROOT / "deploy/maxis-dify"
VERSION = "11111111-1111-4111-8111-111111111111"
PROOF = "LTM_RELEASE_SENTINEL_V1=" + "a" * 64


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load(path):
    spec = importlib.util.spec_from_file_location("query_test_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def compiled():
    contract = load(DIRECTORY / "core_query_contract.py")
    project = load(DIRECTORY / "validate_bundle.py")._dify_structured_output_schema
    schema, digest = canonical_payload_schema(role_output_schema("query_specialist"))
    draft = contract.build_draft_schema(json.loads(schema), project)
    source = "\n\n".join((DIRECTORY / "code" / name).read_text(encoding="utf-8").rstrip()
                         for name in ("core_draft_validation.py", "core_draft_context.py", "core_query_assembler.py"))
    namespace = {"__name__": "query_assembler_test"}
    exec(compile(source + f"\n_QUERY_SCHEMA_JSON = {schema!r}\n_QUERY_SCHEMA_SHA256 = {digest!r}\n_DRAFT_SCHEMA = {draft!r}\n",
                 "<query-assembler>", "exec"), namespace)
    return namespace, draft, contract, project


def leaf(value, kind="recommendation", refs=None):
    return {"value": value, "kind": kind, "source_ids": ["src_intent_query"] if refs is None else refs}


@pytest.fixture
def case(context_guard, now):
    pack = context_guard.build(request_id="query-business", use_case="query_specialist",
        query="데이터 자산 이력을 찾아줘", payload_schema=role_output_schema("query_specialist"),
        knowledge_manifest_sha256="a" * 64, user_identity="synthetic-user", requested_at=now,
        sources=[{"source_id": "src_intent_query", "kind": "ltm", "canonical_id": "ltm:user-intent:query",
                  "title": "Guarded intent", "scope": "", "content": "데이터 자산 이력을 찾아줘", "retrieved_at": now}])
    incoming = {"contract": "ltm-model-input.v1", "context_pack": pack.model_dump(mode="json"),
        "role_id": "query_specialist", "correlation_id": pack.request_id, "output_contract": "remote-role-result.v1",
        "workflow_version": VERSION, "knowledge_version": "kb-test", "knowledge_proof": PROOF, "knowledge_context": []}
    draft = {"contract": "query-draft.v2", "status": "ok", "payload": {"reads": [{
        "source": leaf("confluence"), "subject": leaf("데이터 자산"), "where": leaf("변경 이력", "inference")}]},
        "missing_inputs": []}
    return pack, incoming, draft


def assemble(compiled, incoming, draft):
    return json.loads(compiled[0]["main"](encoded(incoming), encoded(draft))["candidate"])


def guards(pack, result):
    remote = load(DIRECTORY / "code/contract_guard.py").main(encoded(result), encoded(pack.model_dump(mode="json")),
        "query_specialist", pack.request_id, "remote-role-result.v1", VERSION, VERSION, "kb-test", [{"content": PROOF}])
    local = lambda: ResponseGuard().validate_remote_role(encoded(result), expected_role_id="query_specialist",
        payload_model=CompactQueryPlan, evidence=pack, expected_knowledge_manifest_sha256="a" * 64)
    return json.loads(remote["result"]), local


def test_schema_derives_value_constraints_and_retains_optional_fields(compiled):
    ns, draft, _, project = compiled
    canonical = project(json.loads(ns["_QUERY_SCHEMA_JSON"]))
    payload = draft["properties"]["payload"]["anyOf"][1]
    row = payload["properties"]["reads"]["items"]
    original = canonical["properties"]["reads"]["items"]
    assert row["required"] == original["required"] == ["source"]
    assert "required" not in payload
    for field in ("source", "subject", "where"):
        assert row["properties"][field]["properties"]["value"] == original["properties"][field]
    assert original["properties"]["exhaustive"] == {"type": "boolean"}
    assert "exhaustive" not in row["properties"]
    assert row["properties"]["coverage"] == {"type": "string", "enum": ["exhaustive", "standard"]}
    assert draft["properties"]["contract"]["enum"] == ["query-draft.v2"]
    assert payload["properties"]["reads"]["maxItems"] == canonical["properties"]["reads"]["maxItems"]
    assert "$ref" not in encoded(draft) and "$defs" not in encoded(draft)
    assert ns["_MAX_CONTEXT_PACK_UTF8_BYTES"] == MAX_CONTEXT_PACK_UTF8_BYTES


@pytest.mark.parametrize("mutation", ["const", "enum", "union", "allof", "new_property", "new_constraint"])
def test_schema_rejects_unreviewed_canonical_changes(compiled, mutation):
    ns, _, contract, project = compiled
    schema = json.loads(ns["_QUERY_SCHEMA_JSON"])
    fields = schema["$defs"]["QueryIntent"]["properties"]
    if mutation == "new_property":
        fields["new_field"] = {"type": "string"}
    elif mutation == "new_constraint":
        fields["subject"]["minLength"] = 1
    else:
        fields["exhaustive"].update({"const": {"const": False}, "enum": {"enum": [True]},
            "union": {"type": ["boolean"]}, "allof": {"allOf": [{"const": False}]}}[mutation])
    with pytest.raises(ValueError):
        contract.build_draft_schema(schema, project)


def test_assembly_uses_exact_values_kinds_and_metadata_then_both_guards(compiled, case):
    pack, incoming, draft = case
    Draft202012Validator(compiled[1]).validate(draft)
    before = copy.deepcopy(draft)
    result = assemble(compiled, incoming, draft)
    assert draft == before
    assert result["payload"] == {"reads": [{"source": "confluence", "subject": "데이터 자산", "where": "변경 이력"}]}
    assert [row["text"] for row in result["claims"]] == ["confluence", "데이터 자산", "변경 이력"]
    assert [row["kind"] for row in result["claims"]] == ["recommendation", "recommendation", "inference"]
    assert [row["path"] for row in result["payload_claims"]] == ["/reads/0/source", "/reads/0/subject", "/reads/0/where"]
    assert len(result["citations"]) == 1 and result["citations"][0]["locator"] == ""
    remote, local = guards(pack, result)
    assert remote == result and local()[0].status == "ok"


@pytest.mark.parametrize("coverage,value", [("standard", False), ("exhaustive", True)])
def test_coverage_maps_exactly_to_canonical_boolean_without_changing_narratives(compiled, case, coverage, value):
    pack, incoming, draft = case
    draft["payload"]["reads"][0]["coverage"] = coverage
    draft["payload"]["uncertainty"] = [leaf("이력의 조회 기간 확인 필요", "inference")]
    original = copy.deepcopy((incoming, draft))
    Draft202012Validator(compiled[1]).validate(draft)
    result = assemble(compiled, incoming, draft)
    assert (incoming, draft) == original
    assert result["payload"]["reads"][0]["exhaustive"] is value
    assert result["payload"]["reads"][0] == {
        "source": "confluence", "subject": "데이터 자산", "where": "변경 이력", "exhaustive": value}
    assert [row["text"] for row in result["claims"]] == ["confluence", "데이터 자산", "변경 이력", "이력의 조회 기간 확인 필요"]
    assert [row["kind"] for row in result["claims"]] == ["recommendation", "recommendation", "inference", "inference"]
    assert result["citations"] == [{"citation_id": "cite-1", "source_id": "src_intent_query", "locator": ""}]
    assert not any(row["path"].endswith(("exhaustive", "coverage")) for row in result["payload_claims"])
    assert result["payload_claims"][-1]["path"] == "/uncertainty/0"
    remote, local = guards(pack, result)
    assert remote == result and local()[1].reads[0].exhaustive is value


def test_omitted_coverage_preserves_absent_canonical_exhaustive(compiled, case):
    pack, incoming, draft = case
    Draft202012Validator(compiled[1]).validate(draft)
    result = assemble(compiled, incoming, draft)
    assert "coverage" not in result["payload"]["reads"][0]
    assert "exhaustive" not in result["payload"]["reads"][0]
    remote, local = guards(pack, result)
    assert remote == result and local()[0].status == "ok"


@pytest.mark.parametrize("value", ["false", "true", False, True, 0, 1, 1.0, None, "", "EXHAUSTIVE",
                                   "Standard", "exhaustive ", " standard", "unknown", [], {}])
def test_invalid_coverage_is_rejected_without_repair_or_default(compiled, case, value):
    _, incoming, draft = case
    draft["payload"]["reads"][0]["coverage"] = value
    original = copy.deepcopy(draft)
    assert not Draft202012Validator(compiled[1]).is_valid(draft)
    with pytest.raises(ValueError, match="^invalid_core_query_draft$"):
        assemble(compiled, incoming, draft)
    assert draft == original


@pytest.mark.parametrize("value", [False, True, "false", "true", 0, 1])
def test_canonical_exhaustive_field_is_forbidden_in_internal_v2_draft(compiled, case, value):
    _, incoming, draft = case
    draft["payload"]["reads"][0]["exhaustive"] = value
    assert not Draft202012Validator(compiled[1]).is_valid(draft)
    with pytest.raises(ValueError, match="^invalid_core_query_draft$"):
        assemble(compiled, incoming, draft)


@pytest.mark.parametrize("coverage", [None, "standard", "exhaustive"])
def test_previous_draft_version_is_rejected_without_alias(compiled, case, coverage):
    _, incoming, draft = case
    draft["contract"] = "query-draft.v1"
    if coverage is not None:
        draft["payload"]["reads"][0]["coverage"] = coverage
    assert not Draft202012Validator(compiled[1]).is_valid(draft)
    with pytest.raises(ValueError, match="^invalid_core_query_draft$"):
        assemble(compiled, incoming, draft)


def test_coverage_wire_has_no_boolean_type_for_dify_to_rewrite(compiled):
    schema = compiled[1]
    rewritten = json.loads(encoded(schema), object_hook=lambda row:
        {**row, "type": "string"} if row.get("type") == "boolean" else row)
    assert rewritten == schema
    fields = rewritten["properties"]["payload"]["anyOf"][1]["properties"]["reads"]["items"]["properties"]
    assert fields["coverage"] == {"type": "string", "enum": ["exhaustive", "standard"]}
    assert "exhaustive" not in fields


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_internal_adapter_does_not_loosen_external_boolean_guards(compiled, case, value):
    pack, incoming, draft = case
    draft["payload"]["reads"][0]["coverage"] = "standard"
    result = assemble(compiled, incoming, draft)
    result["payload"]["reads"][0]["exhaustive"] = value
    remote, local = guards(pack, result)
    assert remote["warnings"] == ["contract_guard:payload_type_mismatch"]
    with pytest.raises(ResponseGuardError):
        local()


def test_fact_is_never_relabelled_to_bypass_intent_source_guard(compiled, case):
    pack, incoming, draft = case
    draft["payload"]["reads"][0]["subject"]["kind"] = "fact"
    result = assemble(compiled, incoming, draft)
    assert result["claims"][1]["kind"] == "fact"
    remote, local = guards(pack, result)
    assert remote["warnings"] == ["contract_guard:intent_source_fact_forbidden"]
    with pytest.raises(ResponseGuardError, match="intent_source_fact_forbidden"):
        local()


@pytest.mark.parametrize("status", ["blocked", "needs_input"])
def test_empty_control_outcomes_cross_both_guards(compiled, case, status):
    pack, incoming, draft = case
    draft.update(status=status, payload={}, missing_inputs=["조회 기간을 알려주세요"] if status == "needs_input" else [])
    result = assemble(compiled, incoming, draft)
    assert not any(result[key] for key in ("payload", "claims", "payload_claims", "citations"))
    remote, local = guards(pack, result)
    assert remote == result and local()[0].status == status


@pytest.mark.parametrize("mutation", ["string_bool", "number_bool", "extra", "metadata", "kind", "missing_refs",
    "unknown_ref", "duplicate_ref", "numeric_ref", "long_text", "many_reads", "many_uncertainty", "array_binding",
    "missing_question", "nonempty_control", "ok_question", "blocked_question"])
def test_invalid_drafts_never_return_a_partial_candidate(compiled, case, mutation):
    _, incoming, draft = case
    row = draft["payload"]["reads"][0]
    if mutation in {"string_bool", "number_bool"}:
        row["exhaustive"] = "true" if mutation == "string_bool" else 1
    elif mutation in {"extra", "metadata"}:
        draft["execute" if mutation == "extra" else "workflow_version"] = "untrusted"
    elif mutation == "kind": row["subject"]["kind"] = "open_fact"
    elif mutation == "missing_refs": row["subject"]["source_ids"] = []
    elif mutation == "unknown_ref": row["subject"]["source_ids"] = ["invented"]
    elif mutation == "duplicate_ref": row["subject"]["source_ids"] *= 2
    elif mutation == "numeric_ref": row["subject"]["source_ids"] = [1]
    elif mutation == "long_text": row["subject"]["value"] = "가" * 241
    elif mutation == "many_reads": draft["payload"]["reads"] *= 9
    elif mutation == "many_uncertainty": draft["payload"]["uncertainty"] = [leaf("질문")] * 9
    elif mutation == "array_binding": draft["payload"]["reads"] = leaf("조회")
    elif mutation == "missing_question": draft.update(status="needs_input", payload={})
    elif mutation == "nonempty_control": draft["status"] = "blocked"
    elif mutation == "ok_question": draft["missing_inputs"] = ["질문"]
    else: draft.update(status="blocked", payload={}, missing_inputs=["질문"])
    with pytest.raises(ValueError, match="^invalid_core_query_draft$"):
        assemble(compiled, incoming, draft)


@pytest.mark.parametrize("mutation", ["role", "use_case", "request", "schema", "digest", "source_hash", "source_duplicate",
    "extra_input", "extra_pack", "manifest", "workflow", "proof", "unbound"])
def test_input_authority_is_rechecked_before_assembly(compiled, case, mutation):
    _, incoming, draft = case
    pack = incoming["context_pack"]
    if mutation == "role": incoming["role_id"] = "work_architect"
    elif mutation == "use_case": pack["use_case"] = "work_architect"
    elif mutation == "request": incoming["correlation_id"] = "different"
    elif mutation == "schema": pack["payload_schema_json"] += " "
    elif mutation == "digest": pack["payload_schema_sha256"] = "0" * 64
    elif mutation == "source_hash": pack["sources"][0]["content_sha256"] = "0" * 64
    elif mutation == "source_duplicate": pack["sources"] *= 2
    elif mutation == "extra_input": incoming["extra"] = True
    elif mutation == "extra_pack": pack["extra"] = True
    elif mutation == "manifest": pack["knowledge_manifest_sha256"] = "invalid"
    elif mutation == "workflow": incoming["workflow_version"] = "invalid"
    elif mutation == "proof": incoming["knowledge_proof"] = "invalid"
    else: compiled[0]["_DRAFT_SCHEMA"] = None
    with pytest.raises(ValueError, match="^invalid_core_query_draft$"):
        assemble(compiled, incoming, draft)


@pytest.mark.parametrize("raw", ['{"status":"ok","status":"blocked"}', '{"x":NaN}', '[]', '```json\n{}\n```', 'x' * 32769],
                         ids=["duplicate", "nonfinite", "array", "fence", "over_budget"])
def test_strict_json_and_candidate_budget(compiled, case, raw):
    with pytest.raises(ValueError, match="^invalid_core_query_draft$"):
        compiled[0]["main"](encoded(case[1]), raw)
