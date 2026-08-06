"""Planner — 무엇을 원하는 요청인지 가른다. 그래프의 첫 분기가 여기서 정해진다.

"DL-118 어떻게 됐어?"와 "CDC 도입해야 해"는 들어가야 할 길이 완전히 다르다. 전자는 찾아서
답하면 끝이고, 후자는 조사→구체화→담당자→검증→생성까지 간다. 이걸 매번 전 경로로 태우면
느리고 비싸다.

**분류를 Structured Output 으로 받는다.** "이건 업무 계획 요청 같습니다"라는 자유 서술을 받아
정규식으로 긁으면, 모델이 말투를 바꾸는 날 조용히 오분류된다. enum 으로 강제하면 그럴 일이 없다.
"""

from __future__ import annotations

from app.agent.workflow.agents.base import StructuredAgent
from app.agent.workflow.prompts import persona
from app.agent.workflow.state import AgentState, Intent, Node, conversation, note

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [Intent.ASK, Intent.PLAN_WORK, Intent.MODIFY, Intent.CHITCHAT],
            "description": ("ask=이미 있는 것에 대해 물음 / plan_work=새 업무를 시작하려 함 / "
                            "modify=기존 티켓의 담당자·마감 등을 바꾸려 함 / chitchat=업무 요청 아님"),
        },
        "keywords": {
            "type": "array", "items": {"type": "string"},
            "description": "검색에 쓸 핵심어 2~5개. 원문을 그대로 넣지 말고 명사구로 뽑아라. "
                           "약어와 풀어쓴 말을 함께 넣으면 좋다(예: CDC, 변경데이터캡처, 실시간 수집)",
        },
        "module": {
            "type": "string",
            "enum": ["", "ETL", "Catalog", "Runtime", "Workbench", "DataOps", "DevOps"],
            "description": "짐작되는 모듈. 근거가 약하면 빈 문자열 — 틀린 모듈은 없느니만 못하다",
        },
        "mentioned_keys": {
            "type": "array", "items": {"type": "string"},
            "description": "사용자가 직접 언급한 티켓 키(DL-123 형식)만. 추측한 키는 넣지 마라",
        },
        "sufficient": {
            "type": "boolean",
            "description": "되묻지 않고 바로 조사에 들어가도 될 만큼 요청이 구체적인가",
        },
    },
    "required": ["intent", "keywords", "sufficient"],
}


class Planner(StructuredAgent):
    name = Node.PLANNER
    temperature = 0.0          # 분류는 흔들리면 안 된다

    def system(self, state):
        return persona(state, """\
너는 지금 **분류만** 한다. 답을 만들거나 조사하지 않는다.
판단이 애매하면 더 넓은 쪽(plan_work)을 고른다 — 조사를 더 하는 손해가 놓치는 손해보다 작다.""")

    def task(self, state):
        return f"""\
# 명령서
아래 대화에서 사용자가 원하는 것을 분류하고, 검색에 쓸 핵심어를 뽑아라.

## 제약조건
- 핵심어는 **검색용**이다. "해야 한다", "관련해서" 같은 말은 빼고 명사구만 남긴다.
- 티켓 키는 사용자가 실제로 적은 것만 옮긴다.
- 모듈은 확신이 있을 때만 고른다.

## 대화
{conversation(state)}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
        intent = out.get("intent") or Intent.PLAN_WORK
        kws = [k for k in (out.get("keywords") or []) if str(k).strip()]
        return {
            "intent": intent,
            "keywords": kws,
            "module": out.get("module") or "",
            "mentioned_keys": [k for k in (out.get("mentioned_keys") or []) if str(k).strip()],
            "sufficient": bool(out.get("sufficient")),
            "trace": note(state, self.name, f"의도={intent} 핵심어={', '.join(kws) or '없음'}"),
        }
