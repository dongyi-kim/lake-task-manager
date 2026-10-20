"""Derive Result's internal segment contract from the unchanged text payload."""
import copy
import hashlib
import json

TEXT_SCHEMA_SHA256 = "079584f599d0a6605b652506b954dd6df9052ecec8fa7bdf73fc3c35ca6f0a29"


def build_result_system_prompt(local_role_markdown, transport_markdown):
    """Reuse canonical semantics, replacing unavailable local input assumptions.

    The final preflight repeats those semantic rules; the native adapter owns its
    output preflight. No source-content or business-case selector is introduced.
    """
    sections, current = {}, None
    for line in local_role_markdown.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            if current in sections: raise ValueError("Result system prompt requires review")
            sections[current] = []
        elif current is not None: sections[current].append(line)
    expected = {"Purpose", "Inputs", "Response Construction", "Output Contract", "Ticket Token Selection", "Stop and Escalate", "Preflight Check"}
    if set(sections) != expected or not transport_markdown.strip():
        raise ValueError("Result system prompt requires review")
    titles = ("Purpose", "Response Construction", "Output Contract", "Ticket Token Selection", "Stop and Escalate")
    selected = "\n\n".join("## " + title + "\n" + "\n".join(sections[title]).strip() for title in titles)
    prompt = ("## Canonical Result semantics (apply only to supplied context)\n\n"
              + selected + "\n\n" + transport_markdown.strip())
    if len(prompt.encode("utf-8")) > 8192:
        raise ValueError("Result system prompt requires review")
    return prompt


def build_draft_schema(text_schema):
    encoded = json.dumps(text_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(encoded.encode()).hexdigest() != TEXT_SCHEMA_SHA256:
        raise ValueError("unsupported_result_payload_schema")
    segment = {"type": "object", "additionalProperties": False,
        "required": ["value", "kind", "source_ids"], "properties": {
            "value": {**copy.deepcopy(text_schema["properties"]["text"]), "maxLength": 1200},
            "kind": {"type": "string", "enum": ["fact", "inference", "recommendation", "open_fact"]},
            "source_ids": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
                "items": {"type": "string", "minLength": 1, "maxLength": 128,
                          "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]*(?![\s\S])"}}}}
    return {"type": "object", "additionalProperties": False,
        "required": ["contract", "status", "segments", "missing_inputs"], "properties": {
            "contract": {"type": "string", "enum": ["result-draft.v1"]},
            "status": {"type": "string", "enum": ["ok", "needs_input", "blocked"]},
            "segments": {"type": "array", "maxItems": 40, "items": segment},
            "missing_inputs": {"type": "array", "maxItems": 8,
                "items": {"type": "string", "minLength": 1, "maxLength": 500}}},
        "anyOf": [
            {"properties": {"status": {"enum": ["ok"]}, "segments": {"minItems": 1},
                            "missing_inputs": {"maxItems": 0}}},
            {"properties": {"status": {"enum": ["needs_input"]}, "segments": {"maxItems": 0},
                            "missing_inputs": {"minItems": 1}}},
            {"properties": {"status": {"enum": ["blocked"]}, "segments": {"maxItems": 0},
                            "missing_inputs": {"maxItems": 0}}}]}
