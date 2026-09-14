"""Provider-free contracts for the reviewed Editor Input projection."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from app.agent.maxis import EditorDraftV1, EvidencePackV1, ResponseGuard
from app.agent.maxis.contracts import canonical_payload_schema


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "editor_input_test_asset", ROOT / "deploy/maxis-dify/code/editor_assembler.py")
ASSET = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ASSET)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.fixture
def input_pack(context_guard, now):
    schema = EditorDraftV1.model_json_schema()
    schema["properties"]["mode"] = {"type": "string", "const": "comment"}
    return context_guard.build(
        request_id="req-input", use_case="editor_author", knowledge_manifest_sha256="a" * 64,
        query="현재 요청에 맞는 댓글 작성", payload_schema=schema, user_identity="test-reviewer",
        requested_at=now, sources=[{
            "source_id": "source-doc", "kind": "confluence", "canonical_id": "doc-design",
            "title": "검토 기준", "scope": "DATALAKE", "url": "https://wiki.example.local/design",
            "content": '{"scope":"조회 전용이다.","criteria":["검토 기준을 확인한다.","상태를 확인한다."]}',
            "retrieved_at": now,
        }],
    ).model_dump(mode="json")


def model_input(pack):
    return canonical({"contract": "ltm-model-input.v1", "context_pack": pack,
        "role_id": "editor_author", "correlation_id": pack["request_id"],
        "knowledge_context": [{"document_name": "editor-rules", "content": "근거를 보존한다."}],
        "knowledge_proof": "LTM_RELEASE_SENTINEL_V1=test-only",
        "knowledge_version": "kb-test", "workflow_version": "11111111-1111-4111-8111-111111111111",
        "output_contract": "remote-role-result.v1"})


def view(pack):
    return json.loads(ASSET.prepare(model_input(pack))["model_input"])


def add_intent(pack, **request):
    source = copy.deepcopy(pack["sources"][0])
    text = canonical({"kind": "comment", "prompt": "확인할 사항을 작성해 줘", **request})
    source.update(source_id="intent", kind="ltm", canonical_id="ltm:user-intent:test",
                  title="Author request", content=text, url="",
                  content_sha256=hashlib.sha256(text.encode()).hexdigest())
    pack["sources"].append(source)


def test_prepare_is_deterministic_bounded_and_does_not_mint_evidence(input_pack):
    before = copy.deepcopy(input_pack)
    first = view(input_pack)
    assert first == view(input_pack)
    assert input_pack == before
    assert set(first) == {"contract", "role_id", "request", "knowledge_context", "sources"}
    assert first["contract"] == "editor-input.v1"
    assert first["role_id"] == "editor_author"
    assert len(canonical(first).encode()) <= 9000
    assert not {"facts", "payload_schema_json", "knowledge_proof", "context_pack"} & set(first)
    source = first["sources"][0]
    assert source["available_link"] is True and "url" not in source
    assert {x["text"] for x in source["units"]} == {
        "검토 기준", "조회 전용이다.", "검토 기준을 확인한다.", "상태를 확인한다."}
    assert all(set(x) == {"unit_id", "path", "text"} for x in source["units"])
    assert any(x["path"] == "/content/criteria/0" for x in source["units"])


def test_intent_is_request_not_fact_units(input_pack):
    add_intent(input_pack, seed_html="<p>측정 결과가</p>", ticket_key="DL-42")
    result = view(input_pack)
    assert result["request"] == {"kind": "comment", "target": "DL-42", "source_ids": ["intent"],
        "prompt": "확인할 사항을 작성해 줘", "seed_visible": "측정 결과가"}
    assert all(x["source_id"] != "intent" for x in result["sources"])


def test_non_editor_canary_is_byte_identical_passthrough(input_pack):
    schema = {"type": "object", "additionalProperties": False,
              "properties": {"status": {"const": "ready"}}, "required": ["status"]}
    input_pack["payload_schema_json"], input_pack["payload_schema_sha256"] = canonical_payload_schema(schema)
    raw = "  " + model_input(input_pack) + "\n"
    assert ASSET.prepare(raw) == {"model_input": raw}


@pytest.mark.parametrize("mutation", ["digest", "mode", "partial", "stale", "role", "request"])
def test_invalid_context_fails_before_model_without_reflected_data(input_pack, mutation):
    if mutation == "digest":
        input_pack["payload_schema_sha256"] = "0" * 64
    elif mutation == "mode":
        add_intent(input_pack, kind="description")
    elif mutation == "partial":
        input_pack["partial"] = True
    elif mutation == "stale":
        input_pack["freshness"] = "stale"
    raw = model_input(input_pack)
    if mutation in {"role", "request"}:
        value = json.loads(raw)
        value["role_id" if mutation == "role" else "correlation_id"] = "mismatch-secret-value"
        raw = canonical(value)
    with pytest.raises(ValueError, match="^invalid_editor_input$"):
        ASSET.prepare(raw)


def test_unit_id_rebinds_when_original_source_changes(input_pack):
    old_ids = {x["unit_id"] for x in view(input_pack)["sources"][0]["units"]}
    source = input_pack["sources"][0]
    source["content"] = source["content"].replace("조회 전용", "검토 전용")
    source["content_sha256"] = hashlib.sha256(source["content"].encode()).hexdigest()
    new_ids = {x["unit_id"] for x in view(input_pack)["sources"][0]["units"]}
    assert old_ids.isdisjoint(new_ids)


def test_body_display_policy_keeps_prepare_assemble_and_original_binding_consistent(input_pack):
    source = input_pack["sources"][0]
    source.update(kind="jira", content=canonical({"description": "h3. 완료 기준\n* 원문 근거를 보존한다."}))
    source["content_sha256"] = hashlib.sha256(source["content"].encode()).hexdigest()
    before = copy.deepcopy(input_pack)
    projected = view(input_pack)
    selected = next(unit for unit in projected["sources"][0]["units"] if unit["text"] == "완료 기준")
    originals = dict(ASSET._original_units(source))
    assert originals[selected["path"]] == "h3. 완료 기준"
    binding = [ASSET._UNIT_DISPLAY_POLICY, source["source_id"], source["kind"], source["canonical_id"],
               source["content_sha256"], source["title"], source["url"], selected["path"],
               originals[selected["path"]], "완료 기준", "완료 기준", [source["source_id"]]]
    assert ASSET._UNIT_DISPLAY_POLICY == "editor-units.v2-wiki-body-display"
    assert selected["unit_id"] == "u_" + hashlib.sha256(canonical(binding).encode()).hexdigest()[:20]
    plan = {"contract": "editor-plan.v2", "status": "ok", "missing_inputs": [],
        "sections": [{"section": "comment", "blocks": [{"type": "fact", "tag": "p",
            "unit_id": selected["unit_id"]}]}]}
    request = json.loads(model_input(input_pack))
    response = ASSET.main(canonical(plan), canonical(input_pack), "editor_author", input_pack["request_id"],
        "remote-role-result.v1", request["workflow_version"], request["knowledge_version"], request["knowledge_proof"])
    assert json.loads(response["candidate"])["payload"]["content_html"] == "<p>완료 기준</p>"
    assert projected == view(input_pack) and input_pack == before
    source["content"] = source["content"].replace("h3.", "h2.")
    source["content_sha256"] = hashlib.sha256(source["content"].encode()).hexdigest()
    rebound = next(unit for unit in view(input_pack)["sources"][0]["units"] if unit["text"] == "완료 기준")
    assert rebound["unit_id"] != selected["unit_id"]  # Same display, different authoritative bytes.


@pytest.mark.parametrize("kind,other_kind,field", [
    ("confluence", "web", "body"), ("jira", "bitbucket", "description"),
])
def test_unit_id_binds_source_kind_display_policy_without_changing_source(input_pack, kind, other_kind, field):
    source = input_pack["sources"][0]
    source.update(kind=kind, content=canonical({field: "h3. 완료 기준"}))
    source["content_sha256"] = hashlib.sha256(source["content"].encode()).hexdigest()
    before = copy.deepcopy(input_pack)
    first = next(unit for unit in view(input_pack)["sources"][0]["units"] if unit["path"] != "/title")
    assert first["text"] == "완료 기준" and input_pack == before
    source["kind"] = other_kind
    after_kind_change = copy.deepcopy(input_pack)
    second = next(unit for unit in view(input_pack)["sources"][0]["units"] if unit["path"] != "/title")
    assert second["text"] == "h3. 완료 기준"
    assert second["unit_id"] != first["unit_id"]
    assert input_pack == after_kind_change
    assert {key: value for key, value in source.items() if key != "kind"} == {
        key: value for key, value in before["sources"][0].items() if key != "kind"}


def test_unit_id_binds_cross_source_anchor_display_and_prepare_main_stay_in_sync(input_pack):
    source = input_pack["sources"][0]
    target = copy.deepcopy(source)
    target.update(source_id="source-link", canonical_id="doc-linked", title="첫 번째 검토 기준",
                  url="https://wiki.example.local/linked", content="원본 검토 근거를 보존한다.")
    target["content_sha256"] = hashlib.sha256(target["content"].encode()).hexdigest()
    source["content"] = "참고 자료 " + target["url"]
    source["content_sha256"] = hashlib.sha256(source["content"].encode()).hexdigest()
    input_pack["sources"].append(target)
    before = copy.deepcopy(input_pack)

    def selected_unit():
        return next(unit for unit in view(input_pack)["sources"][0]["units"] if unit["path"] == "/content")

    def assemble(unit):
        plan = {"contract": "editor-plan.v2", "status": "ok", "missing_inputs": [],
            "sections": [{"section": "comment", "blocks": [{"type": "fact", "tag": "p",
                "unit_id": unit["unit_id"]}]}]}
        request = json.loads(model_input(input_pack))
        response = ASSET.main(canonical(plan), canonical(input_pack), "editor_author", input_pack["request_id"],
            "remote-role-result.v1", request["workflow_version"], request["knowledge_version"], request["knowledge_proof"])
        return json.loads(response["candidate"])

    first = selected_unit()
    original_result = assemble(first)
    assert first["text"] == "참고 자료 첫 번째 검토 기준"
    assert original_result["status"] == "ok"
    assert original_result["claims"][0]["text"] == first["text"]
    assert input_pack == before

    target["title"] = "변경된 검토 기준"
    after_title_change = copy.deepcopy(input_pack)
    second = selected_unit()
    assert second["text"] == "참고 자료 변경된 검토 기준"
    assert second["unit_id"] != first["unit_id"]
    updated_result = assemble(second)
    assert updated_result["status"] == "ok"
    assert updated_result["claims"][0]["text"] == second["text"]
    assert '>변경된 검토 기준</a>' in updated_result["payload"]["content_html"]
    assert {citation["source_id"] for citation in updated_result["citations"]} == {source["source_id"], target["source_id"]}
    assert assemble(first)["status"] == "blocked"  # A stale selection cannot silently change its display.
    assert source == before["sources"][0] and input_pack == after_title_change


def test_catalog_overflow_fails_without_silent_truncation(input_pack):
    source = input_pack["sources"][0]
    source["content"] = canonical(["항목" + str(x) for x in range(140)])
    source["content_sha256"] = hashlib.sha256(source["content"].encode()).hexdigest()
    with pytest.raises(ValueError, match="^invalid_editor_input$"):
        view(input_pack)


@pytest.mark.parametrize("raw", ["{}", "```json\n{}\n```", '{"a":NaN}',
    '{"contract":"a","contract":"b"}', "x" * 9001])
def test_invalid_model_input_is_content_free(raw):
    with pytest.raises(ValueError, match="^invalid_editor_input$"):
        ASSET.prepare(raw)


def test_request_only_projection_can_cite_intent_as_proposal_through_both_guards(input_pack):
    add_intent(input_pack, seed_html="<p>검토를 요청하려는데</p>")
    input_pack["sources"] = [input_pack["sources"][-1]]
    projected = view(input_pack)
    assert projected["sources"] == []
    assert projected["request"]["source_ids"] == ["intent"]
    plan = {"contract": "editor-plan.v2", "status": "ok", "missing_inputs": [],
        "sections": [{"section": "comment", "blocks": [{"type": "proposal", "tag": "p",
            "text": "검토할 대상과 요청할 내용을 확인해 주세요.",
            "source_ids": projected["request"]["source_ids"]}]}]}
    request = json.loads(model_input(input_pack))
    result = ASSET.main(canonical(plan), canonical(input_pack), "editor_author", input_pack["request_id"],
        "remote-role-result.v1", request["workflow_version"], request["knowledge_version"], request["knowledge_proof"])
    guard_spec = importlib.util.spec_from_file_location("request_only_guard", ROOT / "deploy/maxis-dify/code/contract_guard.py")
    guard = importlib.util.module_from_spec(guard_spec)
    guard_spec.loader.exec_module(guard)
    remote = guard.main(result["candidate"], canonical(input_pack), "editor_author", input_pack["request_id"],
        "remote-role-result.v1", request["workflow_version"], request["workflow_version"],
        request["knowledge_version"], [{"content": request["knowledge_proof"]}])
    accepted, payload = ResponseGuard().validate_remote_role(remote["result"], expected_role_id="editor_author",
        payload_model=EditorDraftV1, evidence=EvidencePackV1.model_validate_json(canonical(input_pack)),
        expected_knowledge_manifest_sha256=input_pack["knowledge_manifest_sha256"])
    assert accepted.status == "ok" and payload.content_html.startswith("<p>검토를 요청하려는데</p>")
    assert {claim.kind for claim in accepted.claims} == {"inference", "recommendation"}
