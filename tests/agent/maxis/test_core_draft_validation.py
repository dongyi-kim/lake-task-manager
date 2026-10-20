"""Strict, bounded shared draft validation; no role semantics or wire repairs."""
import copy
import importlib.util
import json
from pathlib import Path
import re

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'deploy/maxis-dify/code/core_draft_validation.py'


@pytest.fixture
def functions():
    spec = importlib.util.spec_from_file_location('core_draft_validation_test', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("role,node_id", [
    ("request", "1909000000001031"),
    ("work", "1909000000001034"),
    ("knowledge", "1909000000001037"),
])
def test_native_prompt_examples_match_the_live_native_schema(functions, role, node_id):
    prompt = (ROOT / f"deploy/maxis-dify/prompts/core_{role}.md").read_text(encoding="utf-8")
    examples = re.findall(r"```json\n(.*?)\n```", prompt, re.S)
    assert len(examples) == 1
    doc = yaml.safe_load((ROOT / "deploy/maxis-dify/dsl/ltm-agent-core.yml").read_text(encoding="utf-8"))
    schema = next(n["data"]["structured_output"]["schema"] for n in doc["workflow"]["graph"]["nodes"]
                  if n["id"] == node_id)
    value = functions._native_candidate(examples[0], schema, 32768)
    assert set(value) == {"contract", "status", "missing_inputs", "payload"}
    # The non-native provider sees both examples and Dify's ordered schema.
    # Conflicting root order encouraged a second missing_inputs after payload.
    assert list(value) == list(schema["properties"]) == schema["required"]
    assert value["status"] == "ok" and value["missing_inputs"] == []
    # Examples are authoring shapes, never accepted defaults or actual source IDs.
    assert "SOURCE_ID" in examples[0]



def captured(function, *args):
    try:
        value = function(*args)
        return ('ok', json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False))
    except Exception as exc:
        return ('error', type(exc).__name__, str(exc))


def exercise(functions, name, *args):
    def fail():
        raise ValueError('synthetic_invalid_draft')
    before = copy.deepcopy(args)
    result = captured(getattr(functions, name), *args, fail)
    assert args == before
    return result



def validate(functions, value, schema, allowed, root=None):
    result = exercise(functions, '_validate', value, schema, schema if root is None else root)
    assert (result[0] == 'ok') is allowed
    return result


def test_shared_validator_has_only_primitive_scope_and_reviewed_keywords(functions):
    assert functions._SCHEMA_KEYS == {'$defs', '$ref', 'type', 'enum', 'properties', 'required',
        'additionalProperties', 'items', 'maxItems', 'minItems', 'maxLength', 'minLength',
        'pattern', 'uniqueItems', 'maxProperties', 'anyOf'}
    assert {key for key, value in vars(functions).items() if callable(value)} == {
        '_canonical', '_pairs', '_decode', '_validate', '_NativeDraftIssue', '_native_candidate'}


@pytest.mark.parametrize("raw,reason", [
    ('{"value":', "json_syntax"), ('{"value":"a","value":"b"}', "json_structure"),
    ('{"value":42}', "schema"), ('{"extra":"private rejected text"}', "schema"),
])
def test_native_diagnostics_are_fixed_and_never_repair_or_echo_values(functions, raw, reason):
    schema = {"type": "object", "additionalProperties": False, "required": ["value"],
              "properties": {"value": {"type": "string"}}}
    with pytest.raises(functions._NativeDraftIssue) as error:
        functions._native_candidate(raw, schema, 1024)
    assert str(error.value) == "native_draft_" + reason
    assert functions._native_candidate('{"value":"original"}', schema, 1024) == {"value": "original"}


@pytest.mark.parametrize('value', [None, False, 0, 1.0, '가\r\n가', '\ud800',
    {'z': [False, 0, True, 1, 0.0, None], 'a': {'b': '😀'}}, [1, 2], (1, 2),
    float('nan'), float('inf'), -float('inf'), {'non_json'}, {1: 'integer-key', 'a': 'string-key'}])
def test_canonical_json_without_coercions(functions, value):
    def expected(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    assert captured(functions._canonical, value) == captured(expected, value)


@pytest.mark.parametrize('raw', ['{}', '{"b":2,"a":[false,0,true,1,null,1.5]}',
    '{\n "query": "한글", "flag": false\n}\n', '{"unicode":"\\uac00"}',
    '{"tiny":1e-999}', '{"large":9007199254740993}', '{"float":-0.0}',
    '  { "z": 1, "a": 2 }  '])
def test_decode_preserves_values_and_accepts_noncanonical_candidate_json(functions, raw):
    result = exercise(functions, '_decode', raw, 32768)
    assert result[0] == 'ok'
    assert json.loads(result[1]) == json.loads(raw)


@pytest.mark.parametrize('raw', [None, {}, [], 1, True, b'{}', '', '[]', 'null', 'false', '1', '"text"',
    '{} trailing', '```json\n{}\n```', '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}',
    '{"x":1e999}', '{"x":-1e999}', '{"x":1,"x":2}', '{"nested":{"x":1,"x":2}}', '\ud800'])
def test_invalid_decode_rejects_without_repair(functions, raw):
    assert exercise(functions, '_decode', raw, 32768)[0] == 'error'


@pytest.mark.parametrize('unit', ['x', '한', '😀'])
@pytest.mark.parametrize('excess', [0, 1])
def test_decode_enforces_inclusive_caller_utf8_byte_budget(functions, unit, excess):
    raw = json.dumps({'value': unit * 5}, ensure_ascii=False, separators=(',', ':'))
    result = exercise(functions, '_decode', raw, len(raw.encode('utf-8')) - excess)
    assert (result[0] == 'ok') is (excess == 0)


@pytest.mark.parametrize('depth,allowed', [(1, True), (31, True), (32, True), (33, False), (1100, False)])
def test_decode_depth_boundary_including_parser_recursion_failure(functions, depth, allowed):
    raw = '{"value":' + '[' * (depth - 1) + '0' + ']' * (depth - 1) + '}'
    result = exercise(functions, '_decode', raw, 32768)
    assert (result[0] == 'ok') is allowed


@pytest.mark.parametrize('count,allowed', [(4095, True), (4096, True), (4097, False)])
def test_decode_node_budget_counts_root_and_array(functions, count, allowed):
    raw = json.dumps({'value': [0] * (count - 2)}, separators=(',', ':'))
    assert len(raw.encode('utf-8')) < 32768
    result = exercise(functions, '_decode', raw, 32768)
    assert (result[0] == 'ok') is allowed


@pytest.mark.parametrize('schema,value,allowed', [
    ({'type': 'object'}, {}, True), ({'type': 'array'}, [], True),
    ({'type': 'string'}, 'text', True), ({'type': 'boolean'}, True, True),
    ({'type': 'boolean'}, False, True), ({'type': 'object'}, [], False),
    ({'type': 'array'}, {}, False), ({'type': 'array'}, (), False),
    ({'type': 'string'}, 1, False), ({'type': 'boolean'}, 0, False),
    ({'type': 'boolean'}, 1, False), ({'type': 'boolean'}, 'false', False),
    ({'type': 'boolean'}, None, False), ({'type': 'integer'}, 1, False),
    ({'type': 'number'}, 1.0, False), ({'type': 'null'}, None, False),
    ({'type': 'unknown'}, {}, False), ({'type': ['string', 'null']}, 'x', False),
])
def test_reviewed_type_domain_is_strict(functions, schema, value, allowed):
    validate(functions, value, schema, allowed)


@pytest.mark.parametrize('value,allowed', [(False, True), (True, True), (0, False), (1, False),
    (0.0, False), (1.0, False), ('false', False), ('true', False), (None, False)])
def test_boolean_enum_checks_exact_json_type_without_type_keyword(functions, value, allowed):
    validate(functions, value, {'enum': [False, True]}, allowed)


@pytest.mark.parametrize('schema,value,allowed', [
    ({'enum': ['alpha', 'beta']}, 'alpha', True), ({'enum': ['alpha', 'beta']}, 'Alpha', False),
    ({'enum': [1]}, 1, True), ({'enum': [1]}, True, False), ({'enum': [1]}, 1.0, False),
    ({'type': 'string', 'minLength': 2, 'maxLength': 3}, '한😀', True),
    ({'type': 'string', 'minLength': 2, 'maxLength': 3}, 'x', False),
    ({'type': 'string', 'minLength': 2, 'maxLength': 3}, 'xxxx', False),
    ({'type': 'string', 'pattern': '[a-z]+'}, 'abc', True),
    ({'type': 'string', 'pattern': '[a-z]+'}, 'abc\n', False),
    ({'type': 'array', 'minItems': 1, 'maxItems': 2, 'items': {'type': 'string'}}, ['a', 'b'], True),
    ({'type': 'array', 'minItems': 1, 'maxItems': 2}, [], False),
    ({'type': 'array', 'minItems': 1, 'maxItems': 2}, [1, 2, 3], False),
    ({'type': 'array', 'items': {'type': 'string'}}, [1], False),
    ({'type': 'array', 'uniqueItems': True}, [False, 0], True),
    ({'type': 'array', 'uniqueItems': True}, [{'a': 1}, {'a': 1}], False),
    ({'type': 'object', 'maxProperties': 0}, {}, True),
    ({'type': 'object', 'maxProperties': 0}, {'a': 1}, False),
    ({'type': 'object', 'required': ['a']}, {}, False),
    ({'type': 'object', 'properties': {'a': {'type': 'string'}}, 'additionalProperties': False}, {'a': 'x'}, True),
    ({'type': 'object', 'properties': {'a': {'type': 'string'}}, 'additionalProperties': False}, {'b': 'x'}, False),
    ({'type': 'object', 'properties': {'a': {'type': 'string'}}}, {'other': 1}, True),
])
def test_existing_scalar_container_and_optional_constraints(functions, schema, value, allowed):
    validate(functions, value, schema, allowed)


@pytest.mark.parametrize('keyword', ['const', 'allOf', 'oneOf', 'not', 'minimum', 'maximum',
    'minProperties', 'format', 'default', 'description', 'title', 'additionalItems', 'unevaluatedProperties'])
def test_unknown_schema_keywords_remain_rejected(functions, keyword):
    validate(functions, 'x', {'type': 'string', keyword: 'unreviewed'}, False)


@pytest.mark.parametrize('value,allowed', [('ok', True), (1, False)])
def test_local_ref_preserves_json_pointer_unescaping(functions, value, allowed):
    root = {'$defs': {'slash/tilde~': {'type': 'string'}}}
    validate(functions, value, {'$ref': '#/$defs/slash~1tilde~0'}, allowed, root)


@pytest.mark.parametrize('schema,root', [
    ({'$ref': 'https://invalid.example/schema'}, {}), ({'$ref': '#missing'}, {}),
    ({'$ref': 1}, {}), ({'$ref': '#/$defs/x', 'type': 'string'}, {'$defs': {'x': {}}}),
    ({'$ref': '#/$defs/missing'}, {'$defs': {}}),
])
def test_ref_failures_are_not_repaired_or_reinterpreted(functions, schema, root):
    validate(functions, 'x', schema, False, root)


@pytest.mark.parametrize('value,schema,allowed', [
    ('ok', {'anyOf': [{'type': 'boolean'}, {'type': 'string'}]}, True),
    (False, {'anyOf': [{'type': 'boolean'}, {'type': 'string'}]}, True),
    (0, {'anyOf': [{'type': 'boolean'}, {'type': 'string'}]}, False),
    ('ok', {'anyOf': []}, False),
    ('bad', {'anyOf': [{'type': 'string'}], 'enum': ['ok']}, False),
    ('ok', {'anyOf': [{'$ref': '#/$defs/missing'}, {'type': 'string'}]}, False),
])
def test_anyof_preserves_branch_failure_and_sibling_constraint_behavior(functions, value, schema, allowed):
    validate(functions, value, schema, allowed)


class SyntheticValidationError(ValueError):
    pass


@pytest.mark.parametrize('failure_type', [ValueError, SyntheticValidationError])
def test_nonreturning_valueerror_callback_allows_later_anyof_success(functions, failure_type):
    schema = {'anyOf': [{'type': 'boolean'}, {'type': 'string'}]}
    calls = []
    def fail():
        calls.append('first_branch_rejected')
        raise failure_type('synthetic_rejection')
    assert functions._validate('later branch', schema, schema, fail) is None
    assert calls == ['first_branch_rejected']


@pytest.mark.parametrize('value,schema,allowed', [
    ({}, {'type': 'object', 'properties': {'unused': {'type': 'string', 'const': 'unreviewed'}}}, True),
    ({'unused': 'unreviewed'}, {'type': 'object', 'properties': {'unused': {'type': 'string', 'const': 'unreviewed'}}}, False),
    ('ok', {'type': 'string', '$defs': {'unused': {'unknown': 'unreviewed'}}}, True),
    ('ok', {'anyOf': [{'type': 'string'}, {'unknown': 'unreviewed'}]}, True),
])
def test_schema_keyword_checks_preserve_existing_visited_node_scope(functions, value, schema, allowed):
    validate(functions, value, schema, allowed)


@pytest.mark.parametrize('depth,allowed', [(0, True), (31, True), (32, True), (33, False)])
def test_schema_traversal_depth_boundary(functions, depth, allowed):
    value, schema = 'leaf', {'type': 'string'}
    for _ in range(depth):
        value, schema = {'x': value}, {'type': 'object', 'properties': {'x': schema}}
    validate(functions, value, schema, allowed)


def test_ref_cycle_stops_at_existing_depth_limit(functions):
    root = {'$defs': {'cycle': {'$ref': '#/$defs/cycle'}}}
    validate(functions, 'x', {'$ref': '#/$defs/cycle'}, False, root)


@pytest.mark.parametrize('message', ['invalid_core_query_draft', 'synthetic_other_caller_error'])
def test_failure_callback_preserves_the_callers_error_and_is_never_shared(functions, message):
    def fail():
        raise ValueError(message)
    with pytest.raises(ValueError, match='^' + message + '$'):
        functions._decode('[]', 32768, fail)
    with pytest.raises(ValueError, match='^' + message + '$'):
        functions._validate('false', {'enum': [False, True]}, {}, fail)
