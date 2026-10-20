"""Lossless business view of the already guarded Result input, not new authority."""
_TEXT_SCHEMA_JSON = None
_TEXT_SCHEMA_SHA256 = None
_DRAFT_SCHEMA = None
_HIDDEN = {"contract", "payload_schema_json", "payload_schema_sha256", "knowledge_manifest_sha256"}


def _fail():
    raise ValueError("invalid_core_result_input")


def main(model_input):
    try:
        incoming, pack, _schema, _sources = _draft_context(model_input, "result_integrator",
            _TEXT_SCHEMA_JSON, _TEXT_SCHEMA_SHA256, _DRAFT_SCHEMA, _fail)
        context = {key: value for key, value in pack.items() if key not in _HIDDEN}
        # Keep all source kinds, content, business identifiers, timestamps, order,
        # partial/truncated flags, facts, metrics and role context. Hashes remain
        # bound to the original input used by the assembler and final guards.
        for key in ("sources", "role_context"):
            context[key] = [{name: value for name, value in row.items() if name != "content_sha256"}
                for row in pack[key]]
        for row in context["sources"]:
            intent = row["kind"] == "ltm" and row["canonical_id"].startswith("ltm:user-intent:")
            row["fact_paths"] = {"paths": [], "complete": True} if intent else _fact_paths(row["content"], records_only=False)
        view = _encode_fact_view({"contract": "result-input.v1", "context": context,
            "knowledge_context": incoming["knowledge_context"]}, _MAX_MODEL_INPUT_UTF8_BYTES)
        if len(view.encode("utf-8")) > _MAX_MODEL_INPUT_UTF8_BYTES: _fail()
        return {"result_input": view}
    except Exception:
        raise ValueError("invalid_core_result_input") from None
