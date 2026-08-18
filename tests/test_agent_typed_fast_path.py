from __future__ import annotations

import pytest

from app.agent.workflow.typed_fast_path import (
    TYPED_FAST_PATH_CONTRACT,
    evaluate_typed_fast_path,
    typed_fast_path_note,
)


def test_complete_typed_fast_path_records_authority_and_saved_calls():
    decision = evaluate_typed_fast_path(
        "portfolio.progress",
        authority="portfolio_snapshot.v1",
        checks={"requested_targets": True, "typed_rows": True, "source_complete": True},
        saved_calls=1,
    )

    assert decision.complete is True
    assert decision.missing == ()
    assert decision.saved_calls == 1
    assert decision.as_dict() == {
        "contract": TYPED_FAST_PATH_CONTRACT,
        "id": "portfolio.progress",
        "complete": True,
        "authority": "portfolio_snapshot.v1",
        "savedCalls": 1,
        "missing": [],
    }


def test_incomplete_typed_fast_path_is_fail_closed_and_cannot_claim_savings():
    decision = evaluate_typed_fast_path(
        "portfolio.progress",
        authority="portfolio_snapshot.v1",
        checks={"typed_rows": True, "source_complete": False, "requested_targets": False},
        saved_calls=2,
    )

    assert decision.complete is False
    assert decision.missing == ("requested_targets", "source_complete")
    assert decision.saved_calls == 0
    assert decision.as_dict()["savedCalls"] == 0


def test_typed_fast_path_trace_keeps_existing_trace_shape_with_typed_sidecar():
    decision = evaluate_typed_fast_path(
        "result.execution",
        authority="action_result.v1",
        checks={"result_shape": True},
    )

    trace = typed_fast_path_note({}, "result_integrator", "실행 결과 결정적 렌더링", decision)

    assert trace[0]["node"] == "result_integrator"
    assert trace[0]["note"] == "실행 결과 결정적 렌더링"
    assert trace[0]["fastPath"] == decision.as_dict()


@pytest.mark.parametrize(
    ("path_id", "authority", "checks", "saved_calls"),
    [
        ("", "typed.v1", {"shape": True}, 1),
        ("path", "", {"shape": True}, 1),
        ("path", "typed.v1", {}, 1),
        ("path", "typed.v1", {"": True}, 1),
        ("path", "typed.v1", {"shape": True}, 0),
    ],
)
def test_typed_fast_path_contract_rejects_unmeasurable_declarations(
    path_id, authority, checks, saved_calls,
):
    with pytest.raises(ValueError):
        evaluate_typed_fast_path(
            path_id,
            authority=authority,
            checks=checks,
            saved_calls=saved_calls,
        )
