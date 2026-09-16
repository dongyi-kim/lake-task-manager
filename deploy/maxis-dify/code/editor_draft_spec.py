"""Internal selection boundary; embed after editor_assembler.py (stdlib only).

Selections are model judgments, not new evidence. The exact graph supplies the
unchanged Editor Input to both entrypoints; the original EvidencePack remains
authoritative in the assembler and both final guards.
"""

_SPEC_BUCKETS = ("target_unit_ids", "context_unit_ids", "constraint_unit_ids", "history_unit_ids")
_SPEC_FIELDS = {"contract", "status", "mode", "target", *_SPEC_BUCKETS,
                "conflicts", "required_link_source_ids", "missing_inputs"}


def _spec_ids(value, allowed, maximum=32, minimum=0):
    if (not isinstance(value, list) or not minimum <= len(value) <= maximum
            or any(not isinstance(item, str) or item not in allowed for item in value)
            or len(set(value)) != len(value)):
        _fail()
    return set(value)


def _validated_spec(model_input, draft_spec):
    if not isinstance(model_input, str) or len(model_input.encode("utf-8")) > 9000:
        _fail()
    view, spec = _object(model_input), _object(draft_spec)
    if (set(view) != {"contract", "role_id", "request", "draft_contract", "entities", "knowledge_context", "sources"}
            or view["contract"] != "editor-input.v1" or view["role_id"] != "editor_author"
            or set(spec) != _SPEC_FIELDS or spec["contract"] != "editor-draft-spec.v1"):
        _fail()
    request = view["request"]
    if (spec["mode"] not in {"description", "comment"} or spec["mode"] != request["kind"]
            or not isinstance(spec["target"], str) or len(spec["target"]) > 128
            or spec["target"] != request["target"] or spec["status"] not in {"ok", "needs_input", "blocked"}):
        _fail()
    sources = {source["source_id"]: source for source in view["sources"]}
    units = {unit["unit_id"]: (unit, source) for source in sources.values() for unit in source["units"]}
    buckets = {name: _spec_ids(spec[name], units) for name in _SPEC_BUCKETS}
    selected = set()
    for bucket in buckets.values():
        if selected & bucket:
            _fail()
        selected |= bucket
    parents = {entity["key"] for entity in view["entities"] if entity["relation"] == "parent"
               and entity["parent_conflict"] is False}
    for unit_id in buckets["target_unit_ids"]:
        if not spec["target"] or units[unit_id][0]["entity_key"] != spec["target"]:
            _fail()
    for unit_id in buckets["context_unit_ids"]:
        unit, source = units[unit_id]
        if source["kind"] == "jira" and unit["entity_key"] not in parents:
            _fail()
    conflicts = spec["conflicts"]
    if not isinstance(conflicts, list) or len(conflicts) > 8:
        _fail()
    conflicted = set()
    for conflict in conflicts:
        if (not isinstance(conflict, dict) or set(conflict) != {"unit_ids", "blocking"}
                or type(conflict["blocking"]) is not bool):
            _fail()
        conflicted |= _spec_ids(conflict["unit_ids"], units, 8, 2)
    writable = buckets["target_unit_ids"] | buckets["context_unit_ids"] | buckets["constraint_unit_ids"]
    if writable & conflicted or len(selected | conflicted) > 32:
        _fail()
    links = _spec_ids(spec["required_link_source_ids"], sources, 8)
    if any(sources[key]["available_link"] is not True for key in links):
        _fail()
    missing = spec["missing_inputs"]
    if not isinstance(missing, list) or len(missing) > 8:
        _fail()
    for question in missing:
        _text(question, 500)
    if (spec["status"] == "ok" and (missing or any(row["blocking"] for row in conflicts))
            or spec["status"] != "ok" and (writable or links)
            or spec["status"] == "needs_input" and not missing
            or spec["status"] == "blocked" and missing):
        _fail()
    return view, spec, sources, units, buckets


def _spec_terminal(spec):
    return {"contract": "editor-plan.v2", "status": spec["status"],
            "sections": [], "missing_inputs": spec["missing_inputs"]}


def _spec_link_dependencies(view, context_pack):
    """Rebind the projection and count links in the assembler's exact markup."""
    pack = _object(context_pack)
    mode = route(pack, "editor_author", pack.get("request_id"), "remote-role-result.v1")
    sources = _sources(pack)
    _fresh_editor_evidence(pack, sources)
    request = _request(pack, sources, mode)
    catalog, lookup = _catalog(sources, request["target"])
    expected = {"request": request, "sources": catalog, "draft_contract": _draft_contract(mode),
                "entities": _entity_scope(sources, request["target"])}
    if any(_canonical(view[key]) != _canonical(value) for key, value in expected.items()):
        _fail()
    urls = {}
    for key, source in sources.items():
        if not _intent(source) and source.get("url"):
            urls.setdefault(_link(source)[0], []).append(key)
    dependencies = {}
    for key, unit in lookup.items():
        refs = []
        # Only _reference_text creates markup; all source text is HTML-escaped.
        # Keep repeated/self links: deduplicated citation IDs lose multiplicity.
        for href in re.findall(r'<a href="([^"]*)">', unit["html"]):
            targets = urls.get(html.unescape(href), [])
            if len(targets) != 1:
                _fail()
            refs.append(targets[0])
        dependencies[key] = refs
    return dependencies


def prepare_spec(model_input, draft_spec, context_pack):
    """Validate selection and narrow writer input; a non-ok selection skips it."""
    try:
        view, spec, sources, units, buckets = _validated_spec(model_input, draft_spec)
        dependencies = _spec_link_dependencies(view, context_pack)
        if spec["status"] != "ok":
            return {"model_input": "{}", "route": "stop", "candidate": _canonical(_spec_terminal(spec))}
        selected = buckets["target_unit_ids"] | buckets["context_unit_ids"] | buckets["constraint_unit_ids"]
        used = {units[key][1]["source_id"] for key in selected} | set(spec["required_link_source_ids"])
        # History/conflict text cannot accidentally become the writer's evidence.
        writer_sources = [{**source, "units": [{**unit, "link_source_ids": dependencies[unit["unit_id"]]}
                          for unit in source["units"] if unit["unit_id"] in selected]}
                          for key, source in sources.items() if key in used]
        writer_contract = {**view["draft_contract"], "purpose": (
            "Write only the admitted target purpose, work and DoD; parent is background."
            if spec["mode"] == "description" else
            "Write one admitted comment using known person, criteria and document references; server inserts seed."
        )}
        projected = {"contract": "editor-writer-input.v1", "request": view["request"],
                     "draft_contract": writer_contract, "selection": {
                         key: spec[key] for key in ("target_unit_ids", "context_unit_ids", "constraint_unit_ids",
                                                   "required_link_source_ids")}, "sources": writer_sources}
        encoded = _canonical(projected)
        if len(encoded.encode("utf-8")) > 9000:
            _fail()
        return {"model_input": encoded, "route": spec["mode"], "candidate": ""}
    except Exception:
        raise ValueError("invalid_editor_draft_spec") from None


def enforce_spec(candidate, model_input, draft_spec, context_pack):
    """Enforce selection closure before the unchanged original-pack assembler."""
    blocked = {"contract": "editor-plan.v2", "status": "blocked", "sections": [], "missing_inputs": []}
    try:
        view, spec, sources, units, buckets = _validated_spec(model_input, draft_spec)
        dependencies = _spec_link_dependencies(view, context_pack)
        plan = _object(candidate)
        if spec["status"] != "ok":
            if _canonical(plan) != _canonical(_spec_terminal(spec)):
                _fail()
            return {"candidate": _canonical(plan)}
        if (set(plan) != {"contract", "status", "sections", "missing_inputs"}
                or plan["contract"] != "editor-plan.v2" or plan["status"] != "ok"
                or plan["missing_inputs"] != [] or not isinstance(plan["sections"], list)
                or [row.get("section") for row in plan["sections"]] != _section_order(spec["mode"])):
            _fail()
        base = buckets["target_unit_ids"] | buckets["constraint_unit_ids"]
        links, used_links, count = set(spec["required_link_source_ids"]), set(), 0
        for section in plan["sections"]:
            if (set(section) != {"section", "blocks"} or not isinstance(section["blocks"], list)
                    or not section["blocks"]):
                _fail()
            allowed_units = base | (buckets["context_unit_ids"] if section["section"] == "background" else set())
            allowed_sources = {units[key][1]["source_id"] for key in allowed_units} | links | set(view["request"]["source_ids"])
            for block in section["blocks"]:
                count += 1
                if count > 32 or not isinstance(block, dict):
                    _fail()
                kind = block.get("type")
                rendered_links = []
                if kind == "fact":
                    if set(block) != {"type", "unit_id", "tag"} or block["unit_id"] not in allowed_units:
                        _fail()
                    rendered_links = dependencies[block["unit_id"]]
                elif kind == "proposal":
                    if set(block) != {"type", "text", "source_ids", "tag"}:
                        _fail()
                    _text(block["text"])
                    _spec_ids(block["source_ids"], allowed_sources, 8, 1)
                elif kind == "link":
                    if set(block) != {"type", "source_id"}:
                        _fail()
                    rendered_links = [block["source_id"]]
                else:
                    _fail()
                if kind != "link" and block["tag"] not in {"p", "li"}:
                    _fail()
                for source_id in rendered_links:
                    if source_id not in links or source_id in used_links:
                        _fail()
                    used_links.add(source_id)
        if used_links != links:
            _fail()
        return {"candidate": _canonical(plan)}
    except Exception:
        return {"candidate": _canonical(blocked)}
