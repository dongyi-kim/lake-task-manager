from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent.workflow.typed_fast_path import (
    TYPED_FAST_PATH_EVENT_CONTRACT,
    TYPED_FAST_PATH_CONTRACT,
    advance_typed_repair_budget,
    evaluate_typed_fast_path,
    make_typed_check_result,
    parse_typed_fast_path_event,
    parse_typed_check_result,
    typed_fast_path_registry,
    typed_fast_path_note,
    typed_repair_budget,
    typed_repair_retry_allowed,
    zero_typed_repair_budget,
)


PORTFOLIO_CHECKS = {
    "typed_material": True,
    "all_material_complete": True,
    "legacy_material_absent": True,
    "requested_targets_complete": True,
    "non_jql_request": True,
}


def test_registered_fast_path_owns_authority_checks_and_saved_calls():
    decision = evaluate_typed_fast_path(
        "portfolio.intermediate.v1", checks=PORTFOLIO_CHECKS,
    )

    assert decision.complete is True
    assert decision.missing == ()
    assert decision.saved_calls == 1
    assert decision.as_dict() == {
        "contract": TYPED_FAST_PATH_CONTRACT,
        "id": "portfolio.intermediate.v1",
        "complete": True,
        "authority": "portfolio_analyst.raw_tool_snapshot",
        "savedCalls": 1,
        "missing": [],
    }


def test_incomplete_registered_fast_path_cannot_claim_savings():
    decision = evaluate_typed_fast_path(
        "portfolio.intermediate.v1",
        checks={**PORTFOLIO_CHECKS, "requested_targets_complete": False,
                "all_material_complete": False},
    )

    assert decision.complete is False
    assert decision.missing == ("all_material_complete", "requested_targets_complete")
    assert decision.saved_calls == 0


def test_typed_fast_path_trace_keeps_existing_shape_with_registered_sidecar():
    decision = evaluate_typed_fast_path(
        "result.structure_tree.v1",
        checks={"tree": True, "stage_authority": True,
                "tree_seal": True, "render_safe": True},
    )

    trace = typed_fast_path_note({}, "result_integrator", "deterministic render", decision)

    assert trace[0]["node"] == "result_integrator"
    assert trace[0]["note"] == "deterministic render"
    assert trace[0]["fastPath"] == decision.as_dict()


def test_typed_fast_path_event_contract_is_registry_owned_and_pii_free():
    registry = typed_fast_path_registry()
    spec = registry["result.structure_tree.v1"]
    event = parse_typed_fast_path_event({
        "contract": TYPED_FAST_PATH_EVENT_CONTRACT,
        "phase": "evaluated",
        "pathId": "result.structure_tree.v1",
        "authority": spec["authority"],
        "eligible": True,
        "estimatedSavedCalls": spec["savedCalls"],
    })

    assert event is not None
    assert event.as_dict() == {
        "contract": "typed-fast-path-event.v1",
        "phase": "evaluated",
        "pathId": "result.structure_tree.v1",
        "authority": "work_architect.structure_stage",
        "eligible": True,
        "estimatedSavedCalls": 1,
    }
    for forged in (
        {**event.as_dict(), "pathId": "unknown.path"},
        {**event.as_dict(), "authority": "caller-minted"},
        {**event.as_dict(), "estimatedSavedCalls": 8},
        {**event.as_dict(), "prompt": "private prompt"},
        {**event.as_dict(), "eligible": 1},
        {**event.as_dict(), "phase": "committed", "eligible": False,
         "estimatedSavedCalls": 0},
    ):
        assert parse_typed_fast_path_event(forged) is None


@pytest.mark.parametrize(
    ("path_id", "checks"),
    [
        ("unknown.path", {"shape": True}),
        ("portfolio.intermediate.v1", {k: v for k, v in PORTFOLIO_CHECKS.items()
                                        if k != "typed_material"}),
        ("portfolio.intermediate.v1", {**PORTFOLIO_CHECKS, "caller_minted": True}),
        ("portfolio.intermediate.v1", {**PORTFOLIO_CHECKS, "typed_material": 1}),
    ],
)
def test_registry_rejects_unknown_path_or_non_exact_check_contract(path_id, checks):
    with pytest.raises(ValueError):
        evaluate_typed_fast_path(path_id, checks=checks)


def test_typed_machine_result_requires_registered_authority_and_sha256_digest():
    valid = make_typed_check_result(
        authority="auditor.machine-check.v1",
        payload_digest="a" * 64,
        complete=True,
        ok=False,
        errors=[{"field": "priority", "message": "unsupported value"}],
    )

    assert parse_typed_check_result(
        valid.as_dict(), authority="auditor.machine-check.v1", payload_digest="a" * 64,
    ) == valid
    assert parse_typed_check_result(
        valid.as_dict(), authority="auditor.machine-check.v1", payload_digest="b" * 64,
    ) is None

    with pytest.raises(ValidationError):
        make_typed_check_result(
            authority="caller.minted", payload_digest="a" * 64,
            complete=True, ok=True,
        )
    for malformed_digest in ("x", "A" * 64, "a" * 63):
        with pytest.raises(ValidationError):
            make_typed_check_result(
                authority="auditor.machine-check.v1", payload_digest=malformed_digest,
                complete=True, ok=True,
            )


def test_typed_machine_result_rejects_unknown_finding_shape_and_inconsistent_ok():
    with pytest.raises(ValidationError):
        make_typed_check_result(
            authority="auditor.machine-check.v1", payload_digest="a" * 64,
            complete=True, ok=False,
            errors=[{"field": "priority", "message": "bad", "untyped": "value"}],
        )
    with pytest.raises(ValidationError):
        make_typed_check_result(
            authority="auditor.machine-check.v1", payload_digest="a" * 64,
            complete=True, ok=True,
            errors=[{"field": "priority", "message": "bad"}],
        )


def test_repair_budget_projects_legacy_semantic_count_and_advances_independent_lane():
    projected = typed_repair_budget({"revisions": 1})
    assert projected is not None
    assert projected.as_dict() == {
        "contract": "typed-repair-budget.v1", "semantic": 1, "machine": 0, "total": 1,
    }

    advanced = advance_typed_repair_budget({"revisions": 1}, "machine")
    assert advanced is not None
    assert advanced.as_dict() == {
        "contract": "typed-repair-budget.v1", "semantic": 1, "machine": 1, "total": 2,
    }


def test_repair_budget_fails_closed_on_malformed_or_stale_sidecar():
    malformed = {
        "revisions": 0,
        "repair_budget": {
            "contract": "typed-repair-budget.v1", "semantic": 1, "machine": 0, "total": 1,
        },
    }
    assert typed_repair_budget(malformed) is None
    assert advance_typed_repair_budget(malformed, "machine") is None
    assert typed_repair_retry_allowed(
        malformed, "machine", semantic_limit=2, machine_limit=2, total_limit=4,
    ) is False


def test_repair_budget_enforces_lane_and_total_ceilings():
    state = {
        "revisions": 1,
        "repair_budget": {
            "contract": "typed-repair-budget.v1", "semantic": 1, "machine": 1, "total": 2,
        },
    }
    assert typed_repair_retry_allowed(
        state, "machine", semantic_limit=2, machine_limit=2, total_limit=4,
    ) is True
    assert typed_repair_retry_allowed(
        state, "machine", semantic_limit=2, machine_limit=1, total_limit=4,
    ) is False
    assert typed_repair_retry_allowed(
        state, "semantic", semantic_limit=1, machine_limit=2, total_limit=4,
    ) is False
    assert typed_repair_retry_allowed(
        state, "machine", semantic_limit=2, machine_limit=2, total_limit=2,
    ) is False


@pytest.mark.parametrize("continuation", [False, True])
def test_every_user_turn_resets_repair_sidecar_even_for_continuation(monkeypatch, continuation):
    from app.agent.workflow import session

    prior = {
        "revisions": 1,
        "repair_budget": {
            "contract": "typed-repair-budget.v1", "semantic": 1, "machine": 1, "total": 2,
        },
    }
    monkeypatch.setattr(session, "_is_interview_continuation",
                        lambda _text, _prior: continuation)

    patch = session._turn_start_patch("next user turn", prior)

    assert patch["repair_budget"] == zero_typed_repair_budget()


def test_initial_turn_state_explicitly_resets_stale_checkpoint_budget(monkeypatch):
    from app.agent import tools
    from app.agent.tools import people_tools
    from app.agent.workflow import session

    monkeypatch.setattr(tools, "set_thread", lambda _thread_id: None)
    monkeypatch.setattr(people_tools, "set_person_context",
                        lambda _thread_id, _keys: None)
    monkeypatch.setattr(session, "_identity", lambda: "")

    initial = session._initial("thread-1", "new request", "member", "user-1")

    assert initial["revisions"] == 0
    assert initial["repair_budget"] == zero_typed_repair_budget()
