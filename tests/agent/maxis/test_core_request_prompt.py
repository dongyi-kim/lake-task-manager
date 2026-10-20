"""Request semantics come from the local role, separately from native transport."""
import copy
import importlib.util
import json
from pathlib import Path
import re

import pytest

from app.agent.maxis.contracts import canonical_payload_schema
from app.agent.workflow.agents.request_architect import SCHEMA

ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = ROOT / "deploy/maxis-dify"
ERROR = "^Request system prompt requires review$"


@pytest.fixture(scope="module")
def contract():
    spec = importlib.util.spec_from_file_location("request_prompt_test", DIRECTORY / "core_request_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def role():
    return (ROOT / "app/agent/prompts/roles/request_architect.md").read_text(encoding="utf-8")


def section(role, title):
    return re.search(r"(?ms)^## " + re.escape(title) + r"\n(.*?)(?=^## |\Z)", role).group(1)


def replace_section(role, title, body):
    return role.replace(section(role, title), "\n" + body + "\n\n", 1)


def test_description_help_is_exact_and_does_not_mutate_the_local_contract(contract, role):
    before = copy.deepcopy(SCHEMA)
    prompt = contract.build_request_system_prompt(SCHEMA, role, "Native transport")
    assert SCHEMA == before
    for name, row in SCHEMA["properties"].items():
        if "description" in row:
            assert prompt.count("- " + name + ": " + row["description"]) == 1
    assert "## Canonical field meanings (reference only)" in prompt
    assert "Use false for materially ambiguous new development" in prompt
    assert "`write_intent=true`" in prompt
    assert "A draft request remains false." in prompt
    assert "never repeat those internal stages as tasks" not in prompt
    assert "## Clarification Policy" not in prompt
    assert "## Stop and Escalate" not in prompt
    assert "## Preflight Check" not in prompt
    assert section(role, "Ticket Semantics").strip() in prompt
    assert "Do not ask again for information already supplied" in prompt
    assert "Include goal and tasks for user deliverables." in prompt
    assert 'A Bug report uses `intent="plan_work"` and `playbook="bug_report"`.' in prompt


def test_annotation_changes_reach_prompt_without_changing_the_canonical_schema(contract, role):
    changed = copy.deepcopy(SCHEMA)
    changed["properties"]["intent"]["description"] += " Preserve this reviewed semantic revision."
    assert canonical_payload_schema(changed) == canonical_payload_schema(SCHEMA)
    baseline = contract.build_request_system_prompt(SCHEMA, role, "Native transport")
    revised = contract.build_request_system_prompt(changed, role, "Native transport")
    assert revised != baseline
    assert changed["properties"]["intent"]["description"] in revised


def test_required_wire_fields_include_empty_mutation_arrays(contract, role):
    prompt = contract.build_request_system_prompt(SCHEMA, role, "Native transport")
    required = ["research_readiness" if name == "sufficient" else name
                for name in SCHEMA["required"]]
    assert "payload must include ALL of: " + ", ".join(required) + "." in prompt
    assert "Do not omit required arrays when empty." in prompt
    assert "requested_effects and target_selectors must be []" in prompt
    assert "This is not permission to infer an effect." in prompt


def test_selected_continuation_lines_survive_without_semantic_rewriting(contract, role):
    changed = role.replace("2. Treat the latest", "2. Preserve true/false literally.\n   Treat the latest")
    changed = changed.replace("8. Set `write_intent", "8. Keep this line.\n   Set `write_intent")
    changed = changed.replace("- A Bug report", "- Keep this bullet.\n  A Bug report")
    prompt = contract.build_request_system_prompt(SCHEMA, changed, "Native transport")
    assert "Preserve true/false literally.\n   Treat the latest" in prompt
    assert "Keep this line.\n   Set `write_intent" in prompt
    assert "Keep this bullet.\n  A Bug report" in prompt


@pytest.mark.parametrize("mutation", [
    "missing_decision", "duplicate_decision", "empty_decision", "missing_ticket", "empty_ticket",
    "duplicate_other", "missing_output", "empty_output", "missing_number", "extra_number",
    "duplicate_number", "wrong_number", "empty_number", "unindented_number_tail",
    "missing_bullet", "extra_bullet", "empty_bullet", "wrong_bullet", "nested_heading",
])
def test_malformed_local_sections_fail_closed(contract, role, mutation):
    if mutation == "missing_decision":
        role = role.replace("## Decision Process", "## Other Decisions")
    elif mutation == "duplicate_decision":
        role += "\n## Decision Process\n1. Duplicated\n"
    elif mutation == "empty_decision":
        role = replace_section(role, "Decision Process", "")
    elif mutation == "missing_ticket":
        role = role.replace("## Ticket Semantics", "## Other Semantics")
    elif mutation == "empty_ticket":
        role = replace_section(role, "Ticket Semantics", "")
    elif mutation == "duplicate_other":
        role += "\n## Purpose\nDuplicated\n"
    elif mutation == "missing_output":
        role = role.replace("## Output Contract", "## Other Output")
    elif mutation == "empty_output":
        role = replace_section(role, "Output Contract", "")
    elif mutation == "missing_number":
        role = re.sub(r"(?m)^8\..*\n", "", role)
    elif mutation == "extra_number":
        role = replace_section(role, "Decision Process", section(role, "Decision Process") + "9. Extra\n")
    elif mutation == "duplicate_number":
        role = role.replace("3. Decompose", "2. Decompose")
    elif mutation == "wrong_number":
        role = role.replace("8. Set", "08. Set")
    elif mutation == "empty_number":
        role = re.sub(r"(?m)^8\..*", "8. ", role)
    elif mutation == "unindented_number_tail":
        role = replace_section(role, "Decision Process", section(role, "Decision Process") + "Extra prose\n")
    elif mutation == "missing_bullet":
        role = re.sub(r"(?m)^- A Bug report.*\n", "", role)
    elif mutation == "extra_bullet":
        role = replace_section(role, "Output Contract", section(role, "Output Contract") + "- Extra\n")
    elif mutation == "empty_bullet":
        role = re.sub(r"(?m)^- A Bug report.*", "- ", role)
    elif mutation == "wrong_bullet":
        role = role.replace("- A Bug report", "* A Bug report")
    else:
        role = replace_section(role, "Output Contract", section(role, "Output Contract") + "### Unexpected\n")
    with pytest.raises(ValueError, match=ERROR):
        contract.build_request_system_prompt(SCHEMA, role, "Native transport")


@pytest.mark.parametrize("mutation", ["missing_description", "empty_description", "wrong_description", "no_properties"])
def test_missing_or_invalid_field_help_fails_closed(contract, role, mutation):
    schema = copy.deepcopy(SCHEMA)
    if mutation == "missing_description":
        del schema["properties"]["intent"]["description"]
    elif mutation == "empty_description":
        schema["properties"]["intent"]["description"] = "  "
    elif mutation == "wrong_description":
        schema["properties"]["intent"]["description"] = True
    else:
        del schema["properties"]
    with pytest.raises(ValueError, match=ERROR):
        contract.build_request_system_prompt(schema, role, "Native transport")


@pytest.mark.parametrize("field", ["role", "transport"])
@pytest.mark.parametrize("invalid", [None, "", "  \n ", 1])
def test_invalid_markdown_fails_closed(contract, role, field, invalid):
    with pytest.raises(ValueError, match=ERROR):
        contract.build_request_system_prompt(SCHEMA, invalid if field == "role" else role,
                                             invalid if field == "transport" else "Native transport")


def test_system_budget_is_utf8_bytes_and_accepts_the_exact_boundary(contract, role):
    initial = contract.build_request_system_prompt(SCHEMA, role, "Native transport")
    room = 8192 - len(initial.encode("utf-8"))
    padding = "가" * (room // 3) + "x" * (room % 3)
    boundary = contract.build_request_system_prompt(SCHEMA, role, "Native transport" + padding)
    assert len(boundary.encode("utf-8")) == 8192
    assert len(boundary) < 8192
    with pytest.raises(ValueError, match=ERROR):
        contract.build_request_system_prompt(SCHEMA, role, "Native transport" + padding + "가")


def test_transport_explains_only_wire_evidence_and_status_boundaries(contract, role):
    transport = (DIRECTORY / "prompts/core_request.md").read_text(encoding="utf-8").strip()
    prompt = contract.build_request_system_prompt(SCHEMA, role, transport)
    assert len(prompt.encode("utf-8")) <= 8192
    for marker in ("request-input.v1", "request-draft.v2", "context.sources[].source_id",
                   "context.query", "role_context", "source_authority", "allowed_claim_kinds",
                   "user_intent", "evidence_source", "do not verify facts or grant approval",
                   "canonical_id", "{value, kind, source_ids}", "tasks[].kind",
                   "research_readiness", "needs_clarification", "no_write_requested",
                   "canonical flat schema", "even when research_readiness is needs_clarification"):
        assert marker in transport
    assert "ltm-model-input.v1" not in transport
    assert "context_pack." not in transport
    assert "Bug" not in transport
    for row in SCHEMA["properties"].values():
        if "description" in row:
            assert row["description"] not in transport
    assert section(role, "Ticket Semantics").strip() not in transport


def test_transport_requires_unique_keys_before_final_native_output(contract, role):
    transport = (DIRECTORY / "prompts/core_request.md").read_text(encoding="utf-8").strip()
    prompt = contract.build_request_system_prompt(SCHEMA, role, transport)
    for rule in (
        "JSON keys must be unique at every level.",
        "No appended revisions.",
    ):
        assert transport.count(rule) == 1
        assert prompt.count(rule) == 1
    assert prompt.startswith(transport + "\n\n")
    assert len(prompt.encode("utf-8")) < 8192


def test_transport_lists_exact_wrapped_and_plain_payload_paths(contract):
    transport = (DIRECTORY / "prompts/core_request.md").read_text(encoding="utf-8")
    canonical = json.loads(canonical_payload_schema(SCHEMA)[0])
    native = contract.build_draft_schema(canonical, lambda schema: schema)
    expected = {"Wrapped paths": set(), "Plain paths": set()}

    def walk(row, path=()):
        if path in contract._NARRATIVE_PATHS:
            expected["Wrapped paths"].add(".".join(path).replace(".*", "[]"))
        elif row["type"] == "object":
            for key, item in row["properties"].items():
                walk(item, path + (key,))
        elif row["type"] == "array":
            walk(row["items"], path + ("*",))
        else:
            expected["Plain paths"].add(".".join(path).replace(".*", "[]"))

    walk(native["properties"]["payload"]["anyOf"][1])
    for label, paths in expected.items():
        lines = [line for line in transport.splitlines() if line.startswith(label + ": ")]
        assert len(lines) == 1
        assert set(re.findall(r"`([^`]+)`", lines[0])) == paths
    planning = ("intent, keywords[], assumptions[], tasks[].completion_criteria[], blocking_questions[] "
        "and request_questions[].question are planning judgments or questions: their kind is only "
        "inference|recommendation, even when supported by evidence.")
    assert transport.count(planning) == 1
    assert "Other wrapped paths allow fact|inference|recommendation" in transport
    assert "Never annotate a plain value" in transport
