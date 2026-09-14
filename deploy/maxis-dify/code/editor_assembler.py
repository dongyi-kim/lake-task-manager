"""Dify stdlib Code node: assemble explicit editor plans, never approve them.

Wire ``candidate`` to the existing Contract Guard, then retain LTM's ResponseGuard.
Only exact EditorDraftV1 schemas select this path. Other payload contracts pass
through unchanged for their normal final validation. No evidence is created or
changed: quote support comes from original source text, not model-authored facts.
"""

import hashlib
import html
import json
import re
from urllib.parse import urlsplit


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_INTENT = "ltm:user-intent:"
_HEADINGS = {"배경", "작업 범위", "작업", "완료 조건 (DoD)"}
_ANNOTATIONS = {"$comment", "default", "deprecated", "description", "examples",
                "readOnly", "title", "writeOnly"}
_EDITOR_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["mode", "content_html"],
    "properties": {
        "contract": {"const": "editor-draft.v1", "type": "string"},
        "mode": {"enum": ["description", "comment"], "type": "string"},
        "content_html": {"maxLength": 24000, "minLength": 1, "type": "string"},
        "source_ids": {"items": {"maxLength": 128, "minLength": 1,
            "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$", "type": "string"},
            "maxItems": 10, "type": "array"},
        "unsupported_claims": {"items": {"maxLength": 500, "minLength": 1,
            "type": "string"}, "maxItems": 16, "type": "array"},
        "warnings": {"items": {"maxLength": 500, "minLength": 1,
            "type": "string"}, "maxItems": 16, "type": "array"},
    },
}


def _fail():
    raise ValueError("invalid_editor_plan")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            _fail()
        result[key] = value
    return result


def _decode(value):
    if not isinstance(value, str) or len(value.encode("utf-8")) > 131072:
        _fail()
    return json.loads(value, object_pairs_hook=_pairs, parse_constant=lambda _: _fail())


def _object(value):
    result = _decode(value if isinstance(value, str) else _canonical(value))
    if not isinstance(result, dict):
        _fail()
    return result


def _text(value, maximum=1200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        _fail()
    if "LTM_RELEASE_SENTINEL_V1=" in value or any(
        ord(char) < 32 and char not in "\t\r\n" for char in value
    ):
        _fail()
    value.encode("utf-8")
    return value


def _visible(value):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _safe_metadata(value, maximum):
    if not isinstance(value, str) or len(value) > maximum:
        return ""
    try:
        value.encode("utf-8")
    except UnicodeError:
        return ""
    return value


def _seed_claims(text):
    # Split only at existing normalized whitespace: never alter a word or silently
    # truncate a long seed to satisfy the per-claim contract.
    current = ""
    for word in text.split():
        if len(word) > 1200:
            _fail()
        combined = current + " " + word if current else word
        if len(combined) > 1200:
            yield current
            current = word
        else:
            current = combined
    if current:
        yield current


def _schema_without_annotations(value, depth=0):
    if depth > 24:
        _fail()
    if isinstance(value, dict):
        # Property names are identifiers, not JSON Schema annotation keywords.
        return {key: ({name: _schema_without_annotations(child, depth + 1)
                       for name, child in item.items()} if key == "properties" and isinstance(item, dict)
                      else _schema_without_annotations(item, depth + 1))
                for key, item in value.items() if key not in _ANNOTATIONS}
    if isinstance(value, list):
        return [_schema_without_annotations(item, depth + 1) for item in value]
    return value


def _schema(pack):
    raw = pack.get("payload_schema_json")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 4096:
        _fail()
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != pack.get("payload_schema_sha256"):
        _fail()
    schema = _object(raw)
    if _canonical(schema) != raw:
        _fail()
    schema = _schema_without_annotations(schema)
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        _fail()
    looks_editor = "content_html" in props or props.get("contract", {}).get("const") == "editor-draft.v1"
    if not looks_editor:
        return None
    mode_schema = props.get("mode")
    accepted = [_EDITOR_SCHEMA["properties"]["mode"],
                {"type": "string", "const": "description"},
                {"type": "string", "const": "comment"}]
    if mode_schema not in accepted:
        _fail()
    expected = _object(_canonical(_EDITOR_SCHEMA))
    expected["properties"]["mode"] = mode_schema
    if _canonical(schema) != _canonical(expected):
        _fail()
    return mode_schema


def _intent(source):
    return source.get("kind") == "ltm" and str(source.get("canonical_id", "")).startswith(_INTENT)


def _sources(pack):
    rows = pack.get("sources")
    if not isinstance(rows, list) or len(rows) > 10:
        _fail()
    sources = {}
    for row in rows:
        if not isinstance(row, dict):
            _fail()
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not _ID.fullmatch(source_id) or source_id in sources:
            _fail()
        if not isinstance(row.get("title"), str) or not isinstance(row.get("content"), str):
            _fail()
        sources[source_id] = row
    return sources


def _mode(pack, sources, mode_schema):
    modes = set()
    if "const" in mode_schema:
        modes.add(mode_schema["const"])
    for source in sources.values():
        if _intent(source):
            request = _object(source["content"])
            if "kind" in request:
                modes.add(request["kind"])
    for row in pack.get("role_context", []):
        if row.get("name") == "kind":
            modes.add(_decode(row["content_json"]))
        elif row.get("name") == "editor_request":
            request = _object(row["content_json"])
            if "kind" in request:
                modes.add(request["kind"])
    if len(modes) != 1 or not modes.issubset({"description", "comment"}):
        _fail()
    return next(iter(modes))


def _fresh_editor_evidence(pack, sources):
    # The ContextGuard owns observation clocks and expiry. This node consumes its
    # declared state; it neither recomputes time nor upgrades incomplete evidence.
    if pack.get("freshness") != "fresh" or pack.get("partial") is not False:
        _fail()
    for source in sources.values():
        if source.get("partial", False) is not False or source.get("truncated", False) is not False:
            _fail()
    for row in pack.get("role_context", []):
        if row.get("partial", False) is not False:
            _fail()


def _comment_seed(pack, sources, blocks, mode):
    if mode != "comment":
        return
    intent_seeds = {}
    for source_id, source in sources.items():
        if _intent(source):
            seed = _object(source["content"]).get("seed_html", "")
            if not isinstance(seed, str):
                _fail()
            if seed.strip():
                visible = _visible(_text(seed, 24000))
                if visible:
                    intent_seeds[source_id] = visible
    # A single request source owns the seed. Even identical competing nonempty
    # sources are ambiguous; do not choose one by input order or model preference.
    if len(intent_seeds) > 1:
        _fail()
    for row in pack.get("role_context", []):
        if row.get("name") == "seed_html":
            seed = _decode(row["content_json"])
        elif row.get("name") == "editor_request":
            request = _object(row["content_json"])
            if "seed_html" not in request:
                continue  # Missing is not an explicit instruction to clear the seed.
            seed = request["seed_html"]
        else:
            continue
        if not isinstance(seed, str):
            _fail()
        visible = _visible(_text(seed, 24000)) if seed.strip() else ""
        if visible != next(iter(intent_seeds.values()), ""):
            _fail()
    if intent_seeds:
        seed_source = next(iter(intent_seeds))
        seed_blocks = [row for row in blocks if isinstance(row, dict) and row.get("type") == "seed"]
        if (len(seed_blocks) != 1 or blocks[0] is not seed_blocks[0]
                or seed_blocks[0].get("source_id") != seed_source):
            _fail()


def _leaves(value, depth=0):
    if depth > 24:
        _fail()
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _leaves(item, depth + 1)


def _quote_texts(source):
    yield source["title"]
    content = source["content"]
    try:
        parsed = _decode(content)
    except (ValueError, TypeError):
        if content.lstrip().startswith(("{", "[", '"')):
            _fail()
        yield content
    else:
        # JSON syntax/keys are not factual text. Only actual string values support quotes.
        yield from _leaves(parsed)


def _link(source):
    url = _text(source.get("url"), 2048)
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or any(char.isspace() for char in url)):
        _fail()
    _ = parsed.port
    return url, _text(source["title"])


def _blocks(plan, sources):
    claims, citations, fragments, used_sources = [], [], [], []
    citation_ids = {}
    list_open = False
    for block in plan["blocks"]:
        if not isinstance(block, dict):
            _fail()
        kind = block.get("type")
        shapes = {
            "quote": {"type", "tag", "source_id", "text"},
            "proposal": {"type", "tag", "text", "source_ids"},
            "seed": {"type", "source_id"}, "link": {"type", "source_id"},
        }
        if kind not in shapes or set(block) != shapes[kind]:
            _fail()
        refs = block.get("source_ids") if kind == "proposal" else [block["source_id"]]
        if (not isinstance(refs, list) or not 0 < len(refs) <= 8
                or any(not isinstance(ref, str) or ref not in sources for ref in refs)
                or len(refs) != len(set(refs))):
            _fail()
        source = sources[refs[0]]
        tag = block.get("tag", "p")
        if kind == "quote":
            text = _text(block["text"])
            if _intent(source) or tag not in {"p", "li"} or not any(
                text in original for original in _quote_texts(source)
            ):
                _fail()
            claim_kind = "fact"
        elif kind == "proposal":
            text = _text(block["text"])
            if tag not in {"h2", "p", "li"} or (tag == "h2" and text not in _HEADINGS):
                _fail()
            if tag != "h2":
                # Preserve the proposed nature in user-visible HTML as well as the
                # sidecar. This label is NOT semantic grounding or hallucination
                # approval; disguised factual assertions still need direct review.
                text = _text("제안: " + text)
            claim_kind = "recommendation"
        elif kind == "seed":
            if not _intent(source):
                _fail()
            seed = _text(_object(source["content"]).get("seed_html"), 24000)
            text = _text(_visible(seed), 24000)
            claim_kind = "inference"
        else:
            if _intent(source):
                _fail()
            url, text = _link(source)
            claim_kind = "fact"
        rendered = html.escape(text, quote=True)
        if kind == "link":
            rendered = '<a href="' + html.escape(url, quote=True) + '">' + rendered + "</a>"
        if tag == "li" and not list_open:
            fragments.append("<ul>")
            list_open = True
        elif tag != "li" and list_open:
            fragments.append("</ul>")
            list_open = False
        fragments.append("<" + tag + ">" + rendered + "</" + tag + ">")
        linked = []
        for source_id in refs:
            if source_id not in citation_ids:
                cite_id = "cite-" + str(len(citations) + 1)
                citation_ids[source_id] = cite_id
                citations.append({"citation_id": cite_id, "source_id": source_id, "locator": ""})
                used_sources.append(source_id)
            linked.append(citation_ids[source_id])
        for claim_text in _seed_claims(text) if kind == "seed" else [text]:
            if len(claims) >= 40:
                _fail()
            claims.append({"claim_id": "claim-" + str(len(claims) + 1), "kind": claim_kind,
                           "text": claim_text, "citation_ids": linked})
    if list_open:
        fragments.append("</ul>")
    content = "".join(fragments)
    if len(content) > 24000 or _visible(content) != _visible(" ".join(row["text"] for row in claims)):
        _fail()
    return content, claims, citations, used_sources


def main(candidate, context_pack, role_id, correlation_id, output_contract,
         workflow_version, knowledge_version, knowledge_proof):
    """Return a candidate, never an accepted result; all failures are content-free."""
    result = {
        "contract": "remote-role-result.v1", "role_id": _safe_metadata(role_id, 128), "status": "blocked",
        "payload": {}, "claims": [], "payload_claims": [], "citations": [],
        "missing_inputs": [], "warnings": ["editor_assembler:invalid_plan"],
        "workflow_version": _safe_metadata(workflow_version, 120),
        "knowledge_version": _safe_metadata(knowledge_version, 120),
        "knowledge_manifest_sha256": "0" * 64,
        "knowledge_proof": _safe_metadata(knowledge_proof, 240),
    }
    try:
        pack = _object(context_pack)
        if any(not result[key] for key in ("role_id", "workflow_version", "knowledge_version", "knowledge_proof")):
            _fail()
        digest = pack.get("knowledge_manifest_sha256")
        if isinstance(digest, str) and _DIGEST.fullmatch(digest):
            result["knowledge_manifest_sha256"] = digest
        else:
            _fail()
        if (pack.get("contract") != "evidence-pack.v1" or pack.get("request_id") != correlation_id
                or pack.get("use_case") != role_id or output_contract != "remote-role-result.v1"):
            _fail()
        mode_schema = _schema(pack)
        if mode_schema is None:
            # No acceptance is implied: the unchanged final Contract Guard is mandatory.
            return {"candidate": candidate if isinstance(candidate, str) else _canonical(candidate)}
        if role_id != "editor_author":
            _fail()
        sources = _sources(pack)
        mode = _mode(pack, sources, mode_schema)
        plan = _object(candidate)
        if set(plan) != {"contract", "status", "blocks", "missing_inputs"} or plan["contract"] != "editor-plan.v1":
            _fail()
        status, blocks, missing = plan["status"], plan["blocks"], plan["missing_inputs"]
        if (status not in {"ok", "needs_input", "blocked"} or not isinstance(blocks, list)
                or len(blocks) > 32 or not isinstance(missing, list) or len(missing) > 8):
            _fail()
        for question in missing:
            _text(question, 500)
        if (status == "ok" and (not blocks or missing)) or (
            status != "ok" and blocks
        ) or (status == "needs_input" and not missing) or (status == "blocked" and missing):
            _fail()
        if status == "ok":
            _fresh_editor_evidence(pack, sources)
            _comment_seed(pack, sources, blocks, mode)
            content, claims, citations, source_ids = _blocks(plan, sources)
            result.update(payload={"contract": "editor-draft.v1", "mode": mode,
                "content_html": content, "source_ids": source_ids,
                "unsupported_claims": [], "warnings": []}, claims=claims, citations=citations,
                payload_claims=[{"path": "/content_html", "claim_ids": [row["claim_id"] for row in claims]}])
        result.update(status=status, missing_inputs=missing, warnings=[])
    except Exception:
        # Do not serialize exception messages or partially assembled model content.
        result.update(status="blocked", payload={}, claims=[], payload_claims=[],
                      citations=[], missing_inputs=[], warnings=["editor_assembler:invalid_plan"])
    return {"candidate": _canonical(result)}
