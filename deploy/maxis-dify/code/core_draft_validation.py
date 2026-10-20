"""Pure draft JSON/schema primitives; callers own errors and canonical-input policy.

The fail callback MUST never return and MUST raise ValueError or a subclass.
Returning would allow invalid values to continue; anyOf catches only ValueError,
so a different exception type would prevent trying later valid branches.
Schema keyword checks apply only to visited nodes, matching the existing Query
validator. Unused definitions/properties and later anyOf branches are not scanned;
this helper does not validate an entire raw schema independently of a value.
"""
import json
import math
import re

_SCHEMA_KEYS = {"$defs", "$ref", "type", "enum", "properties", "required", "additionalProperties", "items",
                "maxItems", "minItems", "maxLength", "minLength", "pattern", "uniqueItems", "maxProperties", "anyOf"}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _pairs(rows, fail):
    result = {}
    for key, value in rows:
        if key in result:
            fail()
        result[key] = value
    return result


def _decode(raw, maximum, fail):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > maximum:
        fail()
    value = json.loads(raw, object_pairs_hook=lambda rows: _pairs(rows, fail), parse_constant=lambda _: fail())
    remaining = [4096]

    def bounded(item, depth=0):
        remaining[0] -= 1
        if depth > 32 or remaining[0] < 0 or (isinstance(item, float) and not math.isfinite(item)):
            fail()
        for child in item.values() if isinstance(item, dict) else item if isinstance(item, list) else []:
            bounded(child, depth + 1)

    bounded(value)
    if not isinstance(value, dict):
        fail()
    return value


def _validate(value, schema, root, fail, depth=0):
    """Validate the reviewed subset; unknown keywords on visited nodes fail closed."""
    if depth > 32 or not isinstance(schema, dict) or set(schema) - _SCHEMA_KEYS:
        fail()
    if "$ref" in schema:
        ref = schema["$ref"]
        if set(schema) != {"$ref"} or not isinstance(ref, str) or not ref.startswith("#/"):
            fail()
        target = root
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return _validate(value, target, root, fail, depth + 1)
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            try:
                _validate(value, branch, root, fail, depth + 1)
                break
            except ValueError:
                pass
        else:
            fail()
    expected = schema.get("type")
    if expected is not None and type(value) is not {"object": dict, "array": list, "string": str, "boolean": bool}.get(expected):
        fail()
    if "enum" in schema and not any(type(value) is type(row) and value == row for row in schema["enum"]):
        fail()
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if (any(key not in value for key in schema.get("required", []))
                or schema.get("additionalProperties") is False and set(value) - set(properties)
                or len(value) > schema.get("maxProperties", len(value))):
            fail()
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], root, fail, depth + 1)
    elif isinstance(value, list):
        if (not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", len(value))
                or schema.get("uniqueItems") and len({_canonical(row) for row in value}) != len(value)):
            fail()
        if "items" in schema:
            for item in value:
                _validate(item, schema["items"], root, fail, depth + 1)
    elif isinstance(value, str):
        if (not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", len(value))
                or "pattern" in schema and re.fullmatch(schema["pattern"], value) is None):
            fail()


class _NativeDraftIssue(ValueError):
    """Fixed diagnostics only; never serialize candidate text or parser messages."""

    def __init__(self, reason):
        if reason not in {"json_syntax", "json_structure", "schema", "source_id", "fact_selection"}:
            raise ValueError("invalid_native_diagnostic")
        self.marker = "native_draft_" + reason
        super().__init__(self.marker)


def _native_candidate(raw, schema, maximum):
    """The same strict validation, with a bounded reason for a fresh generation."""
    def structure():
        raise _NativeDraftIssue("json_structure")

    def shape():
        raise _NativeDraftIssue("schema")

    try:
        value = _decode(raw, maximum, structure)
    except json.JSONDecodeError:
        raise _NativeDraftIssue("json_syntax") from None
    _validate(value, schema, schema, shape)
    return value
