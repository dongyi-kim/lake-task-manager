"""Request metadata projection; unchanged original input still owns acceptance."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.agent.maxis import ResponseGuard, ResponseGuardError
from app.agent.maxis.contracts import canonical_payload_schema
from app.agent.workflow.agents.request_architect import SCHEMA

DIRECTORY = Path(__file__).resolve().parents[3] / "deploy/maxis-dify"
HIDDEN = {"contract", "payload_schema_json", "payload_schema_sha256", "knowledge_manifest_sha256"}
VERSION = "11111111-1111-4111-8111-111111111111"
PROOF = "LTM_RELEASE_SENTINEL_V1=" + "a" * 64


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def load(path):
    namespace = {"__name__": "request_input_test", "__file__": str(path)}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    return namespace


@pytest.fixture
def compiled():
    schema, digest = canonical_payload_schema(SCHEMA)
    project = load(DIRECTORY / "validate_bundle.py")["_dify_structured_output_schema"]
    native = load(DIRECTORY / "core_request_contract.py")["build_draft_schema"](json.loads(schema), project)
    shared = "\n\n".join((DIRECTORY / "code" / name).read_text(encoding="utf-8").rstrip()
        for name in ("core_draft_validation.py", "core_draft_context.py"))
    constants = f"\n_REQUEST_SCHEMA_JSON={schema!r}\n_REQUEST_SCHEMA_SHA256={digest!r}\n_DRAFT_SCHEMA={native!r}\n"
    result = {}
    for name in ("input", "assembler"):
        path = DIRECTORY / "code" / ("core_request_" + name + ".py")
        namespace = {"__name__": "request_input_" + name}
        exec(compile(shared + "\n\n" + path.read_text(encoding="utf-8") + constants, str(path), "exec"), namespace)
        result[name] = namespace
    return result


@pytest.fixture
def case(context_guard, now):
    pack = context_guard.build(request_id="request-input", use_case="request_architect",
        query="DL-123 변경 초안을 작성해줘", payload_schema=SCHEMA,
        knowledge_manifest_sha256="a" * 64, user_identity="synthetic-user", requested_at=now,
        sources=[{"source_id": "intent-exact", "kind": "ltm", "canonical_id": "ltm:user-intent:" + "b" * 32,
                  "title": "Guarded request", "scope": "", "content": "DL-123 변경 초안을 작성해줘", "retrieved_at": now},
                 {"source_id": "record-exact", "kind": "jira", "canonical_id": "DL-123",
                  "title": "조회 자료", "scope": "DL", "content": "원문 자료", "retrieved_at": now}])
    incoming = {"contract": "ltm-model-input.v1", "context_pack": pack.model_dump(mode="json"),
        "role_id": "request_architect", "correlation_id": pack.request_id, "output_contract": "remote-role-result.v1",
        "workflow_version": VERSION, "knowledge_version": "kb-test", "knowledge_proof": PROOF,
        "knowledge_context": [{"document_name": "policy", "content": "원문 정책"}]}
    return pack, incoming


def projected(compiled, incoming):
    result = compiled["input"]["main"](encoded(incoming))
    assert set(result) == {"request_input"}
    value = json.loads(result["request_input"])
    assert encoded(value) == result["request_input"]
    return value


def rejected(compiled, raw):
    with pytest.raises(ValueError, match="^invalid_core_request_input$"):
        compiled["input"]["main"](raw)


def test_exact_view_removes_only_reviewed_metadata_and_keeps_source_order(compiled, case):
    _, incoming = case
    before = encoded(incoming)
    expected = copy.deepcopy(incoming["context_pack"])
    for key in HIDDEN: del expected[key]
    for index, row in enumerate(expected["sources"]):
        del row["content_sha256"]
        if index == 0: del row["canonical_id"]
        row["source_authority"] = "user_intent" if index == 0 else "evidence_source"
        row["allowed_claim_kinds"] = ["inference", "recommendation"] if index == 0 else ["fact", "inference", "recommendation"]
    assert projected(compiled, incoming) == {"contract": "request-input.v1", "context": expected,
        "knowledge_context": incoming["knowledge_context"]}
    assert encoded(incoming) == before
    assert expected["sources"][1]["canonical_id"] == "DL-123"  # absent from this source's body
    assert compiled["input"]["_MAX_REQUEST_INPUT_UTF8_BYTES"] == 9000
    assert compiled["input"]["_MAX_CONTEXT_PACK_UTF8_BYTES"] == 6144


@pytest.mark.parametrize("count", [0, 1])
def test_empty_or_intent_only_ledger_is_not_replaced_or_expanded(compiled, case, count):
    _, incoming = case
    incoming["context_pack"]["sources"] = incoming["context_pack"]["sources"][:count]
    rows = projected(compiled, incoming)["context"]["sources"]
    assert [row["source_id"] for row in rows] == (["intent-exact"] if count else [])


def test_business_text_nested_json_types_timing_and_partial_markers_are_unchanged(compiled, case):
    _, incoming = case
    pack = incoming["context_pack"]
    row = pack["sources"][0]
    row.update(content='가\r\n가  {"canonical_id":"body","content_sha256":"body"}', partial=True, truncated=True)
    row["content_sha256"] = hashlib.sha256(row["content"].encode("utf-8")).hexdigest()
    pack.update(partial=True, freshness="mixed", flags=["z", "a"],
        facts=[{"text": "원문", "source_ids": ["record-exact", "intent-exact"]}],
        metrics=[{"value": 0.0, "source_ids": ["record-exact"]}],
        role_context=[{"content_json": '{"values":[false,0,true,1,1.5,null]}', "content_sha256": "opaque-context-hash"}])
    incoming["knowledge_context"] = [{"content": "원문\r\n  ", "document_name": "policy"}]
    before = encoded(incoming)
    view = projected(compiled, incoming)
    for key in set(pack) - HIDDEN - {"sources"}:
        assert encoded(view["context"][key]) == encoded(pack[key])
    assert view["context"]["sources"][0]["content"] == row["content"]
    assert view["knowledge_context"] == incoming["knowledge_context"]
    assert encoded(incoming) == before


@pytest.mark.parametrize("kind,canonical,authority", [
    ("ltm", "ltm:user-intent:opaque", "user_intent"), ("ltm", "ltm:user-intentX", "evidence_source"),
    ("ltm", "LTM:user-intent:opaque", "evidence_source"), ("jira", "ltm:user-intent:opaque", "evidence_source"),
    ("confluence", "document-7", "evidence_source"), ("knowledge", "policy-9", "evidence_source"),
])
def test_authority_uses_exact_guard_predicate_not_source_id_or_body(compiled, case, kind, canonical, authority):
    _, incoming = case
    row = incoming["context_pack"]["sources"][0]
    row.update(kind=kind, canonical_id=canonical, source_id="src_intent_looks_like_intent", scope="DL")
    result = projected(compiled, incoming)["context"]["sources"][0]
    assert result["source_id"] == row["source_id"] and result["source_authority"] == authority
    assert ("canonical_id" in result) is (authority != "user_intent")
    assert ("fact" in result["allowed_claim_kinds"]) is (authority != "user_intent")


@pytest.mark.parametrize("mutation", ["missing", "extra", "authority", "allowed", "duplicate_id", "digest",
    "kind", "canonical_type", "canonical_empty", "title_type", "updated_type", "partial_int", "truncated_int"])
def test_invalid_source_shape_is_not_repaired_or_silently_dropped(compiled, case, mutation):
    _, incoming = case
    row = incoming["context_pack"]["sources"][0]
    if mutation == "missing": del row["canonical_id"]
    elif mutation == "extra": row["extra"] = "data"
    elif mutation == "authority": row["source_authority"] = "evidence_source"
    elif mutation == "allowed": row["allowed_claim_kinds"] = ["fact"]
    elif mutation == "duplicate_id": row["source_id"] = "record-exact"
    else:
        key, value = {"digest": ("content_sha256", "0" * 64), "kind": ("kind", "LTM"),
            "canonical_type": ("canonical_id", 1), "canonical_empty": ("canonical_id", ""),
            "title_type": ("title", []), "updated_type": ("updated_at", 0),
            "partial_int": ("partial", 0), "truncated_int": ("truncated", 1)}[mutation]
        row[key] = value
    before = encoded(incoming)
    rejected(compiled, before)
    assert encoded(incoming) == before


@pytest.mark.parametrize("location,key,value", [
    ("input", "role_id", "query_specialist"), ("input", "correlation_id", "other"),
    ("input", "workflow_version", "not-a-uuid"), ("input", "knowledge_proof", "invalid"),
    ("input", "knowledge_context", {}), ("input", "extra", "unreviewed"),
    ("pack", "payload_schema_json", "{}"), ("pack", "payload_schema_sha256", "0" * 64),
    ("pack", "query", []), ("pack", "pseudonymous_user", None), ("pack", "requested_at", 1),
    ("pack", "scope", []), ("pack", "facts", {}), ("pack", "metrics", {}),
    ("pack", "role_context", {}), ("pack", "flags", "flags"), ("pack", "partial", 0),
])
def test_original_binding_and_frame_types_remain_strict(compiled, case, location, key, value):
    _, incoming = case
    (incoming if location == "input" else incoming["context_pack"])[key] = value
    rejected(compiled, encoded(incoming))


@pytest.mark.parametrize("mutation", ["noncanonical", "duplicate", "nan", "depth", "bytes", "not_object"])
def test_shared_strict_decode_boundaries_are_used_without_recovery(compiled, case, mutation):
    _, incoming = case
    raw = encoded(incoming)
    if mutation == "noncanonical": raw += " "
    elif mutation == "duplicate": raw = raw[:-1] + ',"role_id":"request_architect"}'
    elif mutation == "nan": raw = raw.replace('"partial":false', '"partial":NaN', 1)
    elif mutation == "depth":
        incoming["knowledge_context"] = []
        raw = encoded(incoming).replace('"knowledge_context":[]', '"knowledge_context":' + "[" * 33 + "0" + "]" * 33)
    elif mutation == "bytes": raw += " " * 9001
    else: raw = "[]"
    rejected(compiled, raw)


@pytest.mark.parametrize("location,maximum", [("input", 9000), ("pack", 6144)])
@pytest.mark.parametrize("unit", ["x", "한", "😀"])
def test_original_utf8_limits_are_inclusive_without_truncation(compiled, case, location, maximum, unit):
    _, incoming = case
    target = incoming["knowledge_context"][0] if location == "input" else incoming["context_pack"]
    field = "content" if location == "input" else "query"
    target[field] = ""
    measured = incoming if location == "input" else incoming["context_pack"]
    remaining = maximum - len(encoded(measured).encode("utf-8"))
    width = len(unit.encode("utf-8"))
    target[field] = unit * (remaining // width) + "x" * (remaining % width)
    assert len(encoded(measured).encode("utf-8")) == maximum
    projected(compiled, incoming)
    target[field] += "x"
    rejected(compiled, encoded(incoming))


def test_output_bound_fails_without_truncation(compiled, case, monkeypatch):
    _, incoming = case
    expected = encoded(projected(compiled, incoming))
    size = len(expected.encode("utf-8"))
    monkeypatch.setitem(compiled["input"], "_MAX_REQUEST_INPUT_UTF8_BYTES", size)
    assert encoded(projected(compiled, incoming)) == expected
    monkeypatch.setitem(compiled["input"], "_MAX_REQUEST_INPUT_UTF8_BYTES", size - 1)
    rejected(compiled, encoded(incoming))


@pytest.mark.parametrize("kind,ref,text,blocked", [
    ("inference", "intent-exact", "DL-123", False), ("recommendation", "intent-exact", "DL-123", False),
    ("fact", "intent-exact", "DL-123", True), ("fact", "record-exact", "DL-123", False),
])
def test_original_assembler_and_final_guards_keep_exact_refs_and_claim_kinds(compiled, case, kind, ref, text, blocked):
    pack, incoming = case
    view = encoded(projected(compiled, incoming))
    draft = {"contract": "request-draft.v2", "status": "ok", "missing_inputs": [], "payload": {
        "intent": {"value": "modify", "kind": "inference", "source_ids": ["intent-exact"]},
        "keywords": [], "mentioned_keys": [{"value": text, "kind": kind, "source_ids": [ref]}], "research_readiness": "ready",
        "request_questions": [], "requested_effects": [], "target_selectors": []}}
    before = encoded((incoming, draft))
    assemble = compiled["assembler"]["main"]
    result = json.loads(assemble(encoded(incoming), encoded(draft))["candidate"])
    assert encoded((incoming, draft)) == before
    assert {row["text"]: row["kind"] for row in result["claims"]} == {"modify": "inference", text: kind}
    assert {row["source_id"] for row in result["citations"]} == {"intent-exact", ref}
    remote = json.loads(load(DIRECTORY / "code/contract_guard.py")["main"](encoded(result),
        encoded(pack.model_dump(mode="json")), "request_architect", pack.request_id, "remote-role-result.v1",
        VERSION, VERSION, "kb-test", [{"content": PROOF}])["result"])
    validate = lambda: ResponseGuard().validate_remote_role(encoded(result), expected_role_id="request_architect",
        payload_model=SCHEMA, evidence=pack, expected_knowledge_manifest_sha256="a" * 64)
    if blocked:
        assert remote["warnings"] == ["contract_guard:intent_source_fact_forbidden"]
        with pytest.raises(ResponseGuardError, match="intent_source_fact_forbidden"): validate()
    else:
        assert remote == result and validate()[0].status == "ok"
    with pytest.raises(ValueError, match="invalid_core_request_draft"): assemble(view, encoded(draft))
    draft["payload"]["intent"]["source_ids"] = ["src_intent_" + "b" * 32]
    with pytest.raises(ValueError, match="invalid_core_request_draft"): assemble(encoded(incoming), encoded(draft))
