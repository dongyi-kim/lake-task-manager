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
