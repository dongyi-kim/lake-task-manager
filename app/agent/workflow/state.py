"""agent/workflow/state.py — 그래프가 주고받는 State 와 상수.

**State 는 명시적으로 적는다.** 노드가 무엇을 읽고 무엇을 쓰는지가 여기 다 드러나야, 나중에
"이 값이 언제 채워지지?"를 코드 전체를 뒤져 찾지 않는다.

**노드명·의도명은 상수 클래스로 모은다.** 문자열로 흩어 두면 오타가 런타임까지 살아남는다 —
`add_edge("historain", ...)` 는 문법 오류가 아니라 **조용히 안 이어진 그래프**가 된다.

State 는 대화 하나(=`thread_id`)의 수명을 갖는다. Checkpointer 가 턴 사이에 이걸 보관하므로,
사용자가 "그럼 마감은 다음 주로"라고만 말해도 이전 초안이 그대로 남아 있다.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from typing_extensions import TypedDict

from langgraph.graph.message import add_messages

from app.agent.workflow.contracts import ContinuationContract


class Node:
    """노드 이름. 그래프 조립과 UI 표시가 같은 문자열을 봐야 한다."""
    REQUEST_ARCHITECT = "request_architect"
    QUERY_SPECIALIST = "query_specialist"
    QUERY_RUNNER = "query_runner"
    RESEARCH_ANALYST = "research_analyst"
    KNOWLEDGE_CURATOR = "knowledge_curator"
    PORTFOLIO_ANALYST = "portfolio_analyst"
    WORK_ARCHITECT = "work_architect"
    PEOPLE_ADVISOR = "people_advisor"
    AUDITOR = "auditor"
    ACTION_EXECUTOR = "action_executor"
    RESULT_INTEGRATOR = "result_integrator"


class Intent:
    """RequestArchitect 가 고르는 갈래. 이 값이 그래프의 첫 분기를 결정한다.

    갈래를 늘릴 때는 **다른 길이 필요할 때만** 늘린다. "무엇을 조회하나"가 다를 뿐 길이
    같다면(조사→답변) 같은 의도로 묶고 도구 선택은 모델에게 맡긴다 — MY_DAY·PROGRESS·
    ACTIVITY 를 나눈 이유는 지나는 노드와 도구 묶음이 실제로 다르기 때문이다.

    ★ **버그 신고는 갈래가 아니다**(사용자 지적, 2026-08-10 — "결국 버그 신고도 Task
      생성 아니야? Task Type 이 Bug 일 뿐이지"). 예전에는 `report_bug` 가 따로 있었는데
      PLAN_WORK 와 지나는 노드도 도구도 같았고, 코드 전체에서 다르게 쓰이는 곳은 WorkArchitect 의
      goal 문자열 하나뿐이었다 — **갈래가 아니라 산출물 유형**이다.
      갈래로 두면 RequestArchitect 가 plan_work↔report_bug 경계를 맞춰야 하고(few-shot 까지 붙었다),
      틀리는 날 본문 템플릿이 통째로 바뀌었다. 지금은 **본문 규율을 요청의 낱말로** 고른다
      (WorkArchitect `_is_bug`) — 분류가 흔들려도 재현·기대·실제가 유지된다. 흐름 지시는
      `playbook="bug_report"` 가 그대로 나른다.
    """
    ASK = "ask"                # 그냥 물어본 것 — 찾아서 답하면 끝
    PLAN_WORK = "plan_work"    # "~~한 업무를 해야 한다" — 티켓 트리까지 간다(버그 신고 포함)
    MY_DAY = "my_day"          # "나 오늘 뭐 해야 할까" — 내 일감을 보고 우선순위를 제안
    PROGRESS = "progress"      # "~~ 진척도 확인" — Epic/모듈/WBS 진척률과 그 이유
    ACTIVITY = "activity"      # "A가 최근 뭐 했어?" — 타인 활동 조회(매니저 게이트는 도구가 건다)
    MODIFY = "modify"          # 기존 티켓의 속성을 바꿔 달라
    CHITCHAT = "chitchat"      # 업무 요청이 아님

    # 조사(research_analyst)가 필요한 갈래 / 티켓 초안까지 가는 갈래 — 라우터가 이 집합만 본다.
    NEEDS_RESEARCH = (ASK, PLAN_WORK, MODIFY)
    DRAFTS_TICKETS = (PLAN_WORK, MODIFY)
    DIRECT_ANSWER = (MY_DAY, PROGRESS, ACTIVITY)   # 검색 대신 PMO 도구로 바로 답한다


class Role:
    """사용자 역할. 같은 질문이라도 보고 싶은 답이 다르다.

    **사용자가 고르지 않는다** — LTM 은 이미 매니저를 알고 있다(`infra.settings.is_manager`).
    UI 에서 역할을 고르게 했더니 PM/모듈리더는 실질 구분이 없었고, 매니저 여부는 선택이
    아니라 **사실**이라 물어볼 이유가 없다(사용자 피드백). 세션 시작 때 코드가 판별한다.
    """
    MANAGER = "manager"        # 전체 진척·리스크·배분·팀 부하 (매니저 전용 도구 접근)
    MEMBER = "member"          # 내 일의 범위와 다음 행동


class Stage:
    """UI 가 진행 상황을 보여줄 때 쓰는 라벨(한국어)."""
    LABELS = {
        Node.REQUEST_ARCHITECT: "요청 파악",
        Node.QUERY_SPECIALIST: "조회 설계",
        Node.QUERY_RUNNER: "조회 실행",
        Node.RESEARCH_ANALYST: "과거 이력 조사",
        Node.WORK_ARCHITECT: "업무 구체화",
        Node.PEOPLE_ADVISOR: "담당자 검토",
        Node.AUDITOR: "규칙 검증",
        Node.ACTION_EXECUTOR: "티켓 생성",
        Node.RESULT_INTEGRATOR: "답변 정리",
        Node.KNOWLEDGE_CURATOR: "지식 정리",
        Node.PORTFOLIO_ANALYST: "현황 조회",
    }


MAX_REFINE_TURNS = 4       # 되묻기 상한. 넘으면 아는 것만으로 초안을 만든다
MAX_REVISIONS = 2          # Auditor↔WorkArchitect 왕복 상한. 안 걸면 무한 루프가 된다
MAX_MACHINE_REVISIONS = 2  # deterministic repair lane 상한 — semantic 예산과 분리한다
MAX_TOTAL_REPAIR_ATTEMPTS = MAX_REVISIONS + MAX_MACHINE_REVISIONS

TRACE_RESET = {"reset": True}    # 턴 시작에 trace 를 비우는 신호 — 리듀서엔 "대입"이 없다


def merge_trace(old: list | None, new: list | None) -> list:
    """trace 리듀서 — 병렬 노드가 같은 스텝에 써도 이어 붙는다. 델타(추가분)만 받는다."""
    if new and isinstance(new[0], dict) and new[0].get("reset"):
        return list(new[1:])
    return (old or []) + (new or [])


def last_value(old, new):
    """마지막 쓰기가 이긴다 — 단일 값 필드를 병렬 스텝에서도 쓸 수 있게 하는 리듀서."""
    return new


class MaterializedTicketSources(TypedDict, total=False):
    """Bounded verified Jira details that may safely cross an interview turn.

    Search result pages and execution artifacts are per-turn working data.  Work still needs
    the successfully opened ticket details—and the exact subset that were structural parent
    candidates—after a Research/term interview.  Keeping that small typed sidecar separate
    avoids carrying a complete JQL result or silently re-running retrieval.
    """

    ticketDetails: list[dict[str, Any]]
    parentCandidateKeys: list[str]
    parentCandidateSearchAttempted: bool


class RequestRefinement(TypedDict, total=False):
    """Fully parsed execution-field overlay for the current request turn.

    This sidecar is populated only by RequestArchitect's deterministic continuation lane.
    It is deliberately separate from ``request_plan``: the plan remains the authoritative
    outcome DAG, while these values say how WorkArchitect should place and constrain that
    already-known outcome.  ``parent`` is either an exact Jira key or the bounded sentinel
    ``select_existing``/``top_level``; ``phase`` is a normalized ordinal such as ``1차``;
    ``duedate`` is a validated ISO date.
    """

    parent: str
    phase: str
    duedate: str


def verified_parent_epic_candidates(state: "AgentState") -> list[dict[str, Any]]:
    """Return the successfully opened Epics authorized for automatic placement.

    A search hit is discovery, not write authority. Automatic parent selection therefore
    uses only the ordered intersection of the structural parent query and successful ticket
    detail materialization. Keeping this invariant in State gives routing, Work, and Audit
    one definition instead of three subtly different interpretations of the same ledger.
    """
    ledger = state.get("materialized_ticket_sources") or {}
    if not isinstance(ledger, dict):
        return []
    allowed: list[str] = []
    for value in ledger.get("parentCandidateKeys") or []:
        key = str(value or "").strip().upper()
        if key and key not in allowed:
            allowed.append(key)
    if not allowed:
        return []
    details: dict[str, dict[str, Any]] = {}
    for row in ledger.get("ticketDetails") or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        key = str(row.get("key") or "").strip().upper()
        issue_type = (row.get("fields") or {}).get("issuetype") or {}
        kind = str(
            row.get("type") or row.get("issuetype")
            or (issue_type.get("name") if isinstance(issue_type, dict) else issue_type)
            or ""
        ).strip().casefold()
        if key and kind == "epic":
            details[key] = row
    return [details[key] for key in allowed if key in details]


class AgentState(TypedDict, total=False):
    """대화 하나의 전부. `total=False` — 노드가 자기 몫만 채운다."""

    # ── 입력 ──
    messages: Annotated[list, add_messages]
    thread_id: str
    user_role: str                  # Role.*
    user_id: str
    user_identity: str              # "이름(사번), 모듈, 매니저 여부" — 세션 시작에 코드가 해석
    playbook: str                   # RequestArchitect 가 고른 표준 플레이북 id — 전 역할 프롬프트에 주입

    # ── RequestArchitect ──
    intent: str                     # Intent.*
    request_text: str               # ★ 생성 갈래의 **원 요청**(첫 요청 턴의 사용자 문장).
                                    #   last_user_text 는 후속 턴(질문 답변)에서 원 요청을 잃는다 —
                                    #   실측: 제목·본문의 주제가 Epic 본문 쪽으로 끌려갔다.
    keywords: list                  # 검색에 쓸 말들(원문 그대로가 아니라 뽑아낸 것)
    module: str                     # 짐작한 모듈. 확신 없으면 빈 문자열
    mentioned_keys: list            # 사용자가 직접 댄 티켓 키
    sufficient: bool                # 되묻지 않고 진행해도 되나
    answer_depth: str               # "brief"(값·결론만) | "explain"(개념·배경까지)
    request_plan: dict              # Request Architect의 원자 작업 DAG
    request_refinement: RequestRefinement  # 현재 턴의 검증된 parent/phase/duedate overlay
    continuation_contract: ContinuationContract  # 원 요청/effect/target + typed user decisions
    question_receipt_projection: dict  # Session-owned, non-secret one-turn answer projection
    turn_continuation: bool         # 직전 확인 질문에 대한 답변인가(새 요청의 stale state와 구분)
    turn_reset_reason: str          # local debug/evaluation용 턴 경계 판정 근거
    turn_attempt_id: str            # Session-owned per-turn nonce; typed receipt에는 digest만 저장

    # ── Query Specialist / deterministic Query Runner ──
    query_plan: dict
    query_results: list             # LLM에 전달할 compact 결과
    query_artifacts: dict           # 전체 target key snapshot 등 모델 밖 실행 자료
    materialized_ticket_sources: MaterializedTicketSources  # 인터뷰 턴을 건너는 검증된 티켓 ledger(최대 8)
    assignment_completion: dict     # 분담형 Task의 미완료 Sub-Task·담당자 deterministic 집계

    # ── ResearchAnalyst 사전 취합(코드가 만든 자료) ──
    # 선언이 없으면 LangGraph 가 반환값에서 이 키들을 버린다 — 실제로 그래서 KnowledgeCurator 의
    # "사전 조사" 블록이 늘 비어 있었다(노드 안 지역 사본으로만 존재했다).
    pre_survey: str                 # 키워드·의미 검색 결과
    seed_map: str                   # 언급된 티켓 주변 지도(계보·링크·참여자)
    web_context: str                # 웹·GitHub 조사 결과
    topic_dossier: str              # 주제 한 건(테이블·기술·업무)에 얽힌 조각 전부

    # ── ResearchAnalyst ──
    bulk_targets: list              # 조건 일괄 수정 대상(코드 JQL 조회 — "전부" 요청의 집합)
    situation: str                  # "현재 상황" 서술 — 사용자에게 그대로 보인다
    evidence: list                  # [{key,title,why,url,observations:[{source,text}]}], 소스당 한 항목
    related_docs: list              # [{"title","url"}]
    epic_candidate: str             # 붙일 만한 상위 Epic
    already_exists: bool            # 사실상 같은 일을 하는 티켓이 이미 있다 — 새로 만들지 말라는 신호

    # ── PMO (my_day/progress/activity 직행) ──
    pmo_findings: list              # [{"key","point","action"}] 조회에서 확인한 사실
    group_activity: str             # PMO — 로스터 전원 활동 사전 취합(그룹 질의의 3층 자료)
    ticket_progress: str            # PMO — 티켓 한 건의 진척 근거 4갈래 사전 취합
    portfolio_snapshot: dict        # Portfolio의 bounded raw progress/activity 정본
    person_work_snapshot: dict      # 특정 사람의 현재 미완료 할당 티켓 결정적 조회
    daily_priority_snapshot: dict   # 본인 열린 업무의 deadline+priority 결정적 1순위
    knowledge_brief: dict           # KnowledgeCurator — {concepts, our_context, references, gaps}
    pmo_caution: str                # 읽을 때의 주의(활동 적음 ≠ 태만 등)

    # ── WorkArchitect ──
    interpretation: str             # 조사 전 해석 확인 턴 — "제가 이해한 바" (사용자 검증용)
    # ── 구조 합의 단계(사용자 요청) ─────────────────────────────────────
    # 복합 산출물(여러 Task, Task+Sub-Task)을 **본문까지 다 써서** 한 번에 내밀면, 구조가
    # 틀렸을 때 사용자가 고칠 것이 너무 많다 — 티켓 넷의 배경·범위·DoD 를 다 읽고 나서야
    # "2번은 1번에 합쳐야지"를 말하게 된다. 그래서 **뼈대 먼저 합의하고 살은 나중에** 붙인다.
    #   structure_plan  : 합의 중/합의된 뼈대 [{"summary","children":[제목…]}]
    #   structure_ok    : 사용자가 이 뼈대로 가자고 한 순간 True — 그때부터 본문을 쓴다
    #   structure_notes : 뼈대에 대해 사용자가 준 피드백의 **누적**. 매 왕복마다 쌓는다 —
    #                     "3번 빼줘" 다음 턴에 "1번을 둘로" 라고 하면 둘 다 반영해야 한다
    #                     (앞의 말을 잊으면 사용자는 같은 말을 반복하게 된다)
    structure_plan: list
    structure_ok: bool
    structure_notes: list
    questions: list                 # 사용자에게 되물을 것(비면 진행)
    draft: dict                     # {"mode": "task"|"subtask", "items": [...]}  (생성 갈래)
    change_plan: dict               # {"key","changes":{...},"why"}  (modify 갈래 — 기존 티켓 변경)
    turns: int

    # ── PeopleAdvisor ──
    assignments: list               # [{"index","user","reasons":[...],"alternates":[...]}]

    # ── Auditor ──
    review: dict                    # {"ok","errors","warnings","critique"}
    revisions: int
    repair_budget: dict             # typed-repair-budget.v1 semantic/machine/total sidecar

    # ── ActionExecutor (승인 후) ──
    approval_token: str
    comment_token: str              # 변경과 함께 남길 코멘트의 승인 토큰(카드에 코멘트가 보였을 때만)
    result: dict                    # {"created":[...], "failed":[...]}
    execution_receipt: dict         # signed execution-receipt.v1; never exposed as UI result data

    # ── 공통 ──
    reply: str                      # 사용자에게 보일 최종 문장
    trace: Annotated[list, merge_trace]   # [{"node","label","note"}] — 누가 무엇을 했나
    error: Annotated[str, last_value]     # 병렬 노드 둘이 같은 스텝에 실패해도 충돌하지 않게


def note(state: AgentState, node: str, text: str) -> list:
    """trace 한 줄 — **추가분만** 반환한다. 이어 붙이는 것은 `merge_trace` 리듀서의 몫이다.

    노드가 `이전 전체 + 새 줄` 을 반환하던 방식은 병렬 fan-out(PeopleAdvisor∥Auditor)에서
    같은 스텝에 두 노드가 trace 를 쓰는 순간 충돌한다 — 리듀서 + 델타 반환으로 바꿨다.
    (state 인자는 호출부 호환용으로 남겼다.)"""
    return [{"node": node, "label": Stage.LABELS.get(node, node), "note": text}]


def last_user_text(state: AgentState) -> str:
    for m in reversed(state.get("messages") or []):
        if getattr(m, "type", "") == "human":
            return str(getattr(m, "content", "") or "")
    return ""


def request_text(state: AgentState) -> str:
    """생성 갈래의 **원 요청** — 없으면 마지막 발화로 폴백.

    질문에 답하는 후속 턴에서 last_user_text 를 '원문 요청'이라고 프롬프트에 실으면
    원 요청("starrocks puffin ndv …")이 사라지고 답변("기한은 9/30")만 남는다 — 그 틈에
    Epic 본문의 주제가 제목·본문을 잠식했다(실측). RequestArchitect 가 첫 요청 턴에 고정해 둔다."""
    return (state.get("request_text") or "").strip() or last_user_text(state)


def is_memory_only_request(state: AgentState) -> bool:
    """Whether the user explicitly asked for acknowledgement without action or research.

    Keep this deliberately narrow.  A message that both shares context and asks for work
    must continue through the normal workflow; only an explicit "not now, just remember"
    instruction qualifies.
    """
    text = last_user_text(state).strip()
    return bool(re.search(
        r"(?:지금은|당장은|우선은)[^.!?\n]{0,40}"
        r"(?:답하지\s*말|조사하지\s*말|처리하지\s*말)[^.!?\n]{0,40}"
        r"(?:이\s*)?(?:정보|내용)만\s*(?:기억|참고)",
        text,
    ))


def conversation(state: AgentState, limit: int = 12) -> str:
    """최근 대화를 프롬프트에 실을 수 있는 형태로. 되묻기 맥락이 여기서 온다."""
    rows: list[str] = []
    for m in (state.get("messages") or [])[-limit:]:
        who = {"human": "사용자", "ai": "에이전트"}.get(getattr(m, "type", ""), "")
        body = str(getattr(m, "content", "") or "").strip()
        if who and body:
            rows.append(f"{who}: {body}")
    return "\n".join(rows)


_BUG_WORDS = re.compile(r"버그|bug|장애|오류|에러|안\s*(?:떠|나와|돼|된다)|"
                        r"실패한다|깨졌|먹통|안\s*먹|"
                        # 낱말이 없어도 증상 서술이면 버그다 — "화면이 빈다"에는 위의 어떤
                        # 말도 없다(실측: 사용자 관점 리뷰 F5 의 원문). 다만 넓히면 평범한
                        # 요청이 버그가 되므로 **증상으로만 쓰이는 꼴**만 더한다.
                        r"화면이\s*(?:비|빈)|빈\s*화면|안\s*보(?:인다|여|임)|"
                        r"재현\s*(?:경로|되|됩)")


def reads_as_bug(text: str) -> bool:
    """이 요청이 '뭔가 깨졌다'는 말인가 — **갈래가 아니라 낱말로** 판정한다.

    `report_bug` 갈래를 걷어내면서(§7 16-b) 판정을 여기로 모았다. 정의가 두 벌이면
    한쪽만 고쳐지는 날이 오고, 그날 라우팅과 본문 규율이 서로 다른 말을 한다 —
    WorkArchitect(본문에 재현·기대·실제를 넣을 것인가)와 그래프(해석 확인을 앞세울 것인가)가
    **같은 문장을 보고 같은 답**을 해야 한다.
    """
    return bool(_BUG_WORDS.search(text or ""))


def as_dict(state: AgentState) -> dict[str, Any]:
    """UI/SSE 로 내보낼 수 있게 정리. messages 는 뺀다(따로 스트리밍한다)."""
    return {k: v for k, v in dict(state).items() if k != "messages"}
