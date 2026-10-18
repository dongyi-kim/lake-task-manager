"""Derive a bounded Work draft from the actual, unchanged CREATE contract only.

The native draft annotates reviewed text leaves and uses one explicit boolean
label adapter. It closes otherwise extensible question objects. No payload
meaning, source choice, approval, or model output is supplied by this builder.
The assembler must revalidate the complete canonical payload after unwrapping.
"""
import copy
import hashlib
import json

WORK_SCHEMA_SHA256 = "8f46d8cc3bbe4712838a066844b90eeef2272a9f0e8a8a6c054b63a338226f1a"
_NARRATIVE_PATHS = {
    ("interpretation",), ("structure_why",), ("rationale",),
    ("items", "*", "summary"), ("items", "*", "background"),
    ("items", "*", "reproduction", "*"), ("items", "*", "expected"), ("items", "*", "actual"),
    ("items", "*", "scope_in", "*"), ("items", "*", "scope_out", "*"),
    ("items", "*", "dod", "*"), ("items", "*", "references", "*"),
    ("items", "*", "children", "*", "summary"),
    ("items", "*", "children", "*", "scope_in", "*"),
    ("items", "*", "children", "*", "dod", "*"),
    ("questions", "*", "question"), ("questions", "*", "options", "*"),
    ("questions", "*", "why_required"),
}


def build_draft_schema(canonical_schema: dict, project) -> dict:
    """Pin CREATE; neither a TaskFlow WorkDraft nor another Work mode is equivalent."""
    def fail():
        raise ValueError("Work draft canonical schema requires review")

    def canonical(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def derive(row, path=()):
        if path in _NARRATIVE_PATHS:
            return {"type": "object", "additionalProperties": False, "required": ["value", "kind", "source_ids"],
                "properties": {"value": copy.deepcopy(row),
                    "kind": {"type": "string", "enum": ["fact", "inference", "recommendation"]},
                    "source_ids": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1, "maxLength": 128,
                                  "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]*(?![\s\S])"}}}}
        result = copy.deepcopy(row)
        if row.get("type") == "object":
            result["properties"] = {key: derive(value, path + (key,)) for key, value in row["properties"].items()}
        elif row.get("type") == "array":
            result["items"] = derive(row["items"], path + ("*",))
        return result

    try:
        encoded = canonical(canonical_schema)
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != WORK_SCHEMA_SHA256:
            fail()
        projected = project(copy.deepcopy(canonical_schema))
        if canonical(projected) != encoded:
            fail()
        payload = derive(projected)
        question = payload["properties"]["questions"]["items"]
        question["additionalProperties"] = False
        if question["properties"].pop("required_input") != {"type": "boolean"}:
            fail()
        question["properties"]["input_need"] = {"type": "string", "enum": ["required", "optional"]}
        question["required"] = ["input_need" if key == "required_input" else key for key in question["required"]]
        return {"type": "object", "additionalProperties": False,
            "required": ["contract", "status", "payload", "missing_inputs"], "properties": {
                "contract": {"type": "string", "enum": ["work-draft.v1"]},
                "status": {"type": "string", "enum": ["ok", "needs_input", "blocked"]},
                "payload": {"anyOf": [{"type": "object", "additionalProperties": False, "maxProperties": 0}, payload]},
                "missing_inputs": {"type": "array", "maxItems": 8,
                    "items": {"type": "string", "minLength": 1, "maxLength": 500}}}}
    except (KeyError, TypeError, RecursionError) as exc:
        raise ValueError("Work draft canonical schema requires review") from exc
