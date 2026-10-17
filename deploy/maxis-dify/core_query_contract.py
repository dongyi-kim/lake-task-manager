"""Derive the internal Query draft from the canonical payload, never a second payload contract."""
import copy
import json


def build_draft_schema(canonical_schema: dict, project) -> dict:
    """Preserve optional fields and scalar constraints; unknown canonical shapes require review."""
    def fail():
        raise ValueError("Query draft canonical schema requires review")

    def resolve(value, depth=0):
        if depth > 32:
            fail()
        if isinstance(value, list):
            return [resolve(row, depth + 1) for row in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            reference = value["$ref"]
            if set(value) != {"$ref"} or not isinstance(reference, str) or not reference.startswith("#/"):
                fail()
            target = canonical_schema
            for part in reference[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            return resolve(target, depth + 1)
        return {key: resolve(row, depth + 1) for key, row in value.items() if key != "$defs"}

    def shape(row, keys, kind):
        if not isinstance(row, dict) or set(row) != set(keys.split()) or row.get("type") != kind:
            fail()

    def wrap(value):
        return {"type": "object", "additionalProperties": False, "required": ["value", "kind", "source_ids"],
            "properties": {"value": copy.deepcopy(value),
                "kind": {"type": "string", "enum": ["fact", "inference", "recommendation"]},
                "source_ids": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1, "maxLength": 128,
                              "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$"}}}}

    try:
        payload = copy.deepcopy(project(canonical_schema))
        # In particular, the generic Dify projection must not drop a new allOf/const restriction.
        if json.dumps(payload, sort_keys=True) != json.dumps(resolve(canonical_schema), sort_keys=True):
            fail()
        shape(payload, "type additionalProperties properties", "object")
        if payload["additionalProperties"] is not False or set(payload["properties"]) != {"reads", "uncertainty"}:
            fail()
        reads, uncertainty = (payload["properties"][key] for key in ("reads", "uncertainty"))
        for array in (reads, uncertainty):
            shape(array, "type items maxItems", "array")
            if type(array["maxItems"]) is not int or not 0 < array["maxItems"] <= 8:
                fail()
        row = reads["items"]
        shape(row, "type additionalProperties properties required", "object")
        fields = row["properties"]
        if (row["additionalProperties"] is not False or row["required"] != ["source"]
                or set(fields) != {"source", "subject", "where", "exhaustive"}
                or fields["exhaustive"] != {"type": "boolean"}):
            fail()
        shape(fields["source"], "type enum", "string")
        if not isinstance(fields["source"]["enum"], list) or not fields["source"]["enum"] or any(
                not isinstance(value, str) or not value for value in fields["source"]["enum"]):
            fail()
        for text in (fields["subject"], fields["where"], uncertainty["items"]):
            shape(text, "type maxLength", "string")
            if type(text["maxLength"]) is not int or not 0 < text["maxLength"] <= 240:
                fail()
        for key in ("source", "subject", "where"):
            fields[key] = wrap(fields[key])
        fields["exhaustive"] = {"enum": [False, True]}
        uncertainty["items"] = wrap(uncertainty["items"])
        return {"type": "object", "additionalProperties": False,
            "required": ["contract", "status", "payload", "missing_inputs"], "properties": {
                "contract": {"type": "string", "enum": ["query-draft.v1"]},
                "status": {"type": "string", "enum": ["ok", "needs_input", "blocked"]},
                "payload": {"anyOf": [{"type": "object", "additionalProperties": False, "maxProperties": 0}, payload]},
                "missing_inputs": {"type": "array", "maxItems": 8,
                    "items": {"type": "string", "minLength": 1, "maxLength": 500}}}}
    except (KeyError, TypeError, RecursionError) as exc:
        raise ValueError("Query draft canonical schema requires review") from exc
