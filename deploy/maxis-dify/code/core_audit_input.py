"""Expose audit business context, not final-envelope schemas or opaque metadata."""
_AUDIT_SCHEMA_JSON = None
_AUDIT_SCHEMA_SHA256 = None
_DRAFT_SCHEMA = None


def _fail():
    raise ValueError("invalid_core_audit_input")


def main(model_input):
    try:
        incoming, pack, _schema, _sources = _draft_context(model_input, "auditor",
            _AUDIT_SCHEMA_JSON, _AUDIT_SCHEMA_SHA256, _DRAFT_SCHEMA, _fail)
        context = {key: value for key, value in pack.items() if key not in {
            "contract", "payload_schema_json", "payload_schema_sha256", "knowledge_manifest_sha256"}}
        rows = []
        for original in pack["sources"]:
            row = {key: value for key, value in original.items() if key not in {"content_sha256", "canonical_id"}}
            intent = original["kind"] == "ltm" and original["canonical_id"].startswith("ltm:user-intent:")
            if not intent: row["entity_reference"] = original["canonical_id"]
            row["allowed_claim_kinds"] = ["inference", "recommendation"] if intent else ["fact", "inference", "recommendation"]
            rows.append(row)
        context["sources"] = rows
        view = _canonical({"contract": "audit-input.v1", "context": context,
                           "knowledge_context": incoming["knowledge_context"]})
        if len(view.encode("utf-8")) > _MAX_MODEL_INPUT_UTF8_BYTES: _fail()
        return {"audit_input": view}
    except Exception:
        raise ValueError("invalid_core_audit_input") from None
