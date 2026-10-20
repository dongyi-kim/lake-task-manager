"""Compile standalone fragments; both unchanged guards decide final acceptance."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.agent.maxis import ResponseGuard, ResponseGuardError
from app.agent.maxis.contracts import canonical_payload_schema
from app.agent.workflow.agents.request_architect import SCHEMA

DIRECTORY = Path(__file__).resolve().parents[3] / "deploy/maxis-dify"
VERSION = "11111111-1111-4111-8111-111111111111"
PROOF = "LTM_RELEASE_SENTINEL_V1=" + "a" * 64


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load(path):
    spec = importlib.util.spec_from_file_location("request_assembler_test_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def compiled():
    schema, digest = canonical_payload_schema(SCHEMA)
    project = load(DIRECTORY / "validate_bundle.py")._dify_structured_output_schema
    draft = load(DIRECTORY / "core_request_contract.py").build_draft_schema(json.loads(schema), project)
    source = "\n\n".join((DIRECTORY / "code" / name).read_text(encoding="utf-8").rstrip()
        for name in ("core_draft_validation.py", "core_draft_context.py", "core_request_assembler.py"))
    namespace = {"__name__": "request_assembler_test"}
    exec(compile(source + f"\n_REQUEST_SCHEMA_JSON = {schema!r}\n_REQUEST_SCHEMA_SHA256 = {digest!r}\n_DRAFT_SCHEMA = {draft!r}\n",
                 "<request-assembler>", "exec"), namespace)
    return namespace, draft


def leaf(value, kind="recommendation", refs=None):
    return {"value": value, "kind": kind, "source_ids": ["src-intent-73"] if refs is None else refs}


@pytest.fixture
def case(context_guard, now):
    pack = context_guard.build(request_id="request-business", use_case="request_architect",
        query="DL-123의 변경 초안을 작성해줘", payload_schema=SCHEMA,
        knowledge_manifest_sha256="a" * 64, user_identity="synthetic-user", requested_at=now,
        sources=[{"source_id": "src-intent-73", "kind": "ltm", "canonical_id": "ltm:user-intent:request",
                  "title": "Guarded intent", "scope": "", "content": "DL-123의 변경 초안을 작성해줘", "retrieved_at": now},
                 {"source_id": "src-record-29", "kind": "ltm", "canonical_id": "ltm:record:29",
                  "title": "Record", "scope": "", "content": "DL-123 제목은 변경 제목이다", "retrieved_at": now}])
    incoming = {"contract": "ltm-model-input.v1", "context_pack": pack.model_dump(mode="json"),
        "role_id": "request_architect", "correlation_id": pack.request_id, "output_contract": "remote-role-result.v1",
        "workflow_version": VERSION, "knowledge_version": "kb-test", "knowledge_proof": PROOF, "knowledge_context": []}
    payload = {"intent": leaf("plan_work", "inference"), "keywords": [leaf("변경 초안")], "research_readiness": "ready",
        "request_questions": [], "requested_effects": [], "target_selectors": [], "tasks": [
            {"id": "model-task-77", "kind": "plan", "instruction": leaf("변경 초안을 작성한다"),
             "depends_on": [], "effect": "no_write_requested", "completion_criteria": [leaf("초안 검토")]}]}
    draft = {"contract": "request-draft.v2", "status": "ok", "payload": payload, "missing_inputs": []}
    expected = {"intent": "plan_work", "keywords": ["변경 초안"], "sufficient": True,
        "request_questions": [], "requested_effects": [], "target_selectors": [], "tasks": [
            {"id": "model-task-77", "kind": "plan", "instruction": "변경 초안을 작성한다", "depends_on": [],
             "write_intent": False, "completion_criteria": ["초안 검토"]}]}
    return pack, incoming, draft, expected


def assemble(compiled, incoming, draft):
    return json.loads(compiled[0]["main"](encoded(incoming), encoded(draft))["candidate"])


def guards(pack, result):
    remote = load(DIRECTORY / "code/contract_guard.py").main(encoded(result), encoded(pack.model_dump(mode="json")),
        "request_architect", pack.request_id, "remote-role-result.v1", VERSION, VERSION, "kb-test", [{"content": PROOF}])
    local = lambda: ResponseGuard().validate_remote_role(encoded(result), expected_role_id="request_architect",
        payload_model=SCHEMA, evidence=pack, expected_knowledge_manifest_sha256="a" * 64)
    return json.loads(remote["result"]), local


def test_exact_values_metadata_and_intent_annotation_cross_both_guards(compiled, case):
    pack, incoming, draft, expected = case
    before = copy.deepcopy((incoming, draft))
    result = assemble(compiled, incoming, draft)
    assert (incoming, draft) == before and result["payload"] == expected
    assert result["role_id"] == "request_architect" and result["warnings"] == []
    assert {key: result[key] for key in ("workflow_version", "knowledge_version", "knowledge_proof")} == {
        key: incoming[key] for key in ("workflow_version", "knowledge_version", "knowledge_proof")}
    claim = {row["claim_id"]: row for row in result["claims"]}
    for binding in result["payload_claims"]:
        if binding["path"] == "/intent":
            assert claim[binding["claim_ids"][0]]["kind"] == "inference"
    remote, local = guards(pack, result)
    assert remote == result and local()[1] == expected


@pytest.mark.parametrize("readiness,sufficient", [("ready", True), ("needs_clarification", False)])
@pytest.mark.parametrize("effect,write_intent", [("write_requested", True), ("no_write_requested", False)])
def test_wire_labels_map_exactly_without_changing_task_controls(compiled, case, readiness, sufficient, effect, write_intent):
    pack, incoming, draft, expected = case
    draft["payload"]["research_readiness"] = readiness
    draft["payload"]["tasks"][0]["effect"] = effect
    draft["payload"]["tasks"][0]["depends_on"] = ["unchanged-model-id"]
    result = assemble(compiled, incoming, draft)
    task = result["payload"]["tasks"][0]
    assert result["payload"]["sufficient"] is sufficient and task["write_intent"] is write_intent
    assert task["id"] == "model-task-77" and task["depends_on"] == ["unchanged-model-id"] and task["kind"] == "plan"
    remote, local = guards(pack, result)
    assert remote == result and local()[0].status == "ok"


def test_control_only_minimum_has_model_owned_claim_and_optional_absence(compiled, case):
    pack, incoming, draft, _ = case
    del draft["payload"]["tasks"]
    draft["payload"]["keywords"] = []
    result = assemble(compiled, incoming, draft)
    assert set(result["payload"]) == {"intent", "keywords", "sufficient", "request_questions", "requested_effects", "target_selectors"}
    assert len(result["claims"]) == 1 and result["claims"][0]["text"] == "plan_work"
    assert result["payload_claims"] == [{"path": "/intent", "claim_ids": ["claim-1"]}]
    remote, local = guards(pack, result)
    assert remote == result and local()[0].status == "ok"


def test_all_remaining_narratives_controls_and_whitespace_preserved(compiled, case):
    pack, incoming, draft, _ = case
    draft["payload"].update(module="ETL", playbook="knowledge", answer_depth="explain", goal=leaf("  목표  "),
        mentioned_keys=[leaf("DL-123")], blocking_questions=[leaf("범위 확인")], assumptions=[leaf("  ")], plan=leaf("확인 → 초안"),
        request_questions=[{"field": "scope", "question": leaf("적용 범위는?")}],
        requested_effects=[{"target": leaf("DL-123"), "field": "summary", "value": leaf("제목"), "literal": leaf("제목")}],
        target_selectors=[{"task_id": "model-task-77", "anchor_key": "DL-123", "relation": "direct_child",
                           "state": "incomplete", "cardinality": leaf("exactly_one")}])
    result = assemble(compiled, incoming, draft)
    assert result["payload"]["goal"] == "  목표  " and result["payload"]["assumptions"] == ["  "]
    paths = {row["path"] for row in result["payload_claims"]}
    assert {"/requested_effects/0/target", "/target_selectors/0/cardinality", "/mentioned_keys/0"} <= paths
    assert "/assumptions/0" not in paths and "/module" not in paths
    remote, local = guards(pack, result)
    assert remote == result and local()[1] == result["payload"]


@pytest.mark.parametrize("variation", ["same", "kind", "refs", "order", "text"])
def test_only_exact_text_kind_and_ordered_refs_are_deduplicated(compiled, case, variation):
    _, incoming, draft, _ = case
    first = leaf("동일 문장", refs=["src-intent-73", "src-record-29"])
    second = copy.deepcopy(first)
    if variation == "kind": second["kind"] = "inference"
    elif variation == "refs": second["source_ids"] = ["src-record-29"]
    elif variation == "order": second["source_ids"].reverse()
    elif variation == "text": second["value"] += " "
    draft["payload"]["keywords"] = [first, second]
    result = assemble(compiled, incoming, draft)
    bindings = {row["path"]: row["claim_ids"] for row in result["payload_claims"]}
    assert (bindings["/keywords/0"] == bindings["/keywords/1"]) is (variation == "same")
    assert result["payload"]["keywords"] == [first["value"], second["value"]]
    assert all(row["text"] in {"plan_work", "초안 검토", "변경 초안을 작성한다", first["value"], second["value"]}
               for row in result["claims"])


@pytest.mark.parametrize("annotation", ["kind", "source_ids"])
def test_canonical_unique_items_revalidated_after_unwrap(compiled, case, annotation):
    _, incoming, draft, _ = case
    effect = {"target": leaf("DL-123"), "field": "summary", "value": leaf("제목"), "literal": leaf("제목")}
    duplicate = copy.deepcopy(effect)
    duplicate["value"][annotation] = "inference" if annotation == "kind" else ["src-record-29"]
    draft["payload"]["requested_effects"] = [effect, duplicate]
    Draft202012Validator(compiled[1]).validate(draft)
    with pytest.raises(ValueError, match="^invalid_core_request_draft(?: native_draft_(?:json_syntax|json_structure|schema|source_id|fact_selection))?$"):
        assemble(compiled, incoming, draft)


@pytest.mark.parametrize("refs", [["src-intent-73"], ["src-intent-73", "src-record-29"]])
def test_fact_kind_is_not_repaired_to_bypass_final_guards(compiled, case, refs):
    pack, incoming, draft, _ = case
    draft["payload"]["mentioned_keys"] = [leaf("DL-123", "fact", refs)]
    result = assemble(compiled, incoming, draft)
    assert any(row["text"] == "DL-123" and row["kind"] == "fact" for row in result["claims"])
    remote, local = guards(pack, result)
    assert remote["warnings"] == ["contract_guard:intent_source_fact_forbidden"]
    with pytest.raises(ResponseGuardError, match="intent_source_fact_forbidden"):
        local()


@pytest.mark.parametrize("path", [
    ("intent",), ("keywords", 0), ("tasks", 0, "completion_criteria", 0),
    ("blocking_questions", 0), ("request_questions", 0, "question"), ("assumptions", 0),
])
@pytest.mark.parametrize("refs", [["src-intent-73"], ["src-record-29"], ["src-intent-73", "src-record-29"]])
@pytest.mark.parametrize("kind", ["fact", "inference", "recommendation"])
def test_planning_kinds_preserve_values_and_refs_or_fail_without_candidate(compiled, case, path, refs, kind):
    pack, incoming, draft, _ = case
    if path[0] == "assumptions":
        draft["payload"]["assumptions"] = [leaf("요청의 목표를 PoC로 해석한다")]
    elif path[0] == "blocking_questions":
        draft["payload"]["blocking_questions"] = [leaf("어떤 범위를 원하시나요?")]
    elif path[0] == "request_questions":
        draft["payload"]["request_questions"] = [
            {"field": "scope", "question": leaf("어떤 범위를 원하시나요?")}]
    target = draft["payload"]
    for key in path:
        target = target[key]
    target.update(kind=kind, source_ids=refs)
    if path[0] == "keywords":
        target["value"] = "DL-123"
    elif path[0] == "tasks":
        target["value"] = "변경 초안에 대상과 검토 방법을 명시한다"
    before = copy.deepcopy((incoming, draft))
    if kind == "fact":
        with pytest.raises(ValueError, match="^invalid_core_request_draft(?: native_draft_(?:json_syntax|json_structure|schema|source_id|fact_selection))?$"):
            assemble(compiled, incoming, draft)
    else:
        result = assemble(compiled, incoming, draft)
        pointer = "/" + "/".join(map(str, path))
        binding = next(row for row in result["payload_claims"] if row["path"] == pointer)
        claim = next(row for row in result["claims"] if row["claim_id"] == binding["claim_ids"][0])
        citations = {row["citation_id"]: row["source_id"] for row in result["citations"]}
        assert (claim["text"], claim["kind"]) == (target["value"], kind)
        assert [citations[key] for key in claim["citation_ids"]] == refs
        remote, local = guards(pack, result)
        assert remote == result and local()[0].status == "ok"
    assert (incoming, draft) == before


def test_evidence_backed_goal_retains_fact_support(compiled, case):
    pack, incoming, draft, _ = case
    draft["payload"]["goal"] = leaf("DL-123 제목은 변경 제목이다", "fact", ["src-record-29"])
    before = copy.deepcopy(draft)
    result = assemble(compiled, incoming, draft)
    assert draft == before and result["payload"]["goal"] == draft["payload"]["goal"]["value"]
    binding = next(row for row in result["payload_claims"] if row["path"] == "/goal")
    claim = next(row for row in result["claims"] if row["claim_id"] == binding["claim_ids"][0])
    assert claim["kind"] == "fact"
    remote, local = guards(pack, result)
    assert remote == result and local()[1] == result["payload"]


@pytest.mark.parametrize("depth", ["brief", "explain"])
def test_answer_depth_is_plain_and_wrapped_value_is_never_repaired(compiled, case, depth):
    pack, incoming, draft, _ = case
    draft["payload"]["answer_depth"] = depth
    result = assemble(compiled, incoming, draft)
    assert result["payload"]["answer_depth"] == depth
    remote, local = guards(pack, result)
    assert remote == result and local()[0].status == "ok"
    draft["payload"]["answer_depth"] = leaf(depth)
    before = copy.deepcopy((incoming, draft))
    with pytest.raises(ValueError, match="^invalid_core_request_draft(?: native_draft_(?:json_syntax|json_structure|schema|source_id|fact_selection))?$"):
        assemble(compiled, incoming, draft)
    assert (incoming, draft) == before


def test_previous_native_version_has_no_assembler_fallback(compiled, case):
    _, incoming, draft, _ = case
    draft["contract"] = "request-draft.v1"
    with pytest.raises(ValueError, match="^invalid_core_request_draft(?: native_draft_(?:json_syntax|json_structure|schema|source_id|fact_selection))?$"):
        assemble(compiled, incoming, draft)


@pytest.mark.parametrize("status", ["blocked", "needs_input"])
def test_empty_control_outcomes_have_no_provenance(compiled, case, status):
    pack, incoming, draft, _ = case
    draft.update(status=status, payload={}, missing_inputs=["범위를 알려주세요"] if status == "needs_input" else [])
    result = assemble(compiled, incoming, draft)
    assert not any(result[key] for key in ("payload", "claims", "payload_claims", "citations"))
    remote, local = guards(pack, result)
    assert remote == result and local()[0].status == status


@pytest.mark.parametrize("field", ["research_readiness", "effect"])
@pytest.mark.parametrize("value", [False, True, 0, 1, 1.0, "false", "true", "READY", "ready ", "read_only", "", None, [], {}])
def test_malformed_wire_boolean_never_repaired(compiled, case, field, value):
    _, incoming, draft, _ = case
    node = draft["payload"] if field == "research_readiness" else draft["payload"]["tasks"][0]
    node[field] = value
    before = copy.deepcopy((incoming, draft))
    with pytest.raises(ValueError, match="^invalid_core_request_draft(?: native_draft_(?:json_syntax|json_structure|schema|source_id|fact_selection))?$"):
        assemble(compiled, incoming, draft)
    assert (incoming, draft) == before


@pytest.mark.parametrize("mutation", ["unknown_ref", "blank_unknown_ref", "duplicate_ref", "newline_ref", "empty_ref",
    "unknown_kind", "missing_wrapper", "wrapper_extra", "root_extra", "task_extra", "metadata", "missing_readiness",
    "missing_effect", "old_sufficient", "old_write_intent", "empty_ok", "ok_question", "blocked_payload",
    "blocked_question", "needs_input_without_question", "whitespace_question", "old_contract"])
def test_invalid_drafts_fail_without_partial_or_synthesized_result(compiled, case, mutation):
    _, incoming, draft, _ = case
    payload = draft["payload"]
    if mutation in {"unknown_ref", "blank_unknown_ref"}:
        payload["keywords"][0] = leaf(" " if mutation == "blank_unknown_ref" else "내용", refs=["src_intent_query"])
    elif mutation == "duplicate_ref": payload["intent"]["source_ids"] *= 2
    elif mutation == "newline_ref": payload["intent"]["source_ids"] = ["src-intent-73\n"]
    elif mutation == "empty_ref": payload["intent"]["source_ids"] = []
    elif mutation == "unknown_kind": payload["intent"]["kind"] = "open_fact"
    elif mutation == "missing_wrapper": payload["intent"] = "plan_work"
    elif mutation == "wrapper_extra": payload["intent"]["approved"] = True
    elif mutation == "root_extra": payload["approved"] = True
    elif mutation == "task_extra": payload["tasks"][0]["execute"] = True
    elif mutation == "metadata": draft["workflow_version"] = "model-owned"
    elif mutation == "missing_readiness": del payload["research_readiness"]
    elif mutation == "missing_effect": del payload["tasks"][0]["effect"]
    elif mutation == "old_sufficient": payload["sufficient"] = False
    elif mutation == "old_write_intent": payload["tasks"][0]["write_intent"] = False
    elif mutation == "empty_ok": draft["payload"] = {}
    elif mutation == "ok_question": draft["missing_inputs"] = ["질문"]
    elif mutation == "blocked_payload": draft["status"] = "blocked"
    elif mutation == "blocked_question": draft.update(status="blocked", payload={}, missing_inputs=["질문"])
    elif mutation == "needs_input_without_question": draft.update(status="needs_input", payload={})
    elif mutation == "whitespace_question": draft.update(status="needs_input", payload={}, missing_inputs=[" \t"])
    else: draft["contract"] = "request-draft.v0"
    with pytest.raises(ValueError, match="^invalid_core_request_draft(?: native_draft_(?:json_syntax|json_structure|schema|source_id|fact_selection))?$"):
        assemble(compiled, incoming, draft)


@pytest.mark.parametrize("mutation", ["role", "use_case", "request", "schema", "digest", "source_hash", "source_duplicate",
    "source_id", "source_missing", "extra_input", "extra_pack", "manifest", "workflow", "proof", "unbound"])
def test_request_original_input_authority_is_rechecked(compiled, case, mutation):
    _, incoming, draft, _ = case
    pack = incoming["context_pack"]
    if mutation == "role": incoming["role_id"] = "query_specialist"
    elif mutation == "use_case": pack["use_case"] = "query_specialist"
    elif mutation == "request": incoming["correlation_id"] = "different"
    elif mutation == "schema": pack["payload_schema_json"] += " "
    elif mutation == "digest": pack["payload_schema_sha256"] = "0" * 64
    elif mutation == "source_hash": pack["sources"][0]["content_sha256"] = "0" * 64
    elif mutation == "source_duplicate": pack["sources"] *= 2
    elif mutation == "source_id": pack["sources"][0]["source_id"] = "src-intent-73\n"
    elif mutation == "source_missing": pack["sources"] = []
    elif mutation == "extra_input": incoming["extra"] = True
    elif mutation == "extra_pack": pack["extra"] = True
    elif mutation == "manifest": pack["knowledge_manifest_sha256"] = "invalid"
    elif mutation == "workflow": incoming["workflow_version"] = "invalid"
    elif mutation == "proof": incoming["knowledge_proof"] = "invalid"
    else: compiled[0]["_DRAFT_SCHEMA"] = None
    with pytest.raises(ValueError, match="^invalid_core_request_draft(?: native_draft_(?:json_syntax|json_structure|schema|source_id|fact_selection))?$"):
        assemble(compiled, incoming, draft)


@pytest.mark.parametrize("limit,exceeds", [("claims", False), ("claims", True), ("bindings", False), ("bindings", True),
                                        ("text", False), ("text", True)])
def test_envelope_bounds_fail_closed_without_truncation(compiled, case, limit, exceeds):
    _, incoming, draft, _ = case
    if limit == "claims":
        draft["payload"]["mentioned_keys"] = [leaf("model-" + str(i)) for i in range(36 + exceeds)]
    elif limit == "bindings":
        draft["payload"]["mentioned_keys"] = [leaf("repeated")] * (252 + exceeds)
    else:
        draft["payload"]["mentioned_keys"] = [leaf("x" * (1200 + exceeds))]
    before = copy.deepcopy(draft)
    Draft202012Validator(compiled[1]).validate(draft)
    if exceeds:
        with pytest.raises(ValueError, match="^invalid_core_request_draft(?: native_draft_(?:json_syntax|json_structure|schema|source_id|fact_selection))?$"):
            assemble(compiled, incoming, draft)
    else:
        result = assemble(compiled, incoming, draft)
        assert result["payload"]["mentioned_keys"] == [row["value"] for row in draft["payload"]["mentioned_keys"]]
        if limit == "claims": assert len(result["claims"]) == 40
        elif limit == "bindings": assert len(result["payload_claims"]) == 256
        else: assert max(len(row["text"]) for row in result["claims"]) == 1200
    assert draft == before


@pytest.mark.parametrize("raw", ['{"status":"ok","status":"blocked"}', '{"x":NaN}', '{"x":Infinity}',
    '[]', '```json\n{}\n```', 'x' * 32769], ids=["duplicate", "nan", "infinity", "array", "fence", "over_budget"])
def test_candidate_json_is_strict_and_bounded(compiled, case, raw):
    with pytest.raises(ValueError, match="^invalid_core_request_draft(?: native_draft_(?:json_syntax|json_structure|schema|source_id|fact_selection))?$"):
        compiled[0]["main"](encoded(case[1]), raw)


def test_candidate_whitespace_allowed_but_original_input_must_be_canonical(compiled, case):
    _, incoming, draft, _ = case
    expected = assemble(compiled, incoming, draft)
    assert json.loads(compiled[0]["main"](encoded(incoming), json.dumps(draft, indent=2, sort_keys=True))["candidate"]) == expected
    with pytest.raises(ValueError, match="^invalid_core_request_draft(?: native_draft_(?:json_syntax|json_structure|schema|source_id|fact_selection))?$"):
        compiled[0]["main"](json.dumps(incoming, indent=2), encoded(draft))


@pytest.mark.parametrize("field", ["sufficient", "write_intent"])
@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_internal_adapter_does_not_loosen_canonical_boolean_guards(compiled, case, field, value):
    pack, incoming, draft, _ = case
    result = assemble(compiled, incoming, draft)
    node = result["payload"] if field == "sufficient" else result["payload"]["tasks"][0]
    node[field] = value
    remote, local = guards(pack, result)
    assert remote["warnings"] == ["contract_guard:payload_type_mismatch"]
    with pytest.raises(ResponseGuardError):
        local()
