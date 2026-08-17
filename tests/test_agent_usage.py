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
        "promptTokens": 120, "completionTokens": 30, "seconds": detail[0]["seconds"],
    }]
    assert "not recorded" not in str(detail)


def test_evaluation_checkpoint_preserves_per_call_usage(tmp_path, monkeypatch):
    """The real create-battery checkpoint keeps call detail under each raw turn."""
    from tools import agent_create_suite as suite

    usage = {"calls": 1, "callsDetail": [{
        "node": "work_architect", "model": "local-complex",
        "outputContract": "semantic_memo", "finishReason": "stop",
        "promptTokens": 120, "completionTokens": 30, "seconds": 1.25,
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
