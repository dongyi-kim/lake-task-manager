"""JSON schema identity at both raw-DSL validation entrypoints; no provider calls."""
import copy
import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
CASES = [
    ("agent_core", "Contract LLM"), ("agent_core", "Control LLM"),
    ("agent_core", "Query LLM"),
    ("editor_author", "DraftSpec LLM"), ("editor_author", "Description LLM"),
    ("editor_author", "Comment LLM"), ("editor_author", "Control LLM"),
    ("pmo_brief", "Contract LLM"),
]


@pytest.fixture(scope="module")
def modules():
    spec = importlib.util.spec_from_file_location(
        "schema_types_materializer", ROOT / "deploy/maxis-dify/materialize_environment.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module._VALIDATOR


@pytest.fixture(params=CASES, ids=[f"{workflow}-{title}" for workflow, title in CASES])
def raw_case(request, modules, monkeypatch):
    workflow, title = request.param
    materializer, _ = modules
    text = (ROOT / materializer.REVIEWED_SOURCE_DSLS[workflow]).read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    original = yaml.safe_load
    monkeypatch.setattr(yaml, "safe_load", lambda value: raw if value == text else original(value))
    data = next(node["data"] for node in raw["workflow"]["graph"]["nodes"]
                if node["data"]["title"] == title)
    return workflow, raw, data


def _entrypoints(modules, workflow):
    materializer, validator = modules
    environment = materializer.load_environment(
        ROOT / "deploy/maxis-dify/environments/dev.yml")
    return [
        (lambda: validator.validate_bundle(ROOT), validator.BundleValidationError),
        (lambda: materializer._materialize_dsl(environment, workflow),
         materializer.EnvironmentBindingError),
    ]


def _reject_both(modules, raw_case):
    workflow, raw, _ = raw_case
    before = repr(raw)
    for validate, error in _entrypoints(modules, workflow):
        with pytest.raises(error, match="structured output"):
            validate()
        assert repr(raw) == before, "invalid source must not be repaired or rebound"


def _first_field(value, field):
    if isinstance(value, dict):
        if field in value:
            return value
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return None
    for child in children:
        found = _first_field(child, field)
        if found is not None:
            return found
    return None


@pytest.mark.parametrize("mutation", ["false_to_zero", "false_to_float", "one_to_true"])
def test_every_llm_rejects_bool_number_schema_substitution(modules, raw_case, mutation):
    schema = raw_case[2]["structured_output"]["schema"]
    if mutation == "one_to_true":
        owner = _first_field(schema, "minLength")
        assert type(owner["minLength"]) is int and owner["minLength"] == 1
        owner["minLength"] = True
    else:
        schema["additionalProperties"] = 0 if mutation == "false_to_zero" else 0.0
    _reject_both(modules, raw_case)


@pytest.mark.parametrize("mutation", [
    "null_schema", "empty_schema", "array_schema", "scalar_schema",
    "tuple_array", "nonstring_key", "infinity", "nan",
    "extra_wrapper", "array_order", "cycle", "excessive_depth",
])
def test_every_llm_rejects_malformed_or_changed_raw_schema(modules, raw_case, mutation):
    output = raw_case[2]["structured_output"]
    schema = output["schema"]
    if mutation == "null_schema":
        output["schema"] = None
    elif mutation in {"empty_schema", "array_schema", "scalar_schema"}:
        output["schema"] = {"empty_schema": {}, "array_schema": [], "scalar_schema": True}[mutation]
    elif mutation == "tuple_array":
        schema["required"] = tuple(schema["required"])
    elif mutation == "nonstring_key":
        schema[1] = schema.pop("type")
    elif mutation in {"infinity", "nan"}:
        _first_field(schema, "minLength")["minLength"] = float(
            "inf" if mutation == "infinity" else "nan")
    elif mutation == "extra_wrapper":
        output["extra"] = None
    elif mutation == "array_order":
        schema["required"].reverse()
    elif mutation == "cycle":
        schema["cycle"] = schema
    else:
        nested = None
        for _ in range(600):
            nested = [nested]
        schema["deep"] = nested
    _reject_both(modules, raw_case)


@pytest.mark.parametrize("mutation", ["object_order", "equivalent_number"])
def test_every_llm_accepts_json_equivalent_schemas(modules, raw_case, mutation):
    workflow, _, data = raw_case
    schema = data["structured_output"]["schema"]
    if mutation == "object_order":
        schema["properties"] = dict(reversed(list(schema["properties"].items())))
        data["structured_output"]["schema"] = dict(reversed(list(schema.items())))
    else:
        owner = _first_field(schema, "minLength")
        owner["minLength"] = float(owner["minLength"])
    for validate, _ in _entrypoints(modules, workflow):
        validate()


@pytest.fixture(scope="module")
def checks(modules):
    return modules[1]._editor_graph_modules(ROOT)[1]


@pytest.mark.parametrize("left,right,equal", [
    (False, 0, False), (True, 1.0, False), (1, True, False),
    (1, 1.0, True), (0, -0.0, True), (10 ** 400, 10 ** 400, True),
    (10 ** 400, 1.0, False), (1, "1", False), (None, None, True),
    ({"enum": [False, 1]}, {"enum": [0, 1]}, False),
    ({"enum": [False, 1]}, {"enum": [False, 1.0]}, True),
    ([1, 2], [2, 1], False), ({"a": 1, "b": None}, {"b": None, "a": 1}, True),
])
def test_json_comparison_preserves_types_and_number_value(checks, left, right, equal):
    before = (repr(left), repr(right))
    assert checks._json_schema_equal(left, right) is equal
    assert (repr(left), repr(right)) == before


@pytest.mark.parametrize("value", [
    (1, 2), {1: "integer key"}, {True: "boolean key"}, {"nested": {1, 2}},
    b"text", Decimal("1"), float("inf"), float("-inf"), float("nan"),
])
def test_non_json_values_never_compare_equal_even_to_themselves(checks, value):
    assert checks._json_schema_equal(value, copy.deepcopy(value)) is False
