"""Lossless Query-only LLM view of an already authorized KnowledgeGuard output.

Standalone stdlib Dify Code node. Wire only from KnowledgeGuard's allow output;
the assembler and final guards must still receive the ORIGINAL model_input.
This projection checks framing/bounds, not schema, source or policy authority.
"""
import json
import math
import re


_MAX_MODEL_INPUT_UTF8_BYTES = 9000  # KnowledgeGuard output bound
_MAX_CONTEXT_PACK_UTF8_BYTES = 6144  # ContextGuard / KnowledgeGuard pack bound
_INPUT_FIELDS = {"contract", "context_pack", "role_id", "correlation_id", "output_contract",
                 "workflow_version", "knowledge_version", "knowledge_proof", "knowledge_context"}
_PACK_STRINGS = {"contract", "request_id", "use_case", "query", "pseudonymous_user", "requested_at",
                 "freshness", "knowledge_manifest_sha256", "payload_schema_json", "payload_schema_sha256"}
_PACK_ARRAYS = {"sources", "facts", "metrics", "role_context", "flags"}
_PACK_FIELDS = _PACK_STRINGS | _PACK_ARRAYS | {"scope", "partial"}
_HIDDEN_PACK_FIELDS = {"contract", "payload_schema_json", "payload_schema_sha256", "knowledge_manifest_sha256"}
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _fail():
    raise ValueError("invalid_core_query_input")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _pairs(rows):
    value = {}
    for key, item in rows:
        if key in value:
            _fail()
        value[key] = item
    return value


def _decode(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > _MAX_MODEL_INPUT_UTF8_BYTES:
        _fail()
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: _fail())
    pending, remaining = [(value, 0)], 4096
    while pending:
        item, depth = pending.pop()
        remaining -= 1
        if depth > 32 or remaining < 0 or (isinstance(item, float) and not math.isfinite(item)):
            _fail()
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else []
        pending.extend((child, depth + 1) for child in children)
    if not isinstance(value, dict) or _canonical(value) != raw:
        _fail()
    return value


def main(model_input):
    """Return only a canonical semantic view; no repairs, I/O or acceptance decision."""
    try:
        incoming = _decode(model_input)
        if (set(incoming) != _INPUT_FIELDS
                or any(not isinstance(incoming[key], str) for key in _INPUT_FIELDS - {"context_pack", "knowledge_context"})
                or incoming["contract"] != "ltm-model-input.v1"
                or incoming["role_id"] != "query_specialist"
                or incoming["output_contract"] != "remote-role-result.v1"
                or not _REQUEST_ID.fullmatch(incoming["correlation_id"])
                or not isinstance(incoming["knowledge_context"], list)):
            _fail()
        pack = incoming["context_pack"]
        if (not isinstance(pack, dict) or set(pack) != _PACK_FIELDS
                or any(not isinstance(pack[key], str) for key in _PACK_STRINGS)
                or any(not isinstance(pack[key], list) for key in _PACK_ARRAYS)
                or not isinstance(pack["scope"], dict) or type(pack["partial"]) is not bool
                or pack["contract"] != "evidence-pack.v1"
                or pack["use_case"] != "query_specialist"
                or pack["request_id"] != incoming["correlation_id"]
                or len(_canonical(pack).encode("utf-8")) > _MAX_CONTEXT_PACK_UTF8_BYTES):
            _fail()
        return {"query_input": _canonical({"contract": "query-input.v1",
            "context": {key: value for key, value in pack.items() if key not in _HIDDEN_PACK_FIELDS},
            "knowledge_context": incoming["knowledge_context"]})}
    except Exception:
        raise ValueError("invalid_core_query_input") from None
