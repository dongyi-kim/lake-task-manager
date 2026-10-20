"""Query input -> native typed branch -> unchanged remote/local guards; no provider."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from jsonschema import Draft202012Validator

from app.agent.maxis import ResponseGuard, ResponseGuardError
from app.agent.maxis import backend
from app.agent.tools import _ctx
from app.agent.maxis.contracts import canonical_payload_schema
from app.agent.workflow.contracts import CompactQueryPlan, role_output_schema

ROOT = Path(__file__).resolve().parents[3]
QUERY_ID = "1909000000001028"
ASSEMBLER_ID = "1909000000001029"
INPUT_ID = "1909000000001030"
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
def query_case(now, nodes, monkeypatch):
    class Clock(backend.datetime):
        @staticmethod
        def now(tz):
            return now

    monkeypatch.setattr(backend, "datetime", Clock)
    monkeypatch.setattr(backend, "_known_identity_values", lambda *_: ())
    monkeypatch.setattr(_ctx, "settings", lambda: SimpleNamespace(
        search_jira_projects=("DL",), search_confluence_spaces=("DATALAKE",),
        search_bitbucket_projects=(), jira_env="mock", jira_user="synthetic-user", managers=()))
    state = {"correlation_id": "query-business", "correlation_started_at": now.isoformat(),
        "thread_id": "query-thread", "user_id": "synthetic-user",
        "request_text": "데이터 자산 이력을 찾아줘", "knowledge_manifest_sha256": "a" * 64,
        "runtime_profile": "synthetic-component-fixture"}
    pack = backend._context_pack("query_specialist", state, [], role_output_schema("query_specialist"))
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
        "citations": [{"citation_id": "cite", "source_id": pack.sources[0].source_id, "locator": ""}],
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


def draft(candidate):
    """Synthetic semantic values; the model never authors IDs or JSON pointers."""
    payload = copy.deepcopy(candidate["payload"])
    wrap = lambda value: {"value": value, "kind": "recommendation",
                          "source_ids": [candidate["citations"][0]["source_id"]]}
    for read in payload.get("reads", []):
        for field in ("source", "subject", "where"):
            if field in read:
                read[field] = wrap(read[field])
        if "exhaustive" in read:
            value = read.pop("exhaustive")
            assert type(value) is bool
            read["coverage"] = "exhaustive" if value else "standard"
    if "uncertainty" in payload:
        payload["uncertainty"] = [wrap(value) for value in payload["uncertainty"]]
    return {"contract": "query-draft.v2", "status": candidate["status"], "payload": payload,
            "missing_inputs": list(candidate["missing_inputs"])}


def assemble(nodes, model_input, selected):
    return json.loads(code(nodes[ASSEMBLER_ID])["main"](model_input, encoded(selected))["candidate"])


@pytest.mark.parametrize("source", ["web", "github"])
def test_runtime_capability_violation_is_rejected_in_native_branch(nodes, query_case, source):
    _, model_input, candidate = query_case
    incoming = json.loads(model_input)
    business = next(row for row in incoming["context_pack"]["role_context"]
                    if row["name"] == "query_business_input")
    task = json.loads(business["content_json"])
    task["allowed_sources"] = ["comments", "confluence", "jira", "people"]
    business["content_json"] = encoded(task)
    business["content_sha256"] = hashlib.sha256(encoded(task).encode()).hexdigest()
    selected = draft(candidate)
    selected["payload"]["reads"][0]["source"]["value"] = source
    with pytest.raises(ValueError, match="^invalid_core_query_source$"):
        assemble(nodes, encoded(incoming), selected)


def test_query_native_contract_is_resolved_and_both_guards_accept_complete_candidate(nodes, query_case):
    pack, model_input, candidate = query_case
    assert code(nodes["1909000000001024"])["main"](model_input) == {"route": "query"}
    schema = nodes[QUERY_ID]["structured_output"]["schema"]
    assert "$ref" not in encoded(schema) and "$defs" not in encoded(schema)
    assert set(schema["properties"]) == {"contract", "status", "payload", "missing_inputs"}
    selected = draft(candidate)
    Draft202012Validator(schema).validate(selected)
    assembled = assemble(nodes, model_input, selected)
    assert assembled["payload"] == candidate["payload"]
    assert remote(nodes, pack, assembled) == assembled
    result, payload = local(pack, assembled)
    assert result.status == "ok" and payload.reads[0].exhaustive is True


@pytest.mark.parametrize("bad", ["true", "false", "TRUE", 0, 1, None])
def test_query_native_schema_and_both_guards_reject_non_boolean_without_coercion(nodes, query_case, bad):
    pack, model_input, candidate = query_case
    selected = draft(candidate)
    selected["payload"]["reads"][0]["coverage"] = bad
    candidate["payload"]["reads"][0]["exhaustive"] = bad
    before = copy.deepcopy(candidate)
    assert not Draft202012Validator(nodes[QUERY_ID]["structured_output"]["schema"]).is_valid(selected)
    with pytest.raises(ValueError, match="invalid_core_query_draft"):
        assemble(nodes, model_input, selected)
    assert remote(nodes, pack, candidate)["warnings"] == ["contract_guard:payload_type_mismatch"]
    with pytest.raises(ResponseGuardError):
        local(pack, candidate)
    assert candidate == before


@pytest.mark.parametrize("value", [False, True])
def test_query_boolean_domain_survives_dify_native_schema_preparation(nodes, query_case, value):
    # Dify 1.11.1 structured_output.py::convert_boolean_to_string rewrites type
    # declarations recursively before the native call. The draft uses an explicit
    # string enum; only the assembler emits the canonical JSON boolean.
    # Simulate that documented transformation, not an assumed provider response.
    schema = nodes[QUERY_ID]["structured_output"]["schema"]
    transformed = json.loads(encoded(schema), object_hook=lambda row:
        {**row, "type": "string"} if row.get("type") == "boolean" else row)
    assert transformed == schema
    pack, model_input, candidate = query_case
    candidate["payload"]["reads"][0]["exhaustive"] = value
    selected = draft(candidate)
    assert selected["payload"]["reads"][0]["coverage"] == ("exhaustive" if value else "standard")
    assert "exhaustive" not in selected["payload"]["reads"][0]
    Draft202012Validator(transformed).validate(selected)
    assembled = assemble(nodes, model_input, selected)
    assert remote(nodes, pack, assembled) == assembled
    assert local(pack, assembled)[1].reads[0].exhaustive is value
    assert all(not row["path"].endswith("/exhaustive") for row in assembled["payload_claims"])
    canonical = json.loads(pack.payload_schema_json)
    assert canonical["$defs"]["QueryIntent"]["properties"]["exhaustive"]["type"] == "boolean"


@pytest.mark.parametrize("bad", ["", False, True, "Standard", "exhaustive ", "all"])
def test_embedded_coverage_adapter_never_guesses_an_unknown_choice(nodes, query_case, bad):
    _, model_input, candidate = query_case
    selected = draft(candidate)
    selected["payload"]["reads"][0]["coverage"] = bad
    before = copy.deepcopy(selected)
    assert not Draft202012Validator(nodes[QUERY_ID]["structured_output"]["schema"]).is_valid(selected)
    with pytest.raises(ValueError, match="invalid_core_query_draft"):
        assemble(nodes, model_input, selected)
    assert selected == before


@pytest.mark.parametrize("mutation", ["old_contract", "old_boolean_field", "both_fields"])
def test_embedded_coverage_adapter_rejects_legacy_or_mixed_wire_contract(nodes, query_case, mutation):
    _, model_input, candidate = query_case
    selected = draft(candidate)
    read = selected["payload"]["reads"][0]
    if mutation == "old_contract":
        selected["contract"] = "query-draft.v1"
    else:
        read["exhaustive"] = True
        if mutation == "old_boolean_field":
            del read["coverage"]
    assert not Draft202012Validator(nodes[QUERY_ID]["structured_output"]["schema"]).is_valid(selected)
    with pytest.raises(ValueError, match="invalid_core_query_draft"):
        assemble(nodes, model_input, selected)


@pytest.mark.parametrize("status", ["blocked", "needs_input"])
def test_query_native_contract_preserves_empty_control_outcomes(nodes, query_case, status):
    pack, model_input, candidate = query_case
    candidate.update(status=status, payload={}, claims=[], payload_claims=[], citations=[],
                     missing_inputs=["조회 대상을 알려주세요"] if status == "needs_input" else [])
    selected = draft(candidate)
    Draft202012Validator(nodes[QUERY_ID]["structured_output"]["schema"]).validate(selected)
    assembled = assemble(nodes, model_input, selected)
    assert remote(nodes, pack, assembled) == assembled
    assert local(pack, assembled)[0].status == status
    assert all(assembled[key] == empty for key, empty in (
        ("payload", {}), ("claims", []), ("payload_claims", []), ("citations", [])))


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
    assert remote(nodes, pack, candidate)["status"] == "blocked"
    with pytest.raises(ResponseGuardError):
        local(pack, candidate)


def test_assembler_never_relabels_intent_facts_to_make_them_acceptable(nodes, query_case):
    pack, model_input, candidate = query_case
    selected = draft(candidate)
    selected["payload"]["reads"][0]["subject"]["kind"] = "fact"
    assembled = assemble(nodes, model_input, selected)
    assert any(row["kind"] == "fact" and row["text"] == "데이터 자산" for row in assembled["claims"])
    assert remote(nodes, pack, assembled)["warnings"] == ["contract_guard:intent_source_fact_forbidden"]
    with pytest.raises(ResponseGuardError, match="intent_source_fact_forbidden"):
        local(pack, assembled)


@pytest.mark.parametrize("source,where", [
    ("jira", "status = Open"), ("confluence", 'label = "release"'),
    ("comments", "status = Open"), ("people", "ETL"), ("confluence", None),
])
def test_typed_plan_keeps_lexical_subject_and_structural_filter_through_guards_and_compiler(nodes, query_case, source, where):
    from app.agent.workflow.agents.query_specialist import _compile_compact_query_plan
    pack, model_input, candidate = query_case
    selected = draft(candidate)
    read = selected["payload"]["reads"][0]
    read["source"]["value"], read["subject"]["value"] = source, "배포 이력"
    if where is None:
        del read["where"]
    else:
        read["where"]["value"] = where
    assembled = assemble(nodes, model_input, selected)
    assert remote(nodes, pack, assembled) == assembled
    assert local(pack, assembled)[0].status == "ok"
    runtime = _compile_compact_query_plan(assembled["payload"])["queries"][0]
    assert runtime["source"] == source and runtime["query"] == "배포 이력"
    assert runtime["where"] == (where or "") and runtime["completeness"] == "all"
    assert ("where" in assembled["payload"]["reads"][0]) is (where is not None)


def test_query_unit_does_not_receive_conflicting_remote_envelope_instructions(nodes):
    prompt = nodes[QUERY_ID]["prompt_template"][0]["text"]
    local_role = (ROOT / "app/agent/prompts/roles/query_specialist.md").read_text(encoding="utf-8")
    titles = ("Query Design", "Scope and Tool Contract", "Preflight Check")
    positions = []
    for title in titles:
        block = ("## " + title + "\n" + local_role.split("## " + title + "\n", 1)[1].split("\n## ", 1)[0]).rstrip("\n")
        assert prompt.count(block) == 1
        positions.append(prompt.index(block))
    assert positions == sorted(positions)
    assert positions[-1] < prompt.index("## Output: `query-draft.v2` only")
    assert all("## " + title not in prompt for title in ("Inputs", "Output Contract", "Stop and Escalate"))
    assert "Set `exhaustive=true`" not in prompt
    assert "query-draft.v2" in prompt and "## Required response" not in prompt
    assert "Echo input `knowledge_proof`" not in prompt


def test_query_citation_instruction_names_the_exact_identifier_field(nodes, query_case):
    prompt = nodes[QUERY_ID]["prompt_template"][0]["text"]
    assert "context.sources[].source_id" in prompt
    assert "canonical_id" in prompt and "not a citation ID" in prompt
    pack, model_input, candidate = query_case
    selected = draft(candidate)
    selected["payload"]["reads"][0]["source"]["source_ids"] = [pack.sources[0].canonical_id]
    with pytest.raises(ValueError, match="invalid_core_query_draft"):
        assemble(nodes, model_input, selected)
    selected["payload"]["reads"][0]["source"]["source_ids"] = [pack.sources[0].source_id]
    assembled = assemble(nodes, model_input, selected)
    assert remote(nodes, pack, assembled) == assembled
    assert local(pack, assembled)[0].status == "ok"


def test_query_projection_preserves_task_and_evidence_but_excludes_final_schema(nodes, query_case):
    pack, model_input, candidate = query_case
    original = json.loads(model_input)
    projected = json.loads(code(nodes[INPUT_ID])["main"](model_input)["query_input"])
    assert projected["contract"] == "query-input.v2"
    assert projected["task"] == {"text": "데이터 자산 이력을 찾아줘"}
    assert pack.query != projected["task"]["text"] and "Policy search terms:" in pack.query
    assert projected["knowledge_context"] == original["knowledge_context"]
    hidden = {"contract", "payload_schema_json", "payload_schema_sha256", "knowledge_manifest_sha256", "query"}
    expected = {key: value for key, value in original["context_pack"].items() if key not in hidden}
    expected["role_context"] = [row for row in expected["role_context"] if row["name"] != "query_business_input"]
    expected["sources"] = [dict({key: value for key, value in row.items()
        if key != "content_sha256" and not (key == "canonical_id"
            and row["kind"] == "ltm" and row["canonical_id"].startswith("ltm:user-intent:"))},
        source_authority="user_intent" if row["kind"] == "ltm" and row["canonical_id"].startswith("ltm:user-intent:") else "evidence_source",
        allowed_claim_kinds=["inference", "recommendation"] if row["kind"] == "ltm" and row["canonical_id"].startswith("ltm:user-intent:") else ["fact", "inference", "recommendation"])
        for row in expected["sources"]]
    assert projected["context"] == expected
    assert all(key not in projected["context"] for key in hidden)
    assert original["context_pack"] == pack.model_dump(mode="json")
    assert "query-input.v2" in nodes[QUERY_ID]["prompt_template"][0]["text"]
    assert nodes[QUERY_ID]["prompt_template"][1]["text"].endswith("{{#1909000000001030.query_input#}}")
    # Projection is not a replacement for the complete original assembler input.
    with pytest.raises(ValueError, match="invalid_core_query_draft"):
        assemble(nodes, encoded(projected), draft(candidate))
    assembled = assemble(nodes, model_input, draft(candidate))
    assert remote(nodes, pack, assembled) == assembled
    assert local(pack, assembled)[0].status == "ok"


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "hash", "id", "time", "partial", "closed_shape"])
def test_embedded_projection_rejects_malformed_backend_row_without_transport_fallback(nodes, query_case, mutation):
    _, model_input, _ = query_case
    incoming = json.loads(model_input)
    rows = incoming["context_pack"]["role_context"]
    row = next(row for row in rows if row["name"] == "query_business_input")
    if mutation == "missing": rows.remove(row)
    elif mutation == "duplicate": rows.append(copy.deepcopy(row))
    elif mutation == "hash": row["content_sha256"] = "0" * 64
    elif mutation == "id": row["context_id"] += "x"
    elif mutation == "time": row["observed_at"] = "2026-09-04T11:59:59Z"
    elif mutation == "partial": row["partial"] = True
    else:
        row["content_json"] = '{"extra":0,"text":"x"}'
        row["content_sha256"] = hashlib.sha256(row["content_json"].encode()).hexdigest()
    original = encoded(incoming)
    with pytest.raises(ValueError, match="^invalid_core_query_input$"):
        code(nodes[INPUT_ID])["main"](original)
    assert encoded(incoming) == original


def test_query_uncertainty_requires_wrapped_meaning_without_string_repair(nodes, query_case):
    pack, model_input, candidate = query_case
    selected = draft(candidate)
    selected["payload"]["uncertainty"] = ["조회 기간 확인 필요"]
    assert not Draft202012Validator(nodes[QUERY_ID]["structured_output"]["schema"]).is_valid(selected)
    with pytest.raises(ValueError, match="invalid_core_query_draft"):
        assemble(nodes, model_input, selected)
    selected["payload"]["uncertainty"] = [
        {"value": "조회 기간 확인 필요", "kind": "recommendation", "source_ids": [pack.sources[0].source_id]}]
    assembled = assemble(nodes, model_input, selected)
    assert assembled["payload"]["uncertainty"] == ["조회 기간 확인 필요"]
    assert remote(nodes, pack, assembled) == assembled
    assert local(pack, assembled)[0].status == "ok"


@pytest.mark.parametrize("mask", range(16))
def test_embedded_assembler_preserves_every_optional_field_omission(nodes, query_case, mask):
    pack, model_input, candidate = query_case
    read = {"source": "confluence"}
    for index, (field, value) in enumerate((("subject", "데이터 자산"), ("where", "변경 이력"), ("exhaustive", True))):
        if mask & (1 << index):
            read[field] = value
    expected = {"reads": [read]}
    if mask & 8:
        expected["uncertainty"] = ["조회 기간 확인 필요"]
    candidate["payload"] = copy.deepcopy(expected)
    selected = draft(candidate)
    Draft202012Validator(nodes[QUERY_ID]["structured_output"]["schema"]).validate(selected)
    assembled = assemble(nodes, model_input, selected)
    assert assembled["payload"] == expected
    assert remote(nodes, pack, assembled) == assembled
    assert local(pack, assembled)[0].status == "ok"


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


def test_query_authority_reaches_selected_llm_frame_without_replacing_original(nodes, query_case):
    _, model_input, _ = query_case
    output = code(nodes[INPUT_ID])["main"](model_input)["query_input"]
    user_template = nodes[QUERY_ID]["prompt_template"][1]["text"]
    marker = "{{#1909000000001030.query_input#}}"
    assert user_template.count(marker) == 1
    user_frame = user_template.replace(marker, output)
    visible = json.loads(user_frame.split("\n", 1)[1])
    assert visible == json.loads(output)
    assert visible["context"]["sources"][0]["source_authority"] == "user_intent"
    assert visible["context"]["sources"][0]["allowed_claim_kinds"] == ["inference", "recommendation"]
    assert nodes[ASSEMBLER_ID]["variables"] == [
        {"variable": "model_input", "value_selector": ["1909000000001006", "model_input"]},
        {"variable": "candidate", "value_selector": [QUERY_ID, "text"]}]
    assert "source_authority" not in json.loads(model_input)["context_pack"]["sources"][0]


@pytest.mark.parametrize("subject_kind", ["fact", "recommendation"])
def test_query_observed_draft_shape_retains_kind_and_original_guard_verdict(nodes, query_case, subject_kind):
    # Reconstruct observed draft VALUES on synthetic local evidence, not a native replay.
    pack, model_input, _ = query_case
    incoming = json.loads(model_input)
    row = incoming["context_pack"]["sources"][0]
    row.update(source_id="src_intent_d48024cf581e43c943d7", content="fdc.fdc_trace_summary_ic 이력을 찾아줘")
    row["content_sha256"] = hashlib.sha256(row["content"].encode()).hexdigest()
    pack = type(pack).model_validate_json(encoded(incoming["context_pack"]))
    selected = {"contract": "query-draft.v2", "status": "ok", "payload": {"reads": [{
        "source": {"value": "jira", "kind": "recommendation", "source_ids": [row["source_id"]]},
        "subject": {"value": "fdc.fdc_trace_summary_ic", "kind": subject_kind, "source_ids": [row["source_id"]]},
        "coverage": "standard"}]}, "missing_inputs": []}
    before = copy.deepcopy(selected)
    assembled = assemble(nodes, encoded(incoming), selected)
    assert selected == before
    assert any(c["text"] == "fdc.fdc_trace_summary_ic" and c["kind"] == subject_kind for c in assembled["claims"])
    if subject_kind == "fact":
        assert remote(nodes, pack, assembled)["warnings"] == ["contract_guard:intent_source_fact_forbidden"]
        with pytest.raises(ResponseGuardError, match="intent_source_fact_forbidden"):
            local(pack, assembled)
    else:
        assert remote(nodes, pack, assembled) == assembled
        assert local(pack, assembled)[0].status == "ok"
