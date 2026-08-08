"""Assigner — 담당자를 **근거와 함께** 제안한다. 이름만 던지면 PM 이 검증할 수 없다.

담당자 추천이 쓸모없어지는 방식은 정해져 있다: "적합해 보입니다"로 끝나는 것. 리더는 그걸
검증할 수 없으니 결국 자기가 다시 판단한다 — 그러면 에이전트가 한 일이 없다.

그래서 네 신호를 **각각 확인하고 숫자와 티켓 키로** 말하게 한다.
  ① 지금 얼마나 물려 있나   `get_team_workload`
  ② 비슷한 일을 해 봤나     Historian 의 근거 티켓 + `get_ticket_participants`
  ③ 그 논의에 실제로 꼈나   `get_ticket_participants` — 담당자 필드엔 없지만 코멘트엔 있는 사람
  ④ 그 모듈 사람인가        `get_module_people` / `get_person_profile`

**순위를 코드에 박지 않는다.** "진행중 건수가 가장 적은 사람"으로 정하면 난이도를 모르는
추천이 되고, "P1 이 밀려 있으면 예외" 같은 현실의 결을 담을 수 없다. 도구는 신호만 모으고
판단과 문장은 모델이 한다. 대신 **근거 없는 추천을 스키마가 막는다**(reasons 가 필수다).
"""

from __future__ import annotations

from app.agent.workflow.agents.base import ToolAgent
from app.agent.workflow.agents.refiner import draft_text
from app.agent.prompts.roles import SYSTEM_ASSIGNER
from app.agent.workflow.prompts import data_block, persona, wrap_data
from app.agent.workflow.state import AgentState, Node, note

SCHEMA = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "초안 항목 번호(0부터)"},
                    "user": {"type": "string", "description": "Jira user id(skcc.x1042 형식). "
                                                              "확신이 없으면 빈 문자열"},
                    "reasons": {
                        "type": "array", "items": {"type": "string"},
                        "description": ("추천 근거. **각 근거에 숫자나 티켓 키를 넣어라.** "
                                        "예: '유사 티켓 DL-118·DL-127 담당(2건)', "
                                        "'DL-118 에서 CDC 관련 코멘트 4건', '진행중 3건'. "
                                        "'적합해 보임' 같은 근거는 쓰지 마라"),
                    },
                    "alternates": {
                        "type": "array",
                        "items": {"type": "object", "properties": {
                            "user": {"type": "string"},
                            "why": {"type": "string", "description": "대안인 이유와 한계를 함께"}}},
                        "description": "대안 1~2명. 왜 1순위가 아닌지도 적는다",
                    },
                },
                "required": ["index", "user", "reasons"],
            },
        },
        "caution": {
            "type": "string",
            "description": "배정상 주의할 점(과부하·운영 인력에 개발 업무 등). 없으면 빈 문자열",
        },
    },
    "required": ["assignments"],
}


def _similar_history(state) -> str:
    """유사 업무의 **담당 이력 표**를 코드가 만든다.

    실측: 모델에게 맡기면 워크로드·모듈 소속만 확인하고 "유사 업무를 해 봤는가"는
    건너뛴다(도구 걸음을 워크로드에 다 쓴다). 검색과 집계는 판단이 아니다 — 코드가
    돌리고, 모델은 그 표를 근거로 판단만 한다.
    """
    kws = [str(k) for k in (state.get("keywords") or []) if str(k).strip()][:4]
    if not kws:
        return ""
    from app.agent.tools.search_tools import search_work_history
    r = search_work_history.invoke({"query": " ".join(kws), "limit": 12})
    by_user: dict = {}
    for it in (r or {}).get("jira") or []:
        u = (it.get("assignee") or "").strip()
        if u:
            by_user.setdefault(u, []).append(it)
    rows = []
    for u, tickets in sorted(by_user.items(), key=lambda kv: -len(kv[1]))[:6]:
        refs = " · ".join(f"{t.get('key')} \"{t.get('title','')}\"({t.get('status','')})"
                          for t in tickets[:3])
        rows.append(f"- {u} — 유사 {len(tickets)}건: {refs}")
    return "\n".join(rows)


class Assigner(ToolAgent):
    name = Node.ASSIGNER
    temperature = 0.2

    @property
    def tools(self):
        from app.agent import tools as T
        return T.PEOPLE_TOOLS + T.RULE_TOOLS

    def node(self):
        react = super().node()

        def run(state):
            try:
                hist = _similar_history(state)
            except Exception:
                hist = ""            # 검색 실패가 배정 자체를 막으면 안 된다
            if hist:
                state = {**state, "similar_history": hist}
            return react(state)

        return run

    def system(self, state):
        return persona(state, SYSTEM_ASSIGNER)

    def task(self, state):
        ev = "\n".join(f"- {e.get('key','')} {e.get('title','')} — {e.get('why','')}"
                       for e in (state.get("evidence") or []))
        data = wrap_data(
            data_block("현재 상황", state.get("situation")),
            data_block("유사 티켓(여기 등장한 사람들을 확인하라)", ev),
            data_block("유사 업무 담당 이력 (코드가 검색·집계함 — 근거로 활용하라)",
                       state.get("similar_history")))
        return f"""\
# 명령서
아래 티켓 초안의 **각 항목마다** 담당자를 근거와 함께 제안하라.

## 제약조건
- 초안 항목 번호(index)를 그대로 쓴다.
- 사용자 id 는 `skcc.x1042` 형식이어야 한다. 이름을 적지 마라.
- 근거는 **네가 도구로 확인한 것만**. 확인 안 한 것을 근거처럼 적지 마라.
- 근거에는 워크로드 숫자만이 아니라 **유사 업무 이력(티켓 키·건수)** 을 반드시 확인해
  반영하라 — 위 자료의 담당 이력 표가 출발점이다. 이력이 없으면 없다고 적는다.
- **후보는 한 명이 아니다** — alternates 에 대안 후보를 1명 이상, 왜 1순위가 아닌지와
  함께 적는다. 사용자가 화면에서 후보 중 고른다.
- 같은 사람을 모든 항목에 몰지 마라 — 그건 배분이 아니다.

## 티켓 초안
{draft_text(state.get('draft')) or '(초안 없음)'}
짐작 모듈: {state.get('module') or '미상'}{data}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
        # 초안에 없는 항목 번호는 버린다 — 실 모델이 1건짜리 초안에 [0]~[5]를 낸 적이 있다.
        # 화면이 그 유령 배정을 그리면 사용자는 "무슨 티켓의 담당자지?"가 된다.
        n_items = len((state.get("draft") or {}).get("items") or [])
        rows = []
        for a in (out.get("assignments") or []):
            if not isinstance(a, dict):
                continue
            idx = int(a.get("index") or 0)
            if not (0 <= idx < n_items):
                continue
            reasons = [r for r in (a.get("reasons") or []) if str(r).strip()]
            rows.append({"index": idx, "user": (a.get("user") or "").strip(),
                         "reasons": reasons,
                         "alternates": [x for x in (a.get("alternates") or [])
                                        if isinstance(x, dict) and x.get("user")][:2]})
        named = sum(1 for r in rows if r["user"])
        return {"assignments": rows,
                "trace": note(state, self.name,
                              f"{named}/{len(rows)}건 담당자 제안" + (
                                  f" · {out['caution'][:60]}" if out.get("caution") else ""))}


def merge_assignments(draft: dict, assignments: list) -> dict:
    """제안된 담당자를 초안에 실제로 꽂는다. **근거가 없는 제안은 반영하지 않는다** —
    근거 없이 배정된 담당자는 승인 화면에서 사용자가 검증할 방법이 없다."""
    items = list((draft or {}).get("items") or [])
    for a in assignments or []:
        i = a.get("index")
        if isinstance(i, int) and 0 <= i < len(items) and a.get("user") and a.get("reasons"):
            # 사용자가 입으로 지정한 담당("성능 측정은 x1402")은 추천이 못 덮는다 —
            # 지정은 결정이고 추천은 제안이다(실측: 추천이 지정 3건을 전부 한 사람으로 뭉갬).
            if items[i].get("assignee_source") == "user":
                continue
            items[i] = dict(items[i], assignee=a["user"])
    return dict(draft or {}, items=items)
