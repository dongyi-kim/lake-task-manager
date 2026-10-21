"""Derive the native Auditor contract from its actual unchanged runtime schema."""
import copy
import hashlib
import json

AUDIT_SCHEMA_SHA256 = "69ea51bd05efe2d91e8c13f4250b1b3bdab876fe77aa5499d8586d887080504e"


def build_draft_schema(schema):
    raw = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(raw.encode()).hexdigest() != AUDIT_SCHEMA_SHA256:
        raise ValueError("Audit canonical schema requires review")

    def derive(row, path=()):
        value = copy.deepcopy(row)
        if row.get("type") == "boolean":
            return {"type": "string", "enum": ["pass", "fail"]}
        if row.get("type") == "string" and ("enum" not in row or path == ("problems", "*", "check")):
            return {"type": "object", "additionalProperties": False,
                "required": ["value", "kind", "source_ids"], "properties": {
                    "value": value, "kind": {"type": "string", "enum": ["fact", "inference", "recommendation"]},
                    "source_ids": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1, "maxLength": 128}}}}
        if row.get("type") == "object":
            value["additionalProperties"] = False
            value["properties"] = {key: derive(child, path + (key,)) for key, child in row["properties"].items()}
        elif row.get("type") == "array": value["items"] = derive(row["items"], path + ("*",))
        return value

    payload = derive(schema)
    # A completed audit needs a cited explanation even if it has no findings.
    payload["required"] = [*payload["required"], "summary"]
    payload["properties"]["summary"]["properties"]["value"]["minLength"] = 1
    return {"type": "object", "additionalProperties": False,
        "required": ["contract", "status", "payload", "missing_inputs"], "properties": {
            "contract": {"type": "string", "enum": ["audit-draft.v1"]},
            "status": {"type": "string", "enum": ["ok", "needs_input", "blocked"]},
            "payload": {"anyOf": [{"type": "object", "additionalProperties": False, "maxProperties": 0}, payload]},
            "missing_inputs": {"type": "array", "maxItems": 8,
                "items": {"type": "string", "minLength": 1, "maxLength": 500}}}}


def build_system_prompt(local_role, transport):
    sections, current = {}, None
    for line in local_role.splitlines():
        if line.startswith("## "):
            current = line[3:]
            if current in sections: raise ValueError("Audit prompt requires review")
            sections[current] = []
        elif current is not None: sections[current].append(line)
    if set(sections) != {"Purpose", "Inputs", "Output Contract", "Audit Sequence", "Blocking Boundary", "Stop and Escalate", "Preflight Check"}:
        raise ValueError("Audit prompt requires review")
    # Local output keys and local-only inputs are not the native transport.
    titles = ("Purpose", "Audit Sequence", "Blocking Boundary", "Stop and Escalate")
    prompt = "\n\n".join(["## Canonical Auditor semantics (only supplied context)"] + [
        "## " + title + "\n" + "\n".join(sections[title]).strip() for title in titles] + [transport.strip()])
    if not transport.strip() or len(prompt.encode()) > 8192:
        raise ValueError("Audit prompt requires review")
    return prompt
