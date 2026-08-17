"""agent/workflow/graph.py — 역할 manifest의 graph runtime을 잇는다.

```
START ─> request_architect ─┬─ chitchat ─────────────────────────────────────────> result_integrator ─> END
                  ├─ my_day/progress/activity ──> portfolio_analyst ───> result_integrator
                  └─> research_analyst ─┬─ ask ────────────────────────────────> result_integrator
                                 └─> work_architect ─┬─ 질문 있음 ──────────────> result_integrator
                                        ▲     ├─> people_advisor ─┐ (병렬)
                                        │     └─> auditor ─┴> merge(join)
                                        │                        │
                                        └─ 재작성(≤2회) ──────────┤
                                                                 └─ 통과/한계 ─> propose
                                                                                    │
                                                                                    ▼
                                                                               result_integrator
                                                                                    │
                                              ┌─── 승인 대기 (interrupt_before) ─────┘
                                              ▼
                                          action_executor ─> result_integrator ─> END
```

## 왜 이 모양인가

**ResearchAnalyst 을 건너뛰지 않는다**(chitchat 빼고). "CDC 도입해야 해"에 바로 티켓을 만들어 주는
어시스턴트는 중복 티켓 생성기다. 조사가 이 서비스의 값어치다.

**WorkArchitect ↔ Auditor 왕복에 상한을 둔다**(`MAX_REVISIONS`). 안 걸면 모델이 서로 만족하지 못해
무한히 돈다. 상한을 넘으면 **미해결 문제를 그대로 안고** 사용자에게 간다 — 조용히 통과시키는
것보다 "이건 못 고쳤습니다"가 낫다.

**ActionExecutor 앞에서 멈춘다**(`interrupt_before`). 이게 HITL 의 흐름 쪽 절반이다. 나머지 절반인
내용 보증은 승인 토큰이 한다(`agent/approval.py`) — 흐름은 코드 실수로 우회될 수 있지만
토큰은 내용 해시에 묶여 있어 못 우회한다.

**Checkpointer 를 붙인다**(`thread_id`). 이게 없으면 되묻기가 불가능하다 — 사용자가 "범위는
수집까지야"라고만 답했을 때 앞의 조사 결과가 남아 있어야 한다. `MemorySaver` 를 쓰는 이유는
LTM 이 사용자 PC 에서 도는 단일 프로세스 앱이고, 앱을 껐다 켜면 대화가 사라지는 게 맞기
때문이다(진행 중인 승인이 재시작 뒤에도 살아 있으면 그게 더 위험하다).
"""

from __future__ import annotations

import re

from langgraph.graph import END, START, StateGraph

from app.agent.workflow.agents.people_advisor import (
    PeopleAdvisor,
    _normalize_resolved_assignment_rationale,
    merge_assignments,
)
from app.agent.workflow.agents.knowledge_curator import KnowledgeCurator
from app.agent.workflow.agents.research_analyst import ResearchAnalyst
from app.agent.workflow.agents.action_executor import ActionExecutor
from app.agent.workflow.agents.request_architect import RequestArchitect
from app.agent.workflow.agents.portfolio_analyst import PortfolioAnalyst
from app.agent.workflow.agents.query_runner import QueryRunner
from app.agent.workflow.agents.query_specialist import QuerySpecialist
from app.agent.workflow.agents.work_architect import WorkArchitect
from app.agent.workflow.agents.result_integrator import ResultIntegrator
from app.agent.workflow.agents.auditor import Auditor
from app.agent.workflow.state import (MAX_REVISIONS, AgentState, Intent, Node,
                                      is_memory_only_request, reads_as_bug, request_text)

_compiled = {"graph": None}


def _node(agent):
    """붙는 것은 언제나 **함수**다 — 컴파일된 서브그래프를 그대로 노드로 쓰면 전체 State 가
    반환값이 되어 부모의 `add_messages` 리듀서에 통째로 다시 먹힌다."""
    return agent.node()


# ── 라우터: State 만 보고 결정한다(부작용 없음) ──────────────────────
def route_after_request_architect(state: AgentState) -> str:
    """세 갈래 — 조사(research_analyst) / 현황 직행(portfolio_analyst) / 답(result_integrator).

    my_day·progress·activity 를 ResearchAnalyst 에 태우지 않는 이유: 그 요청들은 과거 발굴이 아니라
    **지금 상태의 집계**다. 검색-열람-링크추적은 낭비이고, 느린 데다 답도 더 나빠진다.
"""
    intent = state.get("intent") or ""
    if intent == Intent.CHITCHAT:
        return "respond"
    # "지금은 답하지 말고 이 정보만 기억"은 조회 요청이 아니다.  검색·웹 조사를
    # 시작하면 사용자가 명시적으로 금지한 일을 수행하고, 약어를 외부 동명이의어와 섞으며,
    # 후속 요청에 쓸 필요도 없는 토큰을 소비한다. 대화 메시지는 checkpointer가 보존한다.
    if is_memory_only_request(state):
        return "respond"
    # RequestArchitect can settle a cheap structure choice before any Jira/web lookup. Questions are a
    # terminal result for this turn; sending them through research wastes calls and cannot improve them.
    if state.get("questions"):
        return "respond"
    # 회의록의 사람·로컬 약어는 먼저 관련 자료를 찾고, 그래도 확정되지 않을 때 인터뷰한다.
    # 정확한 티켓 변경 요청이라도 이 검증 전에는 WorkArchitect로 단축하지 않는다.
    from app.agent.workflow.meeting_context import needs_research_interview
    if needs_research_interview(state):
        return "investigate"
    # 직전 조사 후의 인터뷰 답변은 같은 자료를 다시 검색하지 않는다. 조회/웹 결과를 보존한
    # 채 지식 정리 또는 초안 작성으로 이어서 호출 수와 stale 검색 위험을 함께 줄인다.
    if state.get("turn_continuation") and (state.get("situation") or "").strip():
        if intent == Intent.ASK:
            return "curate"
    # 분담형 Task의 미완료자 조회는 Query Specialist의 자유 JQL이나 Portfolio ReAct가
    # 아니라 parent 탐색→직계 Sub-Task 전수 집계라는 고정 join이다.
    from app.agent.workflow.assignment_completion import asks_incomplete_assignees
    from app.agent.workflow.state import last_user_text
    if asks_incomplete_assignees(last_user_text(state)):
        return Node.QUERY_RUNNER
    # ── 빠른 경로 2종 (턴 시간의 최대 낭비 제거) ──────────────────
    # ① 후속 턴: 같은 대화에서 조사 결과(situation)가 이미 있으면 재조사하지 않는다 —
    #    인터뷰 답변 턴마다 ResearchAnalyst 이 통째로 다시 돌던 것이 실측 낭비의 최대 항목
    #    (턴당 LLM 3~5회). WorkArchitect 가 쓸 배치·규칙 재료는 코드가 그 자리에서 다시 모은다.
    if intent in Intent.DRAFTS_TICKETS and (state.get("situation") or "").strip() \
            and (state.get("turns") or 0) > 0:
        return "refine"
    # ② modify + 티켓 키 명시: 키 실재·현재 값 확인은 WorkArchitect 의 get_ticket 이 한다 —
    #    변경 요청에 과거 이력 발굴은 대개 불필요하다.
    if intent == Intent.MODIFY and state.get("mentioned_keys"):
        return "refine"
    # ②-b 승인 대기 초안에 대한 피드백("본문에 롤백 계획도 추가해줘")은 기존 티켓 수정이
    #    아니라 **초안 수정**이다 — 실측: 엉뚱한 기존 티켓(DL-5106)의 변경 계획이 나왔다.
    if (intent == Intent.MODIFY and not state.get("mentioned_keys")
            and (state.get("draft") or {}).get("items")):
        return "refine"    # approval_token 은 턴마다 리셋되므로 조건에 못 쓴다
    # ③ 해석 확인 선행 — 막연한 신규 개발 요청은 **조사보다 사용자 확인이 먼저**다.
    #    실측(STARR NDV): 혼자 오래 조사하고 한 번에 결론을 내니 방향이 틀렸다. 조사 전에
    #    해석·범위를 2~3문항으로 확인받으면 조사가 짧고 정확해진다(사용자 피드백: 일방적
    #    호흡이 길다). 위임("알아서")이면 묻지 않는 기존 원칙이 이긴다.
    #    ★ **버그 신고는 여기 해당 없다** — "2홉 이상 펼치면 화면이 빈다"는 막연한 것이
    #    아니라 증상이 이미 문장에 있다. 물을 것은 해석이 아니라 재현 경로이고, 그 전에
    #    **같은 증상의 Bug 가 이미 열려 있는지**를 봐야 한다(중복 티켓). 갈래를 걷어내며
    #    이 자리가 조용히 넓어졌던 것을 낱말 판정으로 되돌린다(§7 16-b).
    if intent == Intent.PLAN_WORK and not state.get("sufficient") \
            and not (state.get("situation") or "").strip() \
            and not (state.get("turns") or 0) \
            and not reads_as_bug(request_text(state)):
        from app.agent.workflow.agents.work_architect import _said_defaults
        if not _said_defaults(state):
            return "refine"
    if intent in Intent.DIRECT_ANSWER:
        # 데이터 자산 질문("fdc.fdc_trace_summary_ic 적재주기는?")이 progress/activity 로
        # 오분류되면 회복이 불가능하다 — portfolio_analyst에는 검색 도구가 없어서 테이블 이름을
        # 어디서도 못 찾는다. 분류 실수를 프롬프트로 줄이는 것과 별개로, 코드가 막는다.
        from app.agent.tools._ident import find_identifiers
        from app.agent.workflow.state import last_user_text
        if not state.get("mentioned_keys") and find_identifiers(last_user_text(state)):
            return "investigate"
        return Node.PORTFOLIO_ANALYST
    return "investigate"


def route_after_query_runner(state: AgentState) -> str:
    """결정적 집계만으로 답이 완성된 요청은 일반 Research ReAct를 건너뛴다."""
    completion = state.get("assignment_completion") or {}
    return "respond" if completion.get("kind") == "incomplete_assignees" else "investigate"


def route_after_research_analyst(state: AgentState) -> str:
    """조사만 하면 되는 요청은 여기서 끝낸다 — 티켓 초안까지 갈 이유가 없다.

    지식형 질문("X가 뭐야", "정리해줘")은 KnowledgeCurator 를 거친다 — 조사 결과를 개념/우리 상황/
    참고/공백 스키마로 정리해야 답이 재사용 가능한 브리프가 된다(사용자 요청으로 신설).
    """
    intent = state.get("intent") or ""
    # 조사 단계가 확인 질문을 냈다(표기 후보 등) — 정리(curate)로 갈 것이 없다. 바로 묻는다.
    if state.get("questions"):
        return "respond"
    if intent in Intent.DRAFTS_TICKETS:
        return "refine"
    if intent == Intent.ASK:
        from app.agent.workflow.state import last_user_text
        asked = last_user_text(state)
        # 사람 판단·추천이 낀 질문("누가 하면 좋을지", "맡겨도 될까")은 curate 로 보내지
        # 않는다 — KnowledgeCurator 스키마엔 사람이 없어서 개념 강의로 새 버린다(실측).
        people_ish = any(w in asked for w in ("누가", "누구에게", "맡", "적절", "추천",
                                              "관련자", "유관자", "활동"))
        if people_ish:
            return "respond"
        # 데이터 자산 질문은 키워드 목록에 안 걸린다("적재주기는?"). 식별자로 판정한다 —
        # 조각을 모은 답일수록 개념/우리 상황/출처/공백 구조로 정리돼야 쓸모가 있고,
        # 특히 **무엇을 확인 못 했는지(gaps)** 가 이 질문 유형의 값어치다.
        from app.agent.tools._ident import find_identifiers
        if find_identifiers(asked):
            return "curate"
        if any(w in asked for w in ("뭐야", "무엇", "뭔지", "정리", "설명",
                                    "알려줘", "지식", "개념", "어떻게 쓰")):
            return "curate"
    return "respond"


def route_after_work_architect(state: AgentState):
    """되물을 게 있으면 사용자에게 돌아간다. 초안이 섰으면 담당자 제안과 규칙 검증을
    **동시에** 돌린다(fan-out) — 둘은 서로의 출력을 읽지 않는다(PeopleAdvisor 는 초안에 사람을
    제안하고, Auditor 는 초안 자체를 검열한다). 직렬이던 것을 병렬로 바꿔 생성 플로우의
    한 스텝을 통째로 접었다. 배정 사용자 실재 확인은 join(merge_assignments)의 코드가 한다.

    **변경 계획(modify)은 승인으로 직행**한다 — 담당자 추천(새 티켓용)도, validate_bulk
    (생성 검증)도 여기엔 해당이 없다. 안전장치는 승인 카드와 editmeta(편집 불가 필드 거부)다.
    """
    if state.get("questions"):
        return "respond"
    cp = state.get("change_plan") or {}
    if cp.get("key") or cp.get("keys"):
        return "propose"
    if not (state.get("draft") or {}).get("items"):
        return "respond"
    # Cloud APIs benefit from graph fan-out. A single-device local server can instead make
    # two long generations contend for the same queue, so the model profile owns this
    # scheduling capability. Roles remain model/provider agnostic.
    if not _parallel_role_calls_allowed():
        return "sequential"
    return ["assign", "review"]


def _parallel_role_calls_allowed() -> bool:
    """Whether the active complex model can efficiently serve concurrent Role requests."""
    try:
        from app.agent import config as agent_config
        from app.agent import model_profiles
        definition = agent_config.chat_definition("complex")
        capabilities = model_profiles.capabilities_for(
            definition.model, definition.model_profile,
        )
        return capabilities.get("parallel_role_calls") is not False
    except Exception:
        # Unknown/legacy profiles retain the established cloud-friendly fan-out behavior.
        return True


def route_after_auditor(state: AgentState) -> str:
    """통과하면 승인 요청으로, 아니면 다시 쓰게 한다 — 단, 왕복 상한까지만.

    상한 소진 뒤의 갈래가 중요하다:
      · **기계 검증 오류**(없는 부모·틀린 타입)가 남았으면 → respond. 만들어 봤자 Jira 가
        거부하므로 승인 카드를 줄 이유가 없다.
      · **의미 검열 문제**가 남았으면 → respond. 권위 상태와 모순되는 오판은 Auditor가
        먼저 제거하므로, 여기 남은 blocking problem과 실행 가능한 승인 카드를 동시에
        노출하지 않는다.
    """
    review = state.get("review") or {}
    if review.get("ok"):
        return "propose"
    # A genuine blocking problem gets one bounded repair opportunity. Contradicted model
    # findings were already removed by Auditor grounding, so paying this retry only happens
    # for an actionable defect. Exhaustion still fails closed with no pending payload.
    if (review.get("errors") or review.get("problems")) \
            and (state.get("revisions") or 0) < MAX_REVISIONS:
        return "revise"
    return "respond"


def route_after_result_integrator(state: AgentState) -> str:
    """말을 끝냈다. 승인받을 초안이 있으면 **여기서 멈춘다**(interrupt_before=action_executor).

    이미 실행한 뒤(result 가 있음)라면 끝이다 — 안 그러면 result_integrator↔action_executor 를 맴돈다.
    """
    if state.get("result"):
        return "end"
    return "execute" if state.get("approval_token") else "end"


def _merge_assignments(state: AgentState) -> dict:
    """담당자 제안을 초안에 실제로 꽂는다(fan-out 의 join). 근거 없는 제안은 반영되지 않는다.

    별도 노드로 둔 이유 — PeopleAdvisor 는 '제안'을 만들고, 초안을 고치는 것은 다른 일이다.
    한 노드에서 둘 다 하면 "근거 없는 건 반영 안 한다"는 규칙이 프롬프트 안에 묻힌다.

    Auditor 가 배정 **전** 초안을 검증하므로(병렬), 배정된 사용자가 실재하는지는
    여기 코드가 보장한다 — validate_bulk 와 같은 lookup 을 쓴다.
    """
    draft = merge_assignments(state.get("draft"), state.get("assignments"))
    # 사용자 명시 배정은 추천보다 우선이다. fan-out join에서 PeopleAdvisor 제안을 합친 **뒤**
    # 원 발화로 다시 확정해, 병렬 상태 병합이 assignee_source 표식을 잃어도 지정값이
    # 추천값으로 바뀌지 않게 한다(PAR1 실측).
    from app.agent.workflow.agents.work_architect import _apply_named_assignees
    _apply_named_assignees(state, draft.get("items") or [])
    # ★ 분량 분할은 골고루 — **배정이 바뀐 뒤 한 번 더** 본다. 이 규칙은 WorkArchitect 에서만
    #   돌았는데, 자식 담당의 주인이 PeopleAdvisor 로 옮겨 가면서(§5-c) 덮어쓰기 뒤편에 남았다:
    #   실측(생성 스위트 STR1) 테이블 29건이 WorkArchitect 에서 고루 나뉜 뒤 제안으로 전부 한
    #   사람에게 갔다. 규칙은 work_architect.spread_volume_split 한 벌이고, 부르는 자리가 둘이다.
    from app.agent.workflow.agents.work_architect import (
        _preserve_required_user_anchors,
        spread_volume_split,
    )
    spread_volume_split(draft.get("items") or [])
    try:
        from app.agent.tools._ctx import client
        exists = client().bulk_lookup().user_exists
        for it in draft.get("items") or []:
            u = (it.get("assignee") or "").strip()
            if u and not exists(u):
                it["assignee"] = _resolve_user(u, exists)
    except Exception:
        pass                              # lookup 실패가 초안 자체를 버리게 하면 안 된다
    # WorkArchitect and PeopleAdvisor run as a fan-out.  Their join may retain assignment
    # metadata from the newer branch while taking a stale pre-normalization title/body from
    # the other.  Re-seal immutable user anchors at the actual pending-payload boundary.
    _preserve_required_user_anchors(state, draft.get("items") or [])
    # People Advisor runs beside Work Architect. At this final join the assignee fields are
    # authoritative, so pre-advisor prose such as "담당자는 미정" must not survive into the
    # approval reply or staged payload rationale.
    draft = _normalize_resolved_assignment_rationale(draft)
    assignments = _align_assignments_to_draft(state.get("assignments") or [], draft)
    return {"draft": draft, "assignments": assignments}


def _align_assignments_to_draft(assignments: list, draft: dict) -> list:
    """ResultIntegrator가 과거 추천값이 아니라 최종 승인 payload의 담당과 근거를 보게 한다."""
    rows = [dict(a) for a in (assignments or []) if isinstance(a, dict)]
    by_index = {a.get("index"): a for a in rows if isinstance(a.get("index"), int)}
    for index, item in enumerate((draft or {}).get("items") or []):
        row = by_index.get(index)
        if row is None:
            continue
        actual = str(item.get("assignee") or "")
        if actual != str(row.get("user") or ""):
            row["user"] = actual
            row["reasons"] = (["사용자 지정 또는 승인 payload에 확정된 담당자"]
                              if actual else [])
        row["alternates"] = [a for a in (row.get("alternates") or [])
                             if isinstance(a, dict)
                             and str(a.get("user") or "") != actual]
        child_rows = {c.get("index"): dict(c) for c in (row.get("children") or [])
                      if isinstance(c, dict) and isinstance(c.get("index"), int)}
        for child_index, child in enumerate(item.get("children") or []):
            if not isinstance(child, dict):
                continue
            actual_child = str(child.get("assignee") or "")
            child_row = child_rows.get(child_index)
            if child_row is not None and actual_child \
                    and actual_child != str(child_row.get("user") or ""):
                child_row["user"] = actual_child
                child_row["why"] = "사용자 지정 또는 승인 payload에 확정된 담당자"
                child_rows[child_index] = child_row
        if child_rows:
            row["children"] = [child_rows[k] for k in sorted(child_rows)]
    return rows


def _resolve_user(u: str, exists) -> str:
    """접미만 온 아이디("x1103")를 로스터 유일 일치로 풀 아이디(skcc.x1103)로 해소한다.

    사용자는 흔히 접미만 댄다. 직렬 시절엔 Auditor 의 validate_bulk 가 이를 기계 오류로
    잡아 재작성 루프에서 교정됐지만, 병렬화로 Auditor 가 배정 전 초안만 보게 되면서 그
    경로가 사라졌다(실측: 벌크 배정 3건이 통째로 빠짐). 해소 불가면 빈 값 — 유령 배정은
    생성 단계에서 어차피 거부되고, 승인 화면에서 사용자가 채울 수 있다.
    """
    try:
        from app.infra.settings import load_people
        ids = {str(i) for ids in (load_people() or {}).values() for i in ids or []}
        hits = [i for i in ids if i.endswith("." + u)]
        if len(hits) == 1 and exists(hits[0]):
            return hits[0]
    except Exception:
        pass
    return ""


def _propose(state: AgentState) -> dict:
    """승인 토큰을 발급한다 — 화면이 보여 줄 초안과 **같은 내용에 묶인** 토큰이다.

    이 노드가 따로 있는 이유: 토큰 발급은 라우터가 할 수 없고(라우터는 부작용이 없어야 한다),
    Auditor 가 할 일도 아니다(검열자가 실행 허가를 내주면 검열이 아니다).
    """
    from app.agent import approval
    from app.agent.workflow.agents.work_architect import as_bulk_items

    # Defense in depth: routing normally prevents this node from seeing a failed review,
    # but stale checkpoints and direct callers must not turn a rejected draft into a live
    # approval token. Empty strings also clear any token retained from an earlier state.
    if (state.get("review") or {}).get("ok") is False:
        return {"approval_token": "", "comment_token": ""}

    tid = state.get("thread_id") or ""

    # modify 갈래 — 변경 승인. 토큰은 update_ticket 도구가 만들 payload 와 **같은 모양**이어야
    # 지문이 맞는다(도구는 kwargs 를 compact 해서 {"key","changes"} 로 만든다).
    plan = state.get("change_plan") or {}
    # Defense in depth: an explicit comment-only request must never inherit model-produced
    # field edits.  WorkArchitect already enforces this in normal runs, but approval staging
    # is the last boundary before a real write and must independently protect the payload.
    said = request_text(state)
    comment_only = bool((plan.get("comment") or plan.get("comments"))
                        and re.search(r"댓글|코멘트", said, re.I)
                        and re.search(r"댓글\s*(?:만|남겨|달아|작성)|코멘트\s*(?:만|남겨|달아|작성)",
                                      said, re.I)
                        and not re.search(r"우선순위|priority|마감|due|담당자|assignee|제목|summary|"
                                          r"본문|description|라벨|label|component|상태|transition",
                                          said, re.I))
    if comment_only and plan.get("changes"):
        plan = {**plan, "changes": {}}
    if comment_only:
        literal = re.search(r"['\"“‘]([^'\"”’]{2,1000})['\"”’]\s*(?:이라고|라는|라고)?\s*"
                            r"(?:댓글|코멘트)", said, re.I | re.S)
        if literal:
            plan = {**plan, "comment": literal.group(1).strip()}
    # 상태 전이 — 지문은 transition_ticket 도구의 payload 와 같은 모양(comment 없을 때).
    if plan.get("key") and (plan.get("transition") or {}).get("id"):
        p = {"key": plan["key"], "transition": str(plan["transition"]["id"])}
        cmt = (plan.get("comment") or "").strip()
        if cmt:
            p["comment"] = cmt
        return {"approval_token": approval.stage(tid, "transition_ticket", p)}
    # 티켓 링크 — link_tickets 도구의 payload 와 같은 모양.
    if plan.get("key") and (plan.get("link") or {}).get("other"):
        lk = plan["link"]
        return {"approval_token": approval.stage(
            tid, "link_tickets",
            {"key": plan["key"], "other": lk["other"], "relation": lk.get("relation") or "Relates"})}
    # 조건 일괄 수정("마감 지난 것 전부 P1") — keys 복수면 update_tickets(bulk) 지문으로.
    if (plan.get("keys") or []) and plan.get("changes"):
        rows = [{"key": str(k).strip(), "changes": dict(plan["changes"])}
                for k in plan["keys"] if str(k).strip()]
        if rows:
            return {"approval_token": approval.stage(tid, "update_tickets", {"items": rows})}
    # ★ **코멘트만 남기는 일괄** — 필드 변경이 없어 위 갈래를 못 탄다. 그러면 승인 토큰이
    #   안 만들어져 **카드가 아예 안 뜨고**, 사용자는 무엇을 승인할지 볼 수 없다
    #   (실측 CMTB1: change_plan 은 섰는데 pending 이 비어 있었다).
    #   지문은 add_ticket_comments(복수) 도구의 payload 와 같은 모양이다 — 티켓마다
    #   본문이 다르므로(멘션) `comments` 미리보기를 그대로 싣는다.
    if (plan.get("keys") or []) and (plan.get("comments") or
                                     (plan.get("comment") or "").strip()):
        pv = plan.get("comments") or []
        rows = ([{"key": c.get("key"), "body": c.get("body") or ""} for c in pv if c.get("key")]
                or [{"key": str(k).strip(), "body": (plan.get("comment") or "").strip()}
                    for k in plan["keys"] if str(k).strip()])
        if rows:
            return {"approval_token": approval.stage(tid, "add_ticket_comments",
                                                     {"items": rows}),
                    "change_plan": plan}
    if plan.get("key") and (plan.get("changes") or (plan.get("comment") or "").strip()):
        cmt = (plan.get("comment") or "").strip()
        if plan.get("changes"):
            payload = {"key": plan["key"], "changes": plan["changes"]}
            out = {"approval_token": approval.stage(tid, "update_ticket", payload)}
            if cmt:
                # 코멘트도 카드에 보이므로 같은 승인에 묶인다 — 토큰은 내용별로 따로(1회용 지문).
                out["comment_token"] = approval.stage(tid, "add_ticket_comment",
                                                      {"key": plan["key"], "body": cmt})
            return out
        # 댓글만 — 그 토큰이 곧 승인 토큰이다(변경 필드가 없으니 update 토큰은 없다).
        return {"approval_token": approval.stage(tid, "add_ticket_comment",
                                                 {"key": plan["key"], "body": cmt}),
                "change_plan": plan}

    draft = state.get("draft") or {}
    # epic 모드 — 지문은 create_epic 도구의 payload 와 같은 모양(epic_payload 가 정의).
    if (draft.get("mode") or "task") == "epic":
        from app.agent.workflow.agents.work_architect import epic_payload
        p = epic_payload(draft)
        if not p.get("summary"):
            return {}
        return {"approval_token": approval.stage(tid, "create_epic", p)}
    items = as_bulk_items(draft)
    if not items:
        return {}
    payload = {"mode": draft.get("mode") or "task", "items": items}
    # 트리 초안 — Sub-Task 는 부모 키가 있어야 만들어지므로 **부모 생성 뒤 연쇄**로 실행된다.
    # 승인 지문에는 자식까지 넣는다: 화면에 보인 것과 실행되는 것이 어긋나면 HITL 이 무의미하다.
    from app.agent.workflow.agents.work_architect import child_items
    kids = child_items(draft)
    if kids:
        payload["children"] = kids
    return {"approval_token": approval.stage(tid, "create_tickets", payload)}


def build(checkpointer=None):
    """그래프를 조립한다. `checkpointer` 를 주면 대화가 턴을 넘어 이어지고 승인 대기가 가능해진다."""
    g = StateGraph(AgentState)

    g.add_node(Node.REQUEST_ARCHITECT, _node(RequestArchitect()))
    g.add_node(Node.QUERY_SPECIALIST, _node(QuerySpecialist()))
    g.add_node(Node.QUERY_RUNNER, _node(QueryRunner()))
    g.add_node(Node.PORTFOLIO_ANALYST, _node(PortfolioAnalyst()))
    g.add_node(Node.RESEARCH_ANALYST, _node(ResearchAnalyst()))
    g.add_node(Node.KNOWLEDGE_CURATOR, _node(KnowledgeCurator()))
    g.add_node(Node.WORK_ARCHITECT, _node(WorkArchitect()))
    g.add_node(Node.PEOPLE_ADVISOR, _node(PeopleAdvisor()))
    # Same canonical Roles, alternate scheduling only. These node ids are graph plumbing,
    # not Role aliases: trace, prompt, manifest and model profile still use the canonical ids.
    g.add_node("people_advisor_serial", _node(PeopleAdvisor()))
    g.add_node("auditor_serial", _node(Auditor()))
    g.add_node("merge_assignments", _merge_assignments)
    g.add_node(Node.AUDITOR, _node(Auditor()))
    g.add_node("propose", _propose)
    g.add_node(Node.ACTION_EXECUTOR, _node(ActionExecutor()))
    g.add_node(Node.RESULT_INTEGRATOR, _node(ResultIntegrator()))

    g.add_edge(START, Node.REQUEST_ARCHITECT)
    g.add_conditional_edges(Node.REQUEST_ARCHITECT, route_after_request_architect,
                            {"investigate": Node.QUERY_SPECIALIST,
                             Node.QUERY_RUNNER: Node.QUERY_RUNNER,
                             Node.PORTFOLIO_ANALYST: Node.PORTFOLIO_ANALYST,
                             "curate": Node.KNOWLEDGE_CURATOR,
                             "refine": Node.WORK_ARCHITECT, "respond": Node.RESULT_INTEGRATOR})
    g.add_edge(Node.QUERY_SPECIALIST, Node.QUERY_RUNNER)
    g.add_conditional_edges(Node.QUERY_RUNNER, route_after_query_runner,
                            {"investigate": Node.RESEARCH_ANALYST,
                             "respond": Node.RESULT_INTEGRATOR})
    g.add_edge(Node.PORTFOLIO_ANALYST, Node.RESULT_INTEGRATOR)
    g.add_conditional_edges(Node.RESEARCH_ANALYST, route_after_research_analyst,
                            {"refine": Node.WORK_ARCHITECT, "respond": Node.RESULT_INTEGRATOR,
                             "curate": Node.KNOWLEDGE_CURATOR})
    g.add_edge(Node.KNOWLEDGE_CURATOR, Node.RESULT_INTEGRATOR)
    # ★ fan-out — 초안이 서면 PeopleAdvisor(담당 제안)와 Auditor(규칙 검열)가 **동시에** 돈다.
    #   둘 다 끝나야 merge_assignments(join)가 실행되고, 거기서 통과/재작성을 가른다.
    g.add_conditional_edges(Node.WORK_ARCHITECT, route_after_work_architect,
                            {"assign": Node.PEOPLE_ADVISOR, "review": Node.AUDITOR,
                             "sequential": "people_advisor_serial",
                             "respond": Node.RESULT_INTEGRATOR, "propose": "propose"})
    g.add_edge(Node.PEOPLE_ADVISOR, "merge_assignments")
    g.add_edge(Node.AUDITOR, "merge_assignments")
    g.add_edge("people_advisor_serial", "auditor_serial")
    g.add_edge("auditor_serial", "merge_assignments")
    g.add_conditional_edges("merge_assignments", route_after_auditor,
                            {"revise": Node.WORK_ARCHITECT, "propose": "propose",
                             "respond": Node.RESULT_INTEGRATOR})
    g.add_edge("propose", Node.RESULT_INTEGRATOR)
    g.add_conditional_edges(Node.RESULT_INTEGRATOR, route_after_result_integrator,
                            {"execute": Node.ACTION_EXECUTOR, "end": END})
    g.add_edge(Node.ACTION_EXECUTOR, Node.RESULT_INTEGRATOR)

    # ★ 여기서 멈춘다. 사용자가 승인 카드를 누르기 전엔 ActionExecutor 에 닿지 않는다.
    #   checkpointer 가 없으면 멈출 수가 없으므로(재개할 자리가 없다) interrupt 도 걸지 않는다.
    return g.compile(checkpointer=checkpointer,
                     interrupt_before=[Node.ACTION_EXECUTOR] if checkpointer else None)


def get_graph():
    """프로세스 하나가 그래프 하나를 쓴다. 대화 구분은 `thread_id` 가 한다."""
    if _compiled["graph"] is None:
        from langgraph.checkpoint.memory import MemorySaver
        _compiled["graph"] = build(checkpointer=MemorySaver())
    return _compiled["graph"]


def reset():
    """설정(provider·모델)이 바뀌면 그래프를 다시 만든다. 대화 기록도 함께 버려진다."""
    _compiled["graph"] = None


if __name__ == "__main__":      # 다이어그램 산출 — 기획문서에 넣는다
    import pathlib

    from app.infra.settings import CACHE_DIR
    out = pathlib.Path(CACHE_DIR) / "agent_graph.png"
    graph = build().get_graph(xray=1)
    try:
        out.write_bytes(graph.draw_mermaid_png())
        print(f"PNG: {out}")
    except Exception as e:      # 렌더 서비스가 막힌 망에서도 mermaid 원문은 나와야 한다
        print(f"(PNG 실패: {str(e)[:120]})")
    mmd = out.with_suffix(".mmd")
    mmd.write_text(graph.draw_mermaid(), encoding="utf-8")
    print(f"mermaid: {mmd}")
    print(graph.draw_mermaid())
