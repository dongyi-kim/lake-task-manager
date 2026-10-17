"""Original-input binding only; no role payload traversal or meaning decisions.

Prepend core_draft_validation.py. The role supplies its exact schema constants and
a nonreturning fail callback raising ValueError. Final guards remain mandatory.
"""
import hashlib
import re

_MAX_CONTEXT_PACK_UTF8_BYTES = 6144  # app.agent.maxis.context_guard / Knowledge Guard
_MAX_MODEL_INPUT_UTF8_BYTES = 9000  # Knowledge Guard output bound
_INPUT_FIELDS = {"contract", "context_pack", "role_id", "correlation_id", "output_contract",
                 "workflow_version", "knowledge_version", "knowledge_proof", "knowledge_context"}
_PACK_FIELDS = {"contract", "request_id", "use_case", "query", "pseudonymous_user", "requested_at", "scope",
                "sources", "facts", "metrics", "role_context", "freshness", "partial", "flags",
                "knowledge_manifest_sha256", "payload_schema_json", "payload_schema_sha256"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _draft_context(raw, role_id, schema_json, schema_sha256, draft_schema, fail):
    incoming = _decode(raw, _MAX_MODEL_INPUT_UTF8_BYTES, fail)
    if (set(incoming) != _INPUT_FIELDS or _canonical(incoming) != raw
            or incoming["contract"] != "ltm-model-input.v1" or incoming["role_id"] != role_id
            or incoming["output_contract"] != "remote-role-result.v1"):
        fail()
    pack = incoming["context_pack"]
    if (not isinstance(pack, dict) or set(pack) != _PACK_FIELDS
            or len(_canonical(pack).encode("utf-8")) > _MAX_CONTEXT_PACK_UTF8_BYTES
            or pack["contract"] != "evidence-pack.v1" or pack["use_case"] != role_id
            or not isinstance(incoming["correlation_id"], str) or not _ID.fullmatch(incoming["correlation_id"])
            or pack["request_id"] != incoming["correlation_id"] or not isinstance(draft_schema, dict)
            or not isinstance(schema_json, str) or _digest(schema_json) != schema_sha256
            or pack["payload_schema_json"] != schema_json or pack["payload_schema_sha256"] != schema_sha256):
        fail()
    schema = _decode(schema_json, 4096, fail)
    if _canonical(schema) != schema_json:
        fail()
    for value, pattern in ((pack["knowledge_manifest_sha256"], _DIGEST), (incoming["workflow_version"], _UUID)):
        if not isinstance(value, str) or not pattern.fullmatch(value):
            fail()
    if (not isinstance(incoming["knowledge_version"], str) or not 0 < len(incoming["knowledge_version"]) <= 120
            or not isinstance(incoming["knowledge_proof"], str)
            or not re.fullmatch(r"LTM_RELEASE_SENTINEL_V1=[^\r\n]{1,216}", incoming["knowledge_proof"])):
        fail()
    rows = pack["sources"]
    if not isinstance(rows, list) or len(rows) > 10:
        fail()
    sources = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("source_id"), str)
                or not _ID.fullmatch(row["source_id"]) or row["source_id"] in sources
                or not isinstance(row.get("content"), str) or _digest(row["content"]) != row.get("content_sha256")):
            fail()
        sources.add(row["source_id"])
    return incoming, pack, schema, sources
