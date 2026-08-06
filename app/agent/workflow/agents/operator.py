"""Operator — 승인된 것을 **실행**한다. 그래프에서 유일하게 쓰기 도구를 가진 노드.

이 노드 **앞에서 그래프가 멈춘다**(`interrupt_before`). 사용자가 화면에서 승인 카드를 누르기
전까지는 아예 여기 도달하지 않는다. 승인이 나면 `approval_token` 이 State 에 실려 재개된다.

두 겹으로 막는 이유 — 그래프의 interrupt 는 "여기서 멈춘다"는 **흐름 제어**이고, 도구의
승인 토큰은 "이 내용이 승인됐다"는 **내용 보증**이다. 흐름은 코드 실수로 우회될 수 있지만
토큰은 못 우회한다(내용 해시에 묶여 있다). 반대로 토큰만 있으면 사용자는 언제 물어볼지 모른다.
둘 다 필요하다.

**Task 를 먼저, Sub-Task 를 나중에.** Sub-Task 는 부모가 실재해야 만들 수 있어서 한 번에 섞어
보낼 수 없다. 그래서 mode 가 subtask 면 부모 키가 이미 있어야 하고, 새 Task 밑에 Sub-Task 를
달려면 **두 번의 승인**을 거친다(첫 승인으로 Task 를 만들고, 그 키로 두 번째 초안을 짠다).
"""

from __future__ import annotations

from app.agent.workflow.agents.base import ToolAgent
from app.agent.workflow.agents.refiner import draft_json, draft_text
from app.agent.prompts.roles import SYSTEM_OPERATOR
from app.agent.workflow.prompts import persona
from app.agent.workflow.state import AgentState, Node, note

SCHEMA = {
    "type": "object",
    "properties": {
        "created": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "key": {"type": "string"}, "summary": {"type": "string"}}},
            "description": "실제로 만들어진 티켓. 도구 결과에 나온 것만",
        },
        "failed": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "summary": {"type": "string"}, "error": {"type": "string"}}},
            "description": "실패한 항목. **반드시 그대로 옮긴다** — 조용히 넘어가면 "
                           "사용자는 다 만들어진 줄 안다",
        },
        "note": {"type": "string", "description": "사용자에게 알릴 것(후속 조치 등). 없으면 빈 문자열"},
    },
    "required": ["created", "failed"],
}


class Operator(ToolAgent):
    name = Node.OPERATOR
    temperature = 0.0          # 실행은 창의적일 필요가 없다

    @property
    def tools(self):
        from app.agent import tools as T
        return T.WRITE_TOOLS + T.REVIEW_TOOLS

    def system(self, state):
        return persona(state, SYSTEM_OPERATOR)

    def task(self, state):
        draft = state.get("draft") or {}
        return f"""\
# 명령서
아래 승인된 티켓 초안을 실제로 만들어라.

## 실행 인자
mode: {draft.get('mode') or 'task'}
approval_token: {state.get('approval_token') or '(없음 — 실행하지 마라)'}

## items (이 JSON 을 **그대로** 넘긴다)
{draft_json(draft)}

## 사람이 읽는 형태 (참고용 — 넘기는 것은 위 JSON 이다)
{draft_text(draft)}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
        created = [c for c in (out.get("created") or []) if isinstance(c, dict) and c.get("key")]
        failed = [f for f in (out.get("failed") or []) if isinstance(f, dict)]
        return {"result": {"created": created, "failed": failed, "note": out.get("note") or ""},
                "trace": note(state, self.name,
                              f"생성 {len(created)}건" + (f" · 실패 {len(failed)}건" if failed else ""))}
