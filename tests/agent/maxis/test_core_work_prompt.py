"""Native Work instructions select exact reviewed CREATE policy, never copied answers."""
import copy
import hashlib
from pathlib import Path

import pytest

from app.agent.workflow.work_architect.contracts import CREATE_SCHEMA
from test_core_work_contract import contract, canonical, project

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def texts():
    return ((ROOT / "app/agent/prompts/roles/work_architect.md").read_text(encoding="utf-8"),
            (ROOT / "deploy/maxis-dify/prompts/core_work.md").read_text(encoding="utf-8"))


def build(contract, texts, schema=CREATE_SCHEMA):
    return contract.build_work_system_prompt(schema, *texts)


def test_bounded_native_prompt_preserves_exact_local_policy_and_question_help(contract, texts):
    prompt = build(contract, texts)
    assert contract.WORK_MAX_SYSTEM_UTF8_BYTES == 16384
    assert len(prompt.encode()) <= contract.WORK_MAX_SYSTEM_UTF8_BYTES
    assert hashlib.sha256(texts[0].encode()).hexdigest() == contract.WORK_ROLE_SHA256
    assert prompt.startswith(texts[1].strip()) and "work-draft.v1" in prompt
    for token in ("input_need=required", "input_need=optional", "required_input=true", "required_input=false",
                  "source_content_index", "zero-based", "allowed_claim_kinds", "draft-only",
                  "reproduction", "expected", "actual", "40 claims", "256 bindings"):
        assert token in prompt
    for description in CREATE_SCHEMA["properties"]["questions"]["items"]["properties"].values():
        assert description["description"] in prompt
    for line in ("- Never duplicate the deterministic approval card with a question such as \"proceed?\".",
        "- Preserve a structure explicitly requested or already approved by the user.",
        "- Do not copy the same reference list into every item. Include only evidence whose relation to the specific item can be explained.",
        "- Never claim a search or validation occurred; this role has no tools."):
        assert line in texts[0] and prompt.count(line) == 1
    for forbidden in ("structure_plan", "change_plan", "content_template", "<h3>",
                      "payload_claims", "remote-role-result.v1", "Existing Ticket Changes", "Comment Drafting"):
        assert forbidden not in prompt


def test_wrapped_and_control_paths_exhaust_exact_native_schema(contract, canonical, texts):
    native = contract.build_draft_schema(canonical, project)["properties"]["payload"]["anyOf"][1]
    groups = {"Wrapped": [], "Controls": []}
    def walk(row, path=""):
        if row.get("type") == "object":
            if set(row["properties"]) == {"value", "kind", "source_ids"}:
                groups["Wrapped"].append(path)
            else:
                for key, child in row["properties"].items(): walk(child, path + "/" + key)
        elif row.get("type") == "array": walk(row["items"], path + "/*")
        else: groups["Controls"].append(path)
    walk(native)
    prompt = build(contract, texts)
    for name, paths in groups.items():
        actual = next(line for line in prompt.splitlines() if line.startswith(name + ": "))
        assert actual == name + ": " + ", ".join(sorted(paths))
    assert "/questions/*/input_need" in groups["Controls"]
    assert "/questions/*/required_input" not in prompt


@pytest.mark.parametrize("replacement", [lambda s: s.replace("## Clarification Policy", "## Missing"),
    lambda s: s + "\n## Clarification Policy\n", lambda s: s.replace("Ask at most three", "Ask at most four"),
    lambda s: s.replace("Never duplicate", "Never repeat"), lambda s: s + "\n"])
def test_any_local_source_drift_requires_review(contract, texts, replacement):
    with pytest.raises(ValueError, match="Work system prompt requires review"):
        build(contract, (replacement(texts[0]), texts[1]))


@pytest.mark.parametrize("mutation", [lambda s: s["properties"]["questions"]["items"]["properties"]["question"].pop("description"),
    lambda s: s["properties"]["items"].update(maxItems=13),
    lambda s: s["properties"]["questions"]["items"]["properties"]["field"].update(description="changed")])
def test_schema_shape_or_question_annotation_drift_requires_review(contract, texts, mutation):
    schema = copy.deepcopy(CREATE_SCHEMA)
    mutation(schema)
    with pytest.raises(ValueError, match="Work system prompt requires review"):
        build(contract, texts, schema)


@pytest.mark.parametrize("transport", ["", " ", "한" * 3000])
def test_empty_or_oversized_system_is_not_pruned(contract, texts, transport):
    with pytest.raises(ValueError, match="Work system prompt requires review"):
        build(contract, (texts[0], transport))


def policy_sections(local):
    sections, title = {}, None
    for line in local.splitlines():
        if line.startswith("## "):
            title = line[3:]
            sections[title] = []
        elif title is not None: sections[title].append(line)
    return {title: "\n".join(lines).strip() for title, lines in sections.items()}


def policy_bullets(body):
    rows = body.split("\n- ")
    if not rows[0].startswith("- "): rows.pop(0)
    rows[0] = rows[0].removeprefix("- ")
    return ["- " + row for row in rows]


@pytest.mark.parametrize("title,excluded", [
    ("Clarification Policy", ()), ("Structure Selection", (7,)), ("Decomposition Rules", ()),
    ("Ticket Body Contract", (12,)), ("Bulk Sub-Task Creation", ()), ("Pasted Notes and Lists", ()),
    ("Title and Topic Preservation", ()), ("Stop and Escalate", ()), ("Preflight Check", ())])
def test_all_create_applicable_policy_survives_exactly_once(contract, texts, title, excluded):
    prompt = build(contract, texts)
    for index, row in enumerate(policy_bullets(policy_sections(texts[0])[title])):
        if index not in excluded: assert prompt.count(row) == 1, (title, index, row)


def test_askd2_deferred_scope_and_meeting_policy_are_not_replaced_by_guard_assumptions(contract, texts):
    prompt = build(contract, texts)
    sections = policy_sections(texts[0])
    for title, indices in (("Clarification Policy", (4,)), ("Decomposition Rules", (5, 10)),
            ("Pasted Notes and Lists", (1, 2)), ("Ticket Body Contract", (3, 4, 10, 15, 16, 17, 18)),
            ("Title and Topic Preservation", (3, 4)), ("Bulk Sub-Task Creation", (3,))):
        rows = policy_bullets(sections[title])
        assert all(rows[index] in prompt for index in indices)
    assert sections["Epic Creation"] in prompt
    assert "only native-schema fields" in prompt
    assert "assignee and parent_ref are unsupported" in prompt
    assert "use children" in prompt


def test_work_only_system_cap_is_inclusive_and_never_truncates_policy(contract, texts):
    prompt = build(contract, texts)
    padding = "x" * (contract.WORK_MAX_SYSTEM_UTF8_BYTES - len(prompt.encode()))
    transport = texts[1].strip() + padding
    exact = build(contract, (texts[0], transport))
    assert len(exact.encode()) == 16384 and policy_sections(texts[0])["Epic Creation"] in exact
    with pytest.raises(ValueError, match="Work system prompt requires review"):
        build(contract, (texts[0], transport + "x"))


@pytest.mark.parametrize("field", ["assignee", "parent_ref"])
def test_contextual_owner_and_lineage_references_never_expand_native_fields(contract, canonical, texts, field):
    native = contract.build_draft_schema(canonical, project)["properties"]["payload"]["anyOf"][1]
    item = native["properties"]["items"]["items"]
    for row in (native, item, item["properties"]["children"]["items"]):
        assert row["additionalProperties"] is False and field not in row["properties"]
    prompt = build(contract, texts)
    assert "assignee and parent_ref are unsupported" in prompt and "use children" in prompt
