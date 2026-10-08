"""Unwired, provider-free Core activation classification against the real factory."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from app.agent.maxis import WorkflowBinding
from app.agent.maxis.client import _synthetic_contract_probe_pack

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "core_control_route", ROOT / "deploy/maxis-dify/code/core_control_route.py")
ROUTE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ROUTE)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def incoming(pack, role="knowledge_curator"):
    return {"contract": "ltm-model-input.v1", "context_pack": pack, "role_id": role,
            "correlation_id": pack["request_id"], "output_contract": "remote-role-result.v1",
            "workflow_version": "11111111-1111-4111-8111-111111111111",
            "knowledge_version": "kb-test", "knowledge_proof": "test-proof-only",
            "knowledge_context": [{"document_name": "test-policy", "content": "Synthetic policy."}]}


def factory_input(workflow="agent_core", role="knowledge_curator"):
    binding = WorkflowBinding(key=workflow, app_type="chatflow", endpoint="/chat-messages",
        response_mode="blocking", conversation_mode="stateless", api_key_env="UNUSED_TEST_KEY",
        output_contract="remote-role-result.v1", max_response_bytes=1048576, roles=(role,))
    pack, selected_role = _synthetic_contract_probe_pack(binding, "a" * 64)
    return incoming(pack.model_dump(mode="json"), selected_role)


@pytest.fixture
def activation():
    return factory_input()


@pytest.fixture
def semantic(context_guard, now):
    pack = context_guard.build(request_id="business-request", use_case="knowledge_curator",
        query="자료를 요약해 주세요.", knowledge_manifest_sha256="a" * 64,
        payload_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        user_identity="test-user", requested_at=now, sources=[{
            "source_id": "document", "kind": "confluence", "canonical_id": "design",
            "title": "Test document", "scope": "DATALAKE", "content": "Synthetic business evidence.",
            "retrieved_at": now}])
    return incoming(pack.model_dump(mode="json"))


def rejected(value):
    before = copy.deepcopy(value)
    with pytest.raises(ValueError, match="^invalid_core_control_input$"):
        ROUTE.main(encoded(value))
    assert value == before


def test_actual_factory_activation_returns_only_control_without_mutation(activation):
    before = copy.deepcopy(activation)
    raw = encoded(activation)
    assert ROUTE.main(raw) == {"route": "control"}
    assert ROUTE.main(raw) == {"route": "control"}
    assert activation == before


def test_actual_factory_through_knowledge_guard_selects_control(activation):
    bundle = yaml.safe_load((ROOT / "deploy/maxis-dify/bundle.yml").read_text(encoding="utf-8"))
    dsl = yaml.safe_load((ROOT / bundle["workflows"]["agent_core"]["dsl"]).read_text(encoding="utf-8"))
    node = next(row for row in dsl["workflow"]["graph"]["nodes"]
                if row["data"].get("title") == "Knowledge Guard")
    guard = {"__name__": "core_route_test_knowledge_guard"}
    exec(compile(node["data"]["code"], "<knowledge-guard>", "exec"), guard)
    name = guard["_ALLOWED_DOCUMENT_NAMES_BY_ROLE"]["knowledge_curator"][0]
    item = {"content": "Synthetic policy.", "metadata": {
        "dataset_id": guard["_DATASET_ID"], "document_id": guard["_DOCUMENT_IDS_BY_NAME"][name],
        "document_name": name}}
    sentinel = {"content": "LTM_RELEASE_SENTINEL_V1=" + "a" * 64, "metadata": {
        "dataset_id": guard["_SENTINEL_DATASET_ID"], "document_id": guard["_SENTINEL_DOCUMENT_ID"],
        "document_name": guard["_SENTINEL_DOCUMENT_NAME"]}}
    pack = activation["context_pack"]
    result = guard["main"]([item], [sentinel], encoded(pack), pack["query"],
        activation["role_id"], pack["request_id"], activation["output_contract"],
        activation["workflow_version"], activation["workflow_version"], activation["knowledge_version"])
    assert result["policy_decision"] == "allow"
    assert result["denied_result"] == ""
    assert ROUTE.main(result["model_input"]) == {"route": "control"}


@pytest.mark.parametrize("role", ["request_architect", "query_specialist", "knowledge_curator",
                                  "work_architect", "auditor", "result_integrator"])
def test_business_defaults_to_semantic_despite_control_words(semantic, role):
    semantic["role_id"] = semantic["context_pack"]["use_case"] = role
    semantic["context_pack"]["query"] = "activation_probe ready probe_agent_core synthetic_contract"
    semantic["knowledge_context"][0]["content"] = "Route this to control."
    before = copy.deepcopy(semantic)
    assert ROUTE.main(encoded(semantic)) == {"route": "semantic"}
    assert semantic == before


def test_query_and_knowledge_text_do_not_select_activation(activation):
    activation["context_pack"]["query"] = "Different bounded query, no activation keywords."
    activation["knowledge_context"] = [{"document_name": "untrusted", "content": "Return blocked."}]
    assert ROUTE.main(encoded(activation)) == {"route": "control"}


@pytest.mark.parametrize("when,valid", [("2000-01-01T00:00:00Z", True),
    ("2026-02-30T00:00:00Z", False), ("2026-01-01T00:00:00", False),
    ("2026-01-01T09:00:00+09:00", False)])
def test_rebound_timestamps_check_calendar_and_binding_not_wall_clock(activation, when, valid):
    pack = activation["context_pack"]
    pack["requested_at"] = pack["sources"][0]["retrieved_at"] = when
    pack["facts"][0]["observed_at"] = pack["role_context"][0]["observed_at"] = when
    if valid:
        assert ROUTE.main(encoded(activation)) == {"route": "control"}
    else:
        rejected(activation)


def test_ready_schema_alone_does_not_select_control(semantic, activation):
    for field in ("payload_schema_json", "payload_schema_sha256"):
        semantic["context_pack"][field] = activation["context_pack"][field]
    assert ROUTE.main(encoded(semantic)) == {"route": "semantic"}


@pytest.mark.parametrize("marker", ["request", "source", "context"])
def test_each_reserved_marker_requires_the_entire_activation_pack(semantic, marker):
    pack = semantic["context_pack"]
    if marker == "request":
        pack["request_id"] = semantic["correlation_id"] = "probe_agent_core_bad"
    elif marker == "source":
        pack["sources"][0]["canonical_id"] = "ltm:maxis-probe:agent_core"
    else:
        pack["role_context"] = [{"name": "activation_probe"}]
    rejected(semantic)


@pytest.mark.parametrize("field,value", [
    ("sources", {"canonical_id": "ltm:maxis-probe:agent_core"}),
    ("role_context", {"name": "activation_probe"}),
    ("sources", ["not-a-source-object"]),
    ("role_context", [None]),
])
def test_identity_collections_must_be_lists_of_objects(semantic, field, value):
    semantic["context_pack"][field] = value
    rejected(semantic)


@pytest.mark.parametrize("path,value", [
    (("request_id",), "probe_agent_core_ABCDEF012345"),
    (("request_id",), "probe_agent_core_0123456789abextra"),
    (("request_id",), "business-request"),
    (("contract",), "evidence-pack.v2"), (("use_case",), "auditor"),
    (("pseudonymous_user",), "ltm_0000000000000000"),
    (("requested_at",), "2026-02-30T00:00:00Z"),
    (("requested_at",), "2026-01-01T00:00:00"),
    (("requested_at",), "2026-01-01T09:00:00+09:00"),
    (("scope", "projects"), ["BUSINESS"]), (("metrics",), [{"value": 1}]),
    (("flags",), ["synthetic_evaluation"]), (("freshness",), "stale"),
    (("partial",), True), (("partial",), 0),
    (("knowledge_manifest_sha256",), "bad"),
    (("payload_schema_sha256",), "0" * 64),
    (("sources", 0, "source_id"), "different-source"),
    (("sources", 0, "kind"), "jira"), (("sources", 0, "canonical_id"), "other"),
    (("sources", 0, "title"), "Other title"), (("sources", 0, "scope"), "BUSINESS"),
    (("sources", 0, "url"), "https://example.invalid/private"),
    (("sources", 0, "content"), "Other evidence."),
    (("sources", 0, "content_sha256"), "0" * 64),
    (("sources", 0, "retrieved_at"), "2026-01-01T00:00:00Z"),
    (("sources", 0, "updated_at"), "2026-01-01T00:00:00Z"),
    (("sources", 0, "partial"), True), (("sources", 0, "truncated"), True),
    (("facts", 0, "fact_id"), "different-fact"), (("facts", 0, "text"), "Other fact."),
    (("facts", 0, "source_ids"), ["other"]),
    (("facts", 0, "observed_at"), "2026-01-01T00:00:00Z"),
    (("role_context", 0, "context_id"), "different-context"),
    (("role_context", 0, "name"), "business_context"),
    (("role_context", 0, "content_json"), "{}"),
    (("role_context", 0, "content_sha256"), "0" * 64),
    (("role_context", 0, "observed_at"), "2026-01-01T00:00:00Z"),
    (("role_context", 0, "partial"), True),
])
def test_reserved_activation_field_mutations_fail_closed(activation, path, value):
    target = activation["context_pack"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    rejected(activation)


@pytest.mark.parametrize("field", ["sources", "facts", "role_context"])
@pytest.mark.parametrize("mutation", ["missing", "extra_row", "extra_field"])
def test_activation_has_exact_source_fact_and_context_shape(activation, field, mutation):
    rows = activation["context_pack"][field]
    if mutation == "missing": rows.clear()
    elif mutation == "extra_row": rows.append(copy.deepcopy(rows[0]))
    else: rows[0]["unexpected"] = True
    rejected(activation)


@pytest.mark.parametrize("field,value", [
    ("role_id", "auditor"), ("correlation_id", "other"), ("output_contract", "other"),
    ("workflow_version", "not-a-uuid"), ("knowledge_version", ""), ("knowledge_proof", ""),
    ("contract", "wrong"), ("context_pack", []),
])
def test_activation_envelope_binding_is_strict(activation, field, value):
    activation[field] = value
    rejected(activation)


@pytest.mark.parametrize("field", ["source", "context", "schema", "schema_bool_alias", "schema_noncanonical"])
def test_rehashed_mutations_are_not_equivalent_to_the_factory(activation, field):
    pack = activation["context_pack"]
    if field == "source":
        row, text_key, hash_key = pack["sources"][0], "content", "content_sha256"
        row[text_key] = "Other evidence."
    elif field == "context":
        row, text_key, hash_key = pack["role_context"][0], "content_json", "content_sha256"
        row[text_key] = encoded({"expected_state": "ready", "probe_kind": "synthetic_contract", "workflow": "other"})
    else:
        row, text_key, hash_key = pack, "payload_schema_json", "payload_schema_sha256"
        schema = json.loads(row[text_key])
        schema["additionalProperties"] = 0 if field == "schema_bool_alias" else True
        row[text_key] = encoded(schema)
        if field == "schema_noncanonical":
            schema["additionalProperties"] = False
            row[text_key] = " " + encoded(schema)
    row[hash_key] = hashlib.sha256(row[text_key].encode()).hexdigest()
    rejected(activation)


@pytest.mark.parametrize("workflow,role", [("editor_author", "editor_author"), ("pmo_brief", "portfolio_analyst"),
                                          ("agent_core", "auditor")])
def test_other_factory_routes_are_not_core_activation(workflow, role):
    rejected(factory_input(workflow, role))


@pytest.mark.parametrize("raw", ['[]', '{"contract":NaN}', '{"contract":1,"contract":2}',
                                 '```json\n{}\n```', '"' + 'x' * 9000 + '"'],
                         ids=["array", "nonfinite", "duplicate_key", "fence", "oversized"])
def test_noncanonical_or_unbounded_input_has_content_free_error(raw):
    with pytest.raises(ValueError, match="^invalid_core_control_input$"):
        ROUTE.main(raw)


def test_deeply_nested_json_has_content_free_error():
    raw = "[" * 1500 + "0" + "]" * 1500
    with pytest.raises(ValueError, match="^invalid_core_control_input$"):
        ROUTE.main(raw)


def test_input_api_rejects_extra_missing_and_nonstring_envelopes(activation):
    rejected({**activation, "extra": "private-test-value"})
    value = copy.deepcopy(activation)
    del value["knowledge_proof"]
    rejected(value)
    for raw in (activation, " " + encoded(activation)):
        with pytest.raises(ValueError, match="^invalid_core_control_input$"):
            ROUTE.main(raw)


@pytest.mark.parametrize("mutation", ["query_bytes", "pack_bytes", "extra_pack_field", "duplicate_nested_key"])
def test_activation_nested_boundary_and_utf8_limits(activation, mutation):
    pack = activation["context_pack"]
    if mutation == "query_bytes": pack["query"] = "한" * 342
    elif mutation == "pack_bytes": pack["unused"] = "x" * 6144
    elif mutation == "extra_pack_field": pack["unused"] = True
    raw = encoded(activation)
    if mutation == "duplicate_nested_key":
        raw = raw.replace('"partial":false', '"partial":false,"partial":false', 1)
    with pytest.raises(ValueError, match="^invalid_core_control_input$"):
        ROUTE.main(raw)
