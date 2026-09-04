from __future__ import annotations

import json

import pytest
from langchain_core.messages import HumanMessage


@pytest.fixture(autouse=True)
def _approval_isolation():
    from app.agent import approval

    approval.clear()
    yield
    approval.clear()


def _bound_state(monkeypatch):
    from app.agent import approval

    actor = {"id": "jira.stable.actor", "aliases": ("jira.stable.actor",)}
    monkeypatch.setattr(approval, "current_actor_fresh", lambda: actor)
    payload = {"key": "ACME-42", "changes": {"priority": "P2-Major"}}
    token = approval.stage(
        "audit-thread", "update_ticket", payload, actor=actor,
    )
    assert approval.approve(token, "audit-thread")
    binding = approval.change_audit_binding(
        token, correlation_id="corr-audit-execution",
    )
    state = {
        "messages": [HumanMessage(content="승인")],
        "thread_id": "audit-thread",
        "correlation_id": "corr-audit-execution",
        "approval_token": token,
        "change_audit_binding": binding,
        "result": {},
        "trace": [],
    }
    return state, payload, token


def test_action_executor_links_verified_receipt_without_audit_raw_content(
        monkeypatch):
    from app.agent import approval
    from app.agent import tools as tools_module
    from app.agent.maxis.audit import metadata_sha256, validate_change_audit_binding
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.execution_receipt import (
        bind_single_execution_result,
        execution_receipt_audit_proof,
    )

    state, payload, token = _bound_state(monkeypatch)

    class ExactUpdate:
        name = "update_ticket"

        @staticmethod
        def invoke(args):
            ok, why = approval.consume(args["approval_token"], "update_ticket", payload)
            if not ok:
                return {"ok": False, "error": why}
            return bind_single_execution_result(
                {"ok": True, "key": "ACME-42", "updated": ["priority"]},
                action="update_ticket", payload=payload,
            )

    monkeypatch.setitem(tools_module.BY_NAME, "update_ticket", ExactUpdate())
    executed = ActionExecutor().node()(state)
    receipt = executed["execution_receipt"]
    proof = execution_receipt_audit_proof(
        receipt, thread_id="audit-thread", token=token,
    )
    binding = validate_change_audit_binding(executed["change_audit_binding"])

    assert receipt and proof
    assert binding.receipt_status == "verified"
    assert binding.actor_sha256 == metadata_sha256("jira.stable.actor")
    assert binding.approval_payload_sha256 == approval.fingerprint(payload)
    assert binding.capability_sha256 == metadata_sha256(token)
    assert binding.execution_receipt_sha256 == proof["receipt_digest"]
    assert binding.execution_receipt_signature == receipt["signature"]
    encoded = json.dumps(binding.model_dump(mode="json"), ensure_ascii=False)
    for raw in ("jira.stable.actor", "ACME-42", token, "P2-Major"):
        assert raw not in encoded


@pytest.mark.parametrize("mode", ["missing", "tampered"])
def test_action_executor_blocks_missing_or_tampered_change_binding(
        monkeypatch, mode):
    from app.agent import approval
    from app.agent import tools as tools_module
    from app.agent.workflow.agents.action_executor import ActionExecutor

    state, _payload, token = _bound_state(monkeypatch)
    invoked = []

    class MustNotWrite:
        name = "update_ticket"

        @staticmethod
        def invoke(args):
            invoked.append(args)
            raise AssertionError("tampered audit binding reached Jira write")

    monkeypatch.setitem(tools_module.BY_NAME, "update_ticket", MustNotWrite())
    if mode == "missing":
        state.pop("change_audit_binding")
    else:
        changed = dict(state["change_audit_binding"])
        changed["approval_payload_sha256"] = "f" * 64
        state["change_audit_binding"] = changed

    executed = ActionExecutor().node()(state)

    assert invoked == []
    assert executed["execution_receipt"] == {}
    assert executed["change_audit_binding"] == {}
    assert executed["result"]["failed"]
    assert "감사 결속" in executed["result"]["failed"][0]["error"]
    assert approval.peek(token) is None


@pytest.mark.parametrize("rejection", ["pair_mismatch", "loose_comment"])
def test_pre_dispatch_rejection_keeps_proposal_approval_execution_audit_continuity(
        monkeypatch, rejection):
    from app.agent import approval
    from app.agent.maxis import audit, backend
    from app.agent.maxis.audit import validate_change_audit_binding
    from app.agent.workflow.agents.action_executor import ActionExecutor

    actor = {"id": "jira.stable.actor", "aliases": ("jira.stable.actor",)}
    thread_id = f"audit-{rejection}"
    correlation_id = f"corr-{rejection}"
    payload = {"key": "ACME-42", "changes": {"priority": "P2-Major"}}
    if rejection == "pair_mismatch":
        token, peer = approval.stage_pair(
            thread_id, "update_ticket", payload,
            "add_ticket_comment", {"key": "ACME-42", "body": "approved note"},
            actor=actor,
        )
        assert approval.approve(peer, thread_id)
        supplied_comment = ""
    else:
        token = approval.stage(
            thread_id, "update_ticket", payload, actor=actor,
        )
        peer = approval.stage(
            thread_id, "add_ticket_comment",
            {"key": "ACME-42", "body": "unbound note"}, actor=actor,
        )
        assert approval.approve(peer, thread_id)
        supplied_comment = peer
    assert approval.approve(token, thread_id)
    binding = approval.change_audit_binding(
        token, correlation_id=correlation_id,
    )
    state = {
        "messages": [HumanMessage(content="승인")],
        "thread_id": thread_id,
        "correlation_id": correlation_id,
        "runtime_profile": "local",
        "approval_token": token,
        "comment_token": supplied_comment,
        "change_audit_binding": binding,
        "result": {},
        "trace": [],
    }
    events = []
    monkeypatch.setattr(audit, "record_audit_event", events.append)
    backend.record_change_audit(
        state, event="change.proposal", status="started",
        change_binding=binding,
    )
    backend.record_change_audit(
        state, event="change.approved", status="succeeded",
        change_binding=binding,
    )

    executed = ActionExecutor().node()(state)
    terminal = validate_change_audit_binding(executed["change_audit_binding"])
    terminal_state = {**state, **executed}
    backend.record_change_audit(
        terminal_state, event="change.execution", status="failed",
        error_code="pre_dispatch_rejected",
        change_binding=executed["change_audit_binding"],
    )

    assert executed["result"]["failed"]
    assert terminal.receipt_status == "not_issued"
    assert [event.event for event in events] == [
        "change.proposal", "change.approved", "change.execution",
    ]
    assert [event.status for event in events] == ["started", "succeeded", "failed"]
    assert {event.correlation_id for event in events} == {correlation_id}
    assert [event.change_binding.receipt_status for event in events] == [
        "not_applicable", "not_applicable", "not_issued",
    ]
    assert len({
        (
            event.change_binding.actor_sha256,
            event.change_binding.approval_payload_sha256,
            event.change_binding.capability_sha256,
        )
        for event in events
    }) == 1
    assert approval.peek(token) is None


def test_binding_verification_rejection_terminalizes_only_the_existing_valid_binding(
        monkeypatch):
    from app.agent import approval
    from app.agent.maxis.audit import validate_change_audit_binding
    from app.agent.workflow.agents.action_executor import ActionExecutor

    state, _payload, token = _bound_state(monkeypatch)
    original = dict(state["change_audit_binding"])
    monkeypatch.setattr(
        approval, "verify_change_audit_binding",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("reject")),
    )

    executed = ActionExecutor().node()(state)
    terminal = validate_change_audit_binding(executed["change_audit_binding"])

    assert executed["result"]["failed"]
    assert terminal.receipt_status == "not_issued"
    assert terminal.approval_payload_sha256 == original["approval_payload_sha256"]
    assert terminal.capability_sha256 == original["capability_sha256"]
    assert terminal.binding_seal != original["binding_seal"]
    assert approval.peek(token) is None


def test_pre_edit_binding_is_not_terminalized_after_approved_payload_amendment(
        monkeypatch):
    from app.agent import approval
    from app.agent.workflow.agents.action_executor import ActionExecutor

    actor = {"id": "jira.stable.actor", "aliases": ("jira.stable.actor",)}
    thread_id = "audit-stale-edit"
    old_payload = {
        "mode": "task",
        "items": [{"summary": "before edit", "type": "Task"}],
    }
    new_payload = {
        "mode": "task",
        "items": [{"summary": "after edit", "type": "Task"}],
    }
    token = approval.stage(
        thread_id, "create_tickets", old_payload, actor=actor,
    )
    stale_binding = approval.change_audit_binding(
        token, correlation_id="corr-stale-edit",
    )
    amended, why = approval.amend_payload(token, thread_id, new_payload)
    assert amended, why
    assert approval.approve(token, thread_id)
    calls = []
    monkeypatch.setattr(
        ActionExecutor, "_dispatch",
        lambda *_args: calls.append("write") or (_ for _ in ()).throw(
            AssertionError("stale binding reached dispatch")
        ),
    )
    state = {
        "messages": [HumanMessage(content="승인")],
        "thread_id": thread_id,
        "correlation_id": "corr-stale-edit",
        "approval_token": token,
        "change_audit_binding": stale_binding,
        "result": {},
        "trace": [],
    }

    executed = ActionExecutor().node()(state)

    assert calls == []
    assert executed["result"]["failed"]
    assert executed["change_audit_binding"] == {}
    assert approval.peek(token) is None


def test_session_resume_records_terminal_execution_audit_for_pre_dispatch_rejection(
        monkeypatch):
    from types import SimpleNamespace

    from app.agent import approval
    from app.agent.maxis import audit, backend
    from app.agent.workflow import session
    from app.agent.workflow.agents.action_executor import ActionExecutor

    actor = {"id": "jira.stable.actor", "aliases": ("jira.stable.actor",)}
    thread_id = "session-pre-dispatch-audit"
    correlation_id = "corr-session-pre-dispatch"
    payload = {"key": "ACME-42", "changes": {"priority": "P2-Major"}}
    token = approval.stage(
        thread_id, "update_ticket", payload, actor=actor,
    )
    loose_comment = approval.stage(
        thread_id, "add_ticket_comment",
        {"key": "ACME-42", "body": "unbound note"}, actor=actor,
    )
    proposal_binding = approval.change_audit_binding(
        token, correlation_id=correlation_id,
    )

    class FakeGraph:
        def __init__(self):
            self.state = {
                "messages": [HumanMessage(content="변경 요청")],
                "thread_id": thread_id,
                "correlation_id": correlation_id,
                "runtime_profile": "local",
                "approval_token": token,
                "comment_token": loose_comment,
                "change_audit_binding": proposal_binding,
                "result": {},
                "trace": [],
            }

        def get_state(self, _config):
            return SimpleNamespace(
                values=dict(self.state), next=(),
                config={"configurable": {"checkpoint_id": "checkpoint-1"}},
            )

        def update_state(self, _config, patch):
            self.state.update(patch)

        def invoke(self, _input, _config):
            self.state.update(ActionExecutor().node()(dict(self.state)))
            return dict(self.state)

    graph = FakeGraph()
    events = []
    monkeypatch.setattr(session, "get_graph", lambda: graph)
    monkeypatch.setattr(backend, "verify_thread_runtime", lambda _state: None)
    monkeypatch.setattr(approval, "current_actor_fresh", lambda: actor)
    monkeypatch.setattr(audit, "record_audit_event", events.append)
    from app.agent import impact
    monkeypatch.setattr(impact, "note_review", lambda **_kwargs: None)
    backend.record_change_audit(
        graph.state, event="change.proposal", status="started",
        change_binding=proposal_binding,
    )

    result = session.resume(thread_id, token)

    assert result["result"]["failed"]
    assert [event.event for event in events] == [
        "change.proposal", "change.approved", "change.execution",
    ]
    assert events[-1].status == "failed"
    assert events[-1].error_code == "execution_failed"
    assert events[-1].change_binding.receipt_status == "not_issued"
    assert {event.correlation_id for event in events} == {correlation_id}
