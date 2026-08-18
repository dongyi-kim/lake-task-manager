"""Product-neutral Work fast-path contracts for exact single-ticket updates."""

from __future__ import annotations

import itertools
import os

import pytest
from jsonschema import Draft202012Validator
from langchain_core.messages import HumanMessage

os.environ.setdefault("JIRA_ENV", "mock")

from app.agent.workflow.agents.base import StructuredAgent  # noqa: E402
from app.agent.workflow.agents.request_architect import RequestArchitect  # noqa: E402
from app.agent.workflow.agents.work_architect import WorkArchitect  # noqa: E402
from app.agent.workflow.effect_contract import (  # noqa: E402
    derive_pending_decision,
    issue_requested_update_effects,
    project_final_authority_state,
    project_pending_rationale,
    validate_requested_effect_contract,
)


def _state(text: str, *, targets=("ACME-42",), effects: dict | None = None) -> dict:
    state = {
        "thread_id": "exact-update-fast-path",
        "intent": "modify",
        "request_text": text,
        "messages": [HumanMessage(content=text)],
        "mentioned_keys": list(targets),
        "request_plan": {"goal": text, "tasks": [{
            "id": "update", "kind": "write", "write_intent": True,
            "instruction": text,
        }]},
        "continuation_contract": {
            "version": "continuation.v1", "root_request": text,
            "intent": "modify", "action": "update",
            "target_keys": list(targets), "outcome_ids": ["update"],
            "decisions": [],
        },
        "draft": {"mode": "task", "items": [{
            "summary": "Stale generated work", "type": "Task",
        }]},
        "change_plan": {
            "key": "ACME-99", "changes": {"priority": "P4-Trivial"},
            "why": "stale semantic output",
        },
        "turns": 3,
    }
    if effects is not None:
        proposed = [{
            "target": targets[0], "field": field, "value": value,
            "literal": value.split("-", 1)[0] if field == "priority" else value,
        } for field, value in effects.items()]
        state["request_plan"]["requested_effects"] = issue_requested_update_effects(
            proposed, targets, text,
        )
    return state


def test_exact_multi_field_update_skips_semantic_base_across_clause_permutations(
        monkeypatch):
    def forbidden_base(_self):
        return lambda _state: pytest.fail("semantic Work base must not be called")

    monkeypatch.setattr(StructuredAgent, "node", forbidden_base)
    clauses = (
        ("priority", "P1-Critical", "우선순위를 P1로 변경"),
        ("duedate", "2031-10-03", "마감은 2031-10-03으로 변경"),
        ("summary", "Cache rollover", '제목은 "Cache rollover"로 변경'),
    )

    ordered_cases = [
        ordered for size in range(1, len(clauses) + 1)
        for selected in itertools.combinations(clauses, size)
        for ordered in itertools.permutations(selected)
    ]
    for ordered in ordered_cases:
        expected = {field: value for field, value, _clause in ordered}
        state = _state(
            "ACME-42 " + " 그리고 ".join(row[2] for row in ordered), effects=expected,
        )
        result = WorkArchitect().node()(state)
        plan = result["change_plan"]

        assert result["questions"] == []
        assert result["turns"] == 4
        assert result["draft"]["items"] == []
        assert "Stale generated work" not in str(result)
        assert plan["key"] == "ACME-42" and plan["changes"] == expected
        assert "ACME-99" not in str(plan) and "stale semantic output" not in str(plan)
        assert plan["effect_contract"] == "requested-effects.v1"
        assert {
            (row["target"], row["field"], row["value"])
            for row in plan["requested_effects"]["effects"]
        } == {("ACME-42", field, value) for field, value in expected.items()}
        assert plan["rationale_contract"] == "pending-rationale.v1"
        assert plan["why"] == project_pending_rationale(change_plan=plan)
        assert derive_pending_decision(change_plan=plan).kind == "update"
        merged = {**state, **result}
        assert validate_requested_effect_contract(merged) == []
        assert project_final_authority_state(merged)["draft"] == {}
        fast_path = result["trace"][0]["fastPath"]
        assert fast_path == {
            "contract": "typed-fast-path.v1",
            "id": "work.exact_single_ticket_update",
            "complete": True,
            "authority": "request-plan.requested-effects.v1+continuation.v1",
            "savedCalls": 1,
            "missing": [],
        }


@pytest.mark.parametrize(
    ("text", "targets"),
    [
        ("ACME-41과 ACME-42 중 하나의 우선순위를 P1로 변경", ("ACME-41", "ACME-42")),
        ("ACME-42 우선순위를 P1로, 라벨을 urgent로 변경", ("ACME-42",)),
        ("ACME-42 우선순위를 P1로 바꾸고 본문을 장애 분석 내용으로 교체", ("ACME-42",)),
        ("ACME-41과 ACME-42 우선순위를 P1로 일괄 변경", ("ACME-41", "ACME-42")),
    ],
)
def test_incomplete_or_non_scalar_update_preserves_semantic_fallback(
        monkeypatch, text, targets):
    calls = []

    def semantic_base(_self):
        def run(state):
            calls.append(state)
            return {"semantic_fallback": True}
        return run

    monkeypatch.setattr(StructuredAgent, "node", semantic_base)

    result = WorkArchitect().node()(_state(text, targets=targets))

    assert result["semantic_fallback"] is True
    assert len(calls) == 1


def test_current_turn_must_match_frozen_requested_effects(monkeypatch):
    calls = []

    def semantic_base(_self):
        return lambda state: calls.append(state) or {"semantic_fallback": True}

    monkeypatch.setattr(StructuredAgent, "node", semantic_base)
    current = "ACME-42 우선순위를 P1로 변경"
    state = _state(current, effects={"priority": "P1-Critical"})
    state["continuation_contract"]["root_request"] = "ACME-42 우선순위를 P2로 변경"

    result = WorkArchitect().node()(state)

    assert result["semantic_fallback"] is True
    assert len(calls) == 1


@pytest.mark.parametrize("text", [
    "ACME-42 우선순위를 P1에서 P2로 변경",
    "ACME-42 우선순위는 P1이 아니라 P2로 변경",
    "ACME-42 마감 2031-10-03은 잘못이고 2031-10-04로 변경",
    "ACME-42 마감은 2031-10-03 말고 2031-10-04로 변경",
])
def test_multiple_or_negated_scalar_candidates_require_semantic_fallback(monkeypatch, text):
    calls = []

    def semantic_base(_self):
        return lambda state: calls.append(state) or {"semantic_fallback": True}

    monkeypatch.setattr(StructuredAgent, "node", semantic_base)

    result = WorkArchitect().node()(_state(text))

    assert result["semantic_fallback"] is True
    assert len(calls) == 1


def test_request_architect_rejects_correction_candidates_from_exact_lane(monkeypatch):
    text = (
        "ACME-42 우선순위를 P1에서 P2로 바꾸고 "
        "마감 2031-10-03은 잘못이고 2031-10-04로 변경"
    )
    source = {"request_text": text, "messages": [HumanMessage(content=text)]}
    out = {
        "intent": "modify", "keywords": ["ACME-42"], "module": "",
        "mentioned_keys": ["ACME-42"], "sufficient": True, "playbook": "",
        "answer_depth": "brief", "goal": text, "request_questions": [],
        "requested_effects": [
            {"target": "ACME-42", "field": "priority", "value": "P2-Major",
             "literal": "P2"},
            {"target": "ACME-42", "field": "duedate", "value": "2031-10-04",
             "literal": "2031-10-04"},
        ],
        "tasks": [{
            "id": "update", "kind": "write", "instruction": text,
            "depends_on": [], "write_intent": True,
            "completion_criteria": ["두 scalar 변경값을 적용한다"],
        }],
        "blocking_questions": [], "assumptions": [], "plan": "변경값 확인 → 승인",
    }
    Draft202012Validator(RequestArchitect().schema()).validate(out)
    patch = RequestArchitect().apply(source, out)

    assert "requested_effects" not in patch["request_plan"]
    calls = []
    monkeypatch.setattr(StructuredAgent, "node", lambda _self: (
        lambda state: calls.append(state) or {"semantic_fallback": True}
    ))
    result = WorkArchitect().node()({
        **source, **patch, "draft": {"items": [{"summary": "stale"}]},
        "change_plan": {"key": "ACME-99", "changes": {"priority": "P1-Critical"}},
    })

    assert result["semantic_fallback"] is True
    assert len(calls) == 1


def test_request_architect_runtime_grounds_unique_effects_before_zero_call(monkeypatch):
    text = 'ACME-42 우선순위를 P2로 바꾸고 제목은 "Cache rollover"로 변경'
    source = {"request_text": text, "messages": [HumanMessage(content=text)]}
    out = {
        "intent": "modify", "keywords": ["ACME-42"], "module": "",
        "mentioned_keys": ["ACME-42"], "sufficient": True, "playbook": "",
        "answer_depth": "brief", "goal": text, "request_questions": [],
        "requested_effects": [
            {"target": "ACME-42", "field": "priority", "value": "P2-Major",
             "literal": "P2"},
            {"target": "ACME-42", "field": "summary", "value": "Cache rollover",
             "literal": "Cache rollover"},
        ],
        "tasks": [{
            "id": "update", "kind": "write", "instruction": text,
            "depends_on": [], "write_intent": True,
            "completion_criteria": ["두 scalar 변경값을 적용한다"],
        }],
        "blocking_questions": [], "assumptions": [], "plan": "변경값 확인 → 승인",
    }
    Draft202012Validator(RequestArchitect().schema()).validate(out)
    patch = RequestArchitect().apply(source, out)

    issued = patch["request_plan"]["requested_effects"]
    assert [{key: row[key] for key in ("target", "field", "value", "literal")}
            for row in issued] == out["requested_effects"]
    assert all(len(row["source_digest"]) == 64 for row in issued)
    monkeypatch.setattr(
        StructuredAgent, "node",
        lambda _self: lambda _state: pytest.fail("semantic Work base must not be called"),
    )
    result = WorkArchitect().node()({**source, **patch})

    assert result["change_plan"]["changes"] == {
        "priority": "P2-Major", "summary": "Cache rollover",
    }


def test_wrong_but_valid_model_effect_is_not_issued_as_authority(monkeypatch):
    text = "ACME-42 우선순위를 P2로 변경"
    source = {"request_text": text, "messages": [HumanMessage(content=text)]}
    out = {
        "intent": "modify", "keywords": ["ACME-42"], "mentioned_keys": ["ACME-42"],
        "sufficient": True, "request_questions": [],
        "requested_effects": [{
            "target": "ACME-42", "field": "priority", "value": "P1-Critical",
            "literal": "P2",
        }],
        "goal": text, "tasks": [{
            "id": "update", "kind": "write", "instruction": text,
            "depends_on": [], "write_intent": True, "completion_criteria": ["변경"],
        }],
    }
    patch = RequestArchitect().apply(source, out)

    assert "requested_effects" not in patch["request_plan"]
    calls = []
    monkeypatch.setattr(StructuredAgent, "node", lambda _self: (
        lambda state: calls.append(state) or {"semantic_fallback": True}
    ))
    result = WorkArchitect().node()({**source, **patch})
    assert result["semantic_fallback"] is True and len(calls) == 1


@pytest.mark.parametrize("effects", [
    [
        {"target": "ACME-42", "field": "priority", "value": "P1-Critical",
         "literal": "P1"},
        {"target": "ACME-42", "field": "priority", "value": "P2-Major",
         "literal": "P2"},
    ],
    [{"target": "OTHER-9", "field": "priority", "value": "P2-Major",
      "literal": "P2"}],
    [{"target": "ACME-42", "field": "duedate", "value": "2031-02-30",
      "literal": "2031-02-30"}],
])
def test_invalid_or_duplicate_request_effects_are_not_preserved(effects):
    text = "ACME-42 필드를 변경"
    source = {"request_text": text, "messages": [HumanMessage(content=text)]}
    out = {
        "intent": "modify", "keywords": ["ACME-42"], "mentioned_keys": ["ACME-42"],
        "sufficient": True, "request_questions": [], "requested_effects": effects,
        "goal": text, "tasks": [{
            "id": "update", "kind": "write", "instruction": text,
            "depends_on": [], "write_intent": True, "completion_criteria": ["변경"],
        }],
    }

    patch = RequestArchitect().apply(source, out)

    assert "requested_effects" not in patch["request_plan"]
