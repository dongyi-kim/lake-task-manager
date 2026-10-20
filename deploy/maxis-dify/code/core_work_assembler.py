"""Work CREATE wire assembly only; no meaning repair, approval, execution or I/O.

Prepend the unchanged core_draft_validation.py and core_draft_context.py fragments,
then bind the reviewed canonical CREATE and native draft schemas. This returns
an unaccepted candidate. Dify Contract Guard and local ResponseGuard remain
mandatory, including rejection of model-authored facts citing user intent.
"""
_WORK_SCHEMA_JSON = None
_WORK_SCHEMA_SHA256 = None
_DRAFT_SCHEMA = None
_MAX_DRAFT_UTF8_BYTES = 32768
_NEED_TO_REQUIRED = {"required": True, "optional": False}


def _fail():
    raise ValueError("invalid_core_work_draft")


def main(model_input, candidate):
    """Keep model text/kinds/refs; generate only deterministic transport bindings."""
    try:
        incoming, pack, schema, sources = _draft_context(model_input, "work_architect",
            _WORK_SCHEMA_JSON, _WORK_SCHEMA_SHA256, _DRAFT_SCHEMA, _fail)
        draft = _native_candidate(candidate, _DRAFT_SCHEMA, _MAX_DRAFT_UTF8_BYTES)
        status, payload, missing = draft["status"], draft["payload"], draft["missing_inputs"]
        if (draft["contract"] != "work-draft.v1" or any(not value.strip() for value in missing)
                or status == "ok" and missing or status != "ok" and payload
                or status == "needs_input" and not missing or status == "blocked" and missing):
            _fail()
        claims, bindings, citations, citation_ids, claim_ids = [], [], [], {}, {}

        def pointer(key):
            return str(key).replace("~", "~0").replace("/", "~1")

        def unwrap(value, path):
            if isinstance(value, list):
                return [unwrap(row, path + "/" + str(index)) for index, row in enumerate(value)]
            if not isinstance(value, dict):
                return value
            if set(value) != {"value", "kind", "source_ids"}:
                return {key: unwrap(value[key], path + "/" + pointer(key)) for key in sorted(value)}
            text, kind, refs = (value[key] for key in ("value", "kind", "source_ids"))
            if any(ref not in sources for ref in refs):
                raise _NativeDraftIssue("source_id")
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
                bindings.append({"path": path, "claim_ids": [claim_ids[key]]})
            return text

        output = {key: unwrap(payload[key], "/" + pointer(key)) for key in sorted(payload) if key != "questions"}
        if "questions" in payload:
            output["questions"] = [{**{key: unwrap(row[key], f"/questions/{index}/" + pointer(key))
                for key in sorted(row) if key != "input_need"},
                "required_input": _NEED_TO_REQUIRED[row["input_need"]]}
                for index, row in enumerate(payload["questions"])]
        if status == "ok":
            _validate(output, schema, schema, _fail)
        if len(claims) > 40 or len(bindings) > 256 or len(citations) > 40:
            _fail()
        result = {"contract": "remote-role-result.v1", "role_id": "work_architect", "status": status,
            "payload": output, "claims": claims, "payload_claims": bindings, "citations": citations,
            "missing_inputs": missing, "warnings": [], "knowledge_manifest_sha256": pack["knowledge_manifest_sha256"],
            **{key: incoming[key] for key in ("workflow_version", "knowledge_version", "knowledge_proof")}}
        return {"candidate": _canonical(result)}
    except _NativeDraftIssue as error:
        raise ValueError("invalid_core_work_draft " + error.marker) from None
    except Exception:
        raise ValueError("invalid_core_work_draft") from None
