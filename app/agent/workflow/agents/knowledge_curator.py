"""Knowledge KnowledgeCurator — 지식 질문 전담. 조사 결과를 **재사용 가능한 브리프**로.

"X가 뭐야 / X에 대해 우리가 아는 것 정리"류 질문은 조사(ResearchAnalyst)만으로는 답의 절반이다 —
찾은 것을 개념/우리 상황/참고/공백으로 **정리하는 일**이 남는다. ResultIntegrator 는 문장을 만드는
역할이지 지식을 구조화하는 역할이 아니라서, 정리를 스키마로 강제하는 자리가 따로 필요했다.

새 조사는 하지 않는다(도구 없음) — ResearchAnalyst 이 모은 것(사내 이력·웹 조사)을 정리만 한다.
브리프는 ResultIntegrator 의 자료가 되고, 사내 이력이 '없다'는 사실도 정리 대상이다.
"""

from __future__ import annotations

from app.agent.prompts.roles import SYSTEM_KNOWLEDGE_CURATOR
from app.agent.workflow.agents.base import StructuredAgent
from app.agent.workflow.prompts import data_block, persona, wrap_data
from app.agent.workflow.state import AgentState, Node, last_user_text, note, request_text

SCHEMA = {
    "type": "object",
    "properties": {
        "concepts": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "term": {"type": "string"},
                "explanation": {"type": "string",
                                "description": "One concise Korean sentence: what it is and why it matters here."}}},
            "description": "Two to five concepts required to understand the subject.",
        },
        "our_context": {
            "type": "string",
            "description": "Verified internal work, decisions, and attempts. Cite a ticket key or document "
                           "title for each claim. If absent, write the Korean phrase 사내 이력 없음.",
        },
        "references": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "ref": {"type": "string", "description": "Only a ticket key, document title, or URL in the input."},
                "why": {"type": "string", "description": "Why this source is worth opening."}}},
        },
        "gaps": {
            "type": "array", "items": {"type": "string"},
            "description": "Unknown or undecided points and the follow-up verification needed.",
        },
    },
    "required": ["concepts", "our_context", "gaps"],
}


class KnowledgeCurator(StructuredAgent):
    name = Node.KNOWLEDGE_CURATOR
    temperature = 0.2

    def system(self, state):
        return persona(state, SYSTEM_KNOWLEDGE_CURATOR)

    def task(self, state):
        data = wrap_data(
            data_block("Verified Internal Findings", state.get("situation")),
            data_block("Original User Request and Meeting Decisions", request_text(state)),
            data_block("Current Interview Answer", last_user_text(state)),
            data_block("Evidence Tickets", "\n".join(
                f"- {e.get('key', '')} {e.get('title', '')} — {e.get('why', '')}"
                for e in (state.get("evidence") or []))),
            data_block("Prefetched Lexical and Semantic Search", state.get("pre_survey")),
            data_block("Topic Dossier: Tickets, Comments, Field History, and Documents",
                       state.get("topic_dossier")),
            data_block("External Technology Research: Web and GitHub", state.get("web_context")))
        return f"""\
# Task

Organize the supplied evidence into a Korean knowledge brief with concepts, internal context, references, and gaps. Do not perform new research.

## Constraints

- Never invent an interval, column, Job name, owner, setting, or other value absent from the data. Put unresolved facts in `gaps`; identifying what is unknown is part of the result.
- Cite a ticket key or document title inside every factual `our_context` statement.
- Determine a current value from the latest verified change record. Never report an earlier value as current.
- For a single-asset fact lookup such as interval, schema, owner, or policy, limit `concepts` to one or two items and put every supplied operational value in `our_context`, including full column lists, Job names, user IDs, policy values, and change dates.
- Write all natural-language field values in Korean while preserving identifiers exactly.
- For meeting notes, user-written decisions, named owners, deadlines, exclusions, and a definition supplied in
  the interview are primary evidence. Preserve every one in `our_context`; internal tickets and external sources
  supplement them and must not replace them with an older status.

## User Question

{last_user_text(state)}{data}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
        from app.agent.workflow.meeting_context import prune_resolved_gaps

        brief = {
            "concepts": [c for c in (out.get("concepts") or []) if isinstance(c, dict)][:5],
            "our_context": out.get("our_context") or "",
            "references": [r for r in (out.get("references") or []) if isinstance(r, dict)][:8],
            "gaps": prune_resolved_gaps(state, out.get("gaps") or [])[:6],
        }
        return {"knowledge_brief": brief,
                "trace": note(state, self.name,
                              f"개념 {len(brief['concepts'])} · 참고 {len(brief['references'])}"
                              f" · 공백 {len(brief['gaps'])}")}
