"""Derive Request's internal wire draft from the actual RequestArchitect schema.

The canonical schema remains unchanged. The internal payload intentionally closes
its otherwise extensible root; unknown fields cannot become an unbound narrative
or authority channel. Wire structure and individual value constraints are derived
from the canonical schema. Collection constraints are NOT equivalent after wrapping:
requested_effects with identical values but different kind/source_ids can satisfy
draft uniqueItems. After unwrapping, the assembler MUST validate the entire canonical
payload again; this generator does not repair or deduplicate canonical effects.
Intent has a model-authored annotation even though it is a guard control
field: a payload containing only controls still needs an explicit supported claim.
Boolean labels are exact wire adapters, not coercion, approval, or execution.
"""
import copy
import hashlib
import json
import re

REQUEST_SCHEMA_SHA256 = "aca74433bf7e1fb6015309570fa21f85d2b0d7fc411044820ad5751a2724c57c"
_NARRATIVE_PATHS = {
    ("intent",), ("keywords", "*"), ("mentioned_keys", "*"), ("goal",),
    ("tasks", "*", "instruction"), ("tasks", "*", "completion_criteria", "*"),
    ("blocking_questions", "*"), ("request_questions", "*", "question"),
    ("requested_effects", "*", "target"), ("requested_effects", "*", "value"),
    ("requested_effects", "*", "literal"), ("target_selectors", "*", "cardinality"),
    ("assumptions", "*"), ("plan",),
}
# Routes, retrieval terms, and future acceptance conditions are planning judgments,
# even when evidence-backed. Other narrative fields retain fact support.
_PLANNING_PATHS = {("intent",), ("keywords", "*"), ("tasks", "*", "completion_criteria", "*")}


def build_request_system_prompt(schema: dict, local_role_markdown: str, transport_markdown: str) -> str:
    """Deliver exact local semantics once; only the transport defines native output.

    Selection positions are reviewed with the generated system digest. No business
    meaning is copied here, and canonical true/false wording is never rewritten.
    """
    def fail():
        raise ValueError("Request system prompt requires review")

    def items(body, count, numbered=False):
        rows = []
        pattern = r"([1-9][0-9]*)\. (.*)" if numbered else r"- (.*)"
        for line in body.splitlines():
            match = re.fullmatch(pattern, line)
            if match:
                if numbered and match[1] != str(len(rows) + 1):
                    fail()
                text = match[2] if numbered else match[1]
                if not text.strip():
                    fail()
                rows.append([text])
            elif not line.strip():
                if rows:
                    rows[-1].append(line)
            elif rows and line[0].isspace():
                rows[-1].append(line)
            else:
                fail()
        if len(rows) != count:
            fail()
        return ["\n".join(row).rstrip() for row in rows]

    try:
        if (not isinstance(schema, dict) or schema.get("type") != "object"
                or not isinstance(schema.get("properties"), dict)
                or any(not isinstance(value, str) or not value.strip()
                       for value in (local_role_markdown, transport_markdown))):
            fail()
        # Structural selectors, not a second field-meaning contract. Missing help
        # must not silently leave a shape-only prompt after annotation stripping.
        expected = {"intent", "keywords", "module", "mentioned_keys", "sufficient", "playbook",
                    "answer_depth", "goal", "tasks", "blocking_questions", "request_questions",
                    "requested_effects", "target_selectors", "plan"}
        described, meanings = set(), []
        for name, row in schema["properties"].items():
            if not isinstance(name, str) or not name or not isinstance(row, dict):
                fail()
            if "description" in row:
                description = row["description"]
                if not isinstance(description, str) or not description.strip():
                    fail()
                described.add(name)
                meanings.append(f"- {name}: {description}")
        if described != expected:
            fail()

        sections, current = {}, None
        for line in local_role_markdown.splitlines():
            if line.startswith("## "):
                current = line[3:].strip()
                if not current or current in sections:
                    fail()
                sections[current] = []
            elif current is not None:
                sections[current].append(line)
        selected = {}
        for title in ("Decision Process", "Output Contract", "Ticket Semantics"):
            body = "\n".join(sections[title]).strip()
            if not body:
                fail()
            selected[title] = body
        decisions = items(selected["Decision Process"], 8, numbered=True)
        output = items(selected["Output Contract"], 6)
        items(selected["Ticket Semantics"], 2)
        local = ("## Selected local role semantics (canonical reference only)\n"
                 + "\n".join("- " + row for row in (decisions[1], decisions[7], output[5]))
                 + "\n\n## Ticket Semantics\n" + selected["Ticket Semantics"])
        required_wire = [
            "research_readiness" if key == "sufficient" else key
            for key in schema["required"]
        ]
        required_help = (
            "## Required successful payload\n"
            "For status ok, payload must include ALL of: "
            + ", ".join(required_wire)
            + ". Do not omit required arrays when empty. In particular, requested_effects "
              "and target_selectors must be [] unless the user's explicit mutation meets "
              "their canonical criteria; planning/clarification alone does not request "
              "a scalar Jira mutation. This is not permission to infer an effect."
        )
        prompt = "\n\n".join((transport_markdown.strip(), required_help,
            "## Canonical field meanings (reference only)\n" + "\n".join(meanings), local))
        if len(prompt.encode("utf-8")) > 8192:
            fail()
        return prompt
    except (KeyError, TypeError, UnicodeError) as exc:
        raise ValueError("Request system prompt requires review") from exc


def build_draft_schema(canonical_schema: dict, project) -> dict:
    """Pin the reviewed architect schema; never substitute persisted RequestPlan."""
    def fail():
        raise ValueError("Request draft canonical schema requires review")

    def canonical(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def derive(row, path=()):
        if path in _NARRATIVE_PATHS:
            return {"type": "object", "additionalProperties": False, "required": ["value", "kind", "source_ids"],
                "properties": {"value": copy.deepcopy(row),
                    "kind": {"type": "string", "enum": ["inference", "recommendation"]
                        if path in _PLANNING_PATHS else ["fact", "inference", "recommendation"]},
                    "source_ids": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1, "maxLength": 128,
                                  "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]*(?![\s\S])"}}}}
        result = copy.deepcopy(row)
        if row.get("type") == "object":
            result["properties"] = {key: derive(value, path + (key,)) for key, value in row["properties"].items()}
        elif row.get("type") == "array":
            result["items"] = derive(row["items"], path + ("*",))
        return result

    def wire_boolean(row, old, new, labels):
        if row["properties"].pop(old) != {"type": "boolean"}:
            fail()
        row["properties"][new] = {"type": "string", "enum": labels}
        row["required"] = [new if key == old else key for key in row["required"]]

    try:
        encoded = canonical(canonical_schema)
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != REQUEST_SCHEMA_SHA256:
            fail()
        # This reviewed schema has no refs. Projection may not discard restrictions.
        projected = project(canonical_schema)
        if canonical(projected) != encoded:
            fail()
        payload = derive(projected)
        payload["additionalProperties"] = False
        wire_boolean(payload, "sufficient", "research_readiness", ["ready", "needs_clarification"])
        wire_boolean(payload["properties"]["tasks"]["items"], "write_intent", "effect",
                     ["write_requested", "no_write_requested"])
        return {"type": "object", "additionalProperties": False,
            "required": ["contract", "status", "payload", "missing_inputs"], "properties": {
                "contract": {"type": "string", "enum": ["request-draft.v2"]},
                "status": {"type": "string", "enum": ["ok", "needs_input", "blocked"]},
                "payload": {"anyOf": [{"type": "object", "additionalProperties": False, "maxProperties": 0}, payload]},
                "missing_inputs": {"type": "array", "maxItems": 8,
                    "items": {"type": "string", "minLength": 1, "maxLength": 500}}}}
    except (KeyError, TypeError, RecursionError) as exc:
        raise ValueError("Request draft canonical schema requires review") from exc
