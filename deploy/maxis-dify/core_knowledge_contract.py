"""Derive a single-author native subset of unchanged canonical KnowledgeBrief.

Close only the internal draft's known objects: public extensions are not silently
dropped or reinterpreted. Preserve optional absence, blank values and all original
constraints. Any canonical drift requires review before expanding this producer.
"""
import copy
import hashlib
import json

KNOWLEDGE_SCHEMA_SHA256 = "458a377bf712fbc263f86ba5c6654e02bc890a3a85704c88f63373549f52a745"
PRODUCER_LIMITS = {"draft_utf8_bytes": 32768, "envelope_utf8_bytes": 98304, "claims": 40, "bindings": 256}


def build_knowledge_system_prompt(local_role_markdown, transport_markdown):
    """One exact local semantic owner plus the internal wire adapter, no full-envelope prompt."""
    if not all(isinstance(value, str) and value.strip() for value in (local_role_markdown, transport_markdown)):
        raise ValueError("Knowledge system prompt requires review")
    prompt = local_role_markdown.strip() + "\n\n" + transport_markdown.strip()
    if len(prompt.encode("utf-8")) > 8192:
        raise ValueError("Knowledge system prompt requires review")
    return prompt


def build_draft_schema(canonical_schema, project):
    def fail():
        raise ValueError("Knowledge draft canonical schema requires review")

    def canonical(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def resolve(row, depth=0):
        if depth > 32: fail()
        if isinstance(row, list): return [resolve(value, depth + 1) for value in row]
        if not isinstance(row, dict): return row
        if "$ref" in row:
            reference = row["$ref"]
            if set(row) != {"$ref"} or not isinstance(reference, str) or not reference.startswith("#/"): fail()
            target = canonical_schema
            for part in reference[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            return resolve(target, depth + 1)
        return {key: resolve(value, depth + 1) for key, value in row.items() if key != "$defs"}

    def derive(row):
        value = copy.deepcopy(row)
        if row["type"] == "string":
            narrative = {"type": "object", "additionalProperties": False, "required": ["value", "kind", "source_ids"],
                "properties": {"value": value,
                    "kind": {"type": "string", "enum": ["inference", "recommendation"]},
                    "source_ids": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1, "maxLength": 128,
                                  "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$"}}}}
            fact = {"type": "object", "additionalProperties": False,
                "required": ["kind", "source_id", "source_path"], "properties": {
                    "kind": {"type": "string", "enum": ["fact"]},
                    "source_id": copy.deepcopy(narrative["properties"]["source_ids"]["items"]),
                    "source_path": {"type": "string", "maxLength": 500}}}
            return {"anyOf": [narrative, fact]}
        if row["type"] == "array":
            value["items"] = derive(row["items"])
        elif row["type"] == "object":
            value["additionalProperties"] = False
            value["properties"] = {key: derive(child) for key, child in row["properties"].items()}
        else:
            fail()
        return value

    try:
        if hashlib.sha256(canonical(canonical_schema).encode("utf-8")).hexdigest() != KNOWLEDGE_SCHEMA_SHA256:
            fail()
        projected = project(copy.deepcopy(canonical_schema))
        if canonical(projected) != canonical(resolve(canonical_schema)):
            fail()
        payload = derive(projected)
        return {"type": "object", "additionalProperties": False,
            "required": ["contract", "status", "payload", "missing_inputs"], "properties": {
                "contract": {"type": "string", "enum": ["knowledge-draft.v2"]},
                "status": {"type": "string", "enum": ["ok", "needs_input", "blocked"]},
                "payload": {"anyOf": [{"type": "object", "additionalProperties": False, "maxProperties": 0}, payload]},
                "missing_inputs": {"type": "array", "maxItems": 8,
                    "items": {"type": "string", "minLength": 1, "maxLength": 500}}}}
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Knowledge draft canonical schema requires review") from exc
