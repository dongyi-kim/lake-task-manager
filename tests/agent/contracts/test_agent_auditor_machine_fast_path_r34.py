"""Product-neutral regressions for deterministic Auditor machine negatives."""

from __future__ import annotations

import copy

import pytest

from app.agent.workflow.effect_contract import payload_digest
from app.agent.workflow.typed_fast_path import make_typed_check_result

_MACHINE_AUTHORITY = "auditor.machine-check.v1"


def _state(*, revisions: int = 1) -> dict:
    return {
        "request_text": "검증된 레코드 변환 작업을 생성한다",
        "revisions": revisions,
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "검증된 레코드 변환 작업을 생성한다",
            "action": "create",
            "target_keys": [],
            "outcome_ids": [],
            "decisions": [],
        },
        "draft": {
            "mode": "task",
            "items": [
                {
                    "summary": "레코드 변환 작업",
                    "type": "Task",
                    "description": (
                        "<h3>배경</h3><p>변환 요청</p>"
                        "<h3>작업 범위</h3><ul><li>레코드 변환</li></ul>"
                        "<h3>완료 조건 (DoD)</h3><ul><li>변환 결과 검증</li></ul>"
                    ),
                },
                {
                    "summary": "변환 결과 검증 작업",
                    "type": "Task",
                    "description": (
                        "<h3>배경</h3><p>검증 요청</p>"
                        "<h3>작업 범위</h3><ul><li>결과 검증</li></ul>"
                        "<h3>완료 조건 (DoD)</h3><ul><li>검증 기록 확인</li></ul>"
                    ),
                },
            ],
        },
    }


def _machine_result(state: dict, *, complete: bool, ok: bool, errors=(), warnings=(),
                    text: str = "") -> dict:
    return make_typed_check_result(
        authority=_MACHINE_AUTHORITY,
        payload_digest=payload_digest(state),
        complete=complete,
        ok=ok,
        errors=errors,
        warnings=warnings,
        text=text,
    ).as_dict()


def _blocking_machine_result(state: dict) -> dict:
    return _machine_result(
        state,
        complete=True,
        ok=False,
        errors=[{
            "index": 0,
            "field": "priority",
            "expected": "configured value",
            "actual": "unsupported value",
            "message": "허용되지 않은 우선순위 값이다",
        }],
        warnings=[],
        text="- [0] priority: 허용되지 않은 우선순위 값이다",
    )


def _fail_if_semantic_runs(_self):
    def run(_state):
        pytest.fail("authoritative machine blocker must skip the semantic Auditor call")

    return run


def test_authoritative_machine_blocker_skips_semantic_call_and_preserves_budget(monkeypatch):
    from app.agent.workflow import graph
    from app.agent.workflow.agents import auditor

    state = _state(revisions=1)
    monkeypatch.setattr(auditor, "_machine_check", _blocking_machine_result)
    monkeypatch.setattr(auditor.StructuredAgent, "node", _fail_if_semantic_runs)

    out = auditor.Auditor().node()(state)

    assert out["review"]["ok"] is False
    assert out["review"]["errors"] == _blocking_machine_result(state)["errors"]
    assert out["review"]["problems"] == []
    assert out["revisions"] == 1, "machine repair must not consume semantic revision budget"
    metric = out["trace"][-1]["fastPath"]
    assert metric == {
        "contract": "typed-fast-path.v1",
        "id": "auditor.machine_negative.v1",
        "complete": True,
        "authority": _MACHINE_AUTHORITY,
        "savedCalls": 1,
        "missing": [],
    }
    assert graph.route_after_auditor({**state, **out}) == "revise"


def test_repeated_machine_blocker_fails_closed_without_spending_semantic_budget(monkeypatch):
    from app.agent.workflow import graph
    from app.agent.workflow.agents import auditor

    state = _state(revisions=1)
    monkeypatch.setattr(auditor, "_machine_check", _blocking_machine_result)
    monkeypatch.setattr(auditor.StructuredAgent, "node", _fail_if_semantic_runs)
    first = auditor.Auditor().node()(state)
    repaired_state = copy.deepcopy({**state, **first})
    repaired_state["draft"]["repair_attempt"] = {
        "defect_signature": first["review"]["defect_signature"],
        "payload_digest": first["review"]["payload_digest"],
    }

    repeated = auditor.Auditor().node()(repaired_state)

    assert repeated["revisions"] == 1
    assert repeated["review"]["repeated_defect"] is True
    assert graph.route_after_auditor({**repaired_state, **repeated}) == "respond"


@pytest.mark.parametrize(
    "case",
    ["machine-valid", "validation-ambiguous", "missing-blocking-detail"],
    ids=["machine-valid", "validation-ambiguous", "missing-blocking-detail"],
)
def test_non_authoritative_machine_states_keep_semantic_auditor(monkeypatch, case):
    from app.agent.workflow.agents import auditor

    seen = []
    state = _state()
    machine_result = {
        "machine-valid": _machine_result(
            state, complete=True, ok=True, text="통과",
        ),
        "validation-ambiguous": _machine_result(
            state, complete=False, ok=False,
            errors=[{"index": -1, "field": "validation",
                     "message": "검증기를 사용할 수 없다"}],
            text="검증을 수행하지 못했다",
        ),
        "missing-blocking-detail": _machine_result(
            state, complete=True, ok=False,
            text="실패 원인이 구조화되지 않았다",
        ),
    }[case]

    def semantic_node(_self):
        def run(state):
            seen.append(state)
            return {"semantic_audit_ran": True}

        return run

    monkeypatch.setattr(auditor, "_machine_check", lambda _state: dict(machine_result))
    monkeypatch.setattr(auditor.StructuredAgent, "node", semantic_node)

    out = auditor.Auditor().node()(state)

    assert out == {"semantic_audit_ran": True}
    assert len(seen) == 1


def test_machine_valid_semantic_run_reuses_one_payload_sealed_machine_result(monkeypatch):
    from app.agent.workflow.agents import auditor

    machine_calls = []
    semantic_calls = []

    state = _state(revisions=0)

    def machine_check(machine_state):
        machine_calls.append(True)
        return _machine_result(machine_state, complete=True, ok=True, text="통과")

    def invoke(_self, _state, _messages):
        semantic_calls.append(True)
        return {
            "grounded": True,
            "rule_compliant": True,
            "answers_request": True,
            "problems": [],
            "summary": "검증 통과",
        }

    monkeypatch.setattr(auditor, "_machine_check", machine_check)
    monkeypatch.setattr(auditor.Auditor, "invoke_structured", invoke)

    out = auditor.Auditor().node()(state)

    assert out["review"]["ok"] is True
    assert out["revisions"] == 1
    assert len(semantic_calls) == 1
    assert len(machine_calls) == 1, "task/apply must reuse the node's payload-sealed result"
    assert "fastPath" not in out["trace"][-1]


def test_machine_valid_single_item_does_not_auto_approve_opposite_request(monkeypatch):
    from app.agent.workflow.agents import auditor

    state = _state(revisions=0)
    state["request_text"] = "검증된 레코드를 삭제한다"
    state["continuation_contract"]["root_request"] = state["request_text"]
    state["draft"]["items"] = [state["draft"]["items"][0]]
    semantic_calls = []

    monkeypatch.setattr(
        auditor, "_machine_check",
        lambda current: _machine_result(current, complete=True, ok=True, text="통과"),
    )
    monkeypatch.setattr(
        auditor.StructuredAgent,
        "node",
        lambda _self: (lambda current: semantic_calls.append(current)
                       or {"semantic_audit_ran": True}),
    )

    out = auditor.Auditor().node()(state)

    assert out == {"semantic_audit_ran": True}
    assert len(semantic_calls) == 1


def test_machine_blocker_does_not_hide_semantic_evidence_obligation_audit(monkeypatch):
    from app.agent.workflow.agents import auditor

    semantic_calls = []
    monkeypatch.setattr(auditor, "_machine_check", _blocking_machine_result)
    monkeypatch.setattr(
        auditor, "_verified_evidence_obligations",
        lambda _state: [{"kind": "unconfirmed_dependency", "artifact": "typed record"}],
    )
    monkeypatch.setattr(
        auditor.StructuredAgent,
        "node",
        lambda _self: (lambda state: semantic_calls.append(state)
                       or {"semantic_audit_ran": True}),
    )

    out = auditor.Auditor().node()(_state())

    assert out == {"semantic_audit_ran": True}
    assert len(semantic_calls) == 1


def test_validator_exception_keeps_semantic_call_and_returns_fail_closed_review(monkeypatch):
    from app.agent.workflow.agents import auditor

    semantic_calls = []

    def crash(_state):
        raise RuntimeError("validator crash")

    def invoke(_self, _state, _messages):
        semantic_calls.append(True)
        return {
            "grounded": True,
            "rule_compliant": True,
            "answers_request": True,
            "problems": [],
            "summary": "semantic axes passed",
        }

    monkeypatch.setattr(auditor, "_machine_check", crash)
    monkeypatch.setattr(auditor.Auditor, "invoke_structured", invoke)

    out = auditor.Auditor().node()(_state(revisions=0))

    assert len(semantic_calls) == 1
    assert out["review"]["ok"] is False
    assert out["review"]["repair_lane"] == "semantic"
    assert any(row.get("field") == "validation" for row in out["review"]["errors"])
    assert out["revisions"] == 1
    assert out["repair_budget"] == {
        "contract": "typed-repair-budget.v1",
        "semantic": 1,
        "machine": 0,
        "total": 1,
    }


def test_obligation_extractor_exception_cannot_escape_fast_path_gate(monkeypatch):
    from app.agent.workflow.agents import auditor

    semantic_calls = []

    def crash(_state):
        raise RuntimeError("obligation validator crash")

    monkeypatch.setattr(auditor, "_verified_evidence_obligations", crash)
    monkeypatch.setattr(
        auditor.StructuredAgent,
        "node",
        lambda _self: (lambda state: semantic_calls.append(state)
                       or {"semantic_audit_ran": True}),
    )

    out = auditor.Auditor().node()(_state(revisions=0))

    assert out == {"semantic_audit_ran": True}
    assert len(semantic_calls) == 1


def test_alternating_machine_blockers_hit_machine_ceiling_without_semantic_spend(monkeypatch):
    from app.agent.workflow import graph
    from app.agent.workflow.agents import auditor

    state = _state(revisions=0)
    active = {"field": "priority"}

    def machine_result(_state):
        result = _blocking_machine_result(_state)
        field = active["field"]
        result["errors"][0].update({
            "field": field,
            "expected": f"valid {field}",
            "actual": f"invalid {field}",
            "message": f"허용되지 않은 {field} 값이다",
        })
        return result

    monkeypatch.setattr(auditor, "_machine_check", machine_result)
    monkeypatch.setattr(auditor.StructuredAgent, "node", _fail_if_semantic_runs)

    routes = []
    for field in ("priority", "duedate", "priority", "duedate"):
        active["field"] = field
        out = auditor.Auditor().node()(state)
        state = copy.deepcopy({**state, **out})
        routes.append(graph.route_after_auditor(state))
        if routes[-1] == "respond":
            break
        state["draft"]["repair_attempt"] = {
            "defect_signature": out["review"]["defect_signature"],
            "payload_digest": out["review"]["payload_digest"],
        }

    assert routes == ["revise", "respond"]
    assert state["revisions"] == 0
    assert state["repair_budget"] == {
        "contract": "typed-repair-budget.v1",
        "semantic": 0,
        "machine": 2,
        "total": 2,
    }
