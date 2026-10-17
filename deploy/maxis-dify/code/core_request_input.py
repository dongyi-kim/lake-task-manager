"""Request-only semantic view of an already authorized Knowledge Guard output.

Prepend core_draft_validation.py and core_draft_context.py, then append the same
actual Request schema constants used by its assembler. This metadata projection
is not a lossless copy of the complete EvidencePack: user-intent canonical IDs
are opaque producer identities and are hidden, but other canonical IDs remain.
Original input, final guards, model-authored values/kinds/refs remain unchanged.
Source labels describe existing claim-kind restrictions, never approval or facts.
"""
_REQUEST_SCHEMA_JSON = None
_REQUEST_SCHEMA_SHA256 = None
_DRAFT_SCHEMA = None
_MAX_REQUEST_INPUT_UTF8_BYTES = 9000
_HIDDEN_PACK_FIELDS = {
    "contract", "payload_schema_json", "payload_schema_sha256", "knowledge_manifest_sha256",
}
_PACK_STRING_FIELDS = {
    "contract", "request_id", "use_case", "query", "pseudonymous_user", "requested_at",
    "freshness", "knowledge_manifest_sha256", "payload_schema_json", "payload_schema_sha256",
}
_PACK_ARRAY_FIELDS = {"sources", "facts", "metrics", "role_context", "flags"}
_SOURCE_STRING_FIELDS = {
    "source_id", "kind", "canonical_id", "title", "scope", "url", "content",
    "retrieved_at", "content_sha256",
}
_SOURCE_FIELDS = _SOURCE_STRING_FIELDS | {"updated_at", "partial", "truncated"}
_SOURCE_KINDS = {"jira", "confluence", "bitbucket", "knowledge", "ltm"}


def _fail():
    raise ValueError("invalid_core_request_input")


def _source_view(row):
    """Project exact paths; do not infer authority from IDs or source text."""
    if (type(row) is not dict or set(row) != _SOURCE_FIELDS
            or any(type(row[key]) is not str for key in _SOURCE_STRING_FIELDS)
            or row["kind"] not in _SOURCE_KINDS
            or not 1 <= len(row["canonical_id"]) <= 240
            or row["updated_at"] is not None and type(row["updated_at"]) is not str
            or type(row["partial"]) is not bool or type(row["truncated"]) is not bool):
        _fail()
    # Match both final guards exactly. No source_id prefix or hash derivation.
    intent = row["kind"] == "ltm" and row["canonical_id"].startswith("ltm:user-intent:")
    hidden = {"content_sha256", "canonical_id"} if intent else {"content_sha256"}
    result = {key: value for key, value in row.items() if key not in hidden}
    result["source_authority"] = "user_intent" if intent else "evidence_source"
    result["allowed_claim_kinds"] = (["inference", "recommendation"] if intent
                                     else ["fact", "inference", "recommendation"])
    return result


def main(model_input):
    """Return one bounded view, never an accepted candidate or repaired draft."""
    try:
        incoming, pack, _schema, _sources = _draft_context(
            model_input, "request_architect", _REQUEST_SCHEMA_JSON,
            _REQUEST_SCHEMA_SHA256, _DRAFT_SCHEMA, _fail)
        # The shared binding validates schema/identity/source digests, not every
        # EvidencePack field. Check the frame consumed by this projection only.
        if (any(type(incoming[key]) is not str
                for key in _INPUT_FIELDS - {"context_pack", "knowledge_context"})
                or type(incoming["knowledge_context"]) is not list
                or any(type(pack[key]) is not str for key in _PACK_STRING_FIELDS)
                or any(type(pack[key]) is not list for key in _PACK_ARRAY_FIELDS)
                or type(pack["scope"]) is not dict or type(pack["partial"]) is not bool):
            _fail()
        context = {key: value for key, value in pack.items() if key not in _HIDDEN_PACK_FIELDS}
        # Preserve row order for existing index references; no grouping/dedupe.
        context["sources"] = [_source_view(row) for row in pack["sources"]]
        # role_context (including its hashes and content_json) and policy remain
        # unchanged. Never scrub matching words inside business content.
        view = _canonical({"contract": "request-input.v1", "context": context,
                           "knowledge_context": incoming["knowledge_context"]})
        if len(view.encode("utf-8")) > _MAX_REQUEST_INPUT_UTF8_BYTES:
            _fail()
        return {"request_input": view}
    except Exception:
        raise ValueError("invalid_core_request_input") from None
