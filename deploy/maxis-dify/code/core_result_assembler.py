"""Result wire assembly. Prepend unchanged strict validation/context fragments.

Both original guards remain required. The model selects exact source paths for
facts and authors non-factual prose. No paraphrase repair or truth inference.
"""
_TEXT_SCHEMA_JSON = None
_TEXT_SCHEMA_SHA256 = None
_DRAFT_SCHEMA = None
_MAX_DRAFT_UTF8_BYTES = 32768
_SEPARATOR = "\n\n"


def _fail():
    raise ValueError("invalid_core_result_draft")


class _SourceNumber(str):
    """Preserve the JSON numeric lexeme; binary floats may silently change it."""


def _source_json(value):
    if isinstance(value, _SourceNumber):
        return str(value)
    if isinstance(value, dict):
        return "{" + ",".join(_canonical(key) + ":" + _source_json(item)
                              for key, item in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_source_json(item) for item in value) + "]"
    return _canonical(value)


def _extract_fact(content, pointer):
    """Resolve one exact original JSON value, never a model-authored quotation."""
    if not pointer:
        return content
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        _fail()
    # A source can be plain text; only an explicit pointer requires strict JSON.
    value = json.loads(content, object_pairs_hook=lambda rows: _pairs(rows, _fail),
                       parse_int=_SourceNumber, parse_float=_SourceNumber,
                       parse_constant=lambda _: _fail())
    parent_key = None
    for token in pointer[1:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            parent_key = key
            value = value[key]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", key) and int(key) < len(value):
            parent_key = None
            value = value[int(key)]
        else:
            _fail()
    # Retain an original field's name with scalars and arrays. In particular,
    # [] alone loses which bounded search had no hits and has no groundable
    # tokens. Copy its original owner; never invent a label or a conclusion.
    if parent_key is not None and not isinstance(value, dict):
        return _source_json({parent_key: value})
    # Containers keep their explicit keys/owners; no flattening or inferred labels.
    return str(value) if isinstance(value, str) else _source_json(value)


def main(model_input, candidate):
    try:
        incoming, pack, schema, sources = _draft_context(model_input, "result_integrator",
            _TEXT_SCHEMA_JSON, _TEXT_SCHEMA_SHA256, _DRAFT_SCHEMA, _fail)
        draft = _native_candidate(candidate, _DRAFT_SCHEMA, _MAX_DRAFT_UTF8_BYTES)
        if any(not value.strip() for value in draft["missing_inputs"]):
            _fail()
        source_content = {row["source_id"]: row["content"] for row in pack["sources"]}
        claims, citations, citation_ids = [], [], {}
        for segment in draft["segments"]:
            if segment["kind"] == "fact":
                refs = [segment["source_id"]]
                if refs[0] not in sources:
                    raise _NativeDraftIssue("source_id")
                try:
                    value = _extract_fact(source_content[refs[0]], segment["source_path"])
                except ValueError:
                    raise _NativeDraftIssue("fact_selection") from None
            else:
                refs, value = segment["source_ids"], segment["value"]
            if any(ref not in sources for ref in refs):
                raise _NativeDraftIssue("source_id")
            if not value.strip() or len(value) > 1200:
                _fail()
            for ref in refs:
                if ref not in citation_ids:
                    citation_ids[ref] = "cite-" + str(len(citations) + 1)
                    citations.append({"citation_id": citation_ids[ref], "source_id": ref, "locator": ""})
            # Unique per occurrence: repeated values must remain in the /text sequence.
            claims.append({"claim_id": "claim-" + str(len(claims) + 1), "kind": segment["kind"],
                "text": value, "citation_ids": [citation_ids[ref] for ref in refs]})
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
    except _NativeDraftIssue as error:
        raise ValueError("invalid_core_result_draft " + error.marker) from None
    except Exception:
        raise ValueError("invalid_core_result_draft") from None
