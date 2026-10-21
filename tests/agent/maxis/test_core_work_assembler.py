"""Pure Work assembly retains model meaning; both existing guards remain authoritative."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.agent.maxis import ResponseGuard, ResponseGuardError
from app.agent.maxis.contracts import canonical_payload_schema
from app.agent.workflow.work_architect.contracts import CREATE_SCHEMA

DIRECTORY = Path(__file__).resolve().parents[3] / "deploy/maxis-dify"
VERSION = "11111111-1111-4111-8111-111111111111"
PROOF = "LTM_RELEASE_SENTINEL_V1=" + "a" * 64
ERROR = "^invalid_core_work_draft(?: native_draft_(?:json_syntax|json_structure|schema|source_id|fact_selection))?$"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def load(path):
    spec = importlib.util.spec_from_file_location("work_assembler_test_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def compiled():
    schema, digest = canonical_payload_schema(CREATE_SCHEMA)
    project = load(DIRECTORY / "validate_bundle.py")._dify_structured_output_schema
    draft = load(DIRECTORY / "core_work_contract.py").build_draft_schema(json.loads(schema), project)
    source = "\n\n".join((DIRECTORY / "code" / name).read_text(encoding="utf-8").rstrip()
        for name in ("core_draft_validation.py", "core_draft_context.py", "core_fact_text.py", "core_work_assembler.py"))
    namespace = {"__name__": "work_assembler_test"}
    exec(compile(source + f"\n_WORK_SCHEMA_JSON = {schema!r}\n_WORK_SCHEMA_SHA256 = {digest!r}\n_DRAFT_SCHEMA = {draft!r}\n",
                 "<work-assembler>", "exec"), namespace)
    return namespace, draft


def leaf(value, kind="recommendation", refs=None):
    return {"value": value, "kind": kind, "source_ids": ["intent-a"] if refs is None else refs}


@pytest.fixture
def case(context_guard, now):
    pack = context_guard.build(request_id="work-business", use_case="work_architect",
        query="수집 작업 점검 초안을 정리해줘", payload_schema=CREATE_SCHEMA,
        knowledge_manifest_sha256="a" * 64, user_identity="synthetic-user", requested_at=now,
        sources=[{"source_id": "intent-a", "kind": "ltm", "canonical_id": "ltm:user-intent:work",
                  "title": "Guarded intent", "scope": "", "content": "수집 작업 점검 초안을 정리해줘", "retrieved_at": now},
                 {"source_id": "record-b", "kind": "ltm", "canonical_id": "ltm:record:b",
                  "title": "Record", "scope": "", "content": "합성 기록 원문", "retrieved_at": now}])
    incoming = {"contract": "ltm-model-input.v1", "context_pack": pack.model_dump(mode="json"),
        "role_id": "work_architect", "correlation_id": pack.request_id, "output_contract": "remote-role-result.v1",
        "workflow_version": VERSION, "knowledge_version": "kb-test", "knowledge_proof": PROOF, "knowledge_context": []}
    payload = {"questions": [], "mode": "task", "items": [{"summary": leaf("수집 작업 확인"), "type": "Task",
        "background": leaf("수집 작업 점검 요청"), "scope_in": [leaf("수집 경로 확인")], "dod": [leaf("확인 결과 기록")]}]}
    draft = {"contract": "work-draft.v3", "status": "ok", "payload": payload}
    expected = {"questions": [], "mode": "task", "items": [{"summary": "수집 작업 확인", "type": "Task",
        "background": "수집 작업 점검 요청", "scope_in": ["수집 경로 확인"], "dod": ["확인 결과 기록"]}]}
    return pack, incoming, draft, expected


def assemble(compiled, incoming, draft):
    return json.loads(compiled[0]["main"](encoded(incoming), encoded(draft))["candidate"])


def guards(pack, result):
    remote = load(DIRECTORY / "code/contract_guard.py").main(encoded(result), encoded(pack.model_dump(mode="json")),
        "work_architect", pack.request_id, "remote-role-result.v1", VERSION, VERSION, "kb-test", [{"content": PROOF}])
    local = lambda: ResponseGuard().validate_remote_role(encoded(result), expected_role_id="work_architect",
        payload_model=CREATE_SCHEMA, evidence=pack, expected_knowledge_manifest_sha256="a" * 64)
    return json.loads(remote["result"]), local


def test_exact_payload_metadata_and_bindings_cross_both_guards(compiled, case):
    pack, incoming, draft, expected = case
    before = copy.deepcopy((incoming, draft))
    result = assemble(compiled, incoming, draft)
    assert (incoming, draft) == before and result["payload"] == expected
    assert result == assemble(compiled, incoming, draft)
    assert result["role_id"] == "work_architect" and result["warnings"] == []
    for key in ("workflow_version", "knowledge_version", "knowledge_proof"):
        assert result[key] == incoming[key]
    assert result["knowledge_manifest_sha256"] == incoming["context_pack"]["knowledge_manifest_sha256"]
    assert result["citations"] == [{"citation_id": "cite-1", "source_id": "intent-a", "locator": ""}]
    assert {row["path"] for row in result["payload_claims"]} == {
        "/items/0/summary", "/items/0/background", "/items/0/scope_in/0", "/items/0/dod/0"}
    remote, local = guards(pack, result)
    assert remote == result and local()[1] == expected


@pytest.mark.parametrize("label,required", [("required", True), ("optional", False)])
@pytest.mark.parametrize("kind", ["text", "choice", "multi", "date"])
def test_question_only_result_maps_explicit_label_without_status_inference(compiled, case, label, required, kind):
    pack, incoming, draft, _ = case
    draft["payload"].update(items=[], questions=[{"question": leaf("적용 범위는?"), "kind": kind, "field": "scope",
        "input_need": label, "why_required": leaf("범위 결정 필요" if required else ""),
        "options": [leaf("수집 경로"), leaf("검증 경로")]}])
    result = assemble(compiled, incoming, draft)
    question = result["payload"]["questions"][0]
    assert question == {"question": "적용 범위는?", "kind": kind, "field": "scope",
                        "required_input": required, "why_required": "범위 결정 필요" if required else "",
                        "options": ["수집 경로", "검증 경로"]}
    assert question["required_input"] is required and "input_need" not in question
    assert result["status"] == "ok" and result["missing_inputs"] == [] and result["payload"]["items"] == []
    remote, local = guards(pack, result)
    assert remote == result and local()[1] == result["payload"]


def test_all_optional_narratives_controls_children_and_blank_text_are_preserved(compiled, case):
    pack, incoming, draft, _ = case
    payload, item = draft["payload"], draft["payload"]["items"][0]
    payload.update(interpretation=leaf("  해석  "), structure="task_with_subtasks", structure_why=leaf("분리 제안"), rationale=leaf("  "))
    item.update(tier="task", issue_type="Task", epic="", epic_name="", parent="", priority="", duedate="",
        components=[], labels=["draft"], expected=leaf("기대 결과"), actual=leaf("실제 결과"),
        reproduction=[leaf("재현 경로")], scope_out=[leaf("제외 범위")],
        references=[{"kind": "fact", "source_id": "record-b", "source_path": ""}],
        children=[{"summary": leaf("검증 경로"), "scope_in": [leaf("검증 대상")], "dod": [leaf("검증 근거")], "duedate": ""}])
    result = assemble(compiled, incoming, draft)
    assert result["payload"]["interpretation"] == "  해석  " and result["payload"]["rationale"] == "  "
    assert result["payload"]["items"][0]["children"][0]["summary"] == "검증 경로"
    for field in ("tier", "issue_type", "epic", "epic_name", "parent", "priority", "duedate", "components", "labels"):
        assert result["payload"]["items"][0][field] == item[field]
    paths = {row["path"] for row in result["payload_claims"]}
    assert {"/items/0/actual", "/items/0/references/0", "/items/0/children/0/dod/0"} <= paths
    assert "/rationale" not in paths and "/items/0/labels/0" not in paths
    remote, local = guards(pack, result)
    assert remote == result and local()[1] == result["payload"]


@pytest.mark.parametrize("variation", ["same", "kind", "refs", "order", "text"])
def test_only_identical_text_kind_ordered_refs_share_claim_ids(compiled, case, variation):
    _, incoming, draft, _ = case
    first = leaf("동일 문장", refs=["intent-a", "record-b"])
    second = copy.deepcopy(first)
    if variation == "kind": second["kind"] = "inference"
    elif variation == "refs": second["source_ids"] = ["record-b"]
    elif variation == "order": second["source_ids"].reverse()
    elif variation == "text": second["value"] += " "
    draft["payload"]["items"][0]["scope_in"] = [first, second]
    result = assemble(compiled, incoming, draft)
    bindings = {row["path"]: row["claim_ids"] for row in result["payload_claims"]}
    assert (bindings["/items/0/scope_in/0"] == bindings["/items/0/scope_in/1"]) is (variation == "same")
    assert result["payload"]["items"][0]["scope_in"] == [first["value"], second["value"]]
    claims = {row["claim_id"]: row for row in result["claims"]}
    citations = {row["citation_id"]: row["source_id"] for row in result["citations"]}
    for index, expected in enumerate((first, second)):
        actual = claims[bindings[f"/items/0/scope_in/{index}"][0]]
        assert actual["text"] == expected["value"] and actual["kind"] == expected["kind"]
        assert [citations[key] for key in actual["citation_ids"]] == expected["source_ids"]


def test_intent_fact_is_preserved_and_rejected_by_both_final_guards(compiled, case):
    pack, incoming, draft, _ = case
    draft["payload"]["items"][0]["background"] = {
        "kind": "fact", "source_id": "intent-a", "source_path": ""}
    result = assemble(compiled, incoming, draft)
    assert any(row["text"] == pack.sources[0].content and row["kind"] == "fact" for row in result["claims"])
    remote, local = guards(pack, result)
    assert remote["warnings"] == ["contract_guard:intent_source_fact_forbidden"]
    with pytest.raises(ResponseGuardError, match="intent_source_fact_forbidden"):
        local()


def test_authored_fact_cannot_enter_native_work_contract(compiled, case):
    _, incoming, draft, _ = case
    draft["payload"]["items"][0]["background"] = leaf("사용자 요청을 확인된 사실로 표기", "fact")
    with pytest.raises(ValueError, match=ERROR):
        assemble(compiled, incoming, draft)


def test_work_fact_is_exact_source_selection_not_model_rephrasing(compiled, case):
    pack, incoming, draft, _ = case
    draft["payload"]["items"][0]["references"] = [
        {"kind": "fact", "source_id": "record-b", "source_path": ""}]
    result = assemble(compiled, incoming, draft)
    assert result["payload"]["items"][0]["references"] == [pack.sources[1].content]
    remote, local = guards(pack, result)
    assert remote == result and local()[1] == result["payload"]


def test_json_fact_keeps_subject_values_and_readable_ticket_body(compiled, case):
    import hashlib
    from app.agent.workflow.agents.work_architect import _materialize_creation_parts
    pack, incoming, draft, _ = case
    raw = '{"tickets":[{"key":"DL-9044","summary":"배치 <검증>","status":"진행 중","ratio":1.20e-3,"url":"https://example.test/DL-9044","checks":{"한/글~":false}}]}'
    row = incoming["context_pack"]["sources"][1]
    row.update(content=raw, content_sha256=hashlib.sha256(raw.encode()).hexdigest(), url="https://example.test/DL-9044")
    pack = type(pack).model_validate_json(encoded(incoming["context_pack"]))
    item = draft["payload"]["items"][0]
    item["references"] = [{"kind": "fact", "source_id": "record-b", "source_path": "/tickets/0"}]
    result = assemble(compiled, incoming, draft)
    text = result["payload"]["items"][0]["references"][0]
    assert text == '"key": "DL-9044"; "summary": "배치 <검증>"; "status": "진행 중"; "ratio": 1.20e-3; "url": "https://example.test/DL-9044"; "checks" / "한/글~": false'
    remote, local = guards(pack, result)
    assert remote == result and local()[1] == result["payload"]
    output = copy.deepcopy(result["payload"])
    _materialize_creation_parts(output)
    body = output["items"][0]["description"]
    assert "수집 작업 점검 요청" in body and "&quot;status&quot;: &quot;진행 중&quot;" in body
    assert "1.20e-3" in body and "배치 &lt;검증&gt;" in body
    assert "{&quot;" not in body and 'data-ticket-key="DL-9044"' in body


def test_readable_fact_cannot_confuse_fields_with_delimiters_in_data(compiled):
    render = compiled[0]["_record_text"]
    value = {"key": "DL-9044", "a / b": "x", "a": {"b": "y"},
             "description": 'blocked; status: Done\n"quoted"', "status": "Open"}
    text = render(value)
    assert '"a / b": "x"' in text and '"a" / "b": "y"' in text
    assert '"description": "blocked; status: Done\\n\\"quoted\\""' in text
    assert text.endswith('"status": "Open"') and "\n" not in text


def test_selected_source_cannot_return_an_unencodable_candidate(compiled, case):
    import hashlib
    _, incoming, draft, _ = case
    raw = '{"tickets":[{"key":"DL-9044","summary":"\\ud800"}]}'
    incoming["context_pack"]["sources"][1].update(content=raw, content_sha256=hashlib.sha256(raw.encode()).hexdigest())
    draft["payload"]["items"][0]["references"] = [{"kind": "fact", "source_id": "record-b", "source_path": "/tickets/0"}]
    with pytest.raises(ValueError, match=ERROR):
        assemble(compiled, incoming, draft)


def test_selected_record_still_obeys_canonical_field_length(compiled, case):
    import hashlib
    _, incoming, draft, _ = case
    raw = encoded({"tickets": [{"key": "DL-9044", "description": "x" * 510}]})
    incoming["context_pack"]["sources"][1].update(content=raw, content_sha256=hashlib.sha256(raw.encode()).hexdigest())
    draft["payload"]["items"][0]["references"] = [{"kind": "fact", "source_id": "record-b", "source_path": "/tickets/0"}]
    with pytest.raises(ValueError, match=ERROR):
        assemble(compiled, incoming, draft)


@pytest.mark.parametrize("path", ["/content", "/missing", "/0/status", "/~invalid"])
def test_work_fact_does_not_guess_or_repair_a_source_path(compiled, case, path):
    _, incoming, draft, _ = case
    draft["payload"]["items"][0]["references"] = [
        {"kind": "fact", "source_id": "record-b", "source_path": path}]
    with pytest.raises(ValueError, match=ERROR):
        assemble(compiled, incoming, draft)


def test_blocked_outcome_is_empty_without_synthesized_provenance(compiled, case):
    pack, incoming, draft, _ = case
    draft.update(status="blocked", payload={})
    result = assemble(compiled, incoming, draft)
    assert not any(result[key] for key in ("payload", "claims", "payload_claims", "citations"))
    remote, local = guards(pack, result)
    assert remote == result and local()[0].status == "blocked"
    assert result["missing_inputs"] == []


@pytest.mark.parametrize("extra", ["root_questions", "legacy_missing_inputs", "legacy_needs_input", "competing_draft"])
def test_one_question_channel_rejects_legacy_or_competing_payloads(compiled, case, extra):
    _, incoming, draft, _ = case
    question = {"question": leaf("기존 티켓 확장 또는 별도 작업 중 어느 쪽인가요?"),
                "kind": "choice", "input_need": "required", "why_required": leaf("중복 작업 결정 필요"),
                "options": [leaf("기존 작업 확장"), leaf("별도 작업 생성")]}
    if extra == "root_questions": draft["questions"] = [question]
    elif extra == "legacy_missing_inputs": draft["missing_inputs"] = []
    elif extra == "legacy_needs_input": draft.update(status="needs_input", payload={})
    else: draft["payload"]["questions"] = [question]
    before = copy.deepcopy(draft)
    with pytest.raises(ValueError, match=ERROR): assemble(compiled, incoming, draft)
    assert draft == before


def test_native_question_reaches_work_state_and_resumes_the_original_request(compiled, case):
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.work_architect import WorkArchitect
    from app.agent.workflow.continuation import build_continuation_contract
    from app.agent.workflow.session import _turn_start_patch
    from app.agent.workflow.graph import route_after_work_architect
    pack, incoming, draft, _ = case
    draft["payload"].update(items=[], questions=[{
        "question": leaf("어떤 대상의 점검 작업인가요?"), "kind": "text", "field": "target",
        "input_need": "required", "why_required": leaf("점검 대상을 결정해야 함")}])
    result = assemble(compiled, incoming, draft)
    remote, local = guards(pack, result)
    assert remote == result
    canonical = local()[1]
    prior = {"intent": "plan_work", "request_text": pack.query,
             "messages": [HumanMessage(content=pack.query)],
             "request_plan": {"goal": pack.query, "tasks": [], "blocking_questions": [], "assumptions": []}}
    patch = WorkArchitect().apply(prior, canonical)
    assert patch["questions"][0]["question"] == "어떤 대상의 점검 작업인가요?"
    assert patch["questions"][0]["required_input"] is True
    assert not (patch.get("draft") or {}).get("items")
    assert route_after_work_architect({**prior, **patch}) == "respond"
    assert not patch.get("approval_token") and not patch.get("comment_token")
    state = {**prior, **patch}
    state["continuation_contract"] = build_continuation_contract(state)
    answer = "대상은 DL-9003 수집 경로 점검으로 해줘"
    resumed = _turn_start_patch(answer, state)
    assert resumed["turn_continuation"] is True
    assert resumed["request_text"] == pack.query
    assert resumed["continuation_answer"] == answer
    assert resumed["request_plan"] == prior["request_plan"]
    assert resumed["continuation_contract"]["root_request"] == pack.query


@pytest.mark.parametrize("missing", ["questions", "mode", "items"])
def test_each_missing_payload_field_is_rejected_without_diagnostic_key_inference(compiled, case, missing):
    _, incoming, draft, _ = case
    del draft["payload"][missing]
    errors = list(Draft202012Validator(compiled[1]).iter_errors(draft))
    assert len(errors) == 1 and errors[0].validator == "anyOf"
    branches = errors[0].context
    assert [error.validator for error in branches] == ["additionalProperties", "maxProperties", "required"]
    assert all(list(error.absolute_path) == ["payload"] for error in branches)
    assert all(list(error.schema_path)[0] == 0 for error in branches[:2])
    assert list(branches[2].schema_path)[0] == 1
    # The compact diagnostic stores this complete required set, not the omitted key.
    assert branches[2].validator_value == ["questions", "mode", "items"]
    with pytest.raises(ValueError, match=ERROR):
        assemble(compiled, incoming, draft)


@pytest.mark.parametrize("value", [True, False, 0, 1, 1.0, "true", "false", "REQUIRED", "required ", "", None, [], {}])
def test_question_label_rejects_coercion_and_defaults(compiled, case, value):
    _, incoming, draft, _ = case
    draft["payload"]["questions"] = [{"question": leaf("대상은?"), "kind": "text", "input_need": value, "why_required": leaf("")}]
    before = copy.deepcopy(draft)
    with pytest.raises(ValueError, match=ERROR): assemble(compiled, incoming, draft)
    assert draft == before


@pytest.mark.parametrize("mutation", ["unknown_ref", "blank_unknown_ref", "duplicate_ref", "empty_refs", "unknown_kind",
    "plain_text", "wrapper_extra", "root_extra", "item_extra", "child_extra", "question_extra", "old_boolean", "missing_label",
    "metadata", "empty_ok", "ok_missing", "blocked_payload", "blocked_missing", "needs_empty", "blank_missing", "old_contract",
    "too_many_items", "too_many_children", "too_many_questions", "empty_scope", "empty_dod", "missing_summary"])
def test_invalid_draft_has_no_partial_result_or_repair(compiled, case, mutation):
    _, incoming, draft, _ = case
    payload, item = draft["payload"], draft["payload"]["items"][0]
    if mutation in {"unknown_ref", "blank_unknown_ref"}: item["summary"] = leaf(" " if mutation.startswith("blank") else "내용", refs=["unknown"])
    elif mutation == "duplicate_ref": item["summary"]["source_ids"] *= 2
    elif mutation == "empty_refs": item["summary"]["source_ids"] = []
    elif mutation == "unknown_kind": item["summary"]["kind"] = "open_fact"
    elif mutation == "plain_text": item["summary"] = "내용"
    elif mutation == "wrapper_extra": item["summary"]["approved"] = True
    elif mutation == "root_extra": payload["approved"] = True
    elif mutation == "item_extra": item["execute"] = True
    elif mutation == "child_extra": item["children"] = [{"summary": leaf("자식"), "scope_in": [leaf("범위")], "dod": [leaf("조건")], "extra": "x"}]
    elif mutation in {"question_extra", "old_boolean", "missing_label", "too_many_questions"}:
        q = {"question": leaf("대상은?"), "kind": "text", "input_need": "required", "why_required": leaf("대상 필요")}
        payload["questions"] = [q] * (4 if mutation == "too_many_questions" else 1)
        if mutation == "question_extra": q["other"] = "unknown narrative"
        elif mutation == "old_boolean": q["required_input"] = True
        elif mutation == "missing_label": del q["input_need"]
    elif mutation == "metadata": draft["workflow_version"] = "model-owned"
    elif mutation == "empty_ok": draft["payload"] = {}
    elif mutation == "ok_missing": draft["missing_inputs"] = ["질문"]
    elif mutation == "blocked_payload": draft["status"] = "blocked"
    elif mutation == "blocked_missing": draft.update(status="blocked", payload={}, missing_inputs=["질문"])
    elif mutation == "needs_empty": draft.update(status="needs_input", payload={})
    elif mutation == "blank_missing": draft.update(status="needs_input", payload={}, missing_inputs=[" \t"])
    elif mutation == "old_contract": draft["contract"] = "work-draft.v0"
    elif mutation == "too_many_items": payload["items"] *= 13
    elif mutation == "too_many_children": item["children"] = [{"summary": leaf("자식"), "scope_in": [leaf("범위")], "dod": [leaf("조건")]}] * 31
    elif mutation == "empty_scope": item["scope_in"] = []
    elif mutation == "empty_dod": item["dod"] = []
    else: del item["summary"]
    before = copy.deepcopy((incoming, draft))
    with pytest.raises(ValueError, match=ERROR): assemble(compiled, incoming, draft)
    assert (incoming, draft) == before


@pytest.mark.parametrize("mutation", ["role", "use_case", "request", "schema", "digest", "source_hash", "source_duplicate",
    "source_id", "source_missing", "extra_input", "extra_pack", "manifest", "workflow", "proof", "unbound"])
def test_original_input_binding_rechecked(compiled, case, mutation):
    _, incoming, draft, _ = case
    pack = incoming["context_pack"]
    if mutation == "role": incoming["role_id"] = "request_architect"
    elif mutation == "use_case": pack["use_case"] = "request_architect"
    elif mutation == "request": incoming["correlation_id"] = "different"
    elif mutation == "schema": pack["payload_schema_json"] += " "
    elif mutation == "digest": pack["payload_schema_sha256"] = "0" * 64
    elif mutation == "source_hash": pack["sources"][0]["content_sha256"] = "0" * 64
    elif mutation == "source_duplicate": pack["sources"] *= 2
    elif mutation == "source_id": pack["sources"][0]["source_id"] += "\n"
    elif mutation == "source_missing": pack["sources"] = []
    elif mutation == "extra_input": incoming["extra"] = True
    elif mutation == "extra_pack": pack["extra"] = True
    elif mutation == "manifest": pack["knowledge_manifest_sha256"] = "invalid"
    elif mutation == "workflow": incoming["workflow_version"] = "invalid"
    elif mutation == "proof": incoming["knowledge_proof"] = "invalid"
    else: compiled[0]["_DRAFT_SCHEMA"] = None
    with pytest.raises(ValueError, match=ERROR): assemble(compiled, incoming, draft)


@pytest.mark.parametrize("limit", ["claims", "bindings", "text"])
@pytest.mark.parametrize("exceeds", [False, True])
def test_exact_envelope_bounds_fail_without_truncation(compiled, case, limit, exceeds):
    _, incoming, draft, _ = case
    item = draft["payload"]["items"][0]
    if limit == "claims":
        item["children"] = [{"summary": leaf(f"s{i}"), "scope_in": [leaf(f"scope{i}")], "dod": [leaf(f"done{i}")]} for i in range(12)]
        if exceeds: draft["payload"]["rationale"] = leaf("extra")
    elif limit == "bindings":
        item["children"] = [{"summary": leaf("x"), "scope_in": [leaf("x")] * 6, "dod": [leaf("x")] * 5}] * 21
        if exceeds: draft["payload"]["rationale"] = leaf("x")
    else:
        draft["payload"]["items"] = []
        draft["payload"]["questions"] = [{"question": leaf("x" * (1200 + exceeds)), "kind": "text", "input_need": "optional", "why_required": leaf("")}]
    Draft202012Validator(compiled[1]).validate(draft)
    before = copy.deepcopy(draft)
    if exceeds:
        with pytest.raises(ValueError, match=ERROR): assemble(compiled, incoming, draft)
    else:
        result = assemble(compiled, incoming, draft)
        if limit == "claims": assert len(result["claims"]) == 40
        elif limit == "bindings": assert len(result["payload_claims"]) == 256
        else: assert max(len(row["text"]) for row in result["claims"]) == 1200
    assert draft == before


def test_canonical_schema_is_revalidated_even_if_native_contract_is_wrong(compiled, case):
    _, incoming, draft, _ = case
    native = compiled[0]["_DRAFT_SCHEMA"]["properties"]["payload"]["anyOf"][1]
    native["properties"]["items"]["items"]["properties"]["summary"]["anyOf"][0]["properties"]["value"]["maxLength"] = 181
    draft["payload"]["items"][0]["summary"]["value"] = "x" * 181
    Draft202012Validator(compiled[0]["_DRAFT_SCHEMA"]).validate(draft)
    with pytest.raises(ValueError, match=ERROR): assemble(compiled, incoming, draft)


@pytest.mark.parametrize("raw", ['{"status":"ok","status":"blocked"}', '{"x":NaN}', '{"x":Infinity}',
    '{"x":-Infinity}', '{"x":1e999}', '[]', '```json\n{}\n```', '{"x":' + '[' * 34 + '0' + ']' * 34 + '}'])
def test_strict_candidate_json_has_no_extraction_or_repair(compiled, case, raw):
    with pytest.raises(ValueError, match=ERROR): compiled[0]["main"](encoded(case[1]), raw)


def test_raw_candidate_byte_boundary_and_canonical_input(compiled, case):
    _, incoming, draft, _ = case
    expected = assemble(compiled, incoming, draft)
    raw = encoded(draft)
    boundary = raw + " " * (32768 - len(raw.encode("utf-8")))
    assert json.loads(compiled[0]["main"](encoded(incoming), boundary)["candidate"]) == expected
    with pytest.raises(ValueError, match=ERROR): compiled[0]["main"](encoded(incoming), boundary + " ")
    with pytest.raises(ValueError, match=ERROR): compiled[0]["main"](json.dumps(incoming, indent=2), raw)


@pytest.mark.parametrize("value", ["true", "false", 0, 1, None])
def test_internal_adapter_does_not_loosen_either_final_boolean_guard(compiled, case, value):
    pack, incoming, draft, _ = case
    draft["payload"].update(items=[], questions=[{"question": leaf("대상은?"), "kind": "text", "input_need": "required", "why_required": leaf("대상 필요")}])
    result = assemble(compiled, incoming, draft)
    result["payload"]["questions"][0]["required_input"] = value
    remote, local = guards(pack, result)
    assert remote["warnings"] == ["contract_guard:payload_type_mismatch"]
    with pytest.raises(ResponseGuardError): local()
