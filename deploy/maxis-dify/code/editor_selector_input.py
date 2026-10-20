"""Input-only structural hints; embed after the unchanged Editor assembler.

Groups constrain writing buckets, not relevance, current truth or mandatory
selection. Every original unit remains available for history/conflict reasoning.
The unchanged full v2 input and original pack remain the Guard's authorities.
"""


def _editor_selector_projection(view):
    target = view["request"]["target"]
    parents = {entity["key"] for entity in view["entities"]
               if entity["relation"] == "parent" and entity["parent_conflict"] is False}
    projected = {key: value for key, value in view.items() if key != "sources"}
    projected.update(contract="editor-selector-input.v1", sources=[], link_choices=[])
    for source in view["sources"]:
        groups = {name: [] for name in ("current", "context", "shared", "neither")}
        for unit in source["units"]:
            owner = unit["entity_key"]
            bucket = "neither"
            if source["kind"] != "jira":
                bucket = "shared"
            elif target and owner and owner == target:
                bucket = "current"
            elif owner and owner in parents:
                bucket = "context"
            groups[bucket].append(dict(unit))
        projected["sources"].append({"source_id": source["source_id"], "kind": source["kind"],
                                     "title": source["title"], "units": groups})
        if source["available_link"] is True:
            projected["link_choices"].append(source["source_id"])
    return projected


def prepare_selector_input(model_input):
    prepared = prepare(model_input)
    if prepared["route"] == "control":
        return {**prepared, "selector_input": "{}"}  # Unused; never decode/group control input.
    try:
        selector = _canonical(_editor_selector_projection(_object(prepared["model_input"])))
        if len(selector.encode("utf-8")) > 40000:
            _fail()
        return {**prepared, "selector_input": selector}  # Preserve old v2 bytes, not a reserialization.
    except Exception:
        raise ValueError("invalid_editor_input") from None
