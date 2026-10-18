"""Result wire assembly. Prepend unchanged strict validation/context fragments.

Both original guards remain required. Text/kinds/source references are model-owned;
no truth inference, repair, normalization, truncation or terminal bypass occurs here.
"""
_TEXT_SCHEMA_JSON = None
_TEXT_SCHEMA_SHA256 = None
_DRAFT_SCHEMA = None
_MAX_DRAFT_UTF8_BYTES = 32768
_SEPARATOR = "\n\n"


def _fail():
    raise ValueError("invalid_core_result_draft")


def main(model_input, candidate):
    try:
        incoming, pack, schema, sources = _draft_context(model_input, "result_integrator",
            _TEXT_SCHEMA_JSON, _TEXT_SCHEMA_SHA256, _DRAFT_SCHEMA, _fail)
        draft = _decode(candidate, _MAX_DRAFT_UTF8_BYTES, _fail)
        _validate(draft, _DRAFT_SCHEMA, _DRAFT_SCHEMA, _fail)
        if any(not value.strip() for value in draft["missing_inputs"]):
            _fail()
        claims, citations, citation_ids = [], [], {}
        for segment in draft["segments"]:
            if not segment["value"].strip() or any(ref not in sources for ref in segment["source_ids"]):
                _fail()
            for ref in segment["source_ids"]:
                if ref not in citation_ids:
                    citation_ids[ref] = "cite-" + str(len(citations) + 1)
                    citations.append({"citation_id": citation_ids[ref], "source_id": ref, "locator": ""})
            # Unique per occurrence: repeated values must remain in the /text sequence.
            claims.append({"claim_id": "claim-" + str(len(claims) + 1), "kind": segment["kind"],
                "text": segment["value"], "citation_ids": [citation_ids[ref] for ref in segment["source_ids"]]})
        payload = {"text": _SEPARATOR.join(row["text"] for row in claims)} if draft["status"] == "ok" else {}
        if draft["status"] == "ok":
            _validate(payload, schema, schema, _fail)
        result = {"contract": "remote-role-result.v1", "role_id": "result_integrator",
            "status": draft["status"], "payload": payload, "claims": claims, "citations": citations,
            "payload_claims": [{"path": "/text", "claim_ids": [row["claim_id"] for row in claims]}] if claims else [],
            "missing_inputs": draft["missing_inputs"], "warnings": [],
            "knowledge_manifest_sha256": pack["knowledge_manifest_sha256"],
            **{key: incoming[key] for key in ("workflow_version", "knowledge_version", "knowledge_proof")}}
        encoded = _canonical(result)
        encoded.encode("utf-8")
        return {"candidate": encoded}
    except Exception:
        raise ValueError("invalid_core_result_draft") from None
