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
from app.agent.workflow.state import AgentState, Node, last_user_text, note

SCHEMA = {
    "type": "object",
    "properties": {
        "concepts": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "term": {"type": "string"},
                "explanation": {"type": "string",
                                "description": "바쁜 동료에게 하듯 한 문장 — 무엇이고 왜 여기서 중요한가"}}},
            "description": "이해에 필요한 용어 2~5개",
        },
        "our_context": {
            "type": "string",
            "description": "이 프로젝트가 이 주제로 한 일·결정·시도 — 주장마다 티켓 키/문서 제목. "
                           "사내 이력이 없으면 '사내 이력 없음'이라고 그대로 적는다",
        },
        "references": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "ref": {"type": "string", "description": "티켓 키·문서 제목·URL(자료에 있는 것만)"},
                "why": {"type": "string", "description": "열어볼 가치가 뭔가"}}},
        },
        "gaps": {
            "type": "array", "items": {"type": "string"},
            "description": "여전히 모르는 것·미결정 사항 — 후속 조사나 티켓이 답할 질문들",
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
            data_block("조사 결과(사내)", state.get("situation")),
            data_block("근거 티켓", "\n".join(
                f"- {e.get('key', '')} {e.get('title', '')} — {e.get('why', '')}"
                for e in (state.get("evidence") or []))),
            data_block("사전 조사(키워드·의미 검색)", state.get("pre_survey")),
            data_block("주제 조사 자료(티켓·코멘트 인용·필드 변경 이력·문서 본문)",
                       state.get("topic_dossier")),
            data_block("외부 기술 조사(웹·GitHub)", state.get("web_context")))
        return f"""\
# 명령서
아래 자료를 지식 브리프(개념/우리 상황/참고/공백)로 정리하라. 새 조사는 하지 마라.

## 제약조건
- 자료에 없는 값(적재주기·컬럼·Job 이름·담당자)을 지어내지 마라. 확인 못 한 것은 **gaps**
  에 그대로 적는다 — 무엇을 모르는지 밝히는 것이 이 브리프의 값어치다.
- 사실마다 **티켓 키·문서 제목**을 our_context 문장 안에 함께 적는다.
- 속성의 **현재 값은 가장 최근 변경 기록**이다. 변경 전 값을 현재 값으로 적지 마라.
- 대상 하나의 사실을 묻는 질문(주기·스키마·담당·정책)이면 **concepts 는 1~2개로 줄이고**,
  our_context 에 **값을 전부** 담아라(컬럼 목록·Job 이름·담당 사번·정책값·변경 일자).
  개념 설명이 길고 값이 없는 브리프는 이 질문에 아무 쓸모가 없다.

## 질문
{last_user_text(state)}{data}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
        brief = {
            "concepts": [c for c in (out.get("concepts") or []) if isinstance(c, dict)][:5],
            "our_context": out.get("our_context") or "",
            "references": [r for r in (out.get("references") or []) if isinstance(r, dict)][:8],
            "gaps": [str(g) for g in (out.get("gaps") or []) if str(g).strip()][:6],
        }
        return {"knowledge_brief": brief,
                "trace": note(state, self.name,
                              f"개념 {len(brief['concepts'])} · 참고 {len(brief['references'])}"
                              f" · 공백 {len(brief['gaps'])}")}
