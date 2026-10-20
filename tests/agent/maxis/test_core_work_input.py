"""Work's semantic view cannot replace original-input or final-guard authority."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from test_core_work_assembler import case, compiled as assembler, encoded, guards, leaf

DIRECTORY = Path(__file__).resolve().parents[3] / "deploy/maxis-dify"


@pytest.fixture
def compiled(assembler):
    namespace = {"__name__": "work_input_test"}
    source = "\n\n".join((DIRECTORY / "code" / name).read_text(encoding="utf-8") for name in
        ("core_draft_validation.py", "core_draft_context.py", "core_work_input.py"))
    exec(compile(source, "<work-input>", "exec"), namespace)
    for key in ("_WORK_SCHEMA_JSON", "_WORK_SCHEMA_SHA256", "_DRAFT_SCHEMA"):
        namespace[key] = assembler[0][key]
    return namespace


def view(compiled, incoming):
    result = compiled["main"](encoded(incoming))
    assert set(result) == {"work_input"}
    assert encoded(json.loads(result["work_input"])) == result["work_input"]
    return json.loads(result["work_input"])


def context(incoming, value, name="request_plan"):
    raw = encoded(value)
    row = {"context_id": "ctx-plan", "name": name, "content_json": raw,
        "content_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "observed_at": incoming["context_pack"]["requested_at"], "partial": False}
    incoming["context_pack"]["role_context"] = [row]
    return row


def reject(compiled, incoming):
    with pytest.raises(ValueError, match="^invalid_core_work_input$"):
        compiled["main"](incoming if isinstance(incoming, str) else encoded(incoming))


def test_exact_view_keeps_order_identity_hashes_refs_and_business_text(compiled, case):
    _, incoming, _, _ = case
    context(incoming, {"goal": {"source_content_index": 0}, "tasks": [
        {"instruction": {"source_content_index": 0}, "id": "business-id"}],
        "literal": '{"source_content_index":99}'})
    before = copy.deepcopy(incoming)
    expected = copy.deepcopy(incoming["context_pack"])
    for key in ("contract", "payload_schema_json", "payload_schema_sha256", "knowledge_manifest_sha256"):
        del expected[key]
    for index, row in enumerate(expected["sources"]):
        del row["content_sha256"]
        canonical = row.pop("canonical_id")
        if index != 0: row["entity_reference"] = canonical
        row["source_authority"] = "user_intent" if index == 0 else "evidence_source"
        row["allowed_claim_kinds"] = ["inference", "recommendation"] if index == 0 else ["fact", "inference", "recommendation"]
    assert view(compiled, incoming) == {"contract": "work-input.v2", "context": expected,
        "knowledge_context": incoming["knowledge_context"]}
    assert incoming == before


def test_large_complete_query_crosses_native_input_assembler_and_final_guards(
    compiled, assembler, case, context_guard, now,
):
    from app.agent.workflow.work_architect.contracts import CREATE_SCHEMA
    from test_dify_knowledge_guard_code import _arguments, _item
    from test_core_work_assembler import VERSION
    original, _, draft, expected = case
    sources = [row.model_dump() for row in original.sources]
    sources[1]["content"] = encoded({
        "complete": True,
        "tickets": [{"key": f"DL-{index}", "description": "검증 전 운영 반영은 금지한다. " * 55}
                    for index in range(5)],
    })
    pack = context_guard.build(
        request_id=original.request_id, use_case="work_architect", query=original.query,
        payload_schema=CREATE_SCHEMA, knowledge_manifest_sha256="a" * 64,
        user_identity="synthetic", requested_at=now, sources=sources,
    )
    assert 6144 < len(encoded(pack.model_dump(mode="json")).encode()) <= 32768
    assert not pack.partial and pack.freshness == "fresh"
    native, args = _arguments("agent_core", "work_architect")
    name = native["_ALLOWED_DOCUMENT_NAMES_BY_ROLE"]["work_architect"][0]
    args[0] = [_item(dataset_id=native["_DATASET_ID"],
                    document_id=native["_DOCUMENT_IDS_BY_NAME"][name], document_name=name)]
    args[2:6] = [encoded(pack.model_dump(mode="json")), pack.query, "work_architect", pack.request_id]
    args[7:10] = [VERSION, VERSION, "kb-test"]
    accepted = native["main"](*args)
    assert accepted["policy_decision"] == "allow"
    incoming = json.loads(accepted["model_input"])
    assert view(compiled, incoming)["context"]["sources"][1]["content"] == sources[1]["content"]
    candidate = json.loads(assembler[0]["main"](accepted["model_input"], encoded(draft))["candidate"])
    remote, local = guards(pack, candidate)
    assert remote == candidate and local()[1] == expected


@pytest.mark.parametrize("kind,canonical,intent", [
    ("ltm", "ltm:user-intent:x", True), ("ltm", "ltm:user-intent:", True),
    ("jira", "ltm:user-intent:x", False), ("ltm", "DL-123", False),
    ("ltm", "LTM:user-intent:x", False), ("ltm", "xltm:user-intent:x", False)])
def test_authority_matches_final_guards_not_source_id_or_content(compiled, assembler, case, kind, canonical, intent):
    from app.agent.maxis import ResponseGuardError
    pack, incoming, draft, _ = case
    row = incoming["context_pack"]["sources"][0]
    row.update(kind=kind, canonical_id=canonical, scope="DL")
    result = view(compiled, incoming)["context"]["sources"][0]
    assert "canonical_id" not in result and result["content"] == row["content"]
    assert ("entity_reference" not in result) if intent else result["entity_reference"] == canonical
    assert result["allowed_claim_kinds"] == (["inference", "recommendation"] if intent else ["fact", "inference", "recommendation"])
    pack = type(pack).model_validate_json(encoded(incoming["context_pack"]))
    draft["payload"]["items"][0]["summary"] = leaf(row["content"], "fact")
    candidate = json.loads(assembler[0]["main"](encoded(incoming), encoded(draft))["candidate"])
    remote, local = guards(pack, candidate)
    if intent:
        assert remote["warnings"] == ["contract_guard:intent_source_fact_forbidden"]
        with pytest.raises(ResponseGuardError, match="intent_source_fact_forbidden"): local()
    else:
        assert remote == candidate and local()[1] == candidate["payload"]


@pytest.mark.parametrize("location", ["goal", "instruction"])
@pytest.mark.parametrize("value", [True, False, 0.0, "0", -1, 2, None])
def test_only_exact_integer_in_range_intent_refs(compiled, case, location, value):
    _, incoming, _, _ = case
    ref = {"source_content_index": value}
    context(incoming, {"goal": ref} if location == "goal" else {"tasks": [{"instruction": ref}]})
    reject(compiled, incoming)


@pytest.mark.parametrize("value", [{"source_content_index": 1}, {"source_content_index": 0, "extra": 1},
                                  {}, [], True, 1])
def test_bad_reference_shape_or_nonintent_row_fails(compiled, case, value):
    _, incoming, _, _ = case
    context(incoming, {"goal": value})
    reject(compiled, incoming)


def test_last_index_and_both_intent_predicates_are_required(compiled, case):
    _, incoming, _, _ = case
    row = incoming["context_pack"]["sources"][1]
    row.update(canonical_id="ltm:user-intent:last")
    context(incoming, {"goal": {"source_content_index": 1}})
    assert view(compiled, incoming)["context"]["role_context"] == incoming["context_pack"]["role_context"]
    row.update(kind="jira", scope="DL")
    reject(compiled, incoming)


@pytest.mark.parametrize("count", [0, 1])
def test_no_source_is_added_or_replaced(compiled, case, count):
    _, incoming, _, _ = case
    incoming["context_pack"]["sources"] = incoming["context_pack"]["sources"][:count]
    assert len(view(compiled, incoming)["context"]["sources"]) == count


def test_reference_like_business_values_are_neither_decoded_nor_rewritten(compiled, case):
    _, incoming, _, _ = case
    for name, value in [("request_plan", {"goal": '{"source_content_index":99}',
            "tasks": [{"instruction": "원문 source_content_index", "business": {"source_content_index": True}}]}),
            ("evidence", {"source_content_index": 99}), ("situation", ["source_content_index", None, True])]:
        row = context(incoming, value, name)
        assert view(compiled, incoming)["context"]["role_context"] == [row]


@pytest.mark.parametrize("field,value", [("content_sha256", "0" * 64), ("partial", "false"),
    ("name", "bad name"), ("observed_at", None), ("content_json", '{"a":1,"a":2}'),
    ("content_json", '{"a":NaN}'), ("content_json", '{"a":1e999}'), ("content_json", '{ "a":1}')])
def test_role_context_hash_frame_and_canonical_json_are_checked(compiled, case, field, value):
    _, incoming, _, _ = case
    row = context(incoming, {"goal": "요청"})
    row[field] = value
    if field == "content_json": row["content_sha256"] = hashlib.sha256(value.encode()).hexdigest()
    reject(compiled, incoming)


@pytest.mark.parametrize("field,value", [("kind", "unknown"), ("canonical_id", ""), ("canonical_id", 1),
    ("content", None), ("partial", 0), ("truncated", "false"), ("updated_at", []), ("title", None)])
def test_source_frame_has_no_repair_or_authority_coercion(compiled, case, field, value):
    _, incoming, _, _ = case
    incoming["context_pack"]["sources"][0][field] = value
    reject(compiled, incoming)


def test_original_and_output_utf8_budgets_fail_without_truncation(compiled, case, monkeypatch):
    _, incoming, _, _ = case
    current = encoded(view(compiled, incoming))
    monkeypatch.setitem(compiled, "_MAX_WORK_INPUT_UTF8_BYTES", len(current.encode()))
    assert encoded(view(compiled, incoming)) == current
    monkeypatch.setitem(compiled, "_MAX_WORK_INPUT_UTF8_BYTES", len(current.encode()) - 1)
    reject(compiled, incoming)
    monkeypatch.setitem(compiled, "_MAX_WORK_INPUT_UTF8_BYTES", 40000)
    for location, maximum in ((incoming["context_pack"], 32768), (incoming, 40000)):
        target, field = (location, "query") if location is incoming["context_pack"] else (incoming, "knowledge_context")
        if field == "query":
            target[field] = "한" * 11000
        else:
            incoming["context_pack"]["query"] = "원문"
            target[field] = ["한" * 14000]
        assert len(encoded(location).encode()) > maximum
        reject(compiled, incoming)


@pytest.mark.parametrize("kind", ["fact", "recommendation"])
def test_projection_never_replaces_original_assembler_or_guards(compiled, assembler, case, kind):
    from app.agent.maxis import ResponseGuardError
    pack, incoming, draft, _ = case
    projected = view(compiled, incoming)
    draft["payload"]["items"][0]["summary"] = leaf("원문 초안", kind)
    candidate = json.loads(assembler[0]["main"](encoded(incoming), encoded(draft))["candidate"])
    assert candidate["claims"][-1]["kind"] in {"recommendation", "fact"}
    assert any(row["text"] == "원문 초안" and row["kind"] == kind for row in candidate["claims"])
    remote, local = guards(pack, candidate)
    if kind == "fact":
        assert remote["warnings"] == ["contract_guard:intent_source_fact_forbidden"]
        with pytest.raises(ResponseGuardError, match="intent_source_fact_forbidden"): local()
    else:
        assert remote == candidate and local()[1] == candidate["payload"]
    with pytest.raises(ValueError, match="invalid_core_work_draft"):
        assembler[0]["main"](encoded(projected), encoded(draft))


@pytest.mark.parametrize("mutation", [lambda x: x.update(role_id="request_architect"),
    lambda x: x["context_pack"].update(payload_schema_sha256="0" * 64),
    lambda x: x["context_pack"].update(use_case="query_specialist"),
    lambda x: x["context_pack"].update(sources=x["context_pack"]["sources"] * 2),
    lambda x: x["context_pack"].update(role_context=[{}]),
    lambda x: x["context_pack"]["sources"][0].update(extra="field")])
def test_bad_original_bindings_fail_closed(compiled, case, mutation):
    _, incoming, _, _ = case
    mutation(incoming)
    reject(compiled, incoming)
