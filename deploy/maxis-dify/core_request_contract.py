"""Derive Request's internal wire draft from the actual RequestArchitect schema.

The canonical schema remains unchanged. The internal payload intentionally closes
its otherwise extensible root; unknown fields cannot become an unbound narrative
or authority channel. Wire structure and individual value constraints are derived
from the canonical schema. Collection constraints are NOT equivalent after wrapping:
requested_effects with identical values but different kind/source_ids can satisfy
draft uniqueItems. After unwrapping, the assembler MUST validate the entire canonical
payload again; this generator does not repair or deduplicate canonical effects.
Intent has a model-authored annotation even though it is a guard control
field: a payload containing only controls still needs an explicit supported claim.
Boolean labels are exact wire adapters, not coercion, approval, or execution.
"""
import copy
import hashlib
import json

REQUEST_SCHEMA_SHA256 = "aca74433bf7e1fb6015309570fa21f85d2b0d7fc411044820ad5751a2724c57c"
_NARRATIVE_PATHS = {
    ("intent",), ("keywords", "*"), ("mentioned_keys", "*"), ("goal",),
    ("tasks", "*", "instruction"), ("tasks", "*", "completion_criteria", "*"),
    ("blocking_questions", "*"), ("request_questions", "*", "question"),
    ("requested_effects", "*", "target"), ("requested_effects", "*", "value"),
    ("requested_effects", "*", "literal"), ("target_selectors", "*", "cardinality"),
    ("assumptions", "*"), ("plan",),
}


def build_draft_schema(canonical_schema: dict, project) -> dict:
    """Pin the reviewed architect schema; never substitute persisted RequestPlan."""
    def fail():
        raise ValueError("Request draft canonical schema requires review")

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

    def wire_boolean(row, old, new, labels):
        if row["properties"].pop(old) != {"type": "boolean"}:
            fail()
        row["properties"][new] = {"type": "string", "enum": labels}
        row["required"] = [new if key == old else key for key in row["required"]]

    try:
        encoded = canonical(canonical_schema)
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != REQUEST_SCHEMA_SHA256:
            fail()
        # This reviewed schema has no refs. Projection may not discard restrictions.
        projected = project(canonical_schema)
        if canonical(projected) != encoded:
            fail()
        payload = derive(projected)
        payload["additionalProperties"] = False
        wire_boolean(payload, "sufficient", "research_readiness", ["ready", "needs_clarification"])
        wire_boolean(payload["properties"]["tasks"]["items"], "write_intent", "effect",
                     ["write_requested", "no_write_requested"])
        return {"type": "object", "additionalProperties": False,
            "required": ["contract", "status", "payload", "missing_inputs"], "properties": {
                "contract": {"type": "string", "enum": ["request-draft.v1"]},
                "status": {"type": "string", "enum": ["ok", "needs_input", "blocked"]},
                "payload": {"anyOf": [{"type": "object", "additionalProperties": False, "maxProperties": 0}, payload]},
                "missing_inputs": {"type": "array", "maxItems": 8,
                    "items": {"type": "string", "minLength": 1, "maxLength": 500}}}}
    except (KeyError, TypeError, RecursionError) as exc:
        raise ValueError("Request draft canonical schema requires review") from exc
