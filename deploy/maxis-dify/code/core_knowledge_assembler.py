"""Knowledge wire assembly only: one leaf value, one exact claim text.

Prepend unchanged core_draft_validation/context and bind canonical/native schemas.
No evidence retrieval, inference, semantic relabeling, repair or I/O. Returns an
unaccepted candidate; the existing remote and local final guards remain required.
"""
_KNOWLEDGE_SCHEMA_JSON = None
_KNOWLEDGE_SCHEMA_SHA256 = None
_DRAFT_SCHEMA = None
_PRODUCER_LIMITS = None


def _fail():
    raise ValueError("invalid_core_knowledge_draft")


class _SourceNumber(str):
    """Keep JSON numeric lexemes exact inside selected records."""


def _source_json(value):
    if isinstance(value, _SourceNumber):
        return str(value)
    if isinstance(value, dict):
        return "{" + ",".join(_canonical(key) + ":" + _source_json(item) for key, item in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_source_json(item) for item in value) + "]"
    return _canonical(value)


def _fact_text(content, pointer, payload_path):
    """Select an original record, never infer its subject from a scalar status."""
    try:
        parsed = json.loads(content, object_pairs_hook=lambda rows: _pairs(rows, _fail),
                            parse_int=_SourceNumber, parse_float=_SourceNumber,
                            parse_constant=lambda _: _fail())
    except json.JSONDecodeError:
        # Bracket-prefixed ticket prose is still plain text, not a broken JSON path.
        if pointer:
            _fail()
        return content
    scalar_field = re.fullmatch(r"/(?:references/[0-9]+/ref|concepts/[0-9]+/term)", payload_path)
    if not pointer:
        if not scalar_field and isinstance(parsed, dict) and parsed:
            return _source_json(parsed)
        _fail()
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        _fail()
    value = parsed
    tokens = pointer[1:].split("/")
    # Narrative facts select complete records, never nested status/date objects:
    # a root object, a root-array member, or a member of a top-level record array.
    if not scalar_field and not (
        isinstance(parsed, list) and len(tokens) == 1
        or isinstance(parsed, dict) and len(tokens) == 2
        and isinstance(parsed.get(tokens[0].replace("~1", "/").replace("~0", "~")), list)
    ):
        _fail()
    for token in tokens:
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", key) and int(key) < len(value):
            value = value[int(key)]
        else:
            _fail()
    # Only a reference identifier or concept term is intentionally a bare scalar.
    if scalar_field and type(value) is str:
        return value
    if not scalar_field and isinstance(value, dict) and value:
        return _source_json(value)
    _fail()


def main(model_input, candidate):
    try:
        incoming, pack, schema, sources = _draft_context(model_input, "knowledge_curator",
            _KNOWLEDGE_SCHEMA_JSON, _KNOWLEDGE_SCHEMA_SHA256, _DRAFT_SCHEMA, _fail)
        draft = _native_candidate(candidate, _DRAFT_SCHEMA, _PRODUCER_LIMITS["draft_utf8_bytes"])
        status, payload, missing = draft["status"], draft["payload"], draft["missing_inputs"]
        if (draft["contract"] != "knowledge-draft.v2" or any(not value.strip() for value in missing)
                or status == "ok" and missing or status != "ok" and payload
                or status == "needs_input" and not missing or status == "blocked" and missing):
            _fail()
        claims, bindings, citations, citation_ids, claim_ids = [], [], [], {}, {}
        source_content = {row["source_id"]: row["content"] for row in pack["sources"]}

        def unwrap(value, path=""):
            if isinstance(value, list):
                return [unwrap(row, path + "/" + str(index)) for index, row in enumerate(value)]
            if set(value) == {"kind", "source_id", "source_path"}:
                kind, refs = value["kind"], [value["source_id"]]
                if refs[0] not in sources:
                    raise _NativeDraftIssue("source_id")
                try:
                    text = _fact_text(source_content[refs[0]], value["source_path"], path)
                except ValueError:
                    raise _NativeDraftIssue("fact_selection") from None
                if not text.strip():
                    _fail()
            elif set(value) == {"value", "kind", "source_ids"}:
                text, kind, refs = (value[key] for key in ("value", "kind", "source_ids"))
            else:
                return {key: unwrap(value[key], path + "/" + key.replace("~", "~0").replace("/", "~1"))
                        for key in sorted(value)}
            if any(ref not in sources for ref in refs):
                raise _NativeDraftIssue("source_id")
            if text.strip():
                if len(text) > 1200: _fail()
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

        output = unwrap(payload)
        if status == "ok":
            _validate(output, schema, schema, _fail)
        if len(claims) > _PRODUCER_LIMITS["claims"] or len(bindings) > _PRODUCER_LIMITS["bindings"] or len(citations) > 40:
            _fail()
        result = {"contract": "remote-role-result.v1", "role_id": "knowledge_curator", "status": status,
            "payload": output, "claims": claims, "payload_claims": bindings, "citations": citations,
            "missing_inputs": missing, "warnings": [], "knowledge_manifest_sha256": pack["knowledge_manifest_sha256"],
            **{key: incoming[key] for key in ("workflow_version", "knowledge_version", "knowledge_proof")}}
        serialized = _canonical(result)
        if len(serialized.encode("utf-8", errors="strict")) > _PRODUCER_LIMITS["envelope_utf8_bytes"]:
            _fail()
        return {"candidate": serialized}
    except _NativeDraftIssue as error:
        raise ValueError("invalid_core_knowledge_draft " + error.marker) from None
    except Exception:
        raise ValueError("invalid_core_knowledge_draft") from None
