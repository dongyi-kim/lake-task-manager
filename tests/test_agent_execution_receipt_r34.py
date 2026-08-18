"""Exact ActionExecutor -> ResultIntegrator execution-receipt boundary."""

from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.messages import HumanMessage


@pytest.fixture(autouse=True)
def _approval_isolation():
    from app.agent import approval

    approval.clear()
    yield
    approval.clear()


def _state(token: str, result: dict, *, thread_id: str = "receipt-thread") -> dict:
    return {
        "messages": [HumanMessage(content="승인한 변경을 실행해")],
        "request_text": "승인한 변경을 실행해",
        "thread_id": thread_id,
        "approval_token": token,
        "result": result,
        "trace": [],
    }


def _metric(output: dict) -> dict:
    rows = [row["fastPath"] for row in (output.get("trace") or [])
            if isinstance(row, dict) and isinstance(row.get("fastPath"), dict)]
    assert len(rows) == 1
    return rows[0]


def _approved(action: str, payload: dict, *, thread_id: str = "receipt-thread"):
    from app.agent import approval

    token = approval.stage(thread_id, action, payload)
    assert approval.approve(token, thread_id)
    record = approval.peek(token)
    assert record
    return token, record


def _consume_attestation(token: str, record: dict) -> dict:
    from app.agent import approval

    nonce, context_token = approval.begin_consumption_attempt(token)
    try:
        assert approval.consume(token, record["action"], record["payload"])[0]
    finally:
        approval.end_consumption_attempt(context_token)
    attestation = approval.take_consumption(
        token, attempt_nonce=nonce, thread_id=record["thread"],
        action=record["action"], payload=record["payload"],
    )
    assert attestation
    return attestation


def test_exact_update_receipt_skips_one_reachable_result_llm(monkeypatch):
    from app.agent import approval
    from app.agent import tools as tools_module
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    payload = {"key": "ACME-42", "changes": {"priority": "P2-Major"}}
    token, _record = _approved("update_ticket", payload)

    class ExactUpdate:
        name = "update_ticket"

        @staticmethod
        def invoke(args):
            from app.agent.workflow.execution_receipt import bind_single_execution_result
            ok, why = approval.consume(args["approval_token"], "update_ticket", payload)
            return (bind_single_execution_result(
                {"ok": True, "key": payload["key"], "updated": ["priority"]},
                action="update_ticket", payload=payload,
            )
                    if ok else {"ok": False, "error": why})

    monkeypatch.setitem(tools_module.BY_NAME, "update_ticket", ExactUpdate())
    executed = ActionExecutor().node()(_state(token, {}))
    receipt = executed.get("execution_receipt")
    assert receipt and receipt["action"] == "update_ticket"
    assert receipt["payload_digest"] == _record["fp"]
    assert receipt["cardinality"] == 1

    monkeypatch.setattr(TextAgent, "_run", lambda *_args, **_kwargs: (
        _ for _ in ()).throw(AssertionError("exact receipt must skip Result LLM")))
    # Match a checkpointer/serializer superstep: tuples and frozen models become plain JSON lists
    # and mappings before ResultIntegrator consumes the sidecar.
    wire = json.loads(json.dumps(executed, ensure_ascii=False))
    output = ResultIntegrator()._run({
        **_state(token, wire["result"]),
        "execution_receipt": wire["execution_receipt"],
    })

    assert "{{ticket-inline:ACME-42}}" in output["reply"]
    assert _metric(output) == {
        "contract": "typed-fast-path.v1",
        "id": "result.execution_receipt.v1",
        "complete": True,
        "authority": "action-executor.approved-dispatch.v1",
        "savedCalls": 1,
        "missing": [],
    }


def test_create_parent_child_receipt_preserves_both_source_index_namespaces(monkeypatch):
    from app.agent import approval
    from app.agent import tools as tools_module
    from app.agent.workflow.agents.action_executor import ActionExecutor

    payload = {
        "mode": "task",
        "items": [
            {"summary": "parent zero", "type": "Task"},
            {"summary": "parent one", "type": "Task"},
        ],
        "children": [
            {"summary": "child zero", "parent_index": 1},
            {"summary": "child one", "parent_index": 0},
        ],
    }
    token, _record = _approved("create_tickets", payload)

    class ExactTreeCreate:
        name = "create_tickets"

        @staticmethod
        def invoke(args):
            from app.agent.workflow.execution_receipt import bind_execution_rows
            exact = {key: value for key, value in args.items() if key != "approval_token"}
            ok, why = approval.consume(args["approval_token"], "create_tickets", exact)
            if not ok:
                return {"ok": False, "error": why}
            parents = bind_execution_rows([
                {"index": 0, "key": "ACME-100", "summary": "parent zero"},
                {"index": 1, "key": "ACME-101", "summary": "parent one"},
            ], action="create_tickets", items=payload["items"], scope="item")
            children = bind_execution_rows([
                {"index": 0, "key": "ACME-102", "summary": "child zero"},
                {"index": 1, "key": "ACME-103", "summary": "child one"},
            ], action="create_tickets", items=payload["children"], scope="child")
            return {"ok": True, "created": parents + children, "failed": []}

    monkeypatch.setitem(tools_module.BY_NAME, "create_tickets", ExactTreeCreate())
    executed = ActionExecutor().node()(_state(token, {}))
    result = executed["result"]
    receipt = executed["execution_receipt"]

    assert [(row["scope"], row["index"], row.get("parent_index"))
            for row in receipt["expected"]] == [
        ("item", 0, None), ("item", 1, None),
        ("child", 0, 1), ("child", 1, 0),
    ]
    assert [(row["scope"], row["index"], row["key"])
            for row in receipt["outcomes"]] == [
        ("item", 0, "ACME-100"), ("item", 1, "ACME-101"),
        ("child", 0, "ACME-102"), ("child", 1, "ACME-103"),
    ]
    assert all("target_id" not in row and "effect_digest" not in row
               for row in result["created"]), "internal receipt ids must not enter public result"


@pytest.mark.parametrize("result", [
    # Missing one expected target.
    {"created": [], "updated": [{"index": 0, "key": "ACME-1", "fields": ["priority"]}],
     "failed": [], "note": ""},
    # Duplicate result for index zero.
    {"created": [], "updated": [
        {"index": 0, "key": "ACME-1", "fields": ["priority"]},
        {"index": 0, "key": "ACME-1", "fields": ["priority"]},
    ], "failed": [], "note": ""},
    # Unknown result index.
    {"created": [], "updated": [
        {"index": 0, "key": "ACME-1", "fields": ["priority"]},
        {"index": 7, "key": "ACME-2", "fields": ["priority"]},
    ], "failed": [], "note": ""},
    # One target cannot be both successful and failed.
    {"created": [],
     "updated": [{"index": 0, "key": "ACME-1", "fields": ["priority"]}],
     "failed": [{"index": 0, "summary": "ACME-1", "error": "provider failure"}],
     "note": ""},
    # An incomplete/error-shaped top-level payload is not a complete result.
    {"created": [], "updated": [
        {"index": 0, "key": "ACME-1", "fields": ["priority"]},
        {"index": 1, "key": "ACME-2", "fields": ["priority"]},
    ], "failed": [], "note": "", "incomplete": True, "error": "later page missing"},
])
def test_partial_duplicate_unknown_or_conflicting_outcomes_cannot_mint_receipt(result):
    from app.agent.workflow.execution_receipt import (
        bind_execution_rows, execution_raw_complete,
    )

    payload = {"items": [
        {"key": "ACME-1", "changes": {"priority": "P2-Major"}},
        {"key": "ACME-2", "changes": {"priority": "P2-Major"}},
    ]}
    raw = {
        "ok": not bool(result.get("failed")),
        "updated": bind_execution_rows(
            result.get("updated"), action="update_tickets", items=payload["items"],
        ),
        "failed": bind_execution_rows(
            result.get("failed"), action="update_tickets", items=payload["items"],
        ),
    }
    for key in ("incomplete", "error"):
        if key in result:
            raw[key] = result[key]
    assert execution_raw_complete("update_tickets", payload, raw) is False


def test_stale_or_mutated_receipt_falls_back_to_semantic_result(monkeypatch):
    from app.agent import approval
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator
    from app.agent.workflow.execution_receipt import (
        bind_single_execution_result, issue_execution_receipt, scrub_execution_sidecars,
    )

    payload = {"key": "ACME-9", "changes": {"duedate": "2031-10-04"}}
    token, record = _approved("update_ticket", payload)
    raw = bind_single_execution_result(
        {"ok": True, "key": "ACME-9", "updated": ["duedate"]},
        action="update_ticket", payload=payload,
    )
    result = {"created": [], "updated": [{
        "index": 0, "key": "ACME-9", "fields": ["duedate"],
        "target_id": raw["target_id"], "effect_digest": raw["effect_digest"],
    }], "failed": [], "note": ""}
    attestation = _consume_attestation(token, record)
    receipt = issue_execution_receipt(
        record=record, token=token, result=result, raw=raw,
        consumption_attestation=attestation,
    )
    assert receipt
    public_result = scrub_execution_sidecars(result)
    semantic_calls = []
    monkeypatch.setattr(TextAgent, "_run", lambda _self, _state: (
        semantic_calls.append(True) or {"fallback": True, "trace": []}))

    tampered = copy.deepcopy(receipt)
    tampered["outcomes"][0]["key"] = "ACME-999"
    for state in (
        {**_state("different-current-token", copy.deepcopy(public_result)),
         "execution_receipt": copy.deepcopy(receipt)},
        {**_state(token, copy.deepcopy(public_result), thread_id="different-thread"),
         "execution_receipt": copy.deepcopy(receipt)},
        {**_state(token, copy.deepcopy(public_result)), "execution_receipt": tampered},
    ):
        output = ResultIntegrator()._run(state)
        assert output["fallback"] is True
        assert _metric(output)["savedCalls"] == 0
    assert len(semantic_calls) == 3

    monkeypatch.setattr(TextAgent, "_run", lambda *_args, **_kwargs: (
        _ for _ in ()).throw(AssertionError("legacy note is not receipt authority")))
    baseline = ResultIntegrator()._run({
        **_state(token, public_result), "execution_receipt": receipt,
    })["reply"]
    mutated = ResultIntegrator()._run({
        **_state(token, {**public_result, "note": "legacy mutation {{mention:unsafe}}"}),
        "execution_receipt": receipt,
    })["reply"]
    assert mutated == baseline


def test_external_literals_cannot_mint_markup_or_badges_in_deterministic_reply(monkeypatch):
    from app.agent import approval
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator
    from app.agent.workflow.execution_receipt import (
        bind_execution_rows, issue_execution_receipt, scrub_execution_sidecars,
    )

    injected = ("### injected\n```html\n<img src=x> {{mention:unsafe}} "
                "{{ticket-inline:FAKE-9}} |x| [~unsafe] https://example.test \u202e9-EKAF")
    payload = {"items": [
        {"key": "ACME-7", "changes": {"summary": injected}},
        {"key": "ACME-8", "changes": {"summary": "normal"}},
    ]}
    token, record = _approved("update_tickets", payload)
    updated = bind_execution_rows(
        [{"index": 0, "key": "ACME-7", "fields": ["summary"]}],
        action="update_tickets", items=payload["items"],
    )
    failed = bind_execution_rows(
        [{"index": 1, "summary": "ACME-8", "error": injected}],
        action="update_tickets", items=payload["items"],
    )
    raw = {"ok": False, "updated": updated, "failed": failed}
    result = {"created": [], "updated": updated, "failed": failed, "note": injected}
    attestation = _consume_attestation(token, record)
    receipt = issue_execution_receipt(
        record=record, token=token, result=result, raw=raw,
        consumption_attestation=attestation,
    )
    assert receipt
    public_result = scrub_execution_sidecars(result)
    monkeypatch.setattr(TextAgent, "_run", lambda *_args, **_kwargs: (
        _ for _ in ()).throw(AssertionError("complete mixed receipt must skip Result LLM")))

    output = ResultIntegrator()._run({
        **_state(token, public_result), "execution_receipt": receipt,
    })
    reply = output["reply"]

    assert reply.startswith("### 실행 결과") and reply.count("###") == 1
    assert "```" not in reply and "<img" not in reply and "\u202e" not in reply
    assert "{{mention:unsafe}}" not in reply and "{{ticket-inline:FAKE-9}}" not in reply
    assert "{{ticket-inline:ACME-7}}" in reply
    assert "{{ticket-inline:ACME-8}}" in reply
    assert "｛｛mention:unsafe｝｝" in reply and "｜x｜" in reply


def test_invalid_external_ticket_key_or_failure_error_is_not_receiptable():
    from app.agent.workflow.execution_receipt import (
        bind_execution_rows, execution_raw_complete,
    )

    payload = {"summary": "one epic"}
    invalid_results = [
        {"ok": True, "created": [{"index": 0, "key": "### {{mention:unsafe}}",
                                    "summary": "one epic"}], "failed": []},
        {"ok": False, "created": [],
         "failed": [{"index": 0, "summary": "one epic", "error": ""}]},
        {"ok": False, "created": [],
         "failed": [{"index": 0, "summary": "one epic", "error": {"code": 500}}]},
    ]

    for raw in invalid_results:
        raw["created"] = bind_execution_rows(
            raw.get("created"), action="create_epic", items=[payload],
        )
        raw["failed"] = bind_execution_rows(
            raw.get("failed"), action="create_epic", items=[payload],
        )
        assert execution_raw_complete("create_epic", payload, raw) is False


def test_write_boundary_preserves_original_parent_child_ids_without_sending_them(monkeypatch):
    from app.agent import approval
    from app.agent.tools import write_tools
    from app.agent.workflow.execution_receipt import (
        execution_raw_complete, issue_execution_receipt,
    )
    from app.domain import bulk

    calls = []

    class Client:
        desc_field_value = staticmethod(lambda value: value)

        @staticmethod
        def bulk_lookup():
            return object()

        @staticmethod
        def bulk_create(mode, rows, desc_to_field=None):
            calls.append((mode, copy.deepcopy(rows)))
            assert all("target_id" not in row and "effect_digest" not in row for row in rows)
            if mode == "task":
                return {
                    "ok": False,
                    "created": [{"index": 1, "key": "ACME-202", "summary": "parent one"}],
                    "failed": [{"index": 0, "summary": "parent zero", "error": "denied"}],
                }
            return {"ok": True, "created": [
                {"index": 0, "key": "ACME-203", "summary": "bound child"},
            ], "failed": []}

    payload = {
        "mode": "task",
        "items": [{"summary": "parent zero"}, {"summary": "parent one"}],
        "children": [
            {"summary": "orphan child", "parent_index": 0},
            {"summary": "bound child", "parent_index": 1},
        ],
    }
    real_consume = approval.consume
    monkeypatch.setattr(write_tools, "client", lambda: Client())
    monkeypatch.setattr(write_tools.approval, "consume", lambda *args: (True, ""))
    monkeypatch.setattr(bulk, "validate_bulk", lambda *args: {"ok": True, "errors": []})

    raw = write_tools.create_tickets.invoke({**payload, "approval_token": "approved"})

    assert calls[1][0] == "subtask" and len(calls[1][1]) == 1
    assert {row["target_id"] for row in raw["created"] + raw["failed"]} == {
        "primary:create_tickets:item:0", "primary:create_tickets:item:1",
        "primary:create_tickets:child:0", "primary:create_tickets:child:1",
    }
    assert execution_raw_complete("create_tickets", payload, raw) is True
    monkeypatch.setattr(write_tools.approval, "consume", real_consume)
    token, record = _approved("create_tickets", payload, thread_id="parent-child-order")
    attestation = _consume_attestation(token, record)
    receipt = issue_execution_receipt(
        record=record, token=token,
        result={"created": raw["created"], "updated": [], "failed": raw["failed"],
                "note": ""},
        raw=raw,
        consumption_attestation=attestation,
    )
    assert [(row["target_id"], row["status"]) for row in receipt["outcomes"]] == [
        ("primary:create_tickets:item:0", "failure"),
        ("primary:create_tickets:item:1", "success"),
        ("primary:create_tickets:child:0", "failure"),
        ("primary:create_tickets:child:1", "success"),
    ]


def test_child_success_without_its_parent_success_is_not_a_complete_receipt():
    from app.agent.workflow.execution_receipt import bind_execution_rows, execution_raw_complete

    payload = {
        "mode": "task", "items": [{"summary": "parent"}],
        "children": [{"summary": "child", "parent_index": 0}],
    }
    raw = {
        "ok": False,
        "created": bind_execution_rows(
            [{"index": 0, "key": "ACME-22", "summary": "child"}],
            action="create_tickets", items=payload["children"], scope="child",
        ),
        "failed": bind_execution_rows(
            [{"index": 0, "summary": "parent", "error": "parent failed"}],
            action="create_tickets", items=payload["items"], scope="item",
        ),
    }
    assert execution_raw_complete("create_tickets", payload, raw) is False


def test_wrong_but_valid_raw_update_is_not_projected_as_approved_success(monkeypatch):
    from app.agent import approval
    from app.agent import tools as tools_module
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.execution_receipt import bind_single_execution_result

    payload = {"key": "ACME-1", "changes": {"priority": "P2-Major"}}
    token, _record = _approved("update_ticket", payload)

    class ContradictoryUpdate:
        name = "update_ticket"

        @staticmethod
        def invoke(args):
            ok, why = approval.consume(args["approval_token"], "update_ticket", payload)
            if not ok:
                return {"ok": False, "error": why}
            return bind_single_execution_result(
                {"ok": True, "key": "OTHER-9", "updated": ["summary"]},
                action="update_ticket", payload=payload,
            )

    monkeypatch.setitem(tools_module.BY_NAME, "update_ticket", ContradictoryUpdate())
    output = ActionExecutor().node()(_state(token, {}))

    assert output["execution_receipt"] == {}
    assert output["result"]["updated"] == [] and output["result"]["failed"]
    assert "확인" in output["result"]["failed"][0]["error"]
    assert approval.peek(token) is None


def test_success_shape_without_capability_consumption_is_revoked_and_not_projected(monkeypatch):
    from app.agent import approval
    from app.agent import tools as tools_module
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.execution_receipt import bind_single_execution_result

    payload = {"key": "ACME-2", "changes": {"priority": "P2-Major"}}
    token, _record = _approved("update_ticket", payload)

    class NonConsumingUpdate:
        name = "update_ticket"

        @staticmethod
        def invoke(_args):
            return bind_single_execution_result(
                {"ok": True, "key": "ACME-2", "updated": ["priority"]},
                action="update_ticket", payload=payload,
            )

    monkeypatch.setitem(tools_module.BY_NAME, "update_ticket", NonConsumingUpdate())
    output = ActionExecutor().node()(_state(token, {}))

    assert approval.peek(token) is None, "unconsumed capability must not remain replayable"
    assert output["execution_receipt"] == {} and output["result"]["updated"] == []
    assert "소비되지 않아" in output["result"]["failed"][0]["error"]


def test_compound_secondary_success_shape_without_consumption_is_not_projected(monkeypatch):
    from app.agent import approval
    from app.agent.workflow.agents.action_executor import ActionExecutor

    primary_payload = {"key": "ACME-2", "changes": {"priority": "P2-Major"}}
    secondary_payload = {"key": "ACME-2", "body": "decision"}
    primary, secondary = approval.stage_pair(
        "compound-consume", "update_ticket", primary_payload,
        "add_ticket_comment", secondary_payload,
    )
    assert approval.approve(primary, "compound-consume")
    assert approval.approve(secondary, "compound-consume")

    def dispatch(_self, action, payload, token):
        if action == "update_ticket":
            assert approval.consume(token, action, payload)[0]
            return ({"created": [], "updated": [{"key": "ACME-2", "fields": ["priority"]}],
                     "failed": [], "note": ""}, "primary")
        return ({"created": [], "updated": [{"key": "ACME-2", "fields": ["comment"]}],
                 "failed": [], "note": ""}, "secondary-without-consume")

    monkeypatch.setattr(ActionExecutor, "_dispatch", dispatch)
    output = ActionExecutor().node()({
        "thread_id": "compound-consume", "approval_token": primary,
        "comment_token": secondary, "trace": [],
    })
    assert approval.peek(primary) is None and approval.peek(secondary) is None
    assert output["result"]["updated"] == [{"key": "ACME-2", "fields": ["priority"]}]
    assert output["result"]["failed"] and "소비되지 않아" in output["result"]["failed"][0]["error"]


@pytest.mark.parametrize("rows", [
    [{"index": False, "key": "ACME-1", "fields": ["priority"],
      "target_id": "primary:update_tickets:item:0", "effect_digest": "a" * 64}],
    {"index": 0, "key": "ACME-1", "fields": ["priority"]},
    [{"index": 0, "key": "ACME-1", "fields": ["priority"]},
     {"index": 1, "key": "ACME-999", "fields": ["priority"]}],
])
def test_bool_index_non_list_or_extra_provider_rows_fail_closed(rows):
    from app.agent.workflow.execution_receipt import bind_execution_rows, execution_raw_complete

    payload = {"items": [{"key": "ACME-1", "changes": {"priority": "P2-Major"}}]}
    raw = {"ok": True, "updated": bind_execution_rows(
        rows, action="update_tickets", items=payload["items"],
    ), "failed": []}
    assert execution_raw_complete("update_tickets", payload, raw) is False


def test_single_update_duplicate_applied_field_is_not_exact_coverage():
    from app.agent.workflow.execution_receipt import (
        bind_single_execution_result, execution_raw_complete,
    )

    payload = {"key": "ACME-1", "changes": {"priority": "P2-Major"}}
    raw = bind_single_execution_result(
        {"ok": True, "key": "ACME-1", "updated": ["priority", "priority"]},
        action="update_ticket", payload=payload,
    )
    assert execution_raw_complete("update_ticket", payload, raw) is False


def test_bulk_update_wrong_valid_key_and_duplicate_created_key_fail_before_projection():
    from app.agent.workflow.execution_receipt import bind_execution_rows, execution_raw_complete

    update_payload = {"items": [
        {"key": "ACME-1", "changes": {"priority": "P2-Major"}},
    ]}
    wrong_key = {"ok": True, "updated": bind_execution_rows(
        [{"index": 0, "key": "OTHER-9", "fields": ["priority"]}],
        action="update_tickets", items=update_payload["items"],
    ), "failed": []}
    assert execution_raw_complete("update_tickets", update_payload, wrong_key) is False

    create_payload = {"mode": "task", "items": [
        {"summary": "first"}, {"summary": "second"},
    ]}
    duplicate_key = {"ok": True, "created": bind_execution_rows([
        {"index": 0, "key": "ACME-7", "summary": "first"},
        {"index": 1, "key": "ACME-7", "summary": "second"},
    ], action="create_tickets", items=create_payload["items"]), "failed": []}
    assert execution_raw_complete("create_tickets", create_payload, duplicate_key) is False


def test_tool_exception_discards_capability_and_keeps_semantic_result_reachable(monkeypatch):
    from app.agent import approval
    from app.agent import tools as tools_module
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    payload = {"key": "ACME-5", "changes": {"priority": "P2-Major"}}
    token, _record = _approved("update_ticket", payload)

    class ExplodingUpdate:
        name = "update_ticket"

        @staticmethod
        def invoke(_args):
            raise RuntimeError("provider interrupted")

    monkeypatch.setitem(tools_module.BY_NAME, "update_ticket", ExplodingUpdate())
    executed = ActionExecutor().node()(_state(token, {}))
    assert approval.peek(token) is None and executed["execution_receipt"] == {}
    assert "확인" in executed["result"]["failed"][0]["error"]
    calls = []
    monkeypatch.setattr(TextAgent, "_run", lambda _self, _state: (
        calls.append(True) or {"fallback": True, "trace": []}))
    output = ResultIntegrator()._run({**_state(token, executed["result"]),
                                      "execution_receipt": {}})
    assert output["fallback"] is True and len(calls) == 1
    assert _metric(output)["savedCalls"] == 0


def test_result_with_live_approval_but_no_receipt_calls_semantic_once(monkeypatch):
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    calls = []
    monkeypatch.setattr(TextAgent, "_run", lambda _self, _state: (
        calls.append(True) or {"fallback": True, "trace": []}))
    output = ResultIntegrator()._run(_state("still-in-state", {
        "created": [{"key": "ACME-3", "summary": "done"}],
        "updated": [], "failed": [], "note": "",
    }))
    assert output["fallback"] is True and len(calls) == 1
    assert _metric(output)["savedCalls"] == 0


def test_receipt_validator_exception_falls_back_to_exactly_one_semantic_call(monkeypatch):
    from app.agent.workflow.agents import result_integrator as result_module
    from app.agent.workflow.agents.base import TextAgent

    calls = []
    monkeypatch.setattr(result_module, "parse_execution_receipt", lambda *_args, **_kwargs: (
        _ for _ in ()).throw(RuntimeError("validator unavailable")))
    monkeypatch.setattr(TextAgent, "_run", lambda _self, _state: (
        calls.append(True) or {"fallback": True, "trace": []}))
    output = result_module.ResultIntegrator()._run({
        **_state("receipt-token", {"created": [], "updated": [],
                                    "failed": [{"error": "unknown"}], "note": ""}),
        "execution_receipt": {"contract": "execution-receipt.v1"},
    })
    assert output["fallback"] is True and len(calls) == 1
    assert _metric(output)["savedCalls"] == 0


def test_session_shape_never_exposes_receipt_or_internal_target_identity():
    from app.agent.workflow.session import _shape

    state = {
        "execution_receipt": {"signature": "secret", "payload_digest": "a" * 64},
        "result": {"created": [{
            "index": 0, "key": "ACME-1", "summary": "done",
            "target_id": "primary:create_epic:item:0", "effect_digest": "b" * 64,
        }], "updated": [], "failed": [], "note": ""},
    }
    public = _shape("privacy-thread", state, snap=False)
    encoded = repr(public)
    assert "execution_receipt" not in public
    assert "target_id" not in encoded and "effect_digest" not in encoded


def test_consumption_attestation_is_attempt_bound_exact_and_one_use():
    from app.agent import approval
    from app.agent.workflow.execution_receipt import (
        bind_single_execution_result, issue_execution_receipt,
    )

    payload = {"key": "ACME-61", "changes": {"priority": "P2-Major"}}
    token, record = _approved("update_ticket", payload, thread_id="attempt-bound")
    exact_nonce, context_token = approval.begin_consumption_attempt(token)
    try:
        assert approval.consume(token, "update_ticket", payload)[0]
    finally:
        approval.end_consumption_attempt(context_token)

    foreign_nonce, foreign_context = approval.begin_consumption_attempt(token)
    approval.end_consumption_attempt(foreign_context)
    assert approval.take_consumption(
        token, attempt_nonce=foreign_nonce, thread_id="attempt-bound",
        action="update_ticket", payload=payload,
    ) is None
    attestation = approval.take_consumption(
        token, attempt_nonce=exact_nonce, thread_id="attempt-bound",
        action="update_ticket", payload=payload,
    )
    assert attestation

    raw = bind_single_execution_result(
        {"ok": True, "key": "ACME-61", "updated": ["priority"]},
        action="update_ticket", payload=payload,
    )
    result = {"created": [], "updated": [{
        "index": 0, "key": "ACME-61", "fields": ["priority"],
        "target_id": raw["target_id"], "effect_digest": raw["effect_digest"],
    }], "failed": [], "note": ""}
    first = issue_execution_receipt(
        record=record, token=token, result=result, raw=raw,
        consumption_attestation=attestation,
    )
    assert first
    assert issue_execution_receipt(
        record=record, token=token, result=result, raw=raw,
        consumption_attestation=attestation,
    ) is None


def test_concurrent_loser_cannot_erase_winning_dispatch_attestation(monkeypatch):
    from app.agent import approval
    from app.agent import tools as tools_module
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.execution_receipt import bind_single_execution_result

    payload = {"key": "ACME-611", "changes": {"priority": "P2-Major"}}
    token, _record = _approved("update_ticket", payload, thread_id="attempt-race")
    both_inside = threading.Barrier(2)
    loser_returned = threading.Event()
    write_count = 0
    count_lock = threading.Lock()

    class RacingUpdate:
        name = "update_ticket"

        @staticmethod
        def invoke(args):
            nonlocal write_count
            both_inside.wait(timeout=5)
            ok, _why = approval.consume(args["approval_token"], "update_ticket", payload)
            if ok:
                with count_lock:
                    write_count += 1
                assert loser_returned.wait(timeout=5)
            else:
                loser_returned.set()
            return bind_single_execution_result(
                {"ok": True, "key": "ACME-611", "updated": ["priority"]},
                action="update_ticket", payload=payload,
            )

    monkeypatch.setitem(tools_module.BY_NAME, "update_ticket", RacingUpdate())
    state = _state(token, {}, thread_id="attempt-race")
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(lambda _index: ActionExecutor().node()(state), range(2)))

    assert write_count == 1
    assert sum(bool(output["execution_receipt"]) for output in outputs) == 1
    assert sum(bool(output["result"]["updated"]) for output in outputs) == 1
    assert sum(bool(output["result"]["failed"]) for output in outputs) == 1
    assert approval.peek(token) is None


def test_compound_double_click_preserves_winner_secondary_capability(monkeypatch):
    from app.agent import approval
    from app.agent.workflow.agents.action_executor import ActionExecutor

    primary_payload = {"key": "ACME-611", "changes": {"priority": "P2-Major"}}
    comment_payload = {"key": "ACME-611", "body": "approved comment"}
    primary, secondary = approval.stage_pair(
        "compound-race", "update_ticket", primary_payload,
        "add_ticket_comment", comment_payload,
    )
    assert approval.approve(primary, "compound-race")
    assert approval.approve(secondary, "compound-race")
    both_inside = threading.Barrier(2)
    loser_returned = threading.Event()
    writes = {"primary": 0, "comment": 0}
    lock = threading.Lock()

    def dispatch(_self, action, payload, token):
        if action == "update_ticket":
            both_inside.wait(timeout=5)
            ok, _why = approval.consume(token, action, payload)
            if ok:
                with lock:
                    writes["primary"] += 1
                assert loser_returned.wait(timeout=5)
            else:
                loser_returned.set()
            return ({"created": [], "updated": [{
                "key": "ACME-611", "fields": ["priority"],
            }], "failed": [], "note": ""}, "primary")
        ok, _why = approval.consume(token, action, payload)
        if ok:
            with lock:
                writes["comment"] += 1
        return ({"created": [], "updated": [{
            "key": "ACME-611", "fields": ["comment"],
        }], "failed": [], "note": ""}, "comment")

    monkeypatch.setattr(ActionExecutor, "_dispatch", dispatch)
    state = {
        "thread_id": "compound-race", "approval_token": primary,
        "comment_token": secondary, "trace": [],
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(lambda _index: ActionExecutor().node()(state), range(2)))

    complete = [output for output in outputs
                if output["result"]["updated"] and not output["result"]["failed"]]
    assert writes == {"primary": 1, "comment": 1}
    assert len(complete) == 1
    assert set(complete[0]["result"]["updated"][0]["fields"]) == {"priority", "comment"}
    assert sum(bool(output["result"]["failed"]) for output in outputs) == 1
    assert approval.peek(primary) is None and approval.peek(secondary) is None


def test_compound_consumed_primary_exception_owns_and_cancels_secondary(monkeypatch):
    from app.agent import approval
    from app.agent.workflow.agents.action_executor import ActionExecutor

    primary_payload = {"key": "ACME-613", "changes": {"priority": "P2-Major"}}
    comment_payload = {"key": "ACME-613", "body": "must not post"}
    primary, secondary = approval.stage_pair(
        "compound-exception", "update_ticket", primary_payload,
        "add_ticket_comment", comment_payload,
    )
    assert approval.approve(primary, "compound-exception")
    assert approval.approve(secondary, "compound-exception")
    secondary_calls = []

    def dispatch(_self, action, payload, token):
        if action == "update_ticket":
            assert approval.consume(token, action, payload)[0]
            raise RuntimeError("provider failed after write")
        secondary_calls.append(True)
        raise AssertionError("secondary must not run after an uncertain primary write")

    monkeypatch.setattr(ActionExecutor, "_dispatch", dispatch)
    output = ActionExecutor().node()({
        "thread_id": "compound-exception", "approval_token": primary,
        "comment_token": secondary, "trace": [],
    })
    assert output["result"]["failed"] and output["execution_receipt"] == {}
    assert not secondary_calls
    assert approval.peek(primary) is None and approval.peek(secondary) is None


def test_compound_exception_race_only_consuming_winner_cancels_peer(monkeypatch):
    from app.agent import approval
    from app.agent.workflow.agents.action_executor import ActionExecutor

    primary_payload = {"key": "ACME-614", "changes": {"priority": "P2-Major"}}
    comment_payload = {"key": "ACME-614", "body": "must not post"}
    primary, secondary = approval.stage_pair(
        "compound-exception-race", "update_ticket", primary_payload,
        "add_ticket_comment", comment_payload,
    )
    assert approval.approve(primary, "compound-exception-race")
    assert approval.approve(secondary, "compound-exception-race")
    both_inside = threading.Barrier(2)
    loser_returned = threading.Event()
    writes = {"primary": 0, "comment": 0}

    def dispatch(_self, action, payload, token):
        if action != "update_ticket":
            writes["comment"] += 1
            raise AssertionError("secondary must remain unexecuted")
        both_inside.wait(timeout=5)
        ok, _why = approval.consume(token, action, payload)
        if ok:
            writes["primary"] += 1
            assert loser_returned.wait(timeout=5)
            raise RuntimeError("provider failed after write")
        loser_returned.set()
        return ({"created": [], "updated": [{
            "key": "ACME-614", "fields": ["priority"],
        }], "failed": [], "note": ""}, "loser")

    monkeypatch.setattr(ActionExecutor, "_dispatch", dispatch)
    state = {
        "thread_id": "compound-exception-race", "approval_token": primary,
        "comment_token": secondary, "trace": [],
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(lambda _index: ActionExecutor().node()(state), range(2)))

    assert writes == {"primary": 1, "comment": 0}
    assert all(output["result"]["failed"] for output in outputs)
    assert approval.peek(primary) is None and approval.peek(secondary) is None


def test_consumption_attestation_wrong_identity_and_expiry_fail_closed(monkeypatch):
    from app.agent import approval

    def consumed(action="update_ticket", payload=None, thread="identity"):
        payload = payload or {"key": "ACME-612", "changes": {"priority": "P2-Major"}}
        token, record = _approved(action, payload, thread_id=thread)
        nonce, context_token = approval.begin_consumption_attempt(token)
        try:
            assert approval.consume(token, action, payload)[0]
        finally:
            approval.end_consumption_attempt(context_token)
        return token, record, nonce

    token, record, nonce = consumed()
    assert approval.take_consumption(
        token, attempt_nonce=nonce, thread_id=record["thread"],
        action="update_tickets", payload=record["payload"],
    ) is None

    token, record, nonce = consumed(thread="wrong-thread-source")
    assert approval.take_consumption(
        token, attempt_nonce=nonce, thread_id="other-thread",
        action=record["action"], payload=record["payload"],
    ) is None

    token, record, nonce = consumed()
    assert approval.take_consumption(
        token, attempt_nonce=nonce, thread_id=record["thread"],
        action=record["action"],
        payload={"key": "ACME-612", "changes": {"priority": "P1-Critical"}},
    ) is None

    token, record, nonce = consumed()
    now = approval.time.time()
    monkeypatch.setattr(approval.time, "time", lambda: now + approval.TTL_SECONDS + 1)
    assert approval.take_consumption(
        token, attempt_nonce=nonce, thread_id=record["thread"],
        action=record["action"], payload=record["payload"],
    ) is None


def test_rejection_cannot_mint_positive_consumption_receipt(monkeypatch):
    from app.agent import approval
    from app.agent import tools as tools_module
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.execution_receipt import bind_single_execution_result

    payload = {"key": "ACME-62", "changes": {"priority": "P2-Major"}}
    token, _record = _approved("update_ticket", payload)

    class RejectingUpdate:
        name = "update_ticket"

        @staticmethod
        def invoke(args):
            assert approval.reject(args["approval_token"])
            return bind_single_execution_result(
                {"ok": True, "key": "ACME-62", "updated": ["priority"]},
                action="update_ticket", payload=payload,
            )

    monkeypatch.setitem(tools_module.BY_NAME, "update_ticket", RejectingUpdate())
    output = ActionExecutor().node()(_state(token, {}))
    assert output["execution_receipt"] == {}
    assert output["result"]["updated"] == [] and output["result"]["failed"]
    assert approval.peek(token) is None


def test_raw_and_legacy_terminal_status_must_be_the_same_ledger():
    from app.agent.workflow.execution_receipt import (
        bind_execution_rows, bind_single_execution_result, issue_execution_receipt,
    )

    success_payload = {"key": "ACME-63", "changes": {"priority": "P2-Major"}}
    success_token, success_record = _approved("update_ticket", success_payload)
    success_raw = bind_single_execution_result(
        {"ok": True, "key": "ACME-63", "updated": ["priority"]},
        action="update_ticket", payload=success_payload,
    )
    success_attestation = _consume_attestation(success_token, success_record)
    false_failure = {"created": [], "updated": [], "failed": [{
        "index": 0, "summary": "ACME-63", "error": "failed",
        "target_id": success_raw["target_id"],
        "effect_digest": success_raw["effect_digest"],
    }], "note": ""}
    assert issue_execution_receipt(
        record=success_record, token=success_token, result=false_failure,
        raw=success_raw, consumption_attestation=success_attestation,
    ) is None

    failure_payload = {"items": [
        {"key": "ACME-64", "changes": {"priority": "P2-Major"}},
    ]}
    failure_token, failure_record = _approved("update_tickets", failure_payload)
    failed = bind_execution_rows(
        [{"index": 0, "summary": "ACME-64", "error": "denied"}],
        action="update_tickets", items=failure_payload["items"],
    )
    failure_raw = {"ok": False, "updated": [], "failed": failed}
    failure_attestation = _consume_attestation(failure_token, failure_record)
    false_success = {"created": [], "updated": [{
        "index": 0, "key": "ACME-64", "fields": ["priority"],
        "target_id": failed[0]["target_id"], "effect_digest": failed[0]["effect_digest"],
    }], "failed": [], "note": ""}
    assert issue_execution_receipt(
        record=failure_record, token=failure_token, result=false_success,
        raw=failure_raw, consumption_attestation=failure_attestation,
    ) is None


def test_post_write_receipt_issuer_exception_is_semantic_and_non_replayable(monkeypatch):
    from app.agent import approval
    from app.agent import tools as tools_module
    from app.agent.workflow import execution_receipt as receipt_module
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    payload = {"key": "ACME-65", "changes": {"priority": "P2-Major"}}
    token, _record = _approved("update_ticket", payload)

    class ExactUpdate:
        name = "update_ticket"

        @staticmethod
        def invoke(args):
            from app.agent.workflow.execution_receipt import bind_single_execution_result
            assert approval.consume(args["approval_token"], "update_ticket", payload)[0]
            return bind_single_execution_result(
                {"ok": True, "key": "ACME-65", "updated": ["priority"]},
                action="update_ticket", payload=payload,
            )

    monkeypatch.setitem(tools_module.BY_NAME, "update_ticket", ExactUpdate())
    monkeypatch.setattr(receipt_module, "issue_execution_receipt", lambda **_kwargs: (
        _ for _ in ()).throw(RuntimeError("signer unavailable")))
    executed = ActionExecutor().node()(_state(token, {}))
    assert executed["execution_receipt"] == {} and executed["result"]["updated"] == []
    assert executed["result"]["failed"] and approval.peek(token) is None
    calls = []
    monkeypatch.setattr(TextAgent, "_run", lambda _self, _state: (
        calls.append(True) or {"fallback": True, "trace": []}))
    output = ResultIntegrator()._run({
        **_state(token, executed["result"]), "execution_receipt": {},
    })
    assert output["fallback"] is True and len(calls) == 1
    assert _metric(output)["savedCalls"] == 0


def test_exception_and_public_result_sanitize_capability_markup_and_bidi(monkeypatch):
    from app.agent import tools as tools_module
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.session import _shape

    payload = {"key": "ACME-66", "changes": {"priority": "P2-Major"}}
    token, _record = _approved("update_ticket", payload)

    class ExplodingUpdate:
        name = "update_ticket"

        @staticmethod
        def invoke(args):
            raise RuntimeError(
                "provider " + args["approval_token"]
                + " {{mention:unsafe}} ### ``` \u202e9-EKAF"
            )

    monkeypatch.setitem(tools_module.BY_NAME, "update_ticket", ExplodingUpdate())
    executed = ActionExecutor().node()(_state(token, {}))
    public = _shape("receipt-thread", {
        **executed, "approval_token": token,
        "result": {**executed["result"], "note": token + " {{unsafe}} \u202e9-EKAF"},
    }, snap=False)
    encoded = repr(public["result"])
    assert token not in encoded and "{{" not in encoded and "```" not in encoded
    assert "\u202e" not in encoded


def test_signed_update_effect_projection_preserves_substance_safely(monkeypatch):
    from app.agent import approval
    from app.agent import tools as tools_module
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    injected = "### {{mention:unsafe}} \u202e9-EKAF " + "long " * 100
    payload = {"key": "ACME-67", "changes": {
        "priority": "P2-Major", "duedate": "2031-10-04", "assignee": None,
        "labels": [], "description": injected,
    }}
    token, _record = _approved("update_ticket", payload)

    class ExactUpdate:
        name = "update_ticket"

        @staticmethod
        def invoke(args):
            from app.agent.workflow.execution_receipt import bind_single_execution_result
            assert approval.consume(args["approval_token"], "update_ticket", payload)[0]
            return bind_single_execution_result({
                "ok": True, "key": "ACME-67", "updated": list(payload["changes"]),
            }, action="update_ticket", payload=payload)

    monkeypatch.setitem(tools_module.BY_NAME, "update_ticket", ExactUpdate())
    executed = ActionExecutor().node()(_state(token, {}))
    effects = executed["execution_receipt"]["expected"][0]["effects"]
    assert [(row["field"], row["value_kind"]) for row in effects] == [
        ("priority", "scalar"), ("duedate", "scalar"), ("assignee", "clear"),
        ("labels", "list"), ("description", "text"),
    ]
    monkeypatch.setattr(TextAgent, "_run", lambda *_args, **_kwargs: (
        _ for _ in ()).throw(AssertionError("signed exact effects must skip Result LLM")))
    output = ResultIntegrator()._run({
        **_state(token, executed["result"]),
        "execution_receipt": executed["execution_receipt"],
    })
    reply = output["reply"]
    assert reply.count("{{ticket-inline:ACME-67}}") == 1
    assert "P2-Major" in reply and "2031-10-04" in reply and "비움" in reply
    assert "{{mention:unsafe}}" not in reply and "\u202e" not in reply
    assert len(effects[-1]["display"]) <= 200 and _metric(output)["savedCalls"] == 1


def test_valid_receipt_precedes_empty_legacy_result_and_stale_review(monkeypatch):
    from app.agent import approval
    from app.agent import tools as tools_module
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    payload = {"key": "ACME-68", "changes": {"priority": "P2-Major"}}
    token, _record = _approved("update_ticket", payload)

    class ExactUpdate:
        name = "update_ticket"

        @staticmethod
        def invoke(args):
            from app.agent.workflow.execution_receipt import bind_single_execution_result
            assert approval.consume(args["approval_token"], "update_ticket", payload)[0]
            return bind_single_execution_result(
                {"ok": True, "key": "ACME-68", "updated": ["priority"]},
                action="update_ticket", payload=payload,
            )

    monkeypatch.setitem(tools_module.BY_NAME, "update_ticket", ExactUpdate())
    executed = ActionExecutor().node()(_state(token, {}))
    monkeypatch.setattr(TextAgent, "_run", lambda *_args, **_kwargs: (
        _ for _ in ()).throw(AssertionError("valid receipt is terminal")))
    output = ResultIntegrator()._run({
        **_state(token, {}), "execution_receipt": executed["execution_receipt"],
        "review": {"ok": False, "issues": ["stale blocker"]},
    })
    assert "{{ticket-inline:ACME-68}}" in output["reply"]
    assert "stale blocker" not in output["reply"] and _metric(output)["savedCalls"] == 1


def test_invalid_receipt_without_result_cannot_bypass_review_authority(monkeypatch):
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    monkeypatch.setattr(TextAgent, "_run", lambda *_args, **_kwargs: (
        _ for _ in ()).throw(AssertionError("malformed sidecar is not branch authority")))
    state = {
        **_state("stale-token", {}),
        "execution_receipt": {"contract": "execution-receipt.v1"},
        "review": {"ok": False, "problems": [{
            "defect": "stale blocker", "advice": "review authority kept",
        }]},
    }
    output = ResultIntegrator()._run(state)
    assert output.get("fallback") is not True and output.get("reply")


def test_execution_receipt_reexports_common_safe_scalar_renderer():
    from app.agent.workflow.execution_receipt import sanitize_external_scalar as receipt_safe
    from app.agent.workflow.safe_render import sanitize_external_scalar as common_safe

    assert receipt_safe is common_safe
    rendered = common_safe("<script> # {{typed}} [link] \\ \u202e", limit=200)
    assert "<script>" not in rendered
    assert "{{typed}}" not in rendered
    assert "[link]" not in rendered
    assert "\u202e" not in rendered
