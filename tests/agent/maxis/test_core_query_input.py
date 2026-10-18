"""Query input is a lossless view, not a replacement source/authority guard."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.agent.maxis.context_guard import MAX_CONTEXT_PACK_UTF8_BYTES
from app.agent.workflow.contracts import role_output_schema


SOURCE = Path(__file__).resolve().parents[3] / "deploy/maxis-dify/code/core_query_input.py"
HIDDEN = {"contract", "payload_schema_json", "payload_schema_sha256", "knowledge_manifest_sha256", "query"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def task_row(pack, text):
    content = canonical({"text": text})
    return {"name": "query_business_input", "context_id": "rctx_" + hashlib.sha256(
        (pack["request_id"] + ":query_specialist:query_business_input").encode()).hexdigest()[:20],
        "content_json": content, "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "observed_at": pack["requested_at"], "partial": False}


def context_view(pack):
    result = {key: value for key, value in pack.items() if key not in HIDDEN}
    result["role_context"] = [row for row in pack["role_context"]
                              if not isinstance(row, dict) or row.get("name") != "query_business_input"]
    result["sources"] = [dict(row,
        source_authority="user_intent" if row["kind"] == "ltm" and row["canonical_id"].startswith("ltm:user-intent:") else "evidence_source",
        allowed_claim_kinds=["inference", "recommendation"] if row["kind"] == "ltm" and row["canonical_id"].startswith("ltm:user-intent:") else ["fact", "inference", "recommendation"])
        for row in pack["sources"]]
    return result


@pytest.fixture
def projector():
    namespace = {"__name__": "core_query_input_test"}
    exec(compile(SOURCE.read_text(encoding="utf-8"), str(SOURCE), "exec"), namespace)
    return namespace


@pytest.fixture
def incoming(context_guard, now):
    pack = context_guard.build(request_id="query-input-test", use_case="query_specialist",
        query="데이터 자산 이력을 찾아줘", payload_schema=role_output_schema("query_specialist"),
        knowledge_manifest_sha256="a" * 64, user_identity="synthetic-user", requested_at=now,
        sources=[{"source_id": "src_query", "kind": "ltm", "canonical_id": "ltm:user-intent:query",
                  "title": "Guarded intent", "scope": "", "content": "데이터 자산 이력을 찾아줘",
                  "retrieved_at": now}])
    dumped = pack.model_dump(mode="json")
    dumped["role_context"] = [task_row(dumped, "데이터 자산 이력을 찾아줘")]
    return {"contract": "ltm-model-input.v1", "context_pack": dumped,
        "role_id": "query_specialist", "correlation_id": pack.request_id,
        "output_contract": "remote-role-result.v1", "workflow_version": "11111111-1111-4111-8111-111111111111",
        "knowledge_version": "kb-test", "knowledge_proof": "LTM_RELEASE_SENTINEL_V1=" + "b" * 64,
        "knowledge_context": [{"document_name": "ticket-rules", "content": "정책: 원문과 근거를 보존한다."}]}


def project(projector, incoming):
    result = projector["main"](canonical(incoming))
    assert set(result) == {"query_input"}
    output = json.loads(result["query_input"])
    assert result["query_input"] == canonical(output)
    return output


def rejected(projector, raw):
    with pytest.raises(ValueError, match="^invalid_core_query_input$"):
        projector["main"](raw)


def test_projection_removes_only_competing_contract_and_metadata(projector, incoming):
    original = copy.deepcopy(incoming)
    output = project(projector, incoming)
    assert output == {"contract": "query-input.v2", "task": {"text": "데이터 자산 이력을 찾아줘"},
        "context": context_view(incoming["context_pack"]),
        "knowledge_context": incoming["knowledge_context"]}
    assert incoming == original
    assert not HIDDEN.intersection(output["context"])
    assert not set(incoming).intersection(output) - {"contract", "knowledge_context"}
    assert projector["_MAX_CONTEXT_PACK_UTF8_BYTES"] == MAX_CONTEXT_PACK_UTF8_BYTES == 6144
    assert projector["_MAX_MODEL_INPUT_UTF8_BYTES"] == 9000


def test_nested_values_and_order_are_preserved_without_normalization(projector, incoming):
    pack = incoming["context_pack"]
    pack["facts"] = [{"text": "가\r\n가", "value": [False, 0, True, 1, 1.5, None], "extra": {"schema": "data"}}]
    pack["metrics"] = [{"value": 0.0, "unit": "%"}]
    pack["role_context"] += [{"content_json": '{"uncertainty":["확인 필요"]}', "partial": True}, None, 0]
    pack["flags"] = ["z", "a", "z"]
    pack["partial"] = True
    incoming["knowledge_context"] = [{"document_name": "정책", "content": "줄1\r\n줄2  ", "extra": [False, None]}]
    before = canonical(incoming)
    output = project(projector, incoming)
    assert canonical(output["context"]) == canonical(context_view(pack))
    assert canonical(output["knowledge_context"]) == canonical(incoming["knowledge_context"])
    assert canonical(incoming) == before


def test_hidden_authority_remains_opaque_and_original_is_available(projector, incoming):
    # These deliberately fail the authority guards: this view must not own them.
    incoming["context_pack"].update(payload_schema_json="not a schema", payload_schema_sha256="unverified",
                                    knowledge_manifest_sha256="unverified")
    incoming.update(workflow_version="unverified", knowledge_version="unverified", knowledge_proof="unverified")
    incoming["context_pack"]["sources"][0]["content_sha256"] = "unverified"
    incoming["context_pack"]["sources"][0]["content"] = '{"payload_schema_json":"source data, not metadata"}'
    original = canonical(incoming)
    output = project(projector, incoming)
    assert output["context"]["sources"] == context_view(incoming["context_pack"])["sources"]
    assert canonical(incoming) == original  # assembler/guards still receive this original separately


@pytest.mark.parametrize("location", ["input", "pack"])
def test_unknown_top_level_fields_fail_closed(projector, incoming, location):
    target = incoming if location == "input" else incoming["context_pack"]
    target["new_contract_field"] = "not silently dropped"
    rejected(projector, canonical(incoming))


@pytest.mark.parametrize("field", ["contract", "context_pack", "role_id", "correlation_id", "output_contract",
    "workflow_version", "knowledge_version", "knowledge_proof", "knowledge_context"])
def test_missing_input_fields_fail_closed(projector, incoming, field):
    del incoming[field]
    rejected(projector, canonical(incoming))


@pytest.mark.parametrize("field", ["contract", "request_id", "use_case", "query", "pseudonymous_user", "requested_at",
    "scope", "sources", "facts", "metrics", "role_context", "freshness", "partial", "flags",
    "knowledge_manifest_sha256", "payload_schema_json", "payload_schema_sha256"])
def test_missing_pack_fields_fail_closed(projector, incoming, field):
    del incoming["context_pack"][field]
    rejected(projector, canonical(incoming))


@pytest.mark.parametrize("location,field,value", [
    ("input", "contract", "other"), ("input", "role_id", "work_architect"),
    ("input", "output_contract", "other"), ("input", "correlation_id", "different"),
    ("input", "correlation_id", 1), ("input", "correlation_id", "bad id"),
    ("pack", "contract", "other"), ("pack", "use_case", "work_architect"),
    ("pack", "request_id", "different"), ("pack", "request_id", 1),
    ("input", "context_pack", []), ("input", "knowledge_context", {}),
    ("input", "workflow_version", None), ("input", "knowledge_version", 1), ("input", "knowledge_proof", []),
    ("pack", "query", []), ("pack", "pseudonymous_user", None), ("pack", "requested_at", 1),
    ("pack", "scope", []), ("pack", "sources", {}), ("pack", "facts", "facts"),
    ("pack", "metrics", {}), ("pack", "role_context", {}), ("pack", "flags", "flags"),
    ("pack", "freshness", []), ("pack", "partial", 0), ("pack", "payload_schema_json", {}),
    ("pack", "payload_schema_sha256", None), ("pack", "knowledge_manifest_sha256", None),
], ids=lambda value: str(value)[:24])
def test_wrong_contract_role_correlation_or_container_type(projector, incoming, location, field, value):
    target = incoming if location == "input" else incoming["context_pack"]
    target[field] = value
    rejected(projector, canonical(incoming))


@pytest.mark.parametrize("request_id", ["", "x" * 129, "한글", "bad\nrequest"],
                         ids=["empty", "long", "unicode", "newline"])
def test_matching_but_invalid_request_ids_are_rejected(projector, incoming, request_id):
    incoming["correlation_id"] = incoming["context_pack"]["request_id"] = request_id
    rejected(projector, canonical(incoming))


@pytest.mark.parametrize("request_id", ["x", "x" * 128], ids=["minimum", "maximum"])
def test_matching_request_id_boundaries_are_preserved(projector, incoming, request_id):
    incoming["correlation_id"] = incoming["context_pack"]["request_id"] = request_id
    incoming["context_pack"]["role_context"] = [task_row(incoming["context_pack"], "데이터 자산 이력을 찾아줘")]
    assert project(projector, incoming)["context"]["request_id"] == request_id


@pytest.mark.parametrize("raw", [None, {}, [], 1, "[]", "null", "{} trailing", "```json\n{}\n```",
    '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}', '{"x":1e999}', '{"x":"\ud800"}'],
    ids=["none", "dict", "list", "number", "array_json", "null", "trailing", "fence", "nan", "inf", "negative_inf", "overflow", "surrogate"])
def test_invalid_json_is_content_free_failure(projector, raw):
    rejected(projector, raw)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_inside_an_otherwise_valid_envelope_is_rejected(projector, incoming, number):
    incoming["knowledge_context"] = []
    raw = canonical(incoming).replace('"knowledge_context":[]', '"knowledge_context":[' + number + ']')
    rejected(projector, raw)


@pytest.mark.parametrize("mutation", ["space", "pretty", "escaped_unicode", "reordered", "duplicate_input", "duplicate_nested"])
def test_noncanonical_and_duplicate_json_fail(projector, incoming, mutation):
    raw = canonical(incoming)
    if mutation == "space": raw += " "
    elif mutation == "pretty": raw = json.dumps(incoming, ensure_ascii=False, indent=2)
    elif mutation == "escaped_unicode": raw = json.dumps(incoming, sort_keys=True, separators=(",", ":"))
    elif mutation == "reordered": raw = json.dumps(dict(reversed(list(incoming.items()))), ensure_ascii=False, separators=(",", ":"))
    elif mutation == "duplicate_input": raw = raw[:-1] + ',"role_id":"query_specialist"}'
    else: raw = raw.replace('"partial":false', '"partial":false,"partial":false', 1)
    rejected(projector, raw)


def padded(incoming, location, field, maximum, unit):
    value = copy.deepcopy(incoming)
    target = value if location == "input" else value["context_pack"]
    target[field] = ""
    measured = value if location == "input" else value["context_pack"]
    remaining = maximum - len(canonical(measured).encode("utf-8"))
    assert remaining >= 0
    width = len(unit.encode("utf-8"))
    target[field] = unit * (remaining // width) + "x" * (remaining % width)
    assert len(canonical(measured).encode("utf-8")) == maximum
    return value


@pytest.mark.parametrize("unit", ["x", "한", "😀"], ids=["ascii", "korean", "emoji"])
def test_utf8_input_limit_is_inclusive(projector, incoming, unit):
    value = padded(incoming, "input", "knowledge_proof", 9000, unit)
    project(projector, value)
    value["knowledge_proof"] += "x"
    rejected(projector, canonical(value))


@pytest.mark.parametrize("unit", ["x", "한", "😀"], ids=["ascii", "korean", "emoji"])
def test_utf8_pack_limit_is_inclusive(projector, incoming, unit):
    value = padded(incoming, "pack", "payload_schema_json", 6144, unit)
    project(projector, value)
    value["context_pack"]["payload_schema_json"] += "x"
    rejected(projector, canonical(value))


@pytest.mark.parametrize("depth,allowed", [(32, True), (33, False), (1100, False)])
def test_depth_boundary_including_parser_recursion_failure(projector, incoming, depth, allowed):
    # Root is depth 0; the scalar below depth-1 arrays is at the requested depth.
    value = copy.deepcopy(incoming)
    value["knowledge_context"] = []
    raw = canonical(value).replace('"knowledge_context":[]',
        '"knowledge_context":' + "[" * (depth - 1) + "0" + "]" * (depth - 1))
    if allowed:
        projector["main"](raw)
    else:
        rejected(projector, raw)


def test_node_budget_is_inclusive_without_relying_on_byte_limit(projector, incoming):
    value = copy.deepcopy(incoming)
    # Small well-shaped envelope leaves enough bytes to exercise the independent node bound.
    for field in ("workflow_version", "knowledge_version", "knowledge_proof"):
        value[field] = ""
    pack = value["context_pack"]
    for field in ("query", "pseudonymous_user", "payload_schema_json",
                  "payload_schema_sha256", "knowledge_manifest_sha256", "freshness"):
        pack[field] = ""
    for field in ("sources", "facts", "metrics", "flags"):
        pack[field] = []
    pack["scope"] = {}
    pack["role_context"] = [task_row(pack, "x")]
    value["knowledge_context"] = []

    def nodes(item):
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else []
        return 1 + sum(nodes(child) for child in children)

    value["knowledge_context"] = [0] * (4096 - nodes(value))
    assert nodes(value) == 4096 and len(canonical(value).encode("utf-8")) < 9000
    project(projector, value)
    value["knowledge_context"].append(0)
    assert len(canonical(value).encode("utf-8")) < 9000
    rejected(projector, canonical(value))


@pytest.mark.parametrize("mutation", [
    "missing", "duplicate", "foreign_name", "extra_field", "id", "time", "partial", "partial_number",
    "hash", "uppercase_hash", "nonstring_content", "invalid_equal_time", "naive_equal_time",
])
def test_business_row_requires_exact_backend_binding(projector, incoming, mutation):
    pack = incoming["context_pack"]
    row = pack["role_context"][0]
    if mutation == "missing": pack["role_context"] = []
    elif mutation == "duplicate": pack["role_context"].append(copy.deepcopy(row))
    elif mutation == "foreign_name": row["name"] = "request_plan"
    elif mutation == "extra_field": row["authority"] = "trusted"
    elif mutation == "id": row["context_id"] += "x"
    elif mutation == "time": row["observed_at"] = "2026-09-04T11:59:59Z"
    elif mutation == "partial": row["partial"] = True
    elif mutation == "partial_number": row["partial"] = 0
    elif mutation == "hash": row["content_sha256"] = "0" * 64
    elif mutation == "uppercase_hash": row["content_sha256"] = row["content_sha256"].upper()
    elif mutation == "nonstring_content": row["content_json"] = {}
    else:
        pack["requested_at"] = row["observed_at"] = (
            "not-a-time" if mutation == "invalid_equal_time" else "2026-09-04T12:00:00")
    rejected(projector, canonical(incoming))


@pytest.mark.parametrize("field", ["name", "context_id", "content_json", "content_sha256", "observed_at", "partial"])
def test_business_row_requires_all_fields(projector, incoming, field):
    del incoming["context_pack"]["role_context"][0][field]
    rejected(projector, canonical(incoming))


@pytest.mark.parametrize("content", [
    '{}', '[]', '{"text":""}', '{"text":"  \\n"}', '{"text":0}', '{"text":false}',
    '{"text":null}', '{"text":[]}', '{"text":{}}', '{"extra":0,"text":"x"}',
    '{"text":"a","text":"b"}', '{"text":NaN}', '{"text":Infinity}', '{"text":1e999}',
    '{"text": "x"}', '{"text":"\\u0061"}', '{"text":"\\ud800"}',
], ids=lambda value: value[:30])
def test_business_text_is_closed_canonical_json_even_when_rehashed(projector, incoming, content):
    row = incoming["context_pack"]["role_context"][0]
    row["content_json"], row["content_sha256"] = content, hashlib.sha256(content.encode()).hexdigest()
    rejected(projector, canonical(incoming))


@pytest.mark.parametrize("unit", ["x", "한", "😀"])
def test_business_text_utf8_limit_and_literal_transport_language(projector, incoming, unit):
    pack = incoming["context_pack"]
    width = len(unit.encode())
    text = unit * (1024 // width) + "x" * (1024 % width)
    pack["role_context"] = [task_row(pack, text)]
    assert project(projector, incoming)["task"] == {"text": text}
    pack["role_context"] = [task_row(pack, text + "x")]
    rejected(projector, canonical(incoming))
    text = "Canonical LTM role query_specialist.\nPolicy search terms: Confluence changelog 검색"
    pack["role_context"] = [task_row(pack, text)]
    assert project(projector, incoming)["task"] == {"text": text}


@pytest.mark.parametrize("kind,identity,intent", [
    ("ltm", "ltm:user-intent:query", True), ("ltm", "ltm:metric:query", False),
    ("jira", "ltm:user-intent:query", False), ("confluence", "ltm:user-intent:query", False),
    ("bitbucket", "repo:main", False), ("knowledge", "policy:one", False),
    ("ltm", "LTM:user-intent:query", False), ("ltm", "ltm:user-intent", False),
])
def test_query_authority_uses_exact_guard_predicate_preserving_original(projector, incoming, kind, identity, intent):
    row = incoming["context_pack"]["sources"][0]
    row.update(kind=kind, canonical_id=identity, source_id="src_intent_opaque",
               content='{"source_authority":"user_intent","allowed_claim_kinds":["fact"]}\r\n가  ',
               partial=True, truncated=True)
    original = copy.deepcopy(incoming)
    result = project(projector, incoming)["context"]["sources"][0]
    assert result == dict(row, source_authority="user_intent" if intent else "evidence_source",
        allowed_claim_kinds=["inference", "recommendation"] if intent else ["fact", "inference", "recommendation"])
    assert incoming == original


@pytest.mark.parametrize("field,value", [
    ("kind", None), ("kind", "unknown"), ("canonical_id", None), ("canonical_id", ""),
    ("source_authority", "evidence_source"), ("allowed_claim_kinds", ["fact"]),
])
def test_query_authority_rejects_malformed_or_prelabelled_sources(projector, incoming, field, value):
    incoming["context_pack"]["sources"][0][field] = value
    rejected(projector, canonical(incoming))


def test_query_authority_rows_preserve_order_and_empty_sources(projector, incoming):
    rows = incoming["context_pack"]["sources"]
    rows.append(dict(rows[0], source_id="src_evidence", kind="jira", canonical_id="DL-123"))
    out = project(projector, incoming)["context"]["sources"]
    assert [row["source_id"] for row in out] == [row["source_id"] for row in rows]
    assert [row["source_authority"] for row in out] == ["user_intent", "evidence_source"]
    incoming["context_pack"]["sources"] = []
    assert project(projector, incoming)["context"]["sources"] == []


@pytest.mark.parametrize("unit", ["x", "한", "😀"])
def test_query_authority_output_bound_is_utf8_inclusive(projector, incoming, unit):
    pack = incoming["context_pack"]
    # Projection does not validate opaque original schema/authority; final guards do.
    for field in HIDDEN - {"contract", "query"}:
        pack[field] = ""
    row = pack["sources"][0]
    pack["sources"] = [dict(row, source_id=f"src_{i}") for i in range(9)]
    incoming["knowledge_context"] = [{"content": ""}]
    expected = {"contract": "query-input.v2", "task": {"text": "데이터 자산 이력을 찾아줘"},
                "context": context_view(pack), "knowledge_context": incoming["knowledge_context"]}
    remaining = 9000 - len(canonical(expected).encode("utf-8"))
    assert remaining > 0
    width = len(unit.encode("utf-8"))
    incoming["knowledge_context"][0]["content"] = unit * (remaining // width) + "x" * (remaining % width)
    assert len(canonical(incoming).encode("utf-8")) <= 9000
    assert len(canonical(project(projector, incoming)).encode("utf-8")) == 9000
    incoming["knowledge_context"][0]["content"] += "x"
    assert len(canonical(incoming).encode("utf-8")) <= 9000
    rejected(projector, canonical(incoming))
