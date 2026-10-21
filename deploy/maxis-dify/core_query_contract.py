"""Derive the internal Query draft from the canonical payload, never a second payload contract."""
import copy
import json


def build_query_system_prompt(local_role_markdown: str, transport_markdown: str) -> str:
    """Deliver exact reviewed semantic sections, without rewriting wire meanings."""
    def fail():
        raise ValueError("Query system prompt requires review")

    def normalized(value):
        if not isinstance(value, str) or not value.strip():
            fail()
        return value.replace("\r\n", "\n").replace("\r", "\n")

    local, wire = normalized(local_role_markdown), normalized(transport_markdown).strip()
    expected = ("Purpose", "Inputs", "Output Contract", "Query Design",
                "Scope and Tool Contract", "Stop and Escalate", "Preflight Check")
    selected = ("Query Design", "Scope and Tool Contract", "Preflight Check")
    lines, sections = local.splitlines(keepends=True), []
    for index, line in enumerate(lines):
        if line.lstrip(" \t").startswith("##") and not line.startswith("##"):
            fail()
        if line.startswith("##"):
            if not line.startswith("## "):
                fail()
            sections.append((line.removesuffix("\n")[3:], index))
    if tuple(title for title, _ in sections) != expected:
        fail()
    blocks = {}
    for offset, (title, start) in enumerate(sections):
        end = sections[offset + 1][1] if offset + 1 < len(sections) else len(lines)
        if title in selected:
            if not "".join(lines[start + 1:end]).strip():
                fail()
            # Normalize section separators only; preserve every selected line.
            blocks[title] = "".join(lines[start:end]).rstrip("\n")
    marker = "## Output: `query-draft.v2` only"
    if (wire.splitlines().count(marker) != 1
            or any("## " + title in wire.splitlines() for title in selected)):
        fail()
    parts = wire.split("\n" + marker + "\n")
    if len(parts) != 2:
        fail()
    prefix, output = parts
    if not prefix.strip() or not output.strip():
        fail()
    prompt = "\n\n".join((prefix.rstrip("\n"), *(blocks[title] for title in selected),
                           marker + "\n" + output.lstrip("\n")))
    if len(prompt.encode("utf-8")) > 8192:
        fail()
    return prompt


def build_draft_schema(canonical_schema: dict, project) -> dict:
    """Preserve canonical constraints; coverage is an explicit internal boolean wire adapter."""
    def fail():
        raise ValueError("Query draft canonical schema requires review")

    def resolve(value, depth=0):
        if depth > 32:
            fail()
        if isinstance(value, list):
            return [resolve(row, depth + 1) for row in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            reference = value["$ref"]
            if set(value) != {"$ref"} or not isinstance(reference, str) or not reference.startswith("#/"):
                fail()
            target = canonical_schema
            for part in reference[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            return resolve(target, depth + 1)
        return {key: resolve(row, depth + 1) for key, row in value.items() if key != "$defs"}

    def shape(row, keys, kind):
        if not isinstance(row, dict) or set(row) != set(keys.split()) or row.get("type") != kind:
            fail()

    def wrap(value):
        return {"type": "object", "additionalProperties": False, "required": ["value", "kind", "source_ids"],
            "properties": {"value": copy.deepcopy(value),
                "kind": {"type": "string", "enum": ["fact", "inference", "recommendation"]},
                "source_ids": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1, "maxLength": 128,
                              "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$"}}}}

    try:
        payload = copy.deepcopy(project(canonical_schema))
        # In particular, the generic Dify projection must not drop a new allOf/const restriction.
        if json.dumps(payload, sort_keys=True) != json.dumps(resolve(canonical_schema), sort_keys=True):
            fail()
        shape(payload, "type additionalProperties properties", "object")
        if payload["additionalProperties"] is not False or set(payload["properties"]) != {"reads", "uncertainty"}:
            fail()
        reads, uncertainty = (payload["properties"][key] for key in ("reads", "uncertainty"))
        for array in (reads, uncertainty):
            shape(array, "type items maxItems", "array")
            if type(array["maxItems"]) is not int or not 0 < array["maxItems"] <= 8:
                fail()
        row = reads["items"]
        shape(row, "type additionalProperties properties required", "object")
        fields = row["properties"]
        if (row["additionalProperties"] is not False or row["required"] != ["source"]
                or set(fields) != {"source", "subject", "where", "exhaustive"}
                or fields["exhaustive"] != {"type": "boolean"}):
            fail()
        shape(fields["source"], "type enum", "string")
        if not isinstance(fields["source"]["enum"], list) or not fields["source"]["enum"] or any(
                not isinstance(value, str) or not value for value in fields["source"]["enum"]):
            fail()
        for text in (fields["subject"], fields["where"], uncertainty["items"]):
            shape(text, "type maxLength", "string")
            if type(text["maxLength"]) is not int or not 0 < text["maxLength"] <= 240:
                fail()
        for key in ("source", "subject", "where"):
            fields[key] = wrap(fields[key])
        # The external field remains a strict boolean. Only this internal draft
        # uses two explicit wire labels; the assembler preserves optional absence.
        del fields["exhaustive"]
        fields["coverage"] = {"type": "string", "enum": ["exhaustive", "standard"]}
        uncertainty["items"] = wrap(uncertainty["items"])
        return {"type": "object", "additionalProperties": False,
            "required": ["contract", "status", "payload", "missing_inputs"], "properties": {
                "contract": {"type": "string", "enum": ["query-draft.v2"]},
                "status": {"type": "string", "enum": ["ok", "needs_input", "blocked"]},
                "payload": {"anyOf": [{"type": "object", "additionalProperties": False, "maxProperties": 0}, payload]},
                "missing_inputs": {"type": "array", "maxItems": 8,
                    "items": {"type": "string", "minLength": 1, "maxLength": 500}}}}
    except (KeyError, TypeError, RecursionError) as exc:
        raise ValueError("Query draft canonical schema requires review") from exc
