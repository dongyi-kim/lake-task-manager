"""Knowledge-only view; original input and final guards retain authority."""
_KNOWLEDGE_SCHEMA_JSON = None
_KNOWLEDGE_SCHEMA_SHA256 = None
_DRAFT_SCHEMA = None
_PRODUCER_LIMITS = None
_HIDDEN = {"contract", "payload_schema_json", "payload_schema_sha256", "knowledge_manifest_sha256"}
_SOURCE_STRINGS = {"source_id", "kind", "canonical_id", "title", "scope", "url", "content", "retrieved_at", "content_sha256"}
_PACK_STRINGS = {"contract", "request_id", "use_case", "query", "pseudonymous_user", "requested_at",
                 "freshness", "knowledge_manifest_sha256", "payload_schema_json", "payload_schema_sha256"}


def _fail():
    raise ValueError("invalid_core_knowledge_input")


def _source_view(row):
    if (type(row) is not dict or set(row) != _SOURCE_STRINGS | {"updated_at", "partial", "truncated"}
            or any(type(row[key]) is not str for key in _SOURCE_STRINGS)
            or row["kind"] not in {"jira", "confluence", "bitbucket", "knowledge", "ltm"}
            or not 1 <= len(row["canonical_id"]) <= 240
            or row["updated_at"] is not None and type(row["updated_at"]) is not str
            or type(row["partial"]) is not bool or type(row["truncated"]) is not bool):
        _fail()
    # Same predicate as both final guards, never source_id/title/content inference.
    intent = row["kind"] == "ltm" and row["canonical_id"].startswith("ltm:user-intent:")
    result = {key: value for key, value in row.items() if key not in {"content_sha256", "canonical_id"}}
    if not intent: result["entity_reference"] = row["canonical_id"]
    result["allowed_claim_kinds"] = ["inference", "recommendation"] if intent else ["fact", "inference", "recommendation"]
    return result


def main(model_input):
    try:
        incoming, pack, _schema, _sources = _draft_context(model_input, "knowledge_curator",
            _KNOWLEDGE_SCHEMA_JSON, _KNOWLEDGE_SCHEMA_SHA256, _DRAFT_SCHEMA, _fail)
        if (any(type(pack[key]) is not str for key in _PACK_STRINGS)
                or any(type(pack[key]) is not list for key in ("sources", "facts", "metrics", "role_context", "flags"))
                or type(pack["scope"]) is not dict or type(pack["partial"]) is not bool
                or type(incoming["knowledge_context"]) is not list or type(_PRODUCER_LIMITS) is not dict):
            _fail()
        context = {key: value for key, value in pack.items() if key not in _HIDDEN}
        context["sources"] = [_source_view(row) for row in pack["sources"]]
        # Original order, facts, metrics and role_context survive; policy is not evidence.
        view = _canonical({"contract": "knowledge-input.v1", "context": context,
            "knowledge_context": incoming["knowledge_context"], "producer_limits": _PRODUCER_LIMITS})
        if len(view.encode("utf-8", errors="strict")) > _MAX_MODEL_INPUT_UTF8_BYTES: _fail()
        return {"knowledge_input": view}
    except Exception:
        raise ValueError("invalid_core_knowledge_input") from None
