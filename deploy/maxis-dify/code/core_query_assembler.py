"""Assemble Query provenance without changing model meaning; both final guards remain mandatory.

Dify Code fragment, stdlib only. The builder prepends shared draft validation and context
primitives and appends the three reviewed schema constants. Unbound or malformed inputs fail without a partial candidate.
Internal coverage labels map exactly to the unchanged canonical boolean; this
wire adapter does not coerce malformed booleans or infer an omitted value.
"""
_QUERY_SCHEMA_JSON = None
_QUERY_SCHEMA_SHA256 = None
_DRAFT_SCHEMA = None
_MAX_DRAFT_UTF8_BYTES = 32768
_COVERAGE_TO_EXHAUSTIVE = {"exhaustive": True, "standard": False}


def _fail():
    raise ValueError("invalid_core_query_draft")


def _input(raw):
    return _draft_context(raw, "query_specialist", _QUERY_SCHEMA_JSON, _QUERY_SCHEMA_SHA256, _DRAFT_SCHEMA, _fail)


def main(model_input, candidate):
    """Return an unaccepted remote candidate; no I/O, repair, or meaning reclassification."""
    try:
        incoming, pack, schema, sources = _input(model_input)
        plan = _decode(candidate, _MAX_DRAFT_UTF8_BYTES, _fail)
        _validate(plan, _DRAFT_SCHEMA, _DRAFT_SCHEMA, _fail)
        status, payload, missing = plan["status"], plan["payload"], plan["missing_inputs"]
        if (plan["contract"] != "query-draft.v2" or any(not value.strip() for value in missing)
                or status == "ok" and missing or status != "ok" and payload
                or status == "needs_input" and not missing or status == "blocked" and missing):
            _fail()
        claims, bindings, citations, citation_ids = [], [], [], {}
        business = [row for row in pack.get("role_context", [])
                    if row.get("name") == "query_business_input"]
        capabilities = (_decode(business[0]["content_json"], _MAX_DRAFT_UTF8_BYTES, _fail)
                        if len(business) == 1 else {})
        allowed = capabilities.get("allowed_sources")
        if allowed is not None and allowed != ["comments", "confluence", "jira", "people"]:
            _fail()

        def narrative(wrapped, pointer):
            value, kind, refs = (wrapped[key] for key in ("value", "kind", "source_ids"))
            if any(ref not in sources for ref in refs):
                _fail()
            if value.strip():
                for ref in refs:
                    if ref not in citation_ids:
                        citation_ids[ref] = "cite-" + str(len(citations) + 1)
                        citations.append({"citation_id": citation_ids[ref], "source_id": ref, "locator": ""})
                claim_id = "claim-" + str(len(claims) + 1)
                claims.append({"claim_id": claim_id, "kind": kind, "text": value,
                               "citation_ids": [citation_ids[ref] for ref in refs]})
                bindings.append({"path": pointer, "claim_ids": [claim_id]})
            return value

        output = {}
        if "reads" in payload:
            output["reads"] = []
            for index, read in enumerate(payload["reads"]):
                if allowed is not None and read["source"]["value"] not in allowed:
                    _fail()  # Reject, never silently drop a requested read.
                row = {key: narrative(read[key], f"/reads/{index}/{key}")
                       for key in ("source", "subject", "where") if key in read}
                if "coverage" in read:
                    row["exhaustive"] = _COVERAGE_TO_EXHAUSTIVE[read["coverage"]]
                output["reads"].append(row)
        if "uncertainty" in payload:
            output["uncertainty"] = [narrative(row, f"/uncertainty/{index}") for index, row in enumerate(payload["uncertainty"])]
        if status == "ok":
            _validate(output, schema, schema, _fail)
        if len(claims) > 40:
            _fail()
        result = {"contract": "remote-role-result.v1", "role_id": "query_specialist", "status": status,
            "payload": output, "claims": claims, "payload_claims": bindings, "citations": citations,
            "missing_inputs": missing, "warnings": [], "knowledge_manifest_sha256": pack["knowledge_manifest_sha256"],
            **{key: incoming[key] for key in ("workflow_version", "knowledge_version", "knowledge_proof")}}
        return {"candidate": _canonical(result)}
    except Exception:
        raise ValueError("invalid_core_query_draft") from None
