"""Lossless Query-only LLM view of an already authorized KnowledgeGuard output.

Standalone stdlib Dify Code node. Wire only from KnowledgeGuard's allow output;
the assembler and final guards must still receive the ORIGINAL model_input.
This view adds existing claim-kind limits, not fact verification or authority.
Original source fields and the assembler/guard input remain unchanged.
"""
from datetime import datetime
import hashlib
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
_HIDDEN_PACK_FIELDS = {"contract", "payload_schema_json", "payload_schema_sha256", "knowledge_manifest_sha256", "query"}
_SOURCE_STRINGS = {"source_id", "kind", "canonical_id", "title", "scope", "url", "content",
                   "retrieved_at", "content_sha256"}
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


def _business_task(pack):
    # The original KnowledgeGuard/backend boundary owns this row, not its name/hash.
    rows = [row for row in pack["role_context"]
            if isinstance(row, dict) and row.get("name") == "query_business_input"]
    if len(rows) != 1:
        _fail()
    row = rows[0]
    expected_id = "rctx_" + hashlib.sha256(
        (pack["request_id"] + ":query_specialist:query_business_input").encode("utf-8")
    ).hexdigest()[:20]
    if (set(row) != {"context_id", "name", "content_json", "content_sha256", "observed_at", "partial"}
            or row["context_id"] != expected_id or row["partial"] is not False
            or row["observed_at"] != pack["requested_at"]
            or datetime.fromisoformat(pack["requested_at"].replace("Z", "+00:00")).utcoffset() is None):
        _fail()
    task = _decode(row["content_json"])
    if (set(task) != {"text"} or type(task["text"]) is not str or not task["text"].strip()
            or len(task["text"].encode("utf-8")) > 1024
            or row["content_sha256"] != hashlib.sha256(row["content_json"].encode("utf-8")).hexdigest()):
        _fail()
    return task, row


def _source_view(row):
    # Same exact authority predicate as Request/Work and both final guards.
    if (type(row) is not dict or set(row) != _SOURCE_STRINGS | {"updated_at", "partial", "truncated"}
            or any(type(row[key]) is not str for key in _SOURCE_STRINGS)
            or row["kind"] not in {"jira", "confluence", "bitbucket", "knowledge", "ltm"}
            or not 1 <= len(row["canonical_id"]) <= 240
            or row["updated_at"] is not None and type(row["updated_at"]) is not str
            or type(row["partial"]) is not bool or type(row["truncated"]) is not bool):
        _fail()
    intent = row["kind"] == "ltm" and row["canonical_id"].startswith("ltm:user-intent:")
    return dict(row, source_authority="user_intent" if intent else "evidence_source",
                allowed_claim_kinds=["inference", "recommendation"] if intent
                else ["fact", "inference", "recommendation"])


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
        task, task_row = _business_task(pack)
        context = {key: value for key, value in pack.items() if key not in _HIDDEN_PACK_FIELDS}
        context["sources"] = [_source_view(row) for row in pack["sources"]]
        context["role_context"] = [row for row in pack["role_context"] if row is not task_row]
        view = _canonical({"contract": "query-input.v2", "task": task, "context": context,
                           "knowledge_context": incoming["knowledge_context"]})
        if len(view.encode("utf-8")) > _MAX_MODEL_INPUT_UTF8_BYTES:
            _fail()
        return {"query_input": view}
    except Exception:
        raise ValueError("invalid_core_query_input") from None
