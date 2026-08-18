from __future__ import annotations

from copy import deepcopy

import pytest
from langchain_core.messages import HumanMessage


def _state(*, task_kind: str = "query", result: dict | None = None) -> dict:
    from app.agent.workflow.state import Intent

    return {
        "intent": Intent.ASK,
        "messages": [HumanMessage(content="검증된 설계 문서 원문을 조회해줘")],
        "request_text": "검증된 설계 문서 원문을 조회해줘",
        "request_plan": {"goal": "설계 문서 조회", "tasks": [{
            "id": "lookup-current",
            "kind": task_kind,
            "instruction": "설계 문서 원문을 조회한다",
            "completion_criteria": ["원본 출처를 보존한다"],
            "write_intent": False,
            "depends_on": [],
        }]},
        "query_plan": {"queries": [{"id": "document", "source": "confluence"}]},
        "query_results": [{"id": "document", "source": "confluence", "result": result or {
            "documents": [{
                "id": "doc-71", "title": "검증 설계", "url": "https://docs.test/71",
            }],
            "documentBodies": [{
                "id": "doc-71", "title": "검증 설계", "url": "https://docs.test/71",
                "text": "검증된 설계 원문",
            }],
            "complete": True,
        }}],
        "mentioned_keys": [],
        "keywords": ["검증 설계"],
        "trace": [],
    }


def _forbid_semantic_research(monkeypatch):
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst

    forbidden = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("complete typed query must not call Research LLM or ReAct"))
    monkeypatch.setattr(ToolAgent, "node", lambda _self: forbidden)
    analyst = ResearchAnalyst()
    monkeypatch.setattr(analyst, "_conclude", forbidden)
    monkeypatch.setattr(analyst, "invoke_structured", forbidden)
    return analyst


def test_single_complete_query_outcome_skips_research_llm(monkeypatch):
    analyst = _forbid_semantic_research(monkeypatch)

    out = analyst.node()(_state())

    assert out["evidence"][0]["url"] == "https://docs.test/71"
    fast = next(row["fastPath"] for row in out["trace"] if row.get("fastPath"))
    assert fast == {
        "contract": "typed-fast-path.v1",
        "id": "research.single_bounded_query",
        "complete": True,
        "authority": "request-plan.v1+query-plan.v1+query-results.v1",
        "savedCalls": 1,
        "missing": [],
    }


@pytest.mark.parametrize(
    "mutation", ["semantic", "unsupported", "incomplete", "compound", "overflow", "jira"],
)
def test_query_fast_path_fails_closed_outside_exact_contract(monkeypatch, mutation):
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst

    state = _state()
    if mutation == "semantic":
        state["request_plan"]["tasks"][0]["kind"] = "analyze"
    elif mutation == "unsupported":
        state["query_results"][0]["result"] = {
            "documents": [{"id": "doc-71", "title": "검증 설계"}],
            "documentBodies": [], "complete": True,
        }
    elif mutation == "incomplete":
        state["query_results"][0]["result"]["materializationErrors"] = ["ACME-71"]
    elif mutation == "compound":
        state["request_plan"]["tasks"].append({
            "id": "compare", "kind": "query", "instruction": "다른 대상을 함께 조회",
            "completion_criteria": [], "write_intent": False, "depends_on": [],
        })
    elif mutation == "overflow":
        state["query_results"][0]["result"] = {
            "documents": [
                {"id": f"doc-{index}", "title": f"Document {index}",
                 "url": f"https://docs.test/{index}"}
                for index in range(9)
            ],
            "documentBodies": [
                {"id": f"doc-{index}", "title": f"Document {index}",
                 "url": f"https://docs.test/{index}", "text": f"Body {index}"}
                for index in range(9)
            ],
            "complete": True,
        }
    else:
        state["query_plan"]["queries"][0]["source"] = "jira"
        state["query_results"][0] = {
            "id": "document", "source": "jira", "result": {
                "tickets": [{"key": "ACME-71", "summary": "검증 대상"}],
                "ticketDetails": [{
                    "key": "ACME-71", "summary": "검증 대상", "status": "Open",
                }],
                "complete": True,
            },
        }

    calls = []
    monkeypatch.setattr(ToolAgent, "node", lambda _self: lambda _state: (
        calls.append(deepcopy(_state)) or {"situation": "semantic fallback", "evidence": []}
    ))

    out = ResearchAnalyst().node()(state)

    assert len(calls) == 1
    assert out["situation"] == "semantic fallback"
