"""Query Specialist — 복합 요청의 atomic task를 typed read plan으로 변환한다."""

from __future__ import annotations

import json

from app.agent.prompts.roles import SYSTEM_QUERY_SPECIALIST
from app.agent.workflow.agents.base import StructuredAgent
from app.agent.workflow.contracts import QueryPlan
from app.agent.workflow.prompts import persona
from app.agent.workflow.state import AgentState, Node, conversation, note


class QuerySpecialist(StructuredAgent):
    name = Node.QUERY_SPECIALIST
    temperature = 0.0
    tier = "simple"

    def system(self, state):
        return persona(state, SYSTEM_QUERY_SPECIALIST, lite=True)

    def task(self, state):
        return (
            "# 명령서\nRequest Architect의 계획을 조회 가능한 QueryPlan으로 변환하라. "
            "답변이나 추천은 쓰지 마라.\n\n"
            "## request_plan\n" + json.dumps(state.get("request_plan") or {}, ensure_ascii=False)
            + "\n\n## keywords\n" + json.dumps(state.get("keywords") or [], ensure_ascii=False)
            + "\n\n## mentioned_keys\n" + json.dumps(state.get("mentioned_keys") or [], ensure_ascii=False)
            + "\n\n## 최근 대화\n" + conversation(state)
        )

    def schema(self):
        return QueryPlan.model_json_schema()

    def apply(self, state, out):
        plan = QueryPlan.model_validate(out).model_dump()
        return {"query_plan": plan,
                "trace": note(state, self.name, f"조회 {len(plan['queries'])}개 설계")}


__all__ = ["QuerySpecialist"]
