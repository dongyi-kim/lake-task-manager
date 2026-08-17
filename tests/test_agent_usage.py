from __future__ import annotations

import uuid
import json

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app.agent.usage import Meter, callback


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
