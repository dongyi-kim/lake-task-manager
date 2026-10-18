"""Work planning metadata has one payload owner; ticket hierarchy stays nested."""
import copy

import pytest
from jsonschema import Draft202012Validator

from app.agent.workflow.work_architect.contracts import CREATE_SCHEMA
from test_core_work_assembler import ERROR, assemble, case, compiled, guards, leaf
from test_core_work_prompt import build, contract, texts


PLANNING = {
    "interpretation": leaf("요청한 작업 범위를 해석"),
    "structure": "task_with_subtasks",
    "structure_why": leaf("한 산출물의 검증 단계를 하위 작업으로 분리"),
    "rationale": leaf("보류된 배포는 이번 초안에서 제외"),
}


def child():
    return {"summary": leaf("결과 검증"), "scope_in": [leaf("수집 결과 확인")],
            "dod": [leaf("확인 결과 기록")], "duedate": ""}


def test_compiled_prompt_names_planning_owner_without_changing_policy(contract, texts):
    prompt = build(contract, texts)
    scope = next(line for line in prompt.splitlines() if line.startswith("Payload-level planning fields:"))
    assert "interpretation, structure, structure_why, rationale" in scope
    assert "optional siblings of questions, mode, items directly inside payload" in scope
    assert "never belong inside items[] or children[]" in scope
    assert "Per-ticket fields belong in payload.items[]" in scope
    assert "nested child work belongs in that item's children[]" in scope
    assert len(prompt.encode("utf-8")) <= contract.WORK_MAX_SYSTEM_UTF8_BYTES


@pytest.mark.parametrize("keys", [(key,) for key in PLANNING] + [
    ("rationale", "structure", "structure_why"), tuple(PLANNING)])
@pytest.mark.parametrize("level", ["item", "child"])
def test_misplaced_planning_fields_fail_at_the_exact_object_without_repair(compiled, case, keys, level):
    _, incoming, draft, _ = case
    item = draft["payload"]["items"][0]
    item["children"] = [child()]
    target = item if level == "item" else item["children"][0]
    target.update({key: copy.deepcopy(PLANNING[key]) for key in keys})
    before = copy.deepcopy((incoming, draft))
    errors = list(Draft202012Validator(compiled[1]).iter_errors(draft))
    assert len(errors) == 1 and errors[0].validator == "anyOf"
    actual = [error for error in errors[0].context if error.schema_path[0] == 1]
    expected_path = ["payload", "items", 0] + (["children", 0] if level == "child" else [])
    assert len(actual) == 1 and actual[0].validator == "additionalProperties"
    assert list(actual[0].absolute_path) == expected_path
    assert not any(error.validator == "required" for error in errors[0].context)
    with pytest.raises(ValueError, match=ERROR):
        assemble(compiled, incoming, draft)
    assert (incoming, draft) == before


@pytest.mark.parametrize("metadata", [False, True])
def test_correct_level_metadata_and_hierarchy_survive_original_assembly_and_guards(compiled, case, metadata):
    pack, incoming, draft, expected = case
    draft["payload"]["items"][0]["children"] = [child()]
    expected["items"][0]["children"] = [{"summary": "결과 검증", "scope_in": ["수집 결과 확인"],
                                          "dod": ["확인 결과 기록"], "duedate": ""}]
    if metadata:
        draft["payload"].update(copy.deepcopy(PLANNING))
        expected.update({key: row["value"] if isinstance(row, dict) else row for key, row in PLANNING.items()})
    before = copy.deepcopy((incoming, draft))
    Draft202012Validator(compiled[1]).validate(draft)
    result = assemble(compiled, incoming, draft)
    assert result["payload"] == expected
    Draft202012Validator(CREATE_SCHEMA).validate(result["payload"])
    remote, local = guards(pack, result)
    assert remote == result and local()[1] == expected
    assert (incoming, draft) == before
    assert not set(PLANNING) & set(result["payload"]["items"][0])
    assert not set(PLANNING) & set(result["payload"]["items"][0]["children"][0])
    if metadata:
        bindings = {row["path"] for row in result["payload_claims"]}
        assert {"/interpretation", "/structure_why", "/rationale"} <= bindings
        assert "/structure" not in bindings


@pytest.mark.parametrize("mode", ["task", "subtask", "epic"])
def test_question_only_payload_preserves_mode_and_question_field(compiled, case, mode):
    pack, incoming, draft, _ = case
    draft["payload"].update(mode=mode, items=[], questions=[{
        "question": leaf("독립 산출물의 범위는?"), "kind": "text", "field": "structure",
        "input_need": "required", "why_required": leaf("생성할 산출물의 범위 확인 필요")}])
    before = copy.deepcopy((incoming, draft))
    Draft202012Validator(compiled[1]).validate(draft)
    result = assemble(compiled, incoming, draft)
    assert result["status"] == "ok" and result["payload"]["mode"] == mode
    assert result["payload"]["items"] == [] and result["missing_inputs"] == []
    assert result["payload"]["questions"] == [{
        "question": "독립 산출물의 범위는?", "kind": "text", "field": "structure",
        "required_input": True, "why_required": "생성할 산출물의 범위 확인 필요"}]
    remote, local = guards(pack, result)
    assert remote == result and local()[1] == result["payload"]
    assert (incoming, draft) == before
