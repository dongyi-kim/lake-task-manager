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


class Assigner(ToolAgent):
    name = Node.ASSIGNER
    temperature = 0.2

    @property
    def tools(self):
        from app.agent import tools as T
        return T.PEOPLE_TOOLS + T.RULE_TOOLS

    def system(self, state):
        return persona(state, """\
너는 지금 **담당자를 제안**한다. 확정하지 않는다 — 배정은 리더의 권한이고 너는 근거를 갖춘
후보를 올릴 뿐이다.

먼저 `search_rules` 로 담당자 추천 정책을 확인하라(사번 규칙과 하지 말아야 할 것들이 있다).

조사 순서:
1. `get_module_people` 로 후보 풀을 만든다. 모듈을 못 찾으면 `get_team_workload` 로 전원을 본다.
2. Historian 이 찾은 **유사 티켓마다** `get_ticket_participants` 를 부른다.
   ★ 담당자 필드엔 없지만 **코멘트로 그 논의를 끌고 간 사람**이 여기서 나온다. 가장 강한 신호다.
3. 후보를 2~3명으로 좁힌 뒤 `get_person_profile` 로 워크로드와 최근 활동을 본다.

지켜야 할 것:
- **모든 근거에 숫자나 티켓 키를 넣는다.** 없으면 그건 근거가 아니라 인상이다.
- 워크로드 건수만 보고 가장 한가한 사람에게 주지 않는다. 건수는 난이도를 모른다.
- 운영 인력(`i` 로 시작하는 사번)에게 신규 개발 Story 를 기본값으로 주지 않는다.
- 근거를 못 찾으면 **user 를 빈 문자열로 두고** reasons 에 왜 못 정했는지 적는다.
  억지 추천보다 "정하지 못했다"가 낫다.""")

    def task(self, state):
        ev = "\n".join(f"- {e.get('key','')} {e.get('title','')} — {e.get('why','')}"
                       for e in (state.get("evidence") or []))
        data = wrap_data(
            data_block("현재 상황", state.get("situation")),
            data_block("유사 티켓(여기 등장한 사람들을 확인하라)", ev))
        return f"""\
# 명령서
아래 티켓 초안의 **각 항목마다** 담당자를 근거와 함께 제안하라.

## 제약조건
- 초안 항목 번호(index)를 그대로 쓴다.
- 사용자 id 는 `skcc.x1042` 형식이어야 한다. 이름을 적지 마라.
- 근거는 **네가 도구로 확인한 것만**. 확인 안 한 것을 근거처럼 적지 마라.
- 같은 사람을 모든 항목에 몰지 마라 — 그건 배분이 아니다.

## 티켓 초안
{draft_text(state.get('draft')) or '(초안 없음)'}
짐작 모듈: {state.get('module') or '미상'}{data}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
        rows = []
        for a in (out.get("assignments") or []):
            if not isinstance(a, dict):
                continue
            reasons = [r for r in (a.get("reasons") or []) if str(r).strip()]
            rows.append({"index": int(a.get("index") or 0), "user": (a.get("user") or "").strip(),
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
            items[i] = dict(items[i], assignee=a["user"])
    return dict(draft or {}, items=items)
