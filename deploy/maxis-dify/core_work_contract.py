"""Derive a bounded Work draft from the actual, unchanged CREATE contract only.

The native draft annotates reviewed text leaves and uses one explicit boolean
label adapter. It closes otherwise extensible question objects. No payload
meaning, source choice, approval, or model output is supplied by this builder.
The assembler must revalidate the complete canonical payload after unwrapping.
"""
import copy
import hashlib
import json

WORK_SCHEMA_SHA256 = "8f46d8cc3bbe4712838a066844b90eeef2272a9f0e8a8a6c054b63a338226f1a"
_NARRATIVE_PATHS = {
    ("interpretation",), ("structure_why",), ("rationale",),
    ("items", "*", "summary"), ("items", "*", "background"),
    ("items", "*", "reproduction", "*"), ("items", "*", "expected"), ("items", "*", "actual"),
    ("items", "*", "scope_in", "*"), ("items", "*", "scope_out", "*"),
    ("items", "*", "dod", "*"), ("items", "*", "references", "*"),
    ("items", "*", "children", "*", "summary"),
    ("items", "*", "children", "*", "scope_in", "*"),
    ("items", "*", "children", "*", "dod", "*"),
    ("questions", "*", "question"), ("questions", "*", "options", "*"),
    ("questions", "*", "why_required"),
}

WORK_ROLE_SHA256 = "8469aece3f9481282b6117ab4cca38917d93547b95d1302279b8c9ea2355c5eb"
WORK_MAX_SYSTEM_UTF8_BYTES = 16384  # Work-only: preserve required CREATE policy, not a global budget change.
_ANNOTATED_CREATE_SHA256 = "1edacfc695c5c73e0654a2d6ceb56e1fc58c7f2fc264750c48168d240a5ee555"
_POLICY_SELECTION = {
    "Clarification Policy": (11, tuple(range(11))),
    "Structure Selection": (8, tuple(range(7))),  # structure_plan belongs to a different wire contract.
    "Decomposition Rules": (11, tuple(range(11))),
    "Ticket Body Contract": (19, tuple(index for index in range(19) if index != 12)),
    "Bulk Sub-Task Creation": (5, tuple(range(5))),
    "Pasted Notes and Lists": (4, tuple(range(4))),
    "Title and Topic Preservation": (5, tuple(range(5))),
    "Runtime Output Contract": (7, (1, 2, 3, 6)),
    "Stop and Escalate": (4, tuple(range(4))),
    "Preflight Check": (7, tuple(range(7))),
}


def build_work_system_prompt(schema: dict, local_role_markdown: str, transport_markdown: str) -> str:
    """Select exact reviewed CREATE spans; never rewrite their semantic wording."""
    def fail():
        raise ValueError("Work system prompt requires review")

    def canonical(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def strip_help(row):
        if isinstance(row, dict):
            return {key: strip_help(value) for key, value in row.items() if key != "description"}
        return [strip_help(value) for value in row] if isinstance(row, list) else row

    try:
        if (not isinstance(local_role_markdown, str) or not isinstance(transport_markdown, str)
                or not transport_markdown.strip()
                or hashlib.sha256(local_role_markdown.encode()).hexdigest() != WORK_ROLE_SHA256
                or hashlib.sha256(canonical(schema).encode()).hexdigest() != _ANNOTATED_CREATE_SHA256):
            fail()
        native = build_draft_schema(strip_help(schema), lambda row: row)["properties"]["payload"]["anyOf"][1]
        groups = {"Wrapped": [], "Controls": []}

        def walk(row, path=""):
            if row["type"] == "object":
                if set(row["properties"]) == {"value", "kind", "source_ids"}:
                    groups["Wrapped"].append(path)
                else:
                    for key, child in row["properties"].items(): walk(child, path + "/" + key)
            elif row["type"] == "array": walk(row["items"], path + "/*")
            else: groups["Controls"].append(path)

        walk(native)
        sections, heading = {}, None
        for line in local_role_markdown.splitlines():
            if line.startswith("## "):
                heading = line[3:]
                if not heading or heading in sections: fail()
                sections[heading] = []
            elif heading is not None: sections[heading].append(line)
        if len(sections) != 16: fail()
        selected = []
        for title, (count, positions) in _POLICY_SELECTION.items():
            body = "\n".join(sections[title]).strip()
            rows = body.split("\n- ")
            intro = ""
            if title == "Structure Selection":
                intro = rows.pop(0) + "\n"  # Exact table, no structure_plan instruction.
            elif title == "Ticket Body Contract": rows.pop(0)
            elif not rows[0].startswith("- "): fail()
            rows[0] = rows[0].removeprefix("- ")
            if len(rows) != count: fail()
            selected.append("## " + title + "\n" + intro + "\n".join("- " + rows[index] for index in positions))
        epic = "\n".join(sections["Epic Creation"]).strip().split("\n\n")
        if len(epic) != 4 or len(epic[1].splitlines()) != 4: fail()
        selected.append("## Epic Creation\n" + "\n\n".join(epic))
        help_rows = schema["properties"]["questions"]["items"]["properties"]
        help_text = "\n".join(f"- {key}: {value['description']}" for key, value in help_rows.items())
        paths = "\n".join(name + ": " + ", ".join(sorted(values)) for name, values in groups.items())
        prompt = "\n\n".join((transport_markdown.strip(), paths,
            "## Canonical question meanings (reference only)\n" + help_text, *selected))
        if len(prompt.encode("utf-8")) > WORK_MAX_SYSTEM_UTF8_BYTES: fail()
        return prompt
    except (KeyError, TypeError, UnicodeError) as exc:
        raise ValueError("Work system prompt requires review") from exc


def build_draft_schema(canonical_schema: dict, project) -> dict:
    """Pin CREATE; neither a TaskFlow WorkDraft nor another Work mode is equivalent."""
    def fail():
        raise ValueError("Work draft canonical schema requires review")

    def canonical(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def derive(row, path=()):
        if path in _NARRATIVE_PATHS:
            return {"type": "object", "additionalProperties": False, "required": ["value", "kind", "source_ids"],
                "properties": {"value": copy.deepcopy(row),
                    "kind": {"type": "string", "enum": ["fact", "inference", "recommendation"]},
                    "source_ids": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1, "maxLength": 128,
                                  "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]*(?![\s\S])"}}}}
        result = copy.deepcopy(row)
        if row.get("type") == "object":
            result["properties"] = {key: derive(value, path + (key,)) for key, value in row["properties"].items()}
        elif row.get("type") == "array":
            result["items"] = derive(row["items"], path + ("*",))
        return result

    try:
        encoded = canonical(canonical_schema)
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != WORK_SCHEMA_SHA256:
            fail()
        projected = project(copy.deepcopy(canonical_schema))
        if canonical(projected) != encoded:
            fail()
        payload = derive(projected)
        question = payload["properties"]["questions"]["items"]
        question["additionalProperties"] = False
        if question["properties"].pop("required_input") != {"type": "boolean"}:
            fail()
        question["properties"]["input_need"] = {"type": "string", "enum": ["required", "optional"]}
        question["required"] = ["input_need" if key == "required_input" else key for key in question["required"]]
        return {"type": "object", "additionalProperties": False,
            "required": ["contract", "status", "payload", "missing_inputs"], "properties": {
                "contract": {"type": "string", "enum": ["work-draft.v1"]},
                "status": {"type": "string", "enum": ["ok", "needs_input", "blocked"]},
                "payload": {"anyOf": [{"type": "object", "additionalProperties": False, "maxProperties": 0}, payload]},
                "missing_inputs": {"type": "array", "maxItems": 8,
                    "items": {"type": "string", "minLength": 1, "maxLength": 500}}}}
    except (KeyError, TypeError, RecursionError) as exc:
        raise ValueError("Work draft canonical schema requires review") from exc
