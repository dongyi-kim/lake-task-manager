"""Request-only wire adapter; no meaning repair, approval, execution, or I/O.

Prepend shared draft validation and context fragments, then append the three
reviewed schema constants. Return only an unaccepted remote candidate: the final
Dify Contract Guard and local ResponseGuard remain mandatory. Wrapping changes
collection equality, so the full canonical payload is revalidated after unwrap.
"""
_REQUEST_SCHEMA_JSON = None
_REQUEST_SCHEMA_SHA256 = None
_DRAFT_SCHEMA = None
_MAX_DRAFT_UTF8_BYTES = 32768
_READINESS_TO_SUFFICIENT = {"ready": True, "needs_clarification": False}
_EFFECT_TO_WRITE_INTENT = {"write_requested": True, "no_write_requested": False}


def _fail():
    raise ValueError("invalid_core_request_draft")


def main(model_input, candidate):
    """Preserve exact model values/kinds/refs; final guards accept or reject them."""
    try:
        incoming, pack, schema, sources = _draft_context(model_input, "request_architect",
            _REQUEST_SCHEMA_JSON, _REQUEST_SCHEMA_SHA256, _DRAFT_SCHEMA, _fail)
        draft = _decode(candidate, _MAX_DRAFT_UTF8_BYTES, _fail)
        _validate(draft, _DRAFT_SCHEMA, _DRAFT_SCHEMA, _fail)
        status, payload, missing = draft["status"], draft["payload"], draft["missing_inputs"]
        if (draft["contract"] != "request-draft.v2" or any(not value.strip() for value in missing)
                or status == "ok" and missing or status != "ok" and payload
                or status == "needs_input" and not missing or status == "blocked" and missing):
            _fail()
        claims, bindings, citations, citation_ids, claim_ids = [], [], [], {}, {}

        def unwrap(value, pointer):
            if isinstance(value, list):
                return [unwrap(row, pointer + "/" + str(index)) for index, row in enumerate(value)]
            if not isinstance(value, dict):
                return value
            if set(value) != {"value", "kind", "source_ids"}:
                return {key: unwrap(row, pointer + "/" + key) for key, row in value.items()}
            text, kind, refs = (value[key] for key in ("value", "kind", "source_ids"))
            if any(ref not in sources for ref in refs):
                _fail()
            if text.strip():
                if len(text) > 1200:
                    _fail()
                key = (text, kind, tuple(refs))
                if key not in claim_ids:
                    for ref in refs:
                        if ref not in citation_ids:
                            citation_ids[ref] = "cite-" + str(len(citations) + 1)
                            citations.append({"citation_id": citation_ids[ref], "source_id": ref, "locator": ""})
                    claim_ids[key] = "claim-" + str(len(claims) + 1)
                    claims.append({"claim_id": claim_ids[key], "kind": kind, "text": text,
                                   "citation_ids": [citation_ids[ref] for ref in refs]})
                bindings.append({"path": pointer, "claim_ids": [claim_ids[key]]})
            return text

        output = {key: unwrap(value, "/" + key) for key, value in payload.items()
                  if key not in {"research_readiness", "tasks"}}
        if "research_readiness" in payload:
            output["sufficient"] = _READINESS_TO_SUFFICIENT[payload["research_readiness"]]
        if "tasks" in payload:
            output["tasks"] = [{**{key: unwrap(value, f"/tasks/{index}/{key}")
                for key, value in row.items() if key != "effect"},
                "write_intent": _EFFECT_TO_WRITE_INTENT[row["effect"]]}
                for index, row in enumerate(payload["tasks"])]
        if status == "ok":
            _validate(output, schema, schema, _fail)
        if len(claims) > 40 or len(bindings) > 256 or len(citations) > 40:
            _fail()
        result = {"contract": "remote-role-result.v1", "role_id": "request_architect", "status": status,
            "payload": output, "claims": claims, "payload_claims": bindings, "citations": citations,
            "missing_inputs": missing, "warnings": [], "knowledge_manifest_sha256": pack["knowledge_manifest_sha256"],
            **{key: incoming[key] for key in ("workflow_version", "knowledge_version", "knowledge_proof")}}
        return {"candidate": _canonical(result)}
    except Exception:
        raise ValueError("invalid_core_request_draft") from None
