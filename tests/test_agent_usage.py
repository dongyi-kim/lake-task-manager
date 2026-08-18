from __future__ import annotations

import uuid
import json

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.runnables import RunnableLambda

from app.agent.usage import Meter, callback
from app.agent.workflow.typed_fast_path import (
    evaluate_typed_fast_path,
    typed_fast_path_note,
)


def test_usage_records_safe_per_call_contract_model_and_finish_reason():
    meter = Meter()
    handler = callback(meter)
    run_id = uuid.uuid4()
    handler.on_chat_model_start(
        {"name": "ChatModel"}, [[AIMessage(content="")]], run_id=run_id,
        metadata={
            "langgraph_node": "think",
            "ltm_role_id": "work_architect",
            "ltm_output_contract": "semantic_memo",
            "ltm_execution_layer": "deep_semantic",
            "ltm_execution_stage": "semantic",
        },
    )
    message = AIMessage(
        content="not recorded by the meter",
        response_metadata={"finish_reason": "stop", "model_name": "local-complex"},
        usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
    )
    handler.on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=message)]], llm_output={}),
        run_id=run_id,
    )
    detail = meter.snapshot()["callsDetail"]
    assert detail == [{
        "node": "work_architect", "model": "local-complex",
        "outputContract": "semantic_memo", "finishReason": "stop",
        "executionLayer": "deep_semantic", "executionStage": "semantic",
        "promptTokens": 120, "completionTokens": 30, "seconds": detail[0]["seconds"],
    }]
    assert "not recorded" not in str(detail)


def test_usage_records_only_safe_structured_validation_coordinates():
    meter = Meter()
    handler = callback(meter)
    run_id = uuid.uuid4()
    handler.on_chat_model_start(
        {"name": "ChatModel"}, [[AIMessage(content="")]], run_id=run_id,
        metadata={
            "ltm_role_id": "work_architect",
            "ltm_output_contract": "typed_projection_repair",
            "ltm_execution_layer": "projection",
            "ltm_execution_stage": "repair",
            "ltm_validation_category": "schema",
            "ltm_validation_keyword": "required",
            "ltm_validation_path": "$.draft.items[0]",
            "ltm_validation_missing": "outcome_refs",
            # Unknown metadata must not leak into the evaluation raw.
            "ltm_validation_raw": "CONFIDENTIAL INVALID MODEL OUTPUT",
        },
    )
    message = AIMessage(
        content="CONFIDENTIAL REPAIRED MODEL OUTPUT",
        response_metadata={"finish_reason": "stop", "model_name": "local-complex"},
        usage_metadata={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
    )
    handler.on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=message)]], llm_output={}),
        run_id=run_id,
    )

    detail = meter.snapshot()["callsDetail"][0]
    assert detail["validationCategory"] == "schema"
    assert detail["validationKeyword"] == "required"
    assert detail["validationPath"] == "$.draft.items[0]"
    assert detail["validationMissing"] == "outcome_refs"
    assert "CONFIDENTIAL" not in json.dumps(detail, ensure_ascii=False)


def test_evaluation_checkpoint_preserves_per_call_usage(tmp_path, monkeypatch):
    """The real create-battery checkpoint keeps call detail under each raw turn."""
    from tools import agent_create_suite as suite

    usage = {"calls": 1, "callsDetail": [{
        "node": "work_architect", "model": "local-complex",
        "outputContract": "semantic_memo", "finishReason": "stop",
        "executionLayer": "deep_semantic", "executionStage": "semantic",
        "promptTokens": 120, "completionTokens": 30, "seconds": 1.25,
        "validationCategory": "schema", "validationKeyword": "required",
        "validationPath": "$.draft.items[0]", "validationMissing": "outcome_refs",
    }]}
    path = tmp_path / "create.json"
    monkeypatch.setattr(suite, "OUT", str(path))
    monkeypatch.setattr(suite, "RESULTS", [{
        "id": "EXAMPLE", "초": 1.25, "턴": [{"usage": usage}],
    }])
    monkeypatch.setattr(suite, "EVALUATION_METADATA", {"protocolVersion": "test"})
    suite.write_checkpoint(hits=1, total=1, cost=0.0)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["케이스"][0]["턴"][0]["usage"]["callsDetail"] == usage["callsDetail"]


def test_public_custom_event_records_evaluated_and_committed_in_one_safe_scope():
    meter = Meter()
    handler = callback(meter)

    def _fast(_value):
        decision = evaluate_typed_fast_path(
            "result.structure_tree.v1",
            checks={"tree": True, "stage_authority": True,
                    "tree_seal": True, "render_safe": True},
        )
        typed_fast_path_note({}, "result_integrator", "deterministic", decision)
        return "ok"

    assert RunnableLambda(_fast).invoke(
        None, config={"callbacks": [handler], "metadata": {"ltm_role_id": "result_integrator"}},
    ) == "ok"
    usage = meter.snapshot()
    events = usage["fastPathEvents"]

    assert [event["phase"] for event in events] == ["evaluated", "committed"]
    assert len({event["scopeId"] for event in events}) == 1
    assert usage["calls"] == 0
    assert usage["callsDetail"] == []
    assert "deterministic" not in json.dumps(events, ensure_ascii=False)


def test_meter_binds_nested_llm_call_to_active_fast_path_scope_only():
    meter = Meter()
    handler = callback(meter)
    chain_id, llm_id = uuid.uuid4(), uuid.uuid4()
    handler.on_chain_start(
        {}, {}, run_id=chain_id, metadata={"ltm_role_id": "result_integrator"},
    )
    handler.on_custom_event(
        "ltm.typed_fast_path",
        {
            "contract": "typed-fast-path-event.v1", "phase": "evaluated",
            "pathId": "result.structure_tree.v1",
            "authority": "work_architect.structure_stage",
            "eligible": False, "estimatedSavedCalls": 0,
        },
        run_id=chain_id,
    )
    handler.on_chat_model_start({}, [[]], run_id=llm_id, parent_run_id=chain_id)
    handler.on_llm_end(
        LLMResult(generations=[[]], llm_output={
            "token_usage": {"prompt_tokens": 2, "completion_tokens": 1},
        }),
        run_id=llm_id,
    )
    handler.on_chain_end({}, run_id=chain_id)

    usage = meter.snapshot()
    assert usage["callsDetail"][0]["fastPathScopeId"] == usage["fastPathEvents"][0]["scopeId"]


def test_meter_rejects_forged_or_late_event_without_leaking_payload():
    meter = Meter()
    handler = callback(meter)
    run_id = uuid.uuid4()
    handler.on_chain_start(
        {}, {}, run_id=run_id, metadata={"ltm_role_id": "result_integrator"},
    )
    handler.on_custom_event(
        "ltm.typed_fast_path",
        {
            "contract": "typed-fast-path-event.v1", "phase": "evaluated",
            "pathId": "result.structure_tree.v1",
            "authority": "work_architect.structure_stage",
            "eligible": True, "estimatedSavedCalls": 1,
            "prompt": "PRIVATE PROMPT",
        },
        run_id=run_id,
    )
    handler.on_chain_end({}, run_id=run_id)
    handler.on_custom_event(
        "ltm.typed_fast_path",
        {
            "contract": "typed-fast-path-event.v1", "phase": "evaluated",
            "pathId": "result.structure_tree.v1",
            "authority": "work_architect.structure_stage",
            "eligible": True, "estimatedSavedCalls": 1,
        },
        run_id=run_id,
    )

    usage = meter.snapshot()
    assert usage["fastPathInvalidEvents"] == 2
    assert "fastPathEvents" not in usage
    assert "PRIVATE PROMPT" not in json.dumps(usage, ensure_ascii=False)


def test_meter_scopes_llm_call_that_finishes_before_fast_path_events():
    meter = Meter()
    handler = callback(meter)
    chain_id, llm_id = uuid.uuid4(), uuid.uuid4()
    handler.on_chain_start(
        {}, {}, run_id=chain_id, metadata={"ltm_role_id": "result_integrator"},
    )
    handler.on_chat_model_start({}, [[]], run_id=llm_id, parent_run_id=chain_id)
    handler.on_llm_end(
        LLMResult(generations=[[]], llm_output={
            "token_usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }),
        run_id=llm_id,
    )
    for phase in ("evaluated", "committed"):
        handler.on_custom_event(
            "ltm.typed_fast_path",
            {
                "contract": "typed-fast-path-event.v1", "phase": phase,
                "pathId": "result.structure_tree.v1",
                "authority": "work_architect.structure_stage",
                "eligible": True, "estimatedSavedCalls": 1,
            },
            run_id=chain_id,
        )
    handler.on_chain_end({}, run_id=chain_id)

    usage = meter.snapshot()
    assert usage["callsDetail"][0]["fastPathScopeId"] == usage["fastPathEvents"][0]["scopeId"]


def test_meter_rejects_registered_event_from_wrong_role_scope():
    meter = Meter()
    handler = callback(meter)

    def _wrong_role(_value):
        decision = evaluate_typed_fast_path(
            "result.structure_tree.v1",
            checks={"tree": True, "stage_authority": True,
                    "tree_seal": True, "render_safe": True},
        )
        typed_fast_path_note({}, "result_integrator", "deterministic", decision)

    RunnableLambda(_wrong_role).invoke(
        None,
        config={"callbacks": [handler], "metadata": {"ltm_role_id": "research_analyst"}},
    )

    usage = meter.snapshot()
    assert usage["fastPathInvalidEvents"] == 2
    assert "fastPathEvents" not in usage


def test_telemetry_dispatch_failure_never_changes_fast_path_decision(monkeypatch):
    from app.agent.workflow import typed_fast_path

    monkeypatch.setattr(
        typed_fast_path, "_dispatch_custom_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("telemetry unavailable")),
    )
    decision = evaluate_typed_fast_path(
        "result.structure_tree.v1",
        checks={"tree": True, "stage_authority": True,
                "tree_seal": True, "render_safe": True},
    )
    trace = typed_fast_path_note({}, "result_integrator", "deterministic", decision)

    assert decision.complete is True
    assert trace[0]["fastPath"]["savedCalls"] == 1
