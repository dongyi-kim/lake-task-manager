"""Auditor wire assembly only. Never convert an adverse judgment to a pass."""
_AUDIT_SCHEMA_JSON = None
_AUDIT_SCHEMA_SHA256 = None
_DRAFT_SCHEMA = None


def _fail():
    raise ValueError("invalid_core_audit_draft")


def main(model_input, candidate):
    try:
        incoming, pack, schema, sources = _draft_context(model_input, "auditor",
            _AUDIT_SCHEMA_JSON, _AUDIT_SCHEMA_SHA256, _DRAFT_SCHEMA, _fail)
        draft = _native_candidate(candidate, _DRAFT_SCHEMA, 32768)
        status, payload, missing = draft["status"], draft["payload"], draft["missing_inputs"]
        if (any(not value.strip() for value in missing) or status == "ok" and missing
                or status != "ok" and payload or status == "needs_input" and not missing
                or status == "blocked" and missing): _fail()
        claims, bindings, citations, citation_ids = [], [], [], {}

        def unwrap(value, path):
            if isinstance(value, list):
                return [unwrap(row, path + "/" + str(index)) for index, row in enumerate(value)]
            if not isinstance(value, dict): return value
            if set(value) != {"value", "kind", "source_ids"}:
                return {key: unwrap(child, path + "/" + key.replace("~", "~0").replace("/", "~1"))
                        for key, child in value.items()}
            text, kind, refs = (value[key] for key in ("value", "kind", "source_ids"))
            if any(ref not in sources for ref in refs): raise _NativeDraftIssue("source_id")
            if text.strip():
                for ref in refs:
                    if ref not in citation_ids:
                        citation_ids[ref] = "cite-" + str(len(citations) + 1)
                        citations.append({"citation_id": citation_ids[ref], "source_id": ref, "locator": ""})
                claim_id = "claim-" + str(len(claims) + 1)
                claims.append({"claim_id": claim_id, "text": text, "kind": kind,
                               "citation_ids": [citation_ids[ref] for ref in refs]})
                bindings.append({"path": path, "claim_ids": [claim_id]})
            return text

        output = unwrap(payload, "")
        if status == "ok":
            for axis in ("grounded", "rule_compliant", "answers_request"):
                output[axis] = {"pass": True, "fail": False}[output[axis]]
            if not output["summary"].strip(): _fail()
            _validate(output, schema, schema, _fail)
        if len(claims) > 40 or len(citations) > 40 or len(bindings) > 256: _fail()
        result = {"contract": "remote-role-result.v1", "role_id": "auditor", "status": status,
            "payload": output, "claims": claims, "payload_claims": bindings, "citations": citations,
            "missing_inputs": missing, "warnings": [], "knowledge_manifest_sha256": pack["knowledge_manifest_sha256"],
            **{key: incoming[key] for key in ("workflow_version", "knowledge_version", "knowledge_proof")}}
        return {"candidate": _canonical(result)}
    except _NativeDraftIssue as error:
        raise ValueError("invalid_core_audit_draft " + error.marker) from None
    except Exception:
        raise ValueError("invalid_core_audit_draft") from None
