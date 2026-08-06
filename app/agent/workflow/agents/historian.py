"""Historian — "이 일이 처음인가"를 밝힌다. 이 에이전트가 이 서비스의 값어치다.

실무에서 "새 업무"의 상당수는 **이미 누군가 시작했거나, 논의만 하고 멈췄거나, 비슷한 걸 다른
이름으로 하고 있다.** 그걸 모른 채 티켓을 새로 만들면 중복이 생기고, 앞사람이 부딪힌 벽에
다시 부딪힌다. 그래서 티켓을 만들기 전에 **반드시** 여기를 지난다.

ToolAgent 인 이유 — 몇 번 검색해야 충분한지는 미리 알 수 없다. 한 번에 나오면 한 번이고,
약어 때문에 안 나오면 말을 바꿔 다시 찾아야 하고, 실마리를 잡으면 링크를 타고 더 들어가야 한다.
그 판단을 코드에 박을 수 없으니 모델에게 맡긴다(ReAct).

**근거 없는 서술을 금지한다.** "예전에 검토된 적 있는 것 같습니다"는 최악이다 — 확인할 수도,
반박할 수도 없다. 모든 문장에 티켓 키를 달게 하고, 없으면 없다고 말하게 한다.
"""

from __future__ import annotations

from app.agent.workflow.agents.base import ToolAgent
from app.agent.prompts.roles import SYSTEM_HISTORIAN
from app.agent.workflow.prompts import data_block, persona, wrap_data
from app.agent.workflow.state import AgentState, Node, last_user_text, note

SCHEMA = {
    "type": "object",
    "properties": {
        "situation": {
            "type": "string",
            "description": ("지금까지 밝혀진 '현재 상황' 3~6문장. 진행 중인 것, 멈춘 것, 이미 결정된 것을 "
                            "구분해 적는다. **모든 주장에 티켓 키나 문서 제목을 달 것.** "
                            "아무것도 못 찾았으면 '관련 이력을 찾지 못했다'고 그대로 적는다"),
        },
        "evidence": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "key": {"type": "string", "description": "티켓 키 또는 문서 제목"},
                "title": {"type": "string"},
                "why": {"type": "string", "description": "이번 요청과 어떤 관계인지 한 문장"}}},
            "description": "situation 의 근거. 조사에서 실제로 본 것만. 최대 8건",
        },
        "related_docs": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "title": {"type": "string"}, "url": {"type": "string"}}},
            "description": "관련 Confluence 문서. 조사에서 실제로 나온 것만",
        },
        "epic_candidate": {
            "type": "string",
            "description": "이번 일을 매달 만한 상위 Epic 키. 마땅한 것이 없으면 빈 문자열 — "
                           "관련 없는 Epic 에 억지로 붙이지 마라",
        },
        "already_exists": {
            "type": "boolean",
            "description": "이번 요청과 **사실상 같은 일**을 하는 티켓이 이미 있는가. "
                           "true 면 새로 만들지 말고 사용자에게 알려야 한다",
        },
    },
    "required": ["situation", "evidence"],
}


class Historian(ToolAgent):
    name = Node.HISTORIAN
    temperature = 0.1

    @property
    def tools(self):
        from app.agent import tools as T
        return T.SEARCH_TOOLS

    def system(self, state):
        return persona(state, SYSTEM_HISTORIAN)

    def task(self, state):
        kws = ", ".join(state.get("keywords") or []) or last_user_text(state)
        keys = ", ".join(state.get("mentioned_keys") or [])
        return f"""\
# 명령서
아래 업무 요청과 관련된 **과거 이력**을 조사해 '현재 상황'을 정리하라.

## 제약조건
- 모든 주장에 **티켓 키나 문서 제목**을 근거로 단다. 근거 없는 문장은 쓰지 않는다.
- 진행 중 / 멈춤 / 이미 결정됨 을 구분한다. 멈춘 것이 있으면 **왜 멈췄는지** 코멘트에서 찾는다.
- 이번 요청과 사실상 같은 일이 이미 있으면 그 사실을 가장 먼저 말한다.

## 입력
검색 핵심어: {kws}
사용자가 언급한 티켓: {keys or '없음'}
짐작 모듈: {state.get('module') or '미상'}
원문 요청: {last_user_text(state)}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
        ev = [e for e in (out.get("evidence") or []) if isinstance(e, dict)][:8]
        exists = bool(out.get("already_exists"))
        return {
            "situation": out.get("situation") or "",
            "evidence": ev,
            "related_docs": [d for d in (out.get("related_docs") or []) if isinstance(d, dict)][:6],
            "epic_candidate": (out.get("epic_candidate") or "").strip(),
            "already_exists": exists,
            "trace": note(state, self.name,
                          f"근거 {len(ev)}건" + (" · 중복 의심 티켓 있음" if exists else "")),
        }
