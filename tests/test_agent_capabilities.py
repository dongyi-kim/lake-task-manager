from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage
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

    def with_structured_output(self, _schema, method="json_schema"):
        self.methods.append(method)
        return _StructuredFailure()

    def invoke(self, messages):
        self.invocations += 1
        if self.repair and self.invocations == 1:
            return AIMessage(content="JSON이 아닌 응답")
        return AIMessage(content='설명 없이 결과: {"value":"ok"}')


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


def test_invalid_plain_json_gets_one_format_repair(monkeypatch):
    _no_cached_capabilities(monkeypatch)
    fake = _FallbackLLM(repair=True)
    monkeypatch.setattr(base._cfg, "get_llm", lambda **_kwargs: fake)
    result = base.invoke_schema(SCHEMA, [HumanMessage(content="value를 반환")])
    assert result == {"value": "ok"}
    assert fake.invocations == 2


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
    def invoke(self, _messages):
        return AIMessage(content=json.dumps({
            "tool_calls": [{"name": "_probe_echo", "args": {"value": "pong"}},
                           {"name": "nonexistent", "args": {}}],
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
