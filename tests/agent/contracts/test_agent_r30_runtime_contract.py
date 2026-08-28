"""Generic runtime contracts for typed parent resolution and scalar write effects."""

from __future__ import annotations

import copy
import itertools

from langchain_core.messages import HumanMessage


def _typed_parent_state(*, candidates: list[dict] | None = None) -> dict:
    from app.agent.workflow.anchors import (
        bind_single_outcome_contract,
        requested_outcome_contract,
        seal_work_item_identities,
    )

    request = "Acme vector cache migration Task를 구성해줘"
    task = {
        "id": "migration",
        "kind": "ticket",
        "write_intent": True,
        "instruction": request,
    }
    state = {
        "thread_id": "r30-parent",
        "intent": "plan_work",
        "request_text": request,
        "messages": [HumanMessage(content="typed control envelope")],
        "turn_continuation": True,
        "situation": "관련 구현 이력 조회 완료",
        "request_plan": {"goal": request, "tasks": [task]},
        "request_refinement": {
            "parent": "select_existing",
            "phase": "1차",
            "duedate": "2026-10-08",
        },
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": request,
            "intent": "plan_work",
            "action": "create",
            "target_keys": [],
            "outcome_ids": ["migration"],
            "decisions": [{
                "field": "parent",
                "value": "select_existing",
                "source": "explicit_refinement",
            }],
        },
        "materialized_ticket_sources": {
            "parentCandidateSearchAttempted": True,
            "parentCandidateKeys": [str(row.get("key") or "") for row in candidates or []],
            "ticketDetails": copy.deepcopy(candidates or []),
        },
    }
    contract = requested_outcome_contract(state)
    prior = {
        "mode": "task",
        "structure": "single_task",
        "outcome_contract_id": contract["id"],
        "items": [{
            "summary": "Acme vector cache migration 구현",
            "type": "Task",
            "issue_type": "Task",
            "tier": "task",
            "description": (
                "<h3>배경</h3><p>Acme vector cache migration 요청</p>"
                "<h3>작업 범위</h3><ul><li>migration 구현</li>"
                "<li>제외: 요청 외 변경</li></ul>"
                "<h3>완료 조건 (DoD)</h3><ul><li>migration 검증 결과 기록</li></ul>"
            ),
        }],
    }
    assert bind_single_outcome_contract(state, prior)
    seal_work_item_identities(state, prior)
    state["draft"] = prior
    return state


def test_typed_parent_decision_refreshes_stale_zero_hit_without_reparsing_text():
    from app.agent.workflow import graph

    states = [
        _typed_parent_state(candidates=[]),
        _typed_parent_state(candidates=[{
            "key": "ACME-90", "type": "Epic",
            "summary": "Acme vector cache migration program",
        }]),
    ]
    # The human-visible message deliberately contains no parent language. The typed
    # continuation decision is the sole authority. Both a historical zero-hit marker and
    # a historical candidate set predate this new delegation, so each gets one bounded refresh.
    for state in states:
        assert graph._needs_delegated_parent_retrieval(state) is True
        assert graph.route_after_request_architect(state) == "investigate"


def test_untyped_parent_words_do_not_override_a_typed_non_parent_continuation():
    from app.agent.workflow import graph

    state = _typed_parent_state(candidates=[{
        "key": "ACME-999", "type": "Epic",
        "summary": "Unrelated billing settlement program",
    }])
    state["request_refinement"] = {"phase": "1차", "duedate": "2026-10-08"}
    state["continuation_contract"]["decisions"] = []
    state["messages"] = [HumanMessage(content="기존 Epic은 네가 골라줘")]

    assert graph._needs_delegated_parent_retrieval(state) is False
    assert graph.route_after_request_architect(state) == "refine"


def test_parent_refinement_without_typed_authority_cannot_select_a_candidate():
    """A stale/legacy overlay alone is not authority to attach a verified Jira parent."""
    from app.agent.workflow.agents.work_architect import WorkArchitect

    state = _typed_parent_state(candidates=[{
        "key": "ACME-100", "type": "Epic",
        "summary": "Acme vector cache migration program",
    }])
    state.pop("continuation_contract")

    result = WorkArchitect().node()(state)

    assert result["questions"] == []
    assert result["draft"]["items"][0].get("epic") in (None, "")
    assert result["draft"].get("resolved_slots") in (None, [])


def test_work_applies_verified_compatible_parent_as_resolved_slot_and_effect():
    from app.agent.workflow.agents.work_architect import WorkArchitect

    state = _typed_parent_state(candidates=[{
        "key": "ACME-100",
        "type": "Epic",
        "summary": "Acme vector cache migration program",
    }])
    result = WorkArchitect().node()(state)
    draft = result["draft"]
    item = draft["items"][0]

    assert result["questions"] == []
    assert item["epic"] == "ACME-100"
    assert item["parent_source"] == "resolved_slot"
    slot = draft["resolved_slots"][0]
    assert slot == {
        "contract": "resolved-slot.v1",
        "field": "parent",
        "outcome_id": item["outcome_refs"][0],
        "item_id": item["item_id"],
        "request": "select_existing",
        "required": True,
        "status": "resolved",
        "value": "ACME-100",
        "resolution": "verified_candidate",
        "provenance": "materialized_parent_candidates",
        "evidence": ["ACME-100"],
        "decision_digest": slot["decision_digest"],
    }
    assert len(slot["decision_digest"]) == 64
    effects = {
        (row["target"], row["field"], row["value"])
        for row in draft["requested_effects"]["effects"]
    }
    assert (item["item_id"], "parent", "ACME-100") in effects
    assert (item["item_id"], "duedate", "2026-10-08") in effects


def test_required_parent_zero_hit_fails_closed_instead_of_silent_top_level():
    from app.agent.workflow import graph
    from app.agent.workflow.agents.work_architect import WorkArchitect

    state = _typed_parent_state(candidates=[])
    result = WorkArchitect().node()(state)

    assert result["draft"]["items"] == []
    assert result["draft"]["resolved_slots"][0]["status"] == "unresolved"
    assert result["questions"][0]["ownership"] == "user_required"
    assert graph.route_after_work_architect({**state, **result}) == "respond"
    assert graph._propose({**state, **result})["approval_token"] == ""


def test_optional_parent_zero_hit_has_explicit_top_level_provenance():
    from app.agent.workflow.resolved_slots import (
        ParentSelectionAuthority,
        resolve_parent_slot,
    )

    authority = ParentSelectionAuthority(
        decision_digest="a" * 64,
        outcome_ids=("optional",),
        required=False,
    )
    slot = resolve_parent_slot(
        authority,
        outcome_id="optional",
        item_id="work-item:optional",
        selected=None,
    )

    assert slot.status == "resolved"
    assert slot.value == ""
    assert slot.resolution == "top_level"
    assert slot.provenance == "explicit_safe_fallback"


def _update_state(text: str) -> dict:
    return {
        "thread_id": "r30-update",
        "intent": "modify",
        "request_text": text,
        "messages": [HumanMessage(content=text)],
        "request_plan": {"goal": text, "tasks": [{
            "id": "update",
            "kind": "write",
            "write_intent": True,
            "instruction": text,
        }]},
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": text,
            "intent": "modify",
            "action": "update",
            "target_keys": ["ACME-42"],
            "outcome_ids": ["update"],
            "decisions": [],
        },
    }


def test_scalar_requested_effects_materialize_without_repair_for_clause_permutations(
        monkeypatch):
    from app.agent.workflow.agents.work_architect import WorkArchitect
    from app.agent.workflow import effect_contract

    original_decode = effect_contract._literal_requested_values
    decode_calls: list[str] = []

    def counted_decode(text: str):
        decode_calls.append(text)
        return original_decode(text)

    monkeypatch.setattr(effect_contract, "_literal_requested_values", counted_decode)

    clauses = (
        "우선순위를 P1로 변경",
        "마감은 2026-10-03으로 변경",
        '제목은 "Acme cache rollover"로 변경',
    )
    expected = {
        "priority": "P1-Critical",
        "duedate": "2026-10-03",
        "summary": "Acme cache rollover",
    }
    for ordered in itertools.permutations(clauses):
        text = "ACME-42 " + " 그리고 ".join(ordered)
        before = len(decode_calls)
        result = WorkArchitect().apply(_update_state(text), {
            "questions": [],
            # Simulate projection loss: only one of three immutable leaves survived.
            "change": {"key": "ACME-42", "priority": "P1-Critical"},
            "rationale": "요청 필드 변경",
        })
        plan = result["change_plan"]

        assert len(decode_calls) == before + 1, ordered
        assert plan["changes"] == expected, ordered
        assert "repair_attempt" not in plan
        assert {
            (row["target"], row["field"], row["value"])
            for row in plan["requested_effects"]["effects"]
        } == {
            ("ACME-42", field, value) for field, value in expected.items()
        }


def test_multi_target_distinct_scalar_values_fail_closed_without_typed_mapping():
    from app.agent.workflow import graph
    from app.agent.workflow.effect_contract import (
        materialize_requested_update_effects,
        validate_requested_effect_contract,
    )

    text = (
        "ACME-41 마감은 2026-10-03으로, "
        "ACME-42 마감은 2026-10-10으로 변경"
    )
    state = _update_state(text)
    state["continuation_contract"]["target_keys"] = ["ACME-41", "ACME-42"]
    state["request_plan"]["tasks"][0]["instruction"] = text
    plan = materialize_requested_update_effects(state, {
        "keys": ["ACME-41", "ACME-42"],
        "changes": {"duedate": "2026-10-03"},
    })
    checked = {**state, "change_plan": plan}

    assert "requested_effects" not in plan
    assert plan["requested_effects_error"]["kind"] == "per_target_mapping_required"
    errors = validate_requested_effect_contract(checked)
    assert errors and errors[0]["field"] == "requested_effects"
    assert errors[0]["actual"] == "per_target_mapping_required"
    proposed = graph._propose({**checked, "review": {"ok": True}})
    assert proposed["approval_token"] == ""
    assert proposed["review"]["ok"] is False

    corrected = _update_state("ACME-41 마감은 2026-10-03으로 변경")
    corrected["continuation_contract"]["target_keys"] = ["ACME-41"]
    repaired = materialize_requested_update_effects(corrected, plan)
    assert "requested_effects_error" not in repaired
    assert repaired["key"] == "ACME-41" and "keys" not in repaired


def test_finding_signature_set_detects_a_persistent_subset_by_authority():
    from app.agent.workflow.effect_contract import (
        defect_signature_set,
        recurrent_finding_signature_keys,
    )

    persistent = {
        "item_id": "work-item:a", "field_path": "draft.items[a].parent",
        "expected": "ACME-100", "actual": "", "authority": "requested-effects.v1",
    }
    repaired = {
        "item_id": "work-item:b", "field_path": "draft.items[b].duedate",
        "expected": "2026-10-03", "actual": "", "authority": "requested-effects.v1",
    }
    prior = defect_signature_set([persistent, repaired])

    recurrent = recurrent_finding_signature_keys([persistent], prior)
    assert len(recurrent) == 1
    assert recurrent == recurrent_finding_signature_keys([persistent, {
        **persistent, "authority": "requested-outcome.v1",
    }], prior)
