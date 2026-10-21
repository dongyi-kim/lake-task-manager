"""Bind transport metadata from the verified request, never from model spelling.

No status, payload, claim, citation, warning or question is changed. The existing
Contract Guard still checks every semantic value, citation, schema and execution
UUID after this step. This is not JSON repair or a success fallback.
"""
import json
import re

_FIELDS = {
    "contract", "role_id", "workflow_version", "knowledge_version",
    "knowledge_manifest_sha256", "knowledge_proof",
}
_SEMANTIC = {
    "status", "payload", "claims", "citations", "payload_claims",
    "missing_inputs", "warnings",
}


def _fail():
    raise ValueError("invalid_core_candidate_binding")


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            _fail()
        result[key] = value
    return result


def _decode(value):
    if not isinstance(value, str) or len(value.encode("utf-8")) > 48000:
        _fail()
    parsed = json.loads(value, object_pairs_hook=_pairs, parse_constant=lambda _: _fail())
    if not isinstance(parsed, dict):
        _fail()
    return parsed


def main(model_input, candidate):
    try:
        request, result = _decode(model_input), _decode(candidate)
        pack = request.get("context_pack")
        if (request.get("contract") != "ltm-model-input.v1" or not isinstance(pack, dict)
                or request.get("role_id") != pack.get("use_case")
                or request.get("correlation_id") != pack.get("request_id")
                or request.get("output_contract") != "remote-role-result.v1"
                or set(result) != _FIELDS | _SEMANTIC):
            _fail()
        bound = {
            "contract": request["output_contract"], "role_id": request["role_id"],
            "workflow_version": request["workflow_version"],
            "knowledge_version": request["knowledge_version"],
            "knowledge_manifest_sha256": pack["knowledge_manifest_sha256"],
            "knowledge_proof": request["knowledge_proof"],
        }
        if (any(not isinstance(value, str) or not value for value in bound.values())
                or not re.fullmatch(r"[0-9a-f-]{36}", bound["workflow_version"])
                or not re.fullmatch(r"[0-9a-f]{64}", bound["knowledge_manifest_sha256"])):
            _fail()
        return {"candidate": json.dumps({**result, **bound}, ensure_ascii=False,
                                        sort_keys=True, separators=(",", ":"), allow_nan=False)}
    except (ValueError, TypeError, KeyError, RecursionError):
        _fail()
