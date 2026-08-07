"""agent/workflow/graph.py — 여섯 역할을 잇는다.

```
START ─> planner ─┬─ chitchat ─────────────────────────────────────────> responder ─> END
                  ├─ my_day/progress/activity ──> pmo(현황 조회) ────────> responder
                  └─> historian ─┬─ ask ────────────────────────────────> responder
                                 └─> refiner ─┬─ 질문 있음 ──────────────> responder
                                        ▲     ├─> assigner ─┐ (병렬)
                                        │     └─> reviewer ─┴> merge(join)
                                        │                        │
                                        └─ 재작성(≤2회) ──────────┤
                                                                 └─ 통과/한계 ─> propose
                                                                                    │
                                                                                    ▼
                                                                               responder
                                                                                    │
                                              ┌─── 승인 대기 (interrupt_before) ─────┘
                                              ▼
                                          operator ─> responder ─> END
```

## 왜 이 모양인가

**Historian 을 건너뛰지 않는다**(chitchat 빼고). "CDC 도입해야 해"에 바로 티켓을 만들어 주는
어시스턴트는 중복 티켓 생성기다. 조사가 이 서비스의 값어치다.

**Refiner ↔ Reviewer 왕복에 상한을 둔다**(`MAX_REVISIONS`). 안 걸면 모델이 서로 만족하지 못해
무한히 돈다. 상한을 넘으면 **미해결 문제를 그대로 안고** 사용자에게 간다 — 조용히 통과시키는
것보다 "이건 못 고쳤습니다"가 낫다.

**Operator 앞에서 멈춘다**(`interrupt_before`). 이게 HITL 의 흐름 쪽 절반이다. 나머지 절반인
내용 보증은 승인 토큰이 한다(`agent/approval.py`) — 흐름은 코드 실수로 우회될 수 있지만
토큰은 내용 해시에 묶여 있어 못 우회한다.

**Checkpointer 를 붙인다**(`thread_id`). 이게 없으면 되묻기가 불가능하다 — 사용자가 "범위는
수집까지야"라고만 답했을 때 앞의 조사 결과가 남아 있어야 한다. `MemorySaver` 를 쓰는 이유는
LTM 이 사용자 PC 에서 도는 단일 프로세스 앱이고, 앱을 껐다 켜면 대화가 사라지는 게 맞기
때문이다(진행 중인 승인이 재시작 뒤에도 살아 있으면 그게 더 위험하다).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.workflow.agents.assigner import Assigner, merge_assignments
from app.agent.workflow.agents.curator import Curator
from app.agent.workflow.agents.historian import Historian
from app.agent.workflow.agents.operator import Operator
from app.agent.workflow.agents.planner import Planner
from app.agent.workflow.agents.pmo import PMO
from app.agent.workflow.agents.refiner import Refiner
from app.agent.workflow.agents.responder import Responder
from app.agent.workflow.agents.reviewer import Reviewer
from app.agent.workflow.state import (MAX_REVISIONS, AgentState, Intent, Node)

_compiled = {"graph": None}


def _node(agent):
    """붙는 것은 언제나 **함수**다 — 컴파일된 서브그래프를 그대로 노드로 쓰면 전체 State 가
    반환값이 되어 부모의 `add_messages` 리듀서에 통째로 다시 먹힌다."""
    return agent.node()


# ── 라우터: State 만 보고 결정한다(부작용 없음) ──────────────────────
def route_after_planner(state: AgentState) -> str:
    """세 갈래 — 조사(historian) / 현황 직행(pmo) / 그냥 답(responder).

    my_day·progress·activity 를 Historian 에 태우지 않는 이유: 그 요청들은 과거 발굴이 아니라
    **지금 상태의 집계**다. 검색-열람-링크추적은 낭비이고, 느린 데다 답도 더 나빠진다.
    """
    intent = state.get("intent") or ""
    if intent == Intent.CHITCHAT:
        return "respond"
    # ── 빠른 경로 2종 (턴 시간의 최대 낭비 제거) ──────────────────
    # ① 후속 턴: 같은 대화에서 조사 결과(situation)가 이미 있으면 재조사하지 않는다 —
    #    인터뷰 답변 턴마다 Historian 이 통째로 다시 돌던 것이 실측 낭비의 최대 항목
    #    (턴당 LLM 3~5회). 새 정보가 필요하면 Refiner 가 자기 도구로 확인한다.
    if intent in Intent.DRAFTS_TICKETS and (state.get("situation") or "").strip() \
            and (state.get("turns") or 0) > 0:
        return "refine"
    # ② modify + 티켓 키 명시: 키 실재·현재 값 확인은 Refiner 의 get_ticket 이 한다 —
    #    변경 요청에 과거 이력 발굴은 대개 불필요하다.
    if intent == Intent.MODIFY and state.get("mentioned_keys"):
        return "refine"
    if intent in Intent.DIRECT_ANSWER:
        return "pmo"
    return "investigate"


def route_after_historian(state: AgentState) -> str:
    """조사만 하면 되는 요청은 여기서 끝낸다 — 티켓 초안까지 갈 이유가 없다.

    지식형 질문("X가 뭐야", "정리해줘")은 Curator 를 거친다 — 조사 결과를 개념/우리 상황/
    참고/공백 스키마로 정리해야 답이 재사용 가능한 브리프가 된다(사용자 요청으로 신설).
    """
    intent = state.get("intent") or ""
    if intent in Intent.DRAFTS_TICKETS:
        return "refine"
    if intent == Intent.ASK:
        from app.agent.workflow.state import last_user_text
        asked = last_user_text(state)
        # 사람 판단·추천이 낀 질문("누가 하면 좋을지", "맡겨도 될까")은 curate 로 보내지
        # 않는다 — Curator 스키마엔 사람이 없어서 개념 강의로 새 버린다(실측).
        people_ish = any(w in asked for w in ("누가", "누구에게", "맡", "적절", "추천",
                                              "관련자", "유관자", "활동"))
        if not people_ish and any(w in asked for w in ("뭐야", "무엇", "뭔지", "정리", "설명",
                                                       "알려줘", "지식", "개념", "어떻게 쓰")):
            return "curate"
    return "respond"


def route_after_refiner(state: AgentState):
    """되물을 게 있으면 사용자에게 돌아간다. 초안이 섰으면 담당자 제안과 규칙 검증을
    **동시에** 돌린다(fan-out) — 둘은 서로의 출력을 읽지 않는다(Assigner 는 초안에 사람을
    제안하고, Reviewer 는 초안 자체를 검열한다). 직렬이던 것을 병렬로 바꿔 생성 플로우의
    한 스텝을 통째로 접었다. 배정 사용자 실재 확인은 join(merge_assignments)의 코드가 한다.

    **변경 계획(modify)은 승인으로 직행**한다 — 담당자 추천(새 티켓용)도, validate_bulk
    (생성 검증)도 여기엔 해당이 없다. 안전장치는 승인 카드와 editmeta(편집 불가 필드 거부)다.
    """
    if state.get("questions"):
        return "respond"
    if (state.get("change_plan") or {}).get("key"):
        return "propose"
    return ["assign", "review"] if (state.get("draft") or {}).get("items") else "respond"


def route_after_reviewer(state: AgentState) -> str:
    """통과하면 승인 요청으로, 아니면 다시 쓰게 한다 — 단, 왕복 상한까지만.

    상한 소진 뒤의 갈래가 중요하다:
      · **기계 검증 오류**(없는 부모·틀린 타입)가 남았으면 → respond. 만들어 봤자 Jira 가
        거부하므로 승인 카드를 줄 이유가 없다.
      · **LLM 검열 의견만** 남았으면 → propose. 검열 의견은 경고로 카드에 실린다.
        ★ 검열자가 만족할 때까지 승인 자체를 막으면 **사람이 판단할 기회가 사라진다** —
        실제로 멀쩡한 근거를 "불충분"이라며 두 번 반려해 사용자가 승인할 길이 없어졌다.
        최종 판단은 검열자가 아니라 사람이 한다.
    """
    review = state.get("review") or {}
    if review.get("ok"):
        return "propose"
    # 재작성은 **기계 오류가 있을 때만** — LLM 의견만으로 왕복하면 한 턴이 200초를 넘겼다
    # (실측: 반려 1회 = 호출 +4~8회). 의견은 카드에 '검토 의견'으로 실려 사람이 판단한다.
    if review.get("errors") and (state.get("revisions") or 0) < MAX_REVISIONS:
        return "revise"
    return "respond" if review.get("errors") else "propose"


def route_after_responder(state: AgentState) -> str:
    """말을 끝냈다. 승인받을 초안이 있으면 **여기서 멈춘다**(interrupt_before=operator).

    이미 실행한 뒤(result 가 있음)라면 끝이다 — 안 그러면 responder↔operator 를 맴돈다.
    """
    if state.get("result"):
        return "end"
    return "execute" if state.get("approval_token") else "end"


def _merge_assignments(state: AgentState) -> dict:
    """담당자 제안을 초안에 실제로 꽂는다(fan-out 의 join). 근거 없는 제안은 반영되지 않는다.

    별도 노드로 둔 이유 — Assigner 는 '제안'을 만들고, 초안을 고치는 것은 다른 일이다.
    한 노드에서 둘 다 하면 "근거 없는 건 반영 안 한다"는 규칙이 프롬프트 안에 묻힌다.

    Reviewer 가 배정 **전** 초안을 검증하므로(병렬), 배정된 사용자가 실재하는지는
    여기 코드가 보장한다 — validate_bulk 와 같은 lookup 을 쓴다.
    """
    draft = merge_assignments(state.get("draft"), state.get("assignments"))
    try:
        from app.agent.tools._ctx import client
        exists = client().bulk_lookup().user_exists
        for it in draft.get("items") or []:
            u = (it.get("assignee") or "").strip()
            if u and not exists(u):
                it["assignee"] = _resolve_user(u, exists)
    except Exception:
        pass                              # lookup 실패가 초안 자체를 버리게 하면 안 된다
    return {"draft": draft}


def _resolve_user(u: str, exists) -> str:
    """접미만 온 아이디("x1103")를 로스터 유일 일치로 풀 아이디(skcc.x1103)로 해소한다.

    사용자는 흔히 접미만 댄다. 직렬 시절엔 Reviewer 의 validate_bulk 가 이를 기계 오류로
    잡아 재작성 루프에서 교정됐지만, 병렬화로 Reviewer 가 배정 전 초안만 보게 되면서 그
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
    Reviewer 가 할 일도 아니다(검열자가 실행 허가를 내주면 검열이 아니다).
    """
    from app.agent import approval
    from app.agent.workflow.agents.refiner import as_bulk_items

    tid = state.get("thread_id") or ""

    # modify 갈래 — 변경 승인. 토큰은 update_ticket 도구가 만들 payload 와 **같은 모양**이어야
    # 지문이 맞는다(도구는 kwargs 를 compact 해서 {"key","changes"} 로 만든다).
    plan = state.get("change_plan") or {}
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
                                                 {"key": plan["key"], "body": cmt})}

    draft = state.get("draft") or {}
    # epic 모드 — 지문은 create_epic 도구의 payload 와 같은 모양(epic_payload 가 정의).
    if (draft.get("mode") or "task") == "epic":
        from app.agent.workflow.agents.refiner import epic_payload
        p = epic_payload(draft)
        if not p.get("summary"):
            return {}
        return {"approval_token": approval.stage(tid, "create_epic", p)}
    items = as_bulk_items(draft)
    if not items:
        return {}
    payload = {"mode": draft.get("mode") or "task", "items": items}
    return {"approval_token": approval.stage(tid, "create_tickets", payload)}


def build(checkpointer=None):
    """그래프를 조립한다. `checkpointer` 를 주면 대화가 턴을 넘어 이어지고 승인 대기가 가능해진다."""
    g = StateGraph(AgentState)

    g.add_node(Node.PLANNER, _node(Planner()))
    g.add_node("pmo", _node(PMO()))
    g.add_node(Node.HISTORIAN, _node(Historian()))
    g.add_node(Node.CURATOR, _node(Curator()))
    g.add_node(Node.REFINER, _node(Refiner()))
    g.add_node(Node.ASSIGNER, _node(Assigner()))
    g.add_node("merge_assignments", _merge_assignments)
    g.add_node(Node.REVIEWER, _node(Reviewer()))
    g.add_node("propose", _propose)
    g.add_node(Node.OPERATOR, _node(Operator()))
    g.add_node(Node.RESPONDER, _node(Responder()))

    g.add_edge(START, Node.PLANNER)
    g.add_conditional_edges(Node.PLANNER, route_after_planner,
                            {"investigate": Node.HISTORIAN, "pmo": "pmo",
                             "refine": Node.REFINER, "respond": Node.RESPONDER})
    g.add_edge("pmo", Node.RESPONDER)
    g.add_conditional_edges(Node.HISTORIAN, route_after_historian,
                            {"refine": Node.REFINER, "respond": Node.RESPONDER,
                             "curate": Node.CURATOR})
    g.add_edge(Node.CURATOR, Node.RESPONDER)
    # ★ fan-out — 초안이 서면 Assigner(담당 제안)와 Reviewer(규칙 검열)가 **동시에** 돈다.
    #   둘 다 끝나야 merge_assignments(join)가 실행되고, 거기서 통과/재작성을 가른다.
    g.add_conditional_edges(Node.REFINER, route_after_refiner,
                            {"assign": Node.ASSIGNER, "review": Node.REVIEWER,
                             "respond": Node.RESPONDER, "propose": "propose"})
    g.add_edge(Node.ASSIGNER, "merge_assignments")
    g.add_edge(Node.REVIEWER, "merge_assignments")
    g.add_conditional_edges("merge_assignments", route_after_reviewer,
                            {"revise": Node.REFINER, "propose": "propose",
                             "respond": Node.RESPONDER})
    g.add_edge("propose", Node.RESPONDER)
    g.add_conditional_edges(Node.RESPONDER, route_after_responder,
                            {"execute": Node.OPERATOR, "end": END})
    g.add_edge(Node.OPERATOR, Node.RESPONDER)

    # ★ 여기서 멈춘다. 사용자가 승인 카드를 누르기 전엔 Operator 에 닿지 않는다.
    #   checkpointer 가 없으면 멈출 수가 없으므로(재개할 자리가 없다) interrupt 도 걸지 않는다.
    return g.compile(checkpointer=checkpointer,
                     interrupt_before=[Node.OPERATOR] if checkpointer else None)


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
