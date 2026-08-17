from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from app.agent.workflow.agents import base


SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}


class _StructuredFailure:
    def invoke(self, _messages):
        raise RuntimeError("response_format unsupported")


class _FallbackLLM:
    def __init__(self, repair=False):
        self.methods = []
        self.invocations = 0
        self.repair = repair
        self.stop_values = []

    def with_structured_output(self, _schema, method="json_schema"):
        self.methods.append(method)
        return _StructuredFailure()

    def invoke(self, messages, **kwargs):
        self.invocations += 1
        self.stop_values.append(kwargs.get("stop"))
        if self.repair and self.invocations == 1:
            return AIMessage(content="JSON이 아닌 응답")
        return AIMessage(content='{"value":"ok"}')


def _no_cached_capabilities(monkeypatch):
    from app.agent import capabilities
    monkeypatch.setattr(capabilities, "get", lambda _tier="complex": {"checked": {}})
    monkeypatch.setattr(capabilities, "record", lambda *_args, **_kwargs: None)


def test_structured_output_falls_back_from_json_schema_and_json_object_to_plain_json(monkeypatch):
    _no_cached_capabilities(monkeypatch)
    fake = _FallbackLLM()
    monkeypatch.setattr(base._cfg, "get_llm", lambda **_kwargs: fake)
    result = base.invoke_schema(SCHEMA, [HumanMessage(content="value를 반환")], tier="simple")
    assert result == {"value": "ok"}
    assert fake.methods == ["json_schema", "json_mode"]
    assert fake.stop_values == [[base.STRUCTURED_END_TOKEN]]


def test_invalid_plain_json_gets_one_format_repair(monkeypatch):
    _no_cached_capabilities(monkeypatch)
    fake = _FallbackLLM(repair=True)
    monkeypatch.setattr(base._cfg, "get_llm", lambda **_kwargs: fake)
    result = base.invoke_schema(SCHEMA, [HumanMessage(content="value를 반환")])
    assert result == {"value": "ok"}
    assert fake.invocations == 2
    assert fake.stop_values == [[base.STRUCTURED_END_TOKEN], [base.STRUCTURED_END_TOKEN]]


class _SemanticProjectionAgent(base.StructuredAgent):
    """Small schema with WorkArchitect's canonical id and manifest contract."""

    name = "work_architect"

    def system(self, _state):
        return "ORIGINAL SYSTEM SECRET"

    def task(self, _state):
        return "ORIGINAL EVIDENCE SECRET"

    def schema(self):
        return SCHEMA

    def apply(self, _state, out):
        return out


class _SequenceLLM:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.messages = []
        self.stop_values = []
        self.structured_methods = []

    def invoke(self, messages, **kwargs):
        self.messages.append(list(messages))
        self.stop_values.append(kwargs.get("stop"))
        value = self.outputs.pop(0)
        if isinstance(value, dict):
            return value
        return value if isinstance(value, AIMessage) else AIMessage(content=value)

    def with_structured_output(self, _schema, method="json_schema"):
        self.structured_methods.append(method)
        return self


def _message_text(messages):
    return "\n".join(str(getattr(message, "content", message) or "") for message in messages)


def test_semantic_projection_keeps_original_on_semantic_model_and_repairs_projector_only(
        monkeypatch):
    from app.agent import capabilities

    monkeypatch.setattr(capabilities, "get", lambda tier="complex": {
        "checked": {"json_schema": False, "json_object": False}})
    monkeypatch.setattr(capabilities, "record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(base._cfg, "typed_projection_tier", lambda _tier: "simple")

    memo = _SequenceLLM(
        "verified value=ok " + base.SEMANTIC_MEMO_END_TOKEN)
    projector = _SequenceLLM("not json", '{"value":"ok"}')
    requested = []
    agent = _SemanticProjectionAgent()

    def llm(**kwargs):
        requested.append(dict(kwargs))
        return memo if kwargs.get("output_contract") == "semantic_memo" else projector

    monkeypatch.setattr(agent, "llm", llm)
    original = [SystemMessage(content=agent.system({})),
                HumanMessage(content=agent.task({}))]
    assert agent.invoke_structured({}, original) == {"value": "ok"}

    assert len(memo.messages) == 1, "projector repair must not rerun semantic judgment"
    assert "ORIGINAL SYSTEM SECRET" in _message_text(memo.messages[0])
    assert "ORIGINAL EVIDENCE SECRET" in _message_text(memo.messages[0])
    assert memo.stop_values == [[base.SEMANTIC_MEMO_END_TOKEN]]

    assert len(projector.messages) == 2
    for messages in projector.messages:
        text = _message_text(messages)
        assert "ORIGINAL SYSTEM SECRET" not in text
        assert "ORIGINAL EVIDENCE SECRET" not in text
    assert "verified value=ok" in _message_text(projector.messages[0])
    assert base.SEMANTIC_MEMO_END_TOKEN not in _message_text(projector.messages[0])
    assert "verified value=ok" in _message_text(projector.messages[1])
    assert "Validation error:" in _message_text(projector.messages[1])
    assert [row["output_contract"] for row in requested] == [
        "semantic_memo", "typed_projection", "typed_projection"]
    assert requested[1]["profile"] == requested[2]["profile"] == "fast_structured"


def test_native_strict_semantic_role_keeps_existing_single_call(monkeypatch):
    from app.agent import capabilities

    monkeypatch.setattr(capabilities, "get", lambda tier="complex": {
        "checked": {"json_schema": True, "json_object": True}})
    monkeypatch.setattr(capabilities, "record", lambda *_args, **_kwargs: None)

    native = _SequenceLLM({"value": "ok"})
    requested = []
    agent = _SemanticProjectionAgent()

    def llm(**kwargs):
        requested.append(dict(kwargs))
        return native

    monkeypatch.setattr(agent, "llm", llm)
    result = agent.invoke_structured({}, [HumanMessage(content="original")])
    assert result == {"value": "ok"}
    assert native.structured_methods == ["json_schema"]
    assert len(native.messages) == 1
    assert requested == [{"output_contract": "structured"}]


def test_truncated_semantic_memo_fails_before_typed_projection(monkeypatch):
    from app.agent import capabilities

    monkeypatch.setattr(capabilities, "get", lambda tier="complex": {
        "checked": {"json_schema": False, "json_object": False}})
    monkeypatch.setattr(base._cfg, "typed_projection_tier", lambda _tier: "simple")

    memo = _SequenceLLM(AIMessage(
        content="partial semantic decision",
        response_metadata={"finish_reason": "length"},
    ))
    projector = _SequenceLLM('{"value":"must not run"}')
    agent = _SemanticProjectionAgent()
    monkeypatch.setattr(
        agent, "llm",
        lambda **kwargs: memo if kwargs.get("output_contract") == "semantic_memo" else projector,
    )

    with pytest.raises(RuntimeError, match="출력 길이 한도"):
        agent.invoke_structured({}, [HumanMessage(content="original")])
    assert len(memo.messages) == 1
    assert projector.messages == []


def test_prefixed_json_is_not_silently_extracted():
    assert base._loads_loose('설명: {"value":"ok"}') is None
    assert base._loads_loose('```json\n{"value":"ok"}\n```') is None


def test_model_schema_value_error_does_not_poison_provider_capability():
    assert not base._capability_is_unsupported(
        RuntimeError("'높음' is not one of ['high', 'medium', 'low']"), "json_schema")
    assert not base._capability_is_unsupported(
        RuntimeError("Invalid schema for response_format"), "json_schema")
    assert base._capability_is_unsupported(
        RuntimeError("response_format json_schema is not supported"), "json_schema")


@tool
def _probe_echo(value: str) -> str:
    """Return a harmless value for fallback tool execution tests."""
    return value


class _ToolPlanLLM:
    def invoke(self, messages, **_kwargs):
        repairing = any("Validation error:" in str(getattr(m, "content", "")) for m in messages)
        return AIMessage(content=json.dumps({
            "tool_calls": [{"name": "_probe_echo", "args": {"value": "pong"}},
                           *([{"name": "nonexistent", "args": {}}]
                             if not repairing else [])],
            "answer": "",
        }))


class _FallbackToolAgent(base.ToolAgent):
    name = "fallback_tool_test"

    @property
    def tools(self):
        return [_probe_echo]

    def system(self, _state):
        return "test"

    def task(self, _state):
        return "test"

    def schema(self):
        return SCHEMA

    def apply(self, _state, out):
        return out

    def llm(self, **_kwargs):
        return _ToolPlanLLM()


def test_tool_fallback_can_only_schedule_registered_tools(monkeypatch):
    from app.agent import capabilities
    monkeypatch.setattr(capabilities, "get", lambda _tier="complex": {
        "checked": {"tools": False}})
    monkeypatch.setattr(capabilities, "record", lambda *_args, **_kwargs: None)
    message = _FallbackToolAgent()._think({
        "messages": [HumanMessage(content="echo")], "steps": 0})["messages"][0]
    assert [call["name"] for call in message.tool_calls] == ["_probe_echo"]
    assert message.tool_calls[0]["args"] == {"value": "pong"}
    # invalid enum is not silently filtered; exact validation error drives one repair.
    assert message is not None


def test_openai_compat_never_sends_native_tool_payload(monkeypatch):
    from app.agent import capabilities, config

    class TrapLLM(_ToolPlanLLM):
        binds = 0

        def bind_tools(self, *_args, **_kwargs):
            self.binds += 1
            return self

    trap = TrapLLM()
    monkeypatch.setattr(config, "provider", lambda: "openai_compat")
    monkeypatch.setattr(capabilities, "get", lambda _tier="complex": {"checked": {}})
    monkeypatch.setattr(capabilities, "record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_FallbackToolAgent, "llm", lambda self, **_kwargs: trap)
    message = _FallbackToolAgent()._think({
        "messages": [HumanMessage(content="echo")], "steps": 0})["messages"][0]
    assert trap.binds == 0
    assert [call["name"] for call in message.tool_calls] == ["_probe_echo"]


def test_prod_never_sends_native_tools_even_when_provider_is_named_aoai(monkeypatch):
    from types import SimpleNamespace
    from app.agent import capabilities, config
    from app.infra import settings

    monkeypatch.setattr(config, "provider", lambda: "aoai")
    monkeypatch.setattr(settings, "get_settings", lambda: SimpleNamespace(jira_env="prod"))
    assert capabilities.native_tools_allowed() is False
