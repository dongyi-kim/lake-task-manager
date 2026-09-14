"""Dify stdlib Editor Input and Assembler: project/select facts, never approve.

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
_SECTIONS = ("background", "scope", "work", "dod")
_HEADINGS = dict(zip(_SECTIONS, ("배경", "작업 범위", "작업", "완료 조건 (DoD)")))
_URL = re.compile(r"(?<![A-Za-z0-9_])(?:https?://|javascript:|data:)", re.I)
_PERSON = re.compile(r"\[\[person:[0-9a-f]{12}\]\]")
# This version binds display decisions as well as original source bytes. It does
# not register the derived display text as new evidence or relax either guard.
_UNIT_DISPLAY_POLICY = "editor-units.v2-wiki-body-display"
_WIKI_PREFIX = re.compile(r"^(?:h[1-6][.]|[*#]+)[ \t]+(?=\S)")
_JIRA_BODY = re.compile(r"^/content(?:/[0-9]+)*(?:/fields)?/(?:description|(?:comments?|latestComment)(?:/[0-9]+)?(?:/(?:body|text))?)$")
_CONFLUENCE_BODY = re.compile(r"^/content(?:(?:/[0-9]+)*/body)?$")
_CODE_LEAF = re.compile(r"\{(?:code(?::[^}\r\n]*)?|noformat)\}|<\s*/?\s*(?:pre|code)(?=[\s>])|^[ \t]*(?:`{3,}|~{3,})", re.I | re.M)
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
        if hashlib.sha256(row["content"].encode("utf-8")).hexdigest() != row.get("content_sha256"):
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


def _trusted_seed(pack, sources):
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
    return next(iter(intent_seeds.items()), ("", ""))


def _link(source):
    url = _text(source.get("url"), 2048)
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or any(char.isspace() for char in url)):
        _fail()
    _ = parsed.port
    return url, _text(source["title"])


def _paths(value, path, depth=0):
    if depth > 24:
        _fail()
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key in sorted(value):
            escaped = key.replace("~", "~0").replace("/", "~1")
            yield from _paths(value[key], path + "/" + escaped, depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _paths(item, path + "/" + str(index), depth + 1)


def _unit_rows(source):
    """Keep authoritative original spans separate from bounded body display.

    Only typed Jira body/comment paths and Confluence bodies interpret leading
    wiki headings/lists. Any code/pre/noformat/fence marker opts the entire leaf
    out: do not guess which later lines are prose after incomplete code markup.
    """
    yield "/title", source["title"], source["title"]
    content = source["content"]
    try:
        parsed = _decode(content)
    except (ValueError, TypeError):
        if content.lstrip().startswith(("{", "[", '"')):
            _fail()
        leaves = [("/content", content)]
    else:
        leaves = _paths(parsed, "/content")
    for path, text in leaves:
        body = ((source.get("kind") == "jira" and _JIRA_BODY.fullmatch(path))
                or (source.get("kind") == "confluence" and _CONFLUENCE_BODY.fullmatch(path)))
        normalize = body and not _CODE_LEAF.search(text)
        # Existing line boundaries are extractive units, not generated summaries.
        lines = text.splitlines() or [text]
        for index, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            if len(line) > 1200:
                _fail()  # Never silently crop a factual unit.
            display = _WIKI_PREFIX.sub("", line, count=1) if normalize else line
            yield path + ("/line:" + str(index) if len(lines) > 1 else ""), line, display


def _original_units(source):
    for path, original, _ in _unit_rows(source):
        yield path, original


def _reference_text(text, source_id, sources):
    """Derive visible text+HTML only from exact known source URLs/opaque markers.

    Original EvidencePack remains authoritative. Display text is never added to
    facts. Unknown URL authority fails rather than being hidden behind a label.
    """
    text = _text(text)
    known = {}
    for other_id, source in sources.items():
        if not _intent(source) and source.get("url"):
            url, label = _link(source)
            known.setdefault(url, []).append((other_id, label))
    fragments, refs = [], [source_id]
    cursor = 0
    while True:
        match = _URL.search(text, cursor)
        if match is None:
            tail = text[cursor:]
            fragments.append(html.escape(tail, quote=True))
            break
        start = match.start()
        matches = [url for url in known if text.startswith(url, start)]
        if not matches:
            _fail()
        url = max(matches, key=len)
        end = start + len(url)
        targets = known[url]
        if len(targets) != 1:
            _fail()  # Competing titles/identities need upstream resolution.
        target_id, label = targets[0]
        replace_start, replace_end = start, end
        if start > cursor and text[start - 1] == "|" and end < len(text) and text[end] == "]":
            depth, opening = 0, None
            for index in range(start - 2, max(cursor - 1, start - 602), -1):
                char = text[index]
                if char in "\r\n":
                    break
                if char == "]":
                    depth += 1
                elif char == "[":
                    if depth:
                        depth -= 1
                    else:
                        opening = index
                        break
            if opening is None:
                _fail()
            label = _text(text[opening + 1:start - 1])
            replace_start, replace_end = opening, end + 1
        else:
            # Compare the whole plain URL token, allowing only trailing sentence
            # punctuation. A '?' query, '#fragment', '.suffix' or extra path is
            # NOT covered by a known prefix, even on the same host.
            token = re.match(r"[^\s<>\"']+", text[start:]).group(0)
            if token != url and token.rstrip(".,;:!?)]}") != url:
                _fail()
        prefix = text[cursor:replace_start]
        fragments.append(html.escape(prefix, quote=True))
        fragments.append('<a href="' + html.escape(url, quote=True) + '">' + html.escape(label, quote=True) + "</a>")
        if target_id not in refs:
            refs.append(target_id)
        cursor = replace_end
    markup = "".join(fragments)
    # Match the two unchanged guards' visible-text normalization, including
    # whitespace at anchor boundaries next to sentence punctuation.
    display = _text(_visible(markup))
    if len(refs) > 8 or _URL.search(display):
        _fail()
    # Opaque person tokens stay in text nodes until the local identity resolver.
    for marker in re.findall(r"\[\[person:[^\]]*\]\]", display):
        if not _PERSON.fullmatch(marker):
            _fail()
    return display, markup, refs


def _catalog(sources):
    catalog, lookup = [], {}
    for source_id, source in sources.items():
        if _intent(source):
            continue
        units = []
        for path, original, body_display in _unit_rows(source):
            if not original.strip():
                continue
            display, markup, refs = _reference_text(body_display, source_id, sources)
            binding = [_UNIT_DISPLAY_POLICY, source_id, source.get("kind"), source.get("canonical_id"),
                       source["content_sha256"], source["title"], source.get("url", ""), path, original,
                       display, markup, refs]
            unit_id = "u_" + hashlib.sha256(_canonical(binding).encode("utf-8")).hexdigest()[:20]
            if unit_id in lookup or len(lookup) >= 96:
                _fail()
            lookup[unit_id] = {"text": display, "html": markup, "source_ids": refs}
            units.append({"unit_id": unit_id, "path": path, "text": display})
        if not units:
            _fail()
        available_link = bool(source.get("url"))
        if available_link:
            _link(source)
        catalog.append({"source_id": source_id, "kind": source.get("kind"),
                        "title": source["title"], "available_link": available_link, "units": units})
    return catalog, lookup


def _request(pack, sources, mode):
    values = {"prompt": set(), "ticket_key": set()}
    requests = [_object(source["content"]) for source in sources.values() if _intent(source)]
    for row in pack.get("role_context", []):
        if row.get("name") == "editor_request":
            requests.append(_object(row["content_json"]))
        elif row.get("name") in values:
            requests.append({row["name"]: _decode(row["content_json"])})
    for request in requests:
        for key in values:
            if key in request:
                value = request[key]
                if not isinstance(value, str) or len(value) > 3600:
                    _fail()
                values[key].add(value)
    if any(len(items) > 1 for items in values.values()):
        _fail()
    _, seed = _trusted_seed(pack, sources)
    return {"kind": mode, "target": next(iter(values["ticket_key"]), ""),
            "source_ids": [key for key, source in sources.items() if _intent(source)],
            "prompt": next(iter(values["prompt"]), ""), "seed_visible": seed}


def prepare(model_input):
    """Project trusted KnowledgeGuard output for the model, never mint evidence."""
    try:
        if not isinstance(model_input, str) or len(model_input.encode("utf-8")) > 9000:
            _fail()
        incoming = _object(model_input)
        if incoming.get("contract") != "ltm-model-input.v1":
            _fail()
        pack = incoming.get("context_pack")
        if not isinstance(pack, dict) or len(_canonical(pack).encode("utf-8")) > 6144:
            _fail()
        mode_schema = _schema(pack)
        if mode_schema is None:
            return {"model_input": model_input}  # Non-Editor canary: exact bytes.
        if (incoming.get("role_id") != "editor_author" or pack.get("use_case") != "editor_author"
                or incoming.get("correlation_id") != pack.get("request_id")
                or pack.get("contract") != "evidence-pack.v1"
                or incoming.get("output_contract") != "remote-role-result.v1"):
            _fail()
        sources = _sources(pack)
        _fresh_editor_evidence(pack, sources)
        mode = _mode(pack, sources, mode_schema)
        catalog, _ = _catalog(sources)
        knowledge = incoming.get("knowledge_context")
        if not isinstance(knowledge, list) or len(knowledge) > 2:
            _fail()
        total = 0
        for row in knowledge:
            if not isinstance(row, dict) or set(row) != {"document_name", "content"}:
                _fail()
            _text(row["document_name"])
            content = _text(row["content"])
            total += len(content.encode("utf-8"))
            if len(content.encode("utf-8")) > 768 or total > 1024:
                _fail()
        projected = _canonical({"contract": "editor-input.v1", "role_id": "editor_author",
            "request": _request(pack, sources, mode), "knowledge_context": knowledge, "sources": catalog})
        if len(projected.encode("utf-8")) > 9000:
            _fail()
        return {"model_input": projected}
    except Exception:
        raise ValueError("invalid_editor_input") from None


def _blocks(plan, sources, lookup, mode, seed):
    claims, citations, fragments, used_sources = [], [], [], []
    citation_ids = {}
    list_open = False
    rows = []
    seed_source, seed_text = seed
    if seed_text:
        rows.append(("p", seed_text, html.escape(seed_text, quote=True), "inference", [seed_source], True))
    expected = list(_SECTIONS) if mode == "description" else ["comment"]
    sections = plan["sections"]
    if [row.get("section") for row in sections if isinstance(row, dict)] != expected:
        _fail()
    count = 0
    for section in sections:
        if set(section) != {"section", "blocks"} or not isinstance(section["blocks"], list) or not section["blocks"]:
            _fail()
        count += len(section["blocks"])
        if count > 32:
            _fail()
        section_rows = []
        for block in section["blocks"]:
            section_rows.append(_block(block, sources, lookup))
        if mode == "description":
            refs = list(dict.fromkeys(ref for row in section_rows for ref in row[4]))[:8]
            heading = _HEADINGS[section["section"]]
            rows.append(("h2", heading, html.escape(heading), "recommendation", refs, False))
        rows.extend(section_rows)
    if seed_text:
        additional = _visible(" ".join(row[1] for row in rows if not row[5]))
        if _visible(seed_text) in additional:
            _fail()  # Never accept a second model-selected copy of the seed.
    for tag, text, rendered, claim_kind, refs, is_seed in rows:
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
        for claim_text in _seed_claims(text) if is_seed else [text]:
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


def _block(block, sources, lookup):
    if not isinstance(block, dict):
        _fail()
    kind = block.get("type")
    shapes = {"fact": {"type", "unit_id", "tag"},
              "proposal": {"type", "tag", "text", "source_ids"},
              "link": {"type", "source_id"}}
    if not isinstance(kind, str) or kind not in shapes or set(block) != shapes[kind]:
        _fail()
    tag = block.get("tag", "p")
    if tag not in {"p", "li"}:
        _fail()
    if kind == "fact":
        unit_id = block["unit_id"]
        if not isinstance(unit_id, str) or unit_id not in lookup:
            _fail()
        unit = lookup[unit_id]
        return tag, unit["text"], unit["html"], "fact", unit["source_ids"], False
    refs = block["source_ids"] if kind == "proposal" else [block["source_id"]]
    if (not isinstance(refs, list) or not 0 < len(refs) <= 8
            or any(not isinstance(ref, str) or ref not in sources for ref in refs)
            or len(refs) != len(set(refs))):
        _fail()
    if kind == "proposal":
        text = _text("제안: " + _text(block["text"]))
        # Text may be a concrete question. This label does not ground assertions.
        if _URL.search(text):
            _fail()  # Model-authored URL syntax never acquires link authority.
        for marker in re.findall(r"\[\[person:[^\]]*\]\]", text):
            if not _PERSON.fullmatch(marker) or not any(marker in sources[ref]["content"] for ref in refs):
                _fail()
        return tag, text, html.escape(text, quote=True), "recommendation", refs, False
    source = sources[refs[0]]
    if _intent(source):
        _fail()
    url, text = _link(source)
    rendered = '<a href="' + html.escape(url, quote=True) + '">' + html.escape(text, quote=True) + "</a>"
    return "p", text, rendered, "fact", refs, False


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
        if set(plan) != {"contract", "status", "sections", "missing_inputs"} or plan["contract"] != "editor-plan.v2":
            _fail()
        status, sections, missing = plan["status"], plan["sections"], plan["missing_inputs"]
        if (status not in {"ok", "needs_input", "blocked"} or not isinstance(sections, list)
                or len(sections) > 4 or not isinstance(missing, list) or len(missing) > 8):
            _fail()
        for question in missing:
            _text(question, 500)
        if (status == "ok" and (not sections or missing)) or (
            status != "ok" and sections
        ) or (status == "needs_input" and not missing) or (status == "blocked" and missing):
            _fail()
        if status == "ok":
            _fresh_editor_evidence(pack, sources)
            seed = _trusted_seed(pack, sources)
            _, lookup = _catalog(sources)
            _request(pack, sources, mode)  # Reject conflicting trusted author fields.
            content, claims, citations, source_ids = _blocks(plan, sources, lookup, mode, seed)
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
