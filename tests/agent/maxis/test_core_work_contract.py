"""The internal Work draft derives only from the reviewed canonical CREATE schema."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.agent.maxis.contracts import canonical_payload_schema
from app.agent.workflow.work_architect.contracts import CREATE_SCHEMA, UPDATE_SCHEMA

DIRECTORY = Path(__file__).resolve().parents[3] / "deploy/maxis-dify"


def load(path):
    spec = importlib.util.spec_from_file_location("work_contract_test_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def contract():
    return load(DIRECTORY / "core_work_contract.py")


@pytest.fixture
def canonical():
    return json.loads(canonical_payload_schema(CREATE_SCHEMA)[0])


def project(schema):
    return load(DIRECTORY / "validate_bundle.py")._dify_structured_output_schema(schema)


def test_pin_and_complete_derived_shape_preserve_canonical_constraints(contract, canonical):
    before = copy.deepcopy(canonical)
    draft = contract.build_draft_schema(canonical, project)
    assert canonical == before
    assert canonical_payload_schema(CREATE_SCHEMA)[1] == contract.WORK_SCHEMA_SHA256 == (
        "8f46d8cc3bbe4712838a066844b90eeef2272a9f0e8a8a6c054b63a338226f1a")
    assert draft["properties"]["contract"]["enum"] == ["work-draft.v1"]
    assert draft["required"] == ["contract", "status", "payload", "missing_inputs"]
    assert draft["additionalProperties"] is False
    Draft202012Validator.check_schema(draft)
    guard = load(DIRECTORY / "code/contract_guard.py")
    wrapped, expected = set(), set()

    def walk(original, derived, path=(), field=""):
        if original["type"] == "string":
            if not guard._control_field(field):
                expected.add(path)
                assert derived["required"] == ["value", "kind", "source_ids"]
                assert set(derived["properties"]) == {"value", "kind", "source_ids"}
                assert derived["additionalProperties"] is False
                assert derived["properties"]["value"] == original
                assert derived["properties"]["kind"]["enum"] == ["fact", "inference", "recommendation"]
                wrapped.add(path)
            else:
                assert derived == original
        elif original["type"] == "array":
            assert {k: v for k, v in derived.items() if k != "items"} == {
                k: v for k, v in original.items() if k != "items"}
            walk(original["items"], derived["items"], path + ("*",), field)
        elif original["type"] == "object":
            adjusted = copy.deepcopy(original)
            if path == ("questions", "*"):
                assert adjusted["properties"].pop("required_input") == {"type": "boolean"}
                adjusted["properties"]["input_need"] = {"type": "string", "enum": ["required", "optional"]}
                adjusted["required"] = ["input_need" if k == "required_input" else k for k in adjusted["required"]]
                adjusted["additionalProperties"] = False
            assert {k: v for k, v in derived.items() if k != "properties"} == {
                k: v for k, v in adjusted.items() if k != "properties"}
            assert set(derived["properties"]) == set(adjusted["properties"])
            for key, row in adjusted["properties"].items():
                if key == "input_need":
                    assert derived["properties"][key] == row
                else:
                    walk(row, derived["properties"][key], path + (key,), key)

    walk(canonical, draft["properties"]["payload"]["anyOf"][1])
    assert wrapped == expected == set(contract._NARRATIVE_PATHS)
    assert ("items", "*", "children", "*", "summary") in wrapped
    assert ("items", "*", "references", "*") in wrapped


@pytest.mark.parametrize("mutation", ["other_schema", "required", "limit", "new_field", "allof", "annotation"])
def test_canonical_drift_requires_review(contract, canonical, mutation):
    if mutation == "other_schema": canonical = json.loads(canonical_payload_schema(UPDATE_SCHEMA)[0])
    elif mutation == "required": canonical["required"].remove("items")
    elif mutation == "limit": canonical["properties"]["items"]["maxItems"] += 1
    elif mutation == "new_field": canonical["properties"]["unreviewed"] = {"type": "string"}
    elif mutation == "allof": canonical["allOf"] = [{"required": ["rationale"]}]
    else: canonical["description"] = "unreviewed input"
    with pytest.raises(ValueError, match="^Work draft canonical schema requires review$"):
        contract.build_draft_schema(canonical, project)


def test_projection_cannot_silently_drop_a_constraint(contract, canonical):
    def lossy(schema):
        changed = project(schema)
        del changed["properties"]["items"]["maxItems"]
        return changed
    with pytest.raises(ValueError, match="^Work draft canonical schema requires review$"):
        contract.build_draft_schema(canonical, lossy)


@pytest.mark.parametrize("ref,valid", [("a", True), ("A9._:-", True), ("a" * 128, True),
    ("", False), ("a" * 129, False), ("_id", False), ("src\n", False), ("src\r\n", False),
    ("src\u2028", False), ("src ", False), (False, False), (1, False)])
def test_reference_identifier_constraints_match_strict_guard(contract, canonical, ref, valid):
    schema = contract.build_draft_schema(canonical, project)["properties"]["payload"]["anyOf"][1]
    wrapped = schema["properties"]["items"]["items"]["properties"]["summary"]
    assert Draft202012Validator(wrapped).is_valid({"value": "요약", "kind": "recommendation", "source_ids": [ref]}) is valid


@pytest.mark.parametrize("mutation", ["plain", "extra", "missing_value", "missing_kind", "missing_refs",
    "unknown_kind", "empty_refs", "duplicate_refs", "too_many_refs", "long_value"])
def test_wrapped_narratives_are_closed_typed_and_bounded(contract, canonical, mutation):
    schema = contract.build_draft_schema(canonical, project)["properties"]["payload"]["anyOf"][1]
    wrapped = schema["properties"]["items"]["items"]["properties"]["summary"]
    value = {"value": "요약", "kind": "recommendation", "source_ids": ["source-a"]}
    if mutation == "plain": value = "요약"
    elif mutation == "extra": value["approved"] = True
    elif mutation.startswith("missing_"): del value[mutation.removeprefix("missing_").replace("refs", "source_ids")]
    elif mutation == "unknown_kind": value["kind"] = "open_fact"
    elif mutation == "empty_refs": value["source_ids"] = []
    elif mutation == "duplicate_refs": value["source_ids"] *= 2
    elif mutation == "too_many_refs": value["source_ids"] = [f"source-{i}" for i in range(9)]
    else: value["value"] = "x" * 181
    assert not Draft202012Validator(wrapped).is_valid(value)
