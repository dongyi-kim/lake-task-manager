"""Stable synthetic Work input/citation regression; no native run fixtures."""
import copy
import json

import pytest

from app.agent.maxis import ResponseGuard, ResponseGuardError
from app.agent.workflow.work_architect.contracts import CREATE_SCHEMA
from test_core_work_graph import ASSEMBLER, INPUT, WORK, code, nodes, work_case
from test_core_work_assembler import case as base_case, encoded, VERSION, PROOF


def test_source_view_has_one_citation_identity_and_preserves_original_evidence(nodes, work_case):
    _, incoming, _, _ = work_case
    original = json.loads(incoming)
    projected = json.loads(code(nodes[INPUT])["main"](incoming)["work_input"])
    assert projected["contract"] == "work-input.v2"
    expected = copy.deepcopy(original["context_pack"])
    for key in ("contract", "payload_schema_json", "payload_schema_sha256", "knowledge_manifest_sha256"):
        del expected[key]
    for row in expected["sources"]:
        intent = row["kind"] == "ltm" and row["canonical_id"].startswith("ltm:user-intent:")
        canonical = row.pop("canonical_id")
        row.pop("content_sha256")
        if not intent:
            row["entity_reference"] = canonical
        row["source_authority"] = "user_intent" if intent else "evidence_source"
        row["allowed_claim_kinds"] = ["inference", "recommendation"] if intent else ["fact", "inference", "recommendation"]
    assert projected == {"contract": "work-input.v2", "context": expected,
                         "knowledge_context": original["knowledge_context"]}
    system = nodes[WORK]["prompt_template"][0]["text"]
    assert "entity_reference identifies an entity, never a citation" in system
    assert "missing_inputs belongs only at root, never in payload" in system
    assert json.loads(incoming) == original


@pytest.mark.parametrize("choice", ["intent_recommendation", "evidence_fact", "entity_reference", "intent_fact"])
def test_generated_view_citation_choices_reach_unchanged_assembler_and_both_guards(nodes, work_case, choice):
    pack, incoming, draft, _ = work_case
    rows = json.loads(code(nodes[INPUT])["main"](incoming)["work_input"])["context"]["sources"]
    row = next(row for row in rows if row["source_authority"] == (
        "evidence_source" if choice in {"evidence_fact", "entity_reference"} else "user_intent"))
    summary = draft["payload"]["items"][0]["summary"]
    summary.update(value=row["content"], source_ids=[row["source_id"]],
                   kind="recommendation" if choice == "intent_recommendation" else "fact")
    if choice == "entity_reference":
        summary["source_ids"] = [row["entity_reference"]]
        with pytest.raises(ValueError, match="^invalid_core_work_draft$"):
            code(nodes[ASSEMBLER])["main"](incoming, encoded(draft))
        return
    result = json.loads(code(nodes[ASSEMBLER])["main"](incoming, encoded(draft))["candidate"])
    assert result["payload"]["items"][0]["summary"] == row["content"]
    assert any(claim["text"] == row["content"] and claim["kind"] == summary["kind"] for claim in result["claims"])
    remote = json.loads(code(nodes["1909000000001005"])["main"](encoded(result),
        encoded(pack.model_dump(mode="json")), "work_architect", pack.request_id,
        "remote-role-result.v1", VERSION, VERSION, "kb-test", [{"content": PROOF}])["result"])
    local = lambda: ResponseGuard().validate_remote_role(encoded(result), expected_role_id="work_architect",
        payload_model=CREATE_SCHEMA, evidence=pack, expected_knowledge_manifest_sha256="a" * 64)
    if choice == "intent_fact":
        assert remote["warnings"] == ["contract_guard:intent_source_fact_forbidden"]
        with pytest.raises(ResponseGuardError, match="intent_source_fact_forbidden"):
            local()
    else:
        assert remote == result and local()[0].status == "ok"


@pytest.mark.parametrize("fault", ["truncated_text", "misplaced_missing_inputs"])
def test_generated_assembler_never_repairs_malformed_or_wrong_scope_output(nodes, work_case, fault):
    _, incoming, draft, _ = work_case
    if fault == "misplaced_missing_inputs":
        draft["payload"]["missing_inputs"] = draft.pop("missing_inputs")
    candidate = encoded(draft)
    if fault == "truncated_text":
        candidate = candidate[:-1]
    with pytest.raises(ValueError, match="^invalid_core_work_draft$"):
        code(nodes[ASSEMBLER])["main"](incoming, candidate)
