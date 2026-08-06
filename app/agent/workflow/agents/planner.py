"""Planner — 무엇을 원하는 요청인지 가른다. 그래프의 첫 분기가 여기서 정해진다.

"DL-118 어떻게 됐어?"와 "CDC 도입해야 해"는 들어가야 할 길이 완전히 다르다. 전자는 찾아서
답하면 끝이고, 후자는 조사→구체화→담당자→검증→생성까지 간다. 이걸 매번 전 경로로 태우면
느리고 비싸다.

**분류를 Structured Output 으로 받는다.** "이건 업무 계획 요청 같습니다"라는 자유 서술을 받아
정규식으로 긁으면, 모델이 말투를 바꾸는 날 조용히 오분류된다. enum 으로 강제하면 그럴 일이 없다.
"""

from __future__ import annotations

from app.agent.workflow.agents.base import StructuredAgent
from app.agent.prompts.roles import SYSTEM_PLANNER
from app.agent.workflow.prompts import persona
from app.agent.workflow.state import AgentState, Intent, Node, conversation, note

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [Intent.ASK, Intent.PLAN_WORK, Intent.REPORT_BUG, Intent.MY_DAY,
                     Intent.PROGRESS, Intent.ACTIVITY, Intent.MODIFY, Intent.CHITCHAT],
            "description": (
                "ask=이미 있는 것에 대해 물음(이력·경위) / "
                "plan_work=새 업무를 시작하려 함(티켓 트리까지) / "
                "report_bug=버그·장애를 발견했다고 알림(Bug 티켓 생성까지) / "
                "my_day=자기가 오늘/이번주 뭘 해야 하는지 물음 / "
                "progress=Epic·모듈·WBS 의 진척도/현황을 물음 / "
                "activity=특정 **사람**이 최근 무엇을 했는지 물음 / "
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
        return persona(state, SYSTEM_PLANNER)

    def task(self, state):
        # Few-shot — 경계가 애매한 갈래(ask↔progress↔activity, plan_work↔report_bug)를
        # 예시로 가른다. 규칙 문장보다 예시가 분류를 훨씬 안정시킨다(In-Context Learning).
        return f"""\
# 명령서
아래 대화에서 사용자가 원하는 것을 분류하고, 검색에 쓸 핵심어를 뽑아라.

## 제약조건
- 핵심어는 **검색용**이다. "해야 한다", "관련해서" 같은 말은 빼고 명사구만 남긴다.
- 티켓 키는 사용자가 실제로 적은 것만 옮긴다.
- 모듈은 확신이 있을 때만 고른다.

## 분류 예시
- "실시간 수집에 CDC를 도입해야 한다" → plan_work (새 일을 벌인다)
- "적재 배치가 어젯밤부터 계속 실패한다" → report_bug (깨진 것을 알린다)
- "DL-101 어떻게 진행되고 있어?" → progress (티켓·Epic 의 진척 상태)
- "ETL 모듈 진척률 알려줘" → progress
- "나 오늘 뭐 해야 하지?" → my_day (자기 할 일)
- "skcc.x1042 최근 3일간 뭐 했어?" → activity (**사람**의 활동)
- "CDC 검토가 왜 멈췄었지?" → ask (과거 경위를 묻는다 — 상태 숫자가 아니라 이야기)
- "지난 분기에 성능 관련해서 어떤 논의가 있었어?" → ask (★ progress 아님 — 진척률 숫자가
  아니라 **지나간 논의·기록**을 찾는 질문이다. "어디까지 왔어"만 progress 다)
- "DL-207 담당자를 x1103 으로 바꿔줘" → modify

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
