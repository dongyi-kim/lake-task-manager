from __future__ import annotations

from langchain_core.messages import HumanMessage


def _completed_ask_state() -> dict:
    from app.agent.workflow.state import Intent

    request = "Puffin 적용 근거를 내외부 자료로 비교해줘"
    return {
        "intent": Intent.ASK,
        "messages": [HumanMessage(content=request)],
        "request_text": request,
        "request_plan": {
            "goal": request,
            "tasks": [{
                "id": "compare-evidence",
                "kind": "research",
                "instruction": "내부 결정과 외부 공식 자료를 비교한다",
                "depends_on": [],
                "write_intent": False,
                "completion_criteria": ["출처별 시점, 충돌, 한계를 보존한다"],
            }],
        },
        "keywords": ["Puffin"],
        "mentioned_keys": [],
        "query_plan": {"queries": [
            {"id": "jira", "source": "jira"},
            {"id": "docs", "source": "confluence"},
            {"id": "web", "source": "web"},
        ]},
        "query_results": [
            {"id": "jira", "source": "jira", "result": {
                "tickets": [{"key": "DL-1", "summary": "Puffin 검증"}],
                "ticketDetails": [{
                    "key": "DL-1", "summary": "Puffin 검증", "status": "Done",
                    "description": "2026-07-01 내부 검증 결과",
                }],
            }},
            {"id": "docs", "source": "confluence", "result": {
                "documents": [{
                    "id": "doc-1", "title": "Puffin 설계",
                    "url": "https://docs.example/puffin",
                }],
                "documentBodies": [{
                    "id": "doc-1", "title": "Puffin 설계",
                    "url": "https://docs.example/puffin",
                    "text": "2026-07-02 설계 결정과 한계",
                }],
            }},
            {"id": "web", "source": "web", "result": {
                "query": "Puffin specification",
                "results": [{
                    "title": "Puffin specification",
                    "url": "https://iceberg.apache.org/puffin-spec/",
                    "snippet": "Published specification and limitations",
                    "published": "2026-06-30",
                    "official": True,
                }],
            }},
        ],
        "trace": [],
    }


def test_completed_ask_uses_one_prefetched_synthesis_without_empty_transcript(monkeypatch):
    import app.agent.workflow.agents.research_analyst as mod
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst

    monkeypatch.setattr(mod, "_presurvey", lambda _state: (_ for _ in ()).throw(
        AssertionError("completed QueryPlan must not run a duplicate presurvey")))
    monkeypatch.setattr(ToolAgent, "node", lambda _self: lambda _state: (_ for _ in ()).throw(
        AssertionError("completed QueryPlan must not enter ReAct")))
    analyst = ResearchAnalyst()
    monkeypatch.setattr(analyst, "_conclude", lambda *_args: (_ for _ in ()).throw(
        AssertionError("a completed QueryPlan has no ReAct transcript")))

    prompts: list[str] = []

    def invoke_structured(_state, messages):
        prompts.append("\n".join(str(message.content) for message in messages))
        return {
            "situation": "상세 근거 묶음으로 비교",
            "evidence": [],
            "related_docs": [],
            "already_exists": False,
        }

    monkeypatch.setattr(analyst, "invoke_structured", invoke_structured)

    out = analyst.node()(_completed_ask_state())

    assert len(prompts) == 1
    prompt = prompts[0]
    assert prompt.count("### Deterministic QueryPlan Results") == 1
    assert prompt.count("### User-visible Research Outcome Contract") == 1
    assert prompt.count('"id": "compare-evidence"') == 1
    assert prompt.count("2026-07-01 내부 검증 결과") == 1
    assert prompt.count("2026-07-02 설계 결정과 한계") == 1
    assert prompt.count("Published specification and limitations") == 1
    assert "Tool Transcript Data" not in prompt
    assert "Use only this transcript" not in prompt
    assert out["situation"] == "상세 근거 묶음으로 비교"
    assert any("1회 정리" in row.get("note", "") for row in out["trace"])


def test_completed_ask_synthesis_failure_keeps_existing_react_fallback(monkeypatch):
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst

    calls = {"synthesis": 0, "react": 0}

    def react_node(_self):
        def run(_state):
            calls["react"] += 1
            return {"situation": "기존 ReAct 복구", "evidence": []}
        return run

    monkeypatch.setattr(ToolAgent, "node", react_node)
    analyst = ResearchAnalyst()

    def fail_synthesis(_state):
        calls["synthesis"] += 1
        raise RuntimeError("structured output exhausted")

    monkeypatch.setattr(analyst, "_synthesize_prefetched_query_plan", fail_synthesis)

    out = analyst.node()(_completed_ask_state())

    assert calls == {"synthesis": 1, "react": 1}
    assert out["situation"] == "기존 ReAct 복구"
