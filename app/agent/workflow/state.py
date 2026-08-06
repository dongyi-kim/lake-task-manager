"""agent/workflow/state.py — 그래프가 주고받는 State 와 상수.

**State 는 명시적으로 적는다.** 노드가 무엇을 읽고 무엇을 쓰는지가 여기 다 드러나야, 나중에
"이 값이 언제 채워지지?"를 코드 전체를 뒤져 찾지 않는다.

**노드명·의도명은 상수 클래스로 모은다.** 문자열로 흩어 두면 오타가 런타임까지 살아남는다 —
`add_edge("historain", ...)` 는 문법 오류가 아니라 **조용히 안 이어진 그래프**가 된다.

State 는 대화 하나(=`thread_id`)의 수명을 갖는다. Checkpointer 가 턴 사이에 이걸 보관하므로,
사용자가 "그럼 마감은 다음 주로"라고만 말해도 이전 초안이 그대로 남아 있다.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class Node:
    """노드 이름. 그래프 조립과 UI 표시가 같은 문자열을 봐야 한다."""
    PLANNER = "planner"
    HISTORIAN = "historian"
    REFINER = "refiner"
    ASSIGNER = "assigner"
    REVIEWER = "reviewer"
    OPERATOR = "operator"
    RESPONDER = "responder"


class Intent:
    """Planner 가 고르는 갈래. 이 값이 그래프의 첫 분기를 결정한다."""
    ASK = "ask"                # 그냥 물어본 것 — 찾아서 답하면 끝
    PLAN_WORK = "plan_work"    # "~~한 업무를 해야 한다" — 티켓 트리까지 간다
    MODIFY = "modify"          # 기존 티켓의 속성을 바꿔 달라
    CHITCHAT = "chitchat"      # 업무 요청이 아님


class Role:
    """사용자 역할. 같은 질문이라도 보고 싶은 답이 다르다."""
    PM = "pm"                  # 전체 진척·리스크·일정
    LEAD = "lead"              # 모듈 배분·담당자·워크로드
    MEMBER = "member"          # 내 일의 범위와 다음 행동


class Stage:
    """UI 가 진행 상황을 보여줄 때 쓰는 라벨(한국어)."""
    LABELS = {
        Node.PLANNER: "요청 파악",
        Node.HISTORIAN: "과거 이력 조사",
        Node.REFINER: "업무 구체화",
        Node.ASSIGNER: "담당자 검토",
        Node.REVIEWER: "규칙 검증",
        Node.OPERATOR: "티켓 생성",
        Node.RESPONDER: "답변 정리",
    }


MAX_REFINE_TURNS = 4       # 되묻기 상한. 넘으면 아는 것만으로 초안을 만든다
MAX_REVISIONS = 2          # Reviewer↔Refiner 왕복 상한. 안 걸면 무한 루프가 된다


class AgentState(TypedDict, total=False):
    """대화 하나의 전부. `total=False` — 노드가 자기 몫만 채운다."""

    # ── 입력 ──
    messages: Annotated[list, add_messages]
    thread_id: str
    user_role: str                  # Role.*
    user_id: str

    # ── Planner ──
    intent: str                     # Intent.*
    keywords: list                  # 검색에 쓸 말들(원문 그대로가 아니라 뽑아낸 것)
    module: str                     # 짐작한 모듈. 확신 없으면 빈 문자열
    mentioned_keys: list            # 사용자가 직접 댄 티켓 키
    sufficient: bool                # 되묻지 않고 진행해도 되나

    # ── Historian ──
    situation: str                  # "현재 상황" 서술 — 사용자에게 그대로 보인다
    evidence: list                  # [{"key","title","why"}] 근거. 출처 없는 서술은 금지
    related_docs: list              # [{"title","url"}]
    epic_candidate: str             # 붙일 만한 상위 Epic
    already_exists: bool            # 사실상 같은 일을 하는 티켓이 이미 있다 — 새로 만들지 말라는 신호

    # ── Refiner ──
    questions: list                 # 사용자에게 되물을 것(비면 진행)
    draft: dict                     # {"mode": "task"|"subtask", "items": [...]}
    turns: int

    # ── Assigner ──
    assignments: list               # [{"index","user","reasons":[...],"alternates":[...]}]

    # ── Reviewer ──
    review: dict                    # {"ok","errors","warnings","critique"}
    revisions: int

    # ── Operator (승인 후) ──
    approval_token: str
    result: dict                    # {"created":[...], "failed":[...]}

    # ── 공통 ──
    reply: str                      # 사용자에게 보일 최종 문장
    trace: list                     # [{"node","label","note"}] — 어느 에이전트가 무엇을 했나
    error: str


def note(state: AgentState, node: str, text: str) -> list:
    """trace 한 줄 추가. UI 가 "지금 무엇을 하는 중"을 보여주는 근거이자 디버깅 로그다."""
    return (state.get("trace") or []) + [
        {"node": node, "label": Stage.LABELS.get(node, node), "note": text}]


def last_user_text(state: AgentState) -> str:
    for m in reversed(state.get("messages") or []):
        if getattr(m, "type", "") == "human":
            return str(getattr(m, "content", "") or "")
    return ""


def conversation(state: AgentState, limit: int = 12) -> str:
    """최근 대화를 프롬프트에 실을 수 있는 형태로. 되묻기 맥락이 여기서 온다."""
    rows: list[str] = []
    for m in (state.get("messages") or [])[-limit:]:
        who = {"human": "사용자", "ai": "에이전트"}.get(getattr(m, "type", ""), "")
        body = str(getattr(m, "content", "") or "").strip()
        if who and body:
            rows.append(f"{who}: {body}")
    return "\n".join(rows)


def as_dict(state: AgentState) -> dict[str, Any]:
    """UI/SSE 로 내보낼 수 있게 정리. messages 는 뺀다(따로 스트리밍한다)."""
    return {k: v for k, v in dict(state).items() if k != "messages"}
