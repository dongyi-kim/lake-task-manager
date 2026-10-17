"""Validated MAXIS blocks reach the public response without another model call."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from app.agent.maxis.backend import MaxisBlockedResult
from app.agent.maxis.recovery import blocked_recovery
from app.agent.workflow.agents.base import TextAgent
from app.agent.workflow.agents.request_architect import RequestArchitect
from app.agent.workflow.agents.result_integrator import ResultIntegrator
from app.agent.workflow.session import _shape
from app.agent.workflow.state import AgentState


def _state():
    return {
        "thread_id": "blocked-thread", "turn_attempt_id": "blocked-turn",
        "correlation_id": "blocked-correlation", "request_text": "합성 질문",
        "messages": [HumanMessage(content="합성 질문")],
    }


def _blocked(role="request_architect"):
    return MaxisBlockedResult(role, blocked_recovery(
        warnings=("contract_guard:payload_type_mismatch", "private-remote-body"),
        evidence_flags=(),
    ))


def test_blocked_fallback_graph_and_public_shape_keep_guidance_without_model(monkeypatch):
    def no_model(*_args, **_kwargs):
        raise AssertionError("validated blocked result must not call another model")

    monkeypatch.setattr(TextAgent, "_run", no_model)
    graph = StateGraph(AgentState)
    graph.add_node("blocked", lambda state: RequestArchitect().fallback(state, _blocked()))
    graph.add_node("respond", ResultIntegrator().node())
    graph.add_edge(START, "blocked")
    graph.add_edge("blocked", "respond")
    graph.add_edge("respond", END)

    result = graph.compile().invoke(_state())
    shaped = _shape("blocked-thread", result)
    expected = _blocked().recovery
    assert shaped["reply"] == expected.message + " " + expected.next_action
    assert shaped["error"] == "[request_architect] " + shaped["reply"]
    assert shaped["ok"] is False
    assert not shaped["questions"] and not shaped.get("pending")
    assert not result.get("draft") and not result.get("approval_token")
    assert result["messages"][-1].content == shaped["reply"]
    assert "private-remote-body" not in json.dumps(shaped, ensure_ascii=False)
    assert "signature" not in json.dumps(shaped)


def test_result_integrator_own_block_returns_guidance_from_actual_fallback(monkeypatch):
    from app.agent.maxis import backend

    monkeypatch.setattr(ResultIntegrator, "system", lambda *_: "synthetic")
    monkeypatch.setattr(ResultIntegrator, "task", lambda *_: "synthetic")
    monkeypatch.setattr(backend, "resolve_role", lambda *_: backend.RoleResolution(route="maxis"))
    calls = []

    def blocked(role, *_args, **_kwargs):
        calls.append(role)
        raise _blocked(role)

    monkeypatch.setattr(backend, "invoke_role", blocked)
    state = _state()
    patch = ResultIntegrator().node()(state)
    shaped = _shape(state["thread_id"], {**state, **patch})
    assert calls == ["result_integrator"]
    assert shaped["reply"] == str(_blocked("result_integrator"))
    assert shaped["error"] == "[result_integrator] " + shaped["reply"]
    assert not shaped["ok"] and not shaped["questions"] and not shaped.get("pending")


@pytest.mark.parametrize("field,value", [
    ("code", "stale_evidence"), ("signature", "0" * 64),
    ("signature", "한" * 64), ("binding", "0" * 64),
    ("role", "result_integrator"), ("body", "private-remote-body"),
])
def test_mutated_marker_cannot_create_terminal_branch(monkeypatch, field, value):
    state = _state()
    state.update(RequestArchitect().fallback(state, _blocked()))
    state["runtime_role_sidecars"]["request_architect"]["blocked_terminal"][field] = value
    calls = []
    monkeypatch.setattr(TextAgent, "_run", lambda *_: calls.append(True) or {"reply": "normal path"})

    assert ResultIntegrator().node()(state) == {"reply": "normal path"}
    assert calls == [True]


def test_arbitrary_error_text_cannot_create_terminal_branch(monkeypatch):
    state = {**_state(), "error": "[request_architect] " + str(_blocked())}
    calls = []
    monkeypatch.setattr(TextAgent, "_run", lambda *_: calls.append(True) or {"reply": "normal path"})
    assert ResultIntegrator().node()(state) == {"reply": "normal path"}
    assert calls == [True]


def test_exception_prose_is_never_used_as_terminal_content(monkeypatch):
    from app.agent.maxis.recovery import BlockedRecovery

    error = MaxisBlockedResult("request_architect", BlockedRecovery(
        "invalid_result_contract", "private-remote-body", "private-model-reasoning"))
    state = _state()
    state.update(RequestArchitect().fallback(state, error))
    monkeypatch.setattr(TextAgent, "_run", lambda *_: pytest.fail("unexpected model"))
    patch = ResultIntegrator().node()(state)
    assert patch["reply"] == str(_blocked())
    assert "private-" not in json.dumps({**state, **patch}, default=str)


def test_new_turn_rejects_retained_marker_even_when_correlation_is_reused(monkeypatch):
    from app.agent.workflow.session import _turn_start_patch

    state = _state()
    state.update(RequestArchitect().fallback(state, _blocked()))
    prior_marker = deepcopy(state["runtime_role_sidecars"])
    patch = _turn_start_patch("새 질문", state)
    assert patch["turn_attempt_id"] != state["turn_attempt_id"]
    assert patch["error"] == ""
    next_state = {**state, **patch, "correlation_id": state["correlation_id"],
                  "runtime_role_sidecars": prior_marker}
    calls = []
    monkeypatch.setattr(TextAgent, "_run", lambda *_: calls.append(True) or {"reply": "new turn"})
    assert ResultIntegrator().node()(next_state) == {"reply": "new turn"}
    assert calls == [True]


def test_legacy_result_does_not_trigger_model_after_validated_block(monkeypatch):
    state = {**_state(), "result": {"created": [{"key": "ACME-42"}]},
             "execution_receipt": {"signature": "forged"}}
    state.update(RequestArchitect().fallback(state, _blocked()))
    monkeypatch.setattr(TextAgent, "_run", lambda *_: pytest.fail("unexpected model"))
    patch = ResultIntegrator().node()(state)
    assert patch["reply"] == str(_blocked())
    assert "result" not in patch and "execution_receipt" not in patch


def test_blocked_terminal_clears_pending_controls_and_ends_actual_graph(monkeypatch):
    from app.agent.workflow.graph import route_after_result_integrator
    from app.agent.workflow.state import Node

    state = {**_state(), "approval_token": "old-primary", "comment_token": "old-comment",
             "draft": {"items": [{"summary": "old draft"}]},
             "change_plan": {"key": "ACME-42", "changes": {"priority": "P2-Major"}},
             "questions": [{"question": "old question", "kind": "text"}],
             "runtime_role_questions": {"auditor": [{"question": "old question"}]}}
    state.update(RequestArchitect().fallback(state, _blocked()))
    monkeypatch.setattr(TextAgent, "_run", lambda *_: pytest.fail("unexpected model"))
    graph = StateGraph(AgentState)
    graph.add_node("respond", ResultIntegrator().node())
    graph.add_node("execute", lambda *_: pytest.fail("blocked result must end without execution"))
    graph.add_edge(START, "respond")
    graph.add_conditional_edges("respond", route_after_result_integrator,
                               {"end": END, "execute": "execute"})
    graph.add_edge("execute", END)

    result = graph.compile().invoke(state)
    shaped = _shape(state["thread_id"], result, SimpleNamespace(next=(Node.ACTION_EXECUTOR,)))
    assert shaped["reply"] == str(_blocked())
    assert not shaped["questions"] and not shaped.get("pending")
    for key in ("approval_token", "comment_token", "draft", "change_plan", "runtime_role_questions"):
        assert not result[key]


def test_signed_approved_execution_receipt_keeps_priority_over_block(monkeypatch):
    from app.agent import approval, tools as tools_module
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.execution_receipt import bind_single_execution_result
    from app.agent.workflow.graph import build

    approval.clear()
    try:
        payload = {"key": "ACME-42", "changes": {"priority": "P2-Major"}}
        state = _state()
        actor = {"id": "synthetic-actor", "aliases": ("synthetic-actor",)}
        monkeypatch.setattr(approval, "current_actor_fresh", lambda: actor)
        token = approval.stage(state["thread_id"], "update_ticket", payload, actor=actor)
        assert approval.approve(token, state["thread_id"])

        class ExactUpdate:
            name = "update_ticket"

            @staticmethod
            def invoke(args):
                ok, why = approval.consume(args["approval_token"], "update_ticket", payload)
                assert ok, why
                return bind_single_execution_result(
                    {"ok": True, "key": payload["key"], "updated": ["priority"]},
                    action="update_ticket", payload=payload)

        monkeypatch.setitem(tools_module.BY_NAME, "update_ticket", ExactUpdate())
        state["approval_token"] = token
        state["change_audit_binding"] = approval.change_audit_binding(
            token, correlation_id=state["correlation_id"])
        state.update(ActionExecutor().node()(state))
        assert state["execution_receipt"], state["result"]
        state.update(RequestArchitect().fallback(state, _blocked()))
        monkeypatch.setattr(TextAgent, "_run", lambda *_: pytest.fail("unexpected model"))

        patch = ResultIntegrator().node()(state)
        shaped = _shape(state["thread_id"], {**state, **patch})
        assert "{{ticket-inline:ACME-42}}" in patch["reply"]
        assert _blocked().recovery.message not in patch["reply"]
        assert shaped["result"]["updated"]
        assert shaped["error"] == state["error"]
        assert not shaped.get("pending")
        assert "execution_receipt" not in patch
        graph_result = build().invoke(state)
        assert graph_result["reply"] == patch["reply"]
        assert graph_result["result"] == state["result"]
        assert graph_result["execution_receipt"] == state["execution_receipt"]
    finally:
        approval.clear()


@pytest.mark.parametrize("blocked_role,parallel,intent,peer_fails", [
    ("request_architect", True, "plan_work", False),
    ("query_specialist", True, "plan_work", False),
    ("research_analyst", True, "plan_work", False),
    ("research_analyst", True, "ask", False),
    ("work_architect", True, "plan_work", False),
    ("people_advisor", False, "plan_work", False),
    ("auditor", False, "plan_work", False),
    ("people_advisor", True, "plan_work", False),
    ("auditor", True, "plan_work", False),
    ("people_advisor", True, "plan_work", True),
    ("auditor", True, "plan_work", True),
    ("knowledge_curator", True, "ask", False),
    ("portfolio_analyst", True, "progress", False),
])
def test_production_graph_stops_after_block_before_acquisition_or_proposal(
        monkeypatch, blocked_role, parallel, intent, peer_fails):
    from app.agent import approval
    from app.agent.workflow import graph as workflow_graph
    from app.agent.workflow.state import Node

    calls = []
    draft = {"mode": "task", "items": [{"summary": "synthetic draft", "type": "Task"}]}

    def node(agent):
        def run(state):
            calls.append(agent.name)
            if agent.name == blocked_role:
                return agent.fallback(state, _blocked(agent.name))
            if peer_fails and agent.name in {"auditor", "people_advisor"}:
                return agent.fallback(state, RuntimeError("synthetic peer failure"))
            if agent.name == "request_architect":
                return {"intent": intent}
            if agent.name == "work_architect":
                return {"draft": draft}
            if agent.name == "auditor":
                return {"review": {"ok": True}}
            return {}
        return run

    for cls in (workflow_graph.RequestArchitect, workflow_graph.QuerySpecialist,
                workflow_graph.QueryRunner, workflow_graph.ResearchAnalyst,
                workflow_graph.WorkArchitect, workflow_graph.PeopleAdvisor,
                workflow_graph.Auditor, workflow_graph.KnowledgeCurator,
                workflow_graph.PortfolioAnalyst):
        monkeypatch.setattr(cls, "node", node)
    monkeypatch.setattr(workflow_graph, "_parallel_role_calls_allowed", lambda: parallel)
    monkeypatch.setattr(TextAgent, "_run", lambda *_: pytest.fail("unexpected final model"))
    monkeypatch.setattr(workflow_graph, "merge_assignments", lambda *_: pytest.fail("join must not acquire"))
    monkeypatch.setattr(approval, "stage", lambda *_a, **_k: pytest.fail("blocked result must not stage"))
    monkeypatch.setattr(approval, "stage_pair", lambda *_a, **_k: pytest.fail("blocked result must not stage"))
    monkeypatch.setattr(workflow_graph.ActionExecutor, "node", lambda *_: (
        lambda *_: pytest.fail("blocked result must not execute")))
    state = {**_state(), "messages": [HumanMessage(content="합성 개념 정리")],
             "draft": draft, "review": {"ok": True}}

    result = workflow_graph.build().invoke(state)
    shaped = _shape(state["thread_id"], result, SimpleNamespace(next=(Node.ACTION_EXECUTOR,)))

    assert shaped["reply"] == str(_blocked(blocked_role))
    assert not shaped["ok"] and not shaped["questions"] and not shaped.get("pending")
    after_block = calls[calls.index(blocked_role) + 1:]
    # The other fan-out Role may already be running in the same superstep.
    allowed_peer = ({"auditor", "people_advisor"} - {blocked_role}) if (
        parallel and blocked_role in {"auditor", "people_advisor"}) else set()
    assert set(after_block) <= allowed_peer, calls
    assert not result.get("approval_token") and not result.get("draft")
    assert "synthetic peer failure" not in shaped["reply"] + shaped["error"]


def test_authenticated_block_stops_direct_merge_and_proposal_boundaries(monkeypatch):
    from app.agent import approval
    from app.agent.workflow import graph as workflow_graph

    state = {**_state(), "draft": {"items": [{"summary": "stale draft"}]},
             "review": {"ok": True}, "approval_token": "old", "comment_token": "old"}
    state.update(RequestArchitect().fallback(state, _blocked()))
    state["error"] = ""  # The authenticated marker, not error prose, stops new work.
    monkeypatch.setattr(workflow_graph, "merge_assignments", lambda *_: pytest.fail("unexpected lookup"))
    monkeypatch.setattr(workflow_graph, "final_authority_review", lambda *_a, **_k: pytest.fail("unexpected review"))
    monkeypatch.setattr(approval, "stage", lambda *_a, **_k: pytest.fail("unexpected stage"))
    monkeypatch.setattr(approval, "current_actor_fresh", lambda: pytest.fail("unexpected actor lookup"))

    assert workflow_graph._merge_assignments(state) == {}
    assert workflow_graph._propose(state) == {"approval_token": "", "comment_token": ""}
