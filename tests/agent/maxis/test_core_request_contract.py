"""The Request draft derives from the live architect schema, not persisted state."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.agent.maxis.contracts import canonical_payload_schema
from app.agent.workflow.agents.request_architect import RequestArchitect, SCHEMA

DIRECTORY = Path(__file__).resolve().parents[3] / "deploy/maxis-dify"


def load(path):
    spec = importlib.util.spec_from_file_location("request_contract_test_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def contract():
    return load(DIRECTORY / "core_request_contract.py")


@pytest.fixture
def canonical():
    assert RequestArchitect.schema(None) is SCHEMA
    return json.loads(canonical_payload_schema(SCHEMA)[0])


def project(schema):
    return load(DIRECTORY / "validate_bundle.py")._dify_structured_output_schema(schema)


def payload(contract, canonical):
    return contract.build_draft_schema(canonical, project)["properties"]["payload"]["anyOf"][1]


def test_actual_architect_schema_pin_and_internal_root_closed(contract, canonical):
    before = copy.deepcopy(canonical)
    draft = contract.build_draft_schema(canonical, project)
    assert canonical_payload_schema(SCHEMA)[1] == contract.REQUEST_SCHEMA_SHA256
    assert canonical == before and "additionalProperties" not in canonical
    assert payload(contract, canonical)["additionalProperties"] is False
    assert draft["properties"]["contract"]["enum"] == ["request-draft.v2"]
    assert set(draft["properties"]) == {"contract", "status", "payload", "missing_inputs"}
    assert draft["additionalProperties"] is False
    Draft202012Validator.check_schema(draft)


def test_wrappers_cover_exact_guard_narratives_plus_model_owned_intent(contract, canonical):
    draft = payload(contract, canonical)
    guard = load(DIRECTORY / "code/contract_guard.py")
    wrapped, strings = set(), {}

    def walk(original, derived, path="", field=""):
        if original["type"] == "string":
            strings[path] = field
            if derived.get("type") == "object":
                wrapped.add(path)
                assert derived["properties"]["value"] == original
                assert derived["required"] == ["value", "kind", "source_ids"]
                assert derived["additionalProperties"] is False
                kinds = ["inference", "recommendation"] if path in {"/intent", "/keywords/*"} else ["fact", "inference", "recommendation"]
                assert derived["properties"]["kind"]["enum"] == kinds
            else:
                assert derived == original
        elif original["type"] == "array":
            assert {k: v for k, v in derived.items() if k != "items"} == {k: v for k, v in original.items() if k != "items"}
            walk(original["items"], derived["items"], path + "/*", field)
        elif original["type"] == "object":
            for key, row in original["properties"].items():
                if row["type"] != "boolean":
                    walk(row, derived["properties"][key], path + "/" + key, key)

    walk(canonical, draft)
    assert wrapped == {path for path, field in strings.items() if not guard._control_field(field)} | {"/intent"}
    assert "/mentioned_keys/*" in wrapped and "/requested_effects/*/target" in wrapped
    assert "/target_selectors/*/cardinality" in wrapped and "/tasks/*/depends_on/*" not in wrapped


def test_boolean_wire_fields_are_explicit_and_required_without_defaults(contract, canonical):
    result = payload(contract, canonical)
    fields = result["properties"]
    assert "sufficient" not in fields and "sufficient" not in result["required"]
    assert "research_readiness" in result["required"]
    assert fields["research_readiness"] == {"type": "string", "enum": ["ready", "needs_clarification"]}
    task = fields["tasks"]["items"]
    assert "write_intent" not in task["properties"] and "write_intent" not in task["required"]
    assert "effect" in task["required"]
    assert task["properties"]["effect"] == {"type": "string", "enum": ["write_requested", "no_write_requested"]}
    assert fields["requested_effects"]["uniqueItems"] is True
    assert fields["mentioned_keys"]["items"]["properties"]["value"] == {"type": "string"}


@pytest.mark.parametrize("mutation", ["extra", "required", "bound", "enum", "boolean", "const", "root", "nested", "pattern", "annotation"])
def test_unreviewed_canonical_changes_fail_closed(contract, canonical, mutation):
    fields = canonical["properties"]
    if mutation == "extra":
        fields["new_field"] = {"type": "string"}
    elif mutation == "required":
        canonical["required"].remove("sufficient")
    elif mutation == "bound":
        fields["mentioned_keys"]["maxItems"] = 10
    elif mutation == "enum":
        fields["intent"]["enum"].append("new_intent")
    elif mutation == "boolean":
        fields["sufficient"] = {"enum": [False, True]}
    elif mutation == "const":
        fields["sufficient"]["const"] = False
    elif mutation == "root":
        canonical["additionalProperties"] = False
    elif mutation == "nested":
        del fields["tasks"]["items"]["additionalProperties"]
    elif mutation == "pattern":
        fields["requested_effects"]["items"]["properties"]["target"]["pattern"] = ".*"
    else:
        fields["intent"]["description"] = "Not canonical input"
    with pytest.raises(ValueError, match="^Request draft canonical schema requires review$"):
        contract.build_draft_schema(canonical, project)


def test_projection_must_preserve_every_canonical_constraint(contract, canonical):
    def lossy(schema):
        result = copy.deepcopy(schema)
        del result["properties"]["goal"]["maxLength"]
        return result
    with pytest.raises(ValueError, match="^Request draft canonical schema requires review$"):
        contract.build_draft_schema(canonical, lossy)


@pytest.fixture
def examples():
    minimal = {"intent": "ask", "keywords": [], "sufficient": True,
               "request_questions": [], "requested_effects": [], "target_selectors": []}
    complete = {**minimal, "intent": "modify", "keywords": ["작업 초안"], "module": "ETL",
        "mentioned_keys": ["DL-123"], "sufficient": False, "playbook": "knowledge", "answer_depth": "explain",
        "goal": "변경 초안을 검토한다", "tasks": [
            {"id": "task-a", "kind": "plan", "instruction": "변경 초안을 작성한다", "depends_on": [],
             "write_intent": False, "completion_criteria": ["초안 검토"]},
            {"id": "task-b", "kind": "write", "instruction": "명시한 제목을 변경한다", "depends_on": ["task-a"],
             "write_intent": True, "completion_criteria": ["제목 변경"]}],
        "blocking_questions": ["대상 범위를 확인해 주세요"],
        "request_questions": [{"field": "scope", "question": "적용 범위를 알려주세요"}],
        "requested_effects": [{"target": "DL-123", "field": "summary", "value": "변경 제목", "literal": "변경 제목"}],
        "target_selectors": [{"task_id": "task-b", "anchor_key": "DL-123", "relation": "direct_child",
                              "state": "incomplete", "cardinality": "exactly_one"}],
        "assumptions": ["대상 확인 필요"], "plan": "확인 → 초안 작성"}
    return minimal, complete


def draftify(value, field=""):
    """Independent test fixture: use the unchanged guard's narrative field rules."""
    if isinstance(value, dict):
        result = {}
        for key, row in value.items():
            if key == "sufficient":
                result["research_readiness"] = "ready" if row else "needs_clarification"
            elif key == "write_intent":
                result["effect"] = "write_requested" if row else "no_write_requested"
            else:
                result[key] = draftify(row, key)
        return result
    if isinstance(value, list):
        return [draftify(row, field) for row in value]
    if isinstance(value, str) and (field == "intent" or not load(DIRECTORY / "code/contract_guard.py")._control_field(field)):
        return {"value": value, "kind": "recommendation", "source_ids": ["src-intent-1"]}
    return value


def example_draft(example):
    return {"contract": "request-draft.v2", "status": "ok", "payload": draftify(example), "missing_inputs": []}


@pytest.mark.parametrize("path", [("intent",), ("keywords", 0)])
@pytest.mark.parametrize("refs", [["intent-only"], ["evidence-only"], ["intent-only", "evidence-only"]])
@pytest.mark.parametrize("kind", ["fact", "inference", "recommendation"])
def test_selection_kind_is_independent_of_source_authority(contract, canonical, examples, path, refs, kind):
    draft = example_draft(examples[1])
    native = contract.build_draft_schema(canonical, project)
    draft["contract"] = native["properties"]["contract"]["enum"][0]
    target = draft["payload"]
    for key in path:
        target = target[key]
    target.update(kind=kind, source_ids=refs)
    assert Draft202012Validator(native).is_valid(draft) is (kind != "fact")


@pytest.mark.parametrize("path", [
    ("module",), ("playbook",), ("answer_depth",), ("research_readiness",),
    ("tasks", 0, "id"), ("tasks", 0, "kind"), ("tasks", 1, "depends_on", 0), ("tasks", 0, "effect"),
    ("request_questions", 0, "field"), ("requested_effects", 0, "field"),
    ("target_selectors", 0, "task_id"), ("target_selectors", 0, "anchor_key"),
    ("target_selectors", 0, "relation"), ("target_selectors", 0, "state"),
])
def test_plain_controls_reject_annotations_without_coercion(contract, canonical, examples, path):
    draft = example_draft(examples[1])
    validator = Draft202012Validator(contract.build_draft_schema(canonical, project))
    validator.validate(draft)
    target = draft["payload"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = {"value": target[path[-1]], "kind": "recommendation", "source_ids": ["intent-only"]}
    assert not validator.is_valid(draft)


def test_old_native_discriminator_is_rejected(contract, canonical, examples):
    draft = example_draft(examples[0])
    draft["contract"] = "request-draft.v1"
    assert not Draft202012Validator(contract.build_draft_schema(canonical, project)).is_valid(draft)


def test_real_minimal_and_complete_payloads_satisfy_both_schema_shapes(contract, canonical, examples):
    validator = Draft202012Validator(contract.build_draft_schema(canonical, project))
    for example in examples:
        Draft202012Validator(canonical).validate(example)
        validator.validate(example_draft(example))
    assert set(examples[1]) == set(canonical["properties"])


@pytest.mark.parametrize("ref", ["a", "A9._:-", "a" * 128])
def test_source_ids_accept_exact_guard_identifier_boundaries(contract, canonical, examples, ref):
    draft = example_draft(examples[0])
    draft["payload"]["intent"]["source_ids"] = [ref]
    assert load(DIRECTORY / "code/contract_guard.py")._IDENTIFIER.fullmatch(ref)
    Draft202012Validator(contract.build_draft_schema(canonical, project)).validate(draft)


@pytest.mark.parametrize("ref", ["src\n", "src\r\n", "sr\nc", "sr\rc", "\nsrc", "src\u2028", "src\u2029",
                                 "", "_src", "src ", "a" * 129, False, 1])
def test_source_ids_reject_newlines_and_invalid_identifiers(contract, canonical, examples, ref):
    draft = example_draft(examples[0])
    draft["payload"]["intent"]["source_ids"] = [ref]
    assert not Draft202012Validator(contract.build_draft_schema(canonical, project)).is_valid(draft)


@pytest.mark.parametrize("annotation", ["kind", "source_ids"])
def test_wrapped_uniqueness_does_not_replace_canonical_revalidation(contract, canonical, examples, annotation):
    example = examples[1]
    draft = example_draft(example)
    effect = draft["payload"]["requested_effects"][0]
    duplicate = copy.deepcopy(effect)
    duplicate["value"][annotation] = "inference" if annotation == "kind" else ["src-intent-2"]
    draft["payload"]["requested_effects"].append(duplicate)
    # Distinct annotations satisfy draft uniqueItems, but identical unwrapped
    # canonical effects do not. The assembler MUST revalidate the entire payload.
    Draft202012Validator(contract.build_draft_schema(canonical, project)).validate(draft)
    example["requested_effects"] = [
        {key: row["value"] if isinstance(row, dict) else row for key, row in item.items()}
        for item in draft["payload"]["requested_effects"]]
    assert example["requested_effects"][0] == example["requested_effects"][1]
    assert not Draft202012Validator(canonical).is_valid(example)


@pytest.mark.parametrize("path,value", [
    (("contract",), "request-draft.v0"), (("status",), "complete"),
    (("payload", "intent", "value"), "unknown"), (("payload", "intent", "kind"), "open_fact"),
    (("payload", "intent", "source_ids"), []), (("payload", "intent", "source_ids"), ["src-1"] * 2),
    (("payload", "intent", "source_ids"), ["src-" + str(i) for i in range(9)]),
    (("payload", "intent"), "modify"), (("payload", "goal", "value"), "x" * 241),
    (("payload", "module"), "Other"), (("payload", "tasks", 0, "id"), "x" * 61),
    (("payload", "tasks", 0, "kind"), "execute"), (("payload", "tasks", 0, "depends_on"), ["x"] * 6),
    (("payload", "tasks", 0, "completion_criteria"), []),
    (("payload", "request_questions", 0, "question", "value"), ""),
    (("payload", "request_questions", 0, "field"), "wrong"),
    (("payload", "requested_effects", 0, "target", "value"), "dl-123"),
    (("payload", "requested_effects", 0, "value", "value"), ""),
    (("payload", "target_selectors", 0, "cardinality", "value"), "many"),
    (("payload", "target_selectors", 0, "task_id"), ""), (("missing_inputs",), [""]),
])
def test_invalid_real_payload_fields_are_rejected(contract, canonical, examples, path, value):
    draft = example_draft(examples[1])
    node = draft
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert not Draft202012Validator(contract.build_draft_schema(canonical, project)).is_valid(draft)


@pytest.mark.parametrize("path", [("contract",), ("payload", "intent"), ("payload", "research_readiness"),
    ("payload", "intent", "value"), ("payload", "intent", "kind"), ("payload", "intent", "source_ids"),
    ("payload", "tasks", 0, "effect"), ("payload", "tasks", 0, "completion_criteria"),
    ("payload", "requested_effects", 0, "literal"), ("payload", "target_selectors", 0, "task_id")])
def test_required_envelope_payload_and_wrapper_fields_remain_required(contract, canonical, examples, path):
    draft = example_draft(examples[1])
    node = draft
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    assert not Draft202012Validator(contract.build_draft_schema(canonical, project)).is_valid(draft)


@pytest.mark.parametrize("path", [(), ("payload",), ("payload", "intent"), ("payload", "tasks", 0),
    ("payload", "request_questions", 0), ("payload", "requested_effects", 0), ("payload", "target_selectors", 0)])
def test_unknown_fields_rejected_at_every_object_boundary(contract, canonical, examples, path):
    draft = example_draft(examples[1])
    node = draft
    for key in path:
        node = node[key]
    node["approved"] = True
    assert not Draft202012Validator(contract.build_draft_schema(canonical, project)).is_valid(draft)


@pytest.mark.parametrize("value", [False, True, 0, 1, "false", "true", "", "READY", "ready ", "read_only", None])
@pytest.mark.parametrize("field", ["research_readiness", "effect"])
def test_wire_labels_never_accept_boolean_repair_or_wrong_labels(contract, canonical, examples, field, value):
    draft = example_draft(examples[1])
    node = draft["payload"] if field == "research_readiness" else draft["payload"]["tasks"][0]
    node[field] = value
    assert not Draft202012Validator(contract.build_draft_schema(canonical, project)).is_valid(draft)


@pytest.mark.parametrize("field", ["sufficient", "write_intent"])
@pytest.mark.parametrize("value", [False, True, "false", "true"])
def test_old_canonical_boolean_keys_are_not_internal_wire_fields(contract, canonical, examples, field, value):
    draft = example_draft(examples[1])
    node = draft["payload"] if field == "sufficient" else draft["payload"]["tasks"][0]
    node[field] = value
    assert not Draft202012Validator(contract.build_draft_schema(canonical, project)).is_valid(draft)
