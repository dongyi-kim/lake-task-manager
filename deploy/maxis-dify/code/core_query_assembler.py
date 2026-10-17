"""Assemble Query provenance without changing model meaning; both final guards remain mandatory.

Standalone Dify Code node, stdlib only. The builder appends the three reviewed
schema constants. Unbound or malformed inputs fail without a partial candidate.
Internal coverage labels map exactly to the unchanged canonical boolean; this
wire adapter does not coerce malformed booleans or infer an omitted value.
"""
import hashlib
import json
import math
import re

_QUERY_SCHEMA_JSON = None
_QUERY_SCHEMA_SHA256 = None
_DRAFT_SCHEMA = None
_MAX_CONTEXT_PACK_UTF8_BYTES = 6144  # app.agent.maxis.context_guard / Knowledge Guard
_MAX_MODEL_INPUT_UTF8_BYTES = 9000  # Knowledge Guard output bound
_MAX_DRAFT_UTF8_BYTES = 32768
_COVERAGE_TO_EXHAUSTIVE = {"exhaustive": True, "standard": False}
_INPUT_FIELDS = {"contract", "context_pack", "role_id", "correlation_id", "output_contract",
                 "workflow_version", "knowledge_version", "knowledge_proof", "knowledge_context"}
_PACK_FIELDS = {"contract", "request_id", "use_case", "query", "pseudonymous_user", "requested_at", "scope",
                "sources", "facts", "metrics", "role_context", "freshness", "partial", "flags",
                "knowledge_manifest_sha256", "payload_schema_json", "payload_schema_sha256"}
_SCHEMA_KEYS = {"$defs", "$ref", "type", "enum", "properties", "required", "additionalProperties", "items",
                "maxItems", "minItems", "maxLength", "minLength", "pattern", "uniqueItems", "maxProperties", "anyOf"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def _fail():
    raise ValueError("invalid_core_query_draft")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            _fail()
        result[key] = value
    return result


def _decode(raw, maximum):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > maximum:
        _fail()
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: _fail())
    remaining = [4096]

    def bounded(item, depth=0):
        remaining[0] -= 1
        if depth > 32 or remaining[0] < 0 or (isinstance(item, float) and not math.isfinite(item)):
            _fail()
        for child in item.values() if isinstance(item, dict) else item if isinstance(item, list) else []:
            bounded(child, depth + 1)

    bounded(value)
    if not isinstance(value, dict):
        _fail()
    return value


def _validate(value, schema, root, depth=0):
    """Only the reviewed Query subset; new schema keywords fail closed."""
    if depth > 32 or not isinstance(schema, dict) or set(schema) - _SCHEMA_KEYS:
        _fail()
    if "$ref" in schema:
        ref = schema["$ref"]
        if set(schema) != {"$ref"} or not isinstance(ref, str) or not ref.startswith("#/"):
            _fail()
        target = root
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return _validate(value, target, root, depth + 1)
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            try:
                _validate(value, branch, root, depth + 1)
                break
            except ValueError:
                pass
        else:
            _fail()
    expected = schema.get("type")
    if expected is not None and type(value) is not {"object": dict, "array": list, "string": str, "boolean": bool}.get(expected):
        _fail()
    if "enum" in schema and not any(type(value) is type(row) and value == row for row in schema["enum"]):
        _fail()
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if (any(key not in value for key in schema.get("required", []))
                or schema.get("additionalProperties") is False and set(value) - set(properties)
                or len(value) > schema.get("maxProperties", len(value))):
            _fail()
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], root, depth + 1)
    elif isinstance(value, list):
        if (not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", len(value))
                or schema.get("uniqueItems") and len({_canonical(row) for row in value}) != len(value)):
            _fail()
        if "items" in schema:
            for item in value:
                _validate(item, schema["items"], root, depth + 1)
    elif isinstance(value, str):
        if (not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", len(value))
                or "pattern" in schema and re.fullmatch(schema["pattern"], value) is None):
            _fail()


def _input(raw):
    incoming = _decode(raw, _MAX_MODEL_INPUT_UTF8_BYTES)
    if (set(incoming) != _INPUT_FIELDS or _canonical(incoming) != raw
            or incoming["contract"] != "ltm-model-input.v1" or incoming["role_id"] != "query_specialist"
            or incoming["output_contract"] != "remote-role-result.v1"):
        _fail()
    pack = incoming["context_pack"]
    if (not isinstance(pack, dict) or set(pack) != _PACK_FIELDS
            or len(_canonical(pack).encode("utf-8")) > _MAX_CONTEXT_PACK_UTF8_BYTES
            or pack["contract"] != "evidence-pack.v1" or pack["use_case"] != "query_specialist"
            or not isinstance(incoming["correlation_id"], str) or not _ID.fullmatch(incoming["correlation_id"])
            or pack["request_id"] != incoming["correlation_id"] or not isinstance(_DRAFT_SCHEMA, dict)
            or not isinstance(_QUERY_SCHEMA_JSON, str) or _digest(_QUERY_SCHEMA_JSON) != _QUERY_SCHEMA_SHA256
            or pack["payload_schema_json"] != _QUERY_SCHEMA_JSON or pack["payload_schema_sha256"] != _QUERY_SCHEMA_SHA256):
        _fail()
    schema = _decode(_QUERY_SCHEMA_JSON, 4096)
    if _canonical(schema) != _QUERY_SCHEMA_JSON:
        _fail()
    for value, pattern in ((pack["knowledge_manifest_sha256"], _DIGEST), (incoming["workflow_version"], _UUID)):
        if not isinstance(value, str) or not pattern.fullmatch(value):
            _fail()
    if (not isinstance(incoming["knowledge_version"], str) or not 0 < len(incoming["knowledge_version"]) <= 120
            or not isinstance(incoming["knowledge_proof"], str)
            or not re.fullmatch(r"LTM_RELEASE_SENTINEL_V1=[^\r\n]{1,216}", incoming["knowledge_proof"])):
        _fail()
    rows = pack["sources"]
    if not isinstance(rows, list) or len(rows) > 10:
        _fail()
    sources = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("source_id"), str)
                or not _ID.fullmatch(row["source_id"]) or row["source_id"] in sources
                or not isinstance(row.get("content"), str) or _digest(row["content"]) != row.get("content_sha256")):
            _fail()
        sources.add(row["source_id"])
    return incoming, pack, schema, sources


def main(model_input, candidate):
    """Return an unaccepted remote candidate; no I/O, repair, or meaning reclassification."""
    try:
        incoming, pack, schema, sources = _input(model_input)
        plan = _decode(candidate, _MAX_DRAFT_UTF8_BYTES)
        _validate(plan, _DRAFT_SCHEMA, _DRAFT_SCHEMA)
        status, payload, missing = plan["status"], plan["payload"], plan["missing_inputs"]
        if (plan["contract"] != "query-draft.v2" or any(not value.strip() for value in missing)
                or status == "ok" and missing or status != "ok" and payload
                or status == "needs_input" and not missing or status == "blocked" and missing):
            _fail()
        claims, bindings, citations, citation_ids = [], [], [], {}

        def narrative(wrapped, pointer):
            value, kind, refs = (wrapped[key] for key in ("value", "kind", "source_ids"))
            if any(ref not in sources for ref in refs):
                _fail()
            if value.strip():
                for ref in refs:
                    if ref not in citation_ids:
                        citation_ids[ref] = "cite-" + str(len(citations) + 1)
                        citations.append({"citation_id": citation_ids[ref], "source_id": ref, "locator": ""})
                claim_id = "claim-" + str(len(claims) + 1)
                claims.append({"claim_id": claim_id, "kind": kind, "text": value,
                               "citation_ids": [citation_ids[ref] for ref in refs]})
                bindings.append({"path": pointer, "claim_ids": [claim_id]})
            return value

        output = {}
        if "reads" in payload:
            output["reads"] = []
            for index, read in enumerate(payload["reads"]):
                row = {key: narrative(read[key], f"/reads/{index}/{key}")
                       for key in ("source", "subject", "where") if key in read}
                if "coverage" in read:
                    row["exhaustive"] = _COVERAGE_TO_EXHAUSTIVE[read["coverage"]]
                output["reads"].append(row)
        if "uncertainty" in payload:
            output["uncertainty"] = [narrative(row, f"/uncertainty/{index}") for index, row in enumerate(payload["uncertainty"])]
        if status == "ok":
            _validate(output, schema, schema)
        if len(claims) > 40:
            _fail()
        result = {"contract": "remote-role-result.v1", "role_id": "query_specialist", "status": status,
            "payload": output, "claims": claims, "payload_claims": bindings, "citations": citations,
            "missing_inputs": missing, "warnings": [], "knowledge_manifest_sha256": pack["knowledge_manifest_sha256"],
            **{key: incoming[key] for key in ("workflow_version", "knowledge_version", "knowledge_proof")}}
        return {"candidate": _canonical(result)}
    except Exception:
        raise ValueError("invalid_core_query_draft") from None
