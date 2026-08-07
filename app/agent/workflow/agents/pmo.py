"""PMO — 현황을 조회해서 바로 답하는 길(my_day / progress / activity).

이 셋은 "과거 이력을 발굴"하는 요청이 아니라 **지금 상태를 집계**하는 요청이다. Historian 의
검색-열람-링크 추적은 여기서 낭비다 — 필요한 건 PMO 도구(get_my_workload / get_progress /
find_stale_tickets / get_user_activity)를 몇 번 부르고 숫자를 읽어 주는 것이다.

한 노드가 세 의도를 다 받는 이유 — 지나는 길(도구 조회 → 정리)이 같고 도구 묶음이 같다.
갈래를 나누는 건 길이 다를 때만이다(state.Intent 의 주석).

역할이 판단 기준을 바꾼다:
  · my_day + 실무자  → 마감 임박·지연을 앞세운 **오늘의 우선순위**
  · my_day + 매니저  → 자기 일 + **팀에서 정체된 것**(find_stale_tickets)까지
  · activity        → 매니저 게이트는 도구가 건다. 여기서 또 막지 않는다(이중 규칙은 갈라진다).
"""

from __future__ import annotations

from app.agent.workflow.agents.base import ToolAgent
from app.agent.prompts.roles import SYSTEM_PMO
from app.agent.workflow.prompts import persona
from app.agent.workflow.state import AgentState, Intent, last_user_text, note

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string",
                     "description": "한 줄 결론. '오늘은 DL-123 마감이 가장 급합니다' 처럼 구체적으로"},
        "findings": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "key": {"type": "string", "description": "관련 티켓 키. 없으면 빈 문자열"},
                "point": {"type": "string",
                          "description": "발견한 사실 한 문장 — **티켓 제목을 도구 결과 표기 그대로** "
                                         "포함하고 숫자·날짜를 넣는다. 키만 달랑 쓰면 읽는 사람이 "
                                         "무슨 티켓인지 모른다. 예: '\"[ETL] 적재 재시도\" — 12일째 "
                                         "업데이트 없음, 마감 7/24'"},
                "action": {"type": "string", "description": "권하는 행동. 없으면 빈 문자열"}}},
            "description": "조회에서 실제로 확인한 것만. 최대 10건",
        },
        "caution": {"type": "string",
                    "description": "주의할 점(예: 활동이 적다고 일을 안 한 게 아니다). 없으면 빈 문자열"},
    },
    "required": ["headline", "findings"],
}


_MODULES = ("ETL", "Catalog", "Runtime", "Workbench", "DataOps", "DevOps")


def _group_activity(state) -> str:
    """그룹 활동 질의의 사전 취합 — 로스터 전원의 활동을 **코드가** 조회해 자료로 만든다.

    실측 2회: 모델에게 맡기면 한 명만 조회하고 끝내거나, 티켓 표만 나열하고 사람별
    정리를 건너뛴다. 전원 조회는 판단이 아니라 반복문이다 — 코드가 돌리고, 모델은
    3층 구조(로스터→모듈 전체→개인별)로 서술만 한다.
    """
    import re as _re
    from app.agent.workflow.state import Intent as _I
    if (state.get("intent") or "") != _I.ACTIVITY:
        return ""
    asked = last_user_text(state)
    if not any(w in asked for w in ("모듈", "인력", "구성원", "팀", "들의", "들이",
                                    "관련자", "유관자")):
        return ""                       # 특정 개인 질문은 기존 경로
    m = _re.search(r"(\d+)\s*일", asked)
    days = max(1, min(int(m.group(1)) if m else 7, 90))

    from app.agent import tools as T
    # 로스터의 두 출처: ① 모듈 ② **티켓 유관자 서클**("DL-101 관련자들") — 담당·보고·코멘트 참여자.
    keys = state.get("mentioned_keys") or []
    if keys and any(w in asked for w in ("관련자", "유관자")):
        who = "티켓 " + keys[0] + " 유관자"
        p = T.BY_NAME["get_ticket_participants"].invoke({"key": keys[0]}) or {}
        roster = [x.get("id") if isinstance(x, dict) else x for x in (p.get("people") or [])]
        roster = [x for x in roster if x][:8]
    else:
        module = state.get("module") or next((mm for mm in _MODULES if mm.lower() in asked.lower()), "")
        if not module:
            return ""
        who = module
        roster = (T.BY_NAME["get_module_people"].invoke({"key_or_component": module}) or {}).get("people") or []
    if not roster:
        return ""
    rows = [f"[로스터] {who}: {', '.join(roster)} ({len(roster)}명)", f"[조회 기간] 최근 {days}일"]
    # 전원 활동 조회를 병렬로 — N명 직렬(사람당 1~2초)이 턴 시간의 큰 몫이었다.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        acts = list(ex.map(lambda u: (u, T.BY_NAME["get_user_activity"].invoke(
            {"user_id": u, "days": days}) or {}), roster[:8]))
    for uid, a in acts:
        if a.get("denied"):
            return ""                   # 매니저 아님 — 도구 게이트 존중, 기존 경로가 거부를 전한다
        touched = ", ".join(f"{t.get('key')} \"{t.get('summary','')}\"({t.get('status','')})"
                            for t in (a.get("touched") or [])[:5]) or "없음"
        cmts = ", ".join(f"{j.get('key')} {j.get('what','')}"
                         for j in (a.get("jiraActivity") or [])[:4]) or "없음"
        docs = ", ".join(d.get("title", "") for d in (a.get("docActivity") or [])[:3]) or "없음"
        rows.append(f"[{uid}] 담당/변경 티켓: {touched} | 코멘트 등 활동: {cmts} | 문서 활동: {docs}")
    return "\n".join(rows)


class PMO(ToolAgent):
    name = "pmo"
    temperature = 0.1
    max_steps = 12             # 그룹 질의: whoami+로스터+워크로드+인원별 활동 — 6걸음으론 소진

    def node(self):
        react = super().node()

        def run(state):
            try:
                pre = _group_activity(state)
            except Exception:
                pre = ""
            if pre:
                state = {**state, "group_activity": pre}
            from app.agent.tools.search_tools import take_last_jql
            take_last_jql()                    # 이전 턴 잔여 비우기
            out = react(state)
            if pre:
                out["group_activity"] = pre    # Responder 도 이 자료로 3층을 쓴다 — State 에 싣는다
            q = take_last_jql()
            if q and "JQL" in last_user_text(state).upper():
                # 사용자가 JQL 을 원했다 — 어느 조회 도구를 썼든 **실행된 쿼리**를 코드가
                # 근거 줄로 박는다(조회 도구들이 내부 JQL 을 기록해 둔다).
                out.setdefault("pmo_findings", []).append(
                    {"key": "", "point": f"실행한 JQL: `{q}`", "action": ""})
            return out

        return run

    @property
    def tools(self):
        from app.agent import tools as T
        # 로스터·팀 워크로드 — 그룹 활동 질문("ETL 인력들 요즘 뭐 해")에 필요하다.
        return T.PMO_TOOLS + [T.BY_NAME["get_ticket"], T.BY_NAME["get_module_people"],
                              T.BY_NAME["get_team_workload"],
                              T.BY_NAME["run_jql"],           # 조건 조합 검색("P1 미배정 진행중")
                              T.BY_NAME["get_ticket_participants"]]  # 특정 티켓 유관자 대상 질의

    def system(self, state):
        return persona(state, SYSTEM_PMO)

    def task(self, state):
        intent = state.get("intent") or ""
        goal = {
            Intent.MY_DAY: "이 사용자가 **오늘 무엇에 집중해야 하는지** 골라라. "
                           "지연/마감임박/정체를 근거 숫자와 함께 제시하고, 매니저라면 팀 정체 티켓도 언급하라. "
                           "'담당자 없는 업무를 집고 싶다'면 whoami 로 **내 모듈**을 알아낸 뒤 "
                           "find_unassigned_tickets(module=그 모듈) 로 조회하라 — 물은 것과 다른 "
                           "기준(Epic 미연결 등)으로 바꿔치기해 답하지 마라.",
            Intent.PROGRESS: "요청된 대상의 **진척률과 그 이유**를 설명하라. "
                             "숫자가 이상해 보이면 분모에서 빠진 것(Bug·VoC·Epic Link 없음)을 짚어라. "
                             "정체·조용한 티켓을 물었으면 find_stale_tickets 를 **사용자가 말한 "
                             "기준일수(days)** 로 불러라('2일 이상' → days=2). 존재 질문('~있니')은 "
                             "**단정적으로** 답한다 — 있으면 전부 findings 에 싣고, 없으면 headline 에 "
                             "'없습니다'라고 말한다. '기록을 찾지 못했다' 같은 얼버무림 금지.",
            Intent.ACTIVITY: "요청된 사람의 **최근 활동**을 조회해 정리하라. "
                             "무엇을 만졌고 어떤 티켓이 움직였는지를 사실 위주로. "
                             "**그룹**('ETL 인력들', '우리 모듈 사람들')을 물었으면 한 명을 고르지 말고 "
                             "get_module_people 로 로스터를 얻은 뒤 get_team_workload(module) 로 "
                             "**전원**의 진행중 업무를 모아 사람별 한 줄(이름 — 주로 하는 일)로 정리하라. "
                             "로스터에 없는 사번을 지어내지 마라.",
        }.get(intent, "요청에 맞는 현황을 조회해 정리하라.")
        # 'JQL' 을 입에 올린 요청은 run_jql 이 **의무**다 — 결과만이 아니라 쿼리 자체를 원한다.
        if "JQL" in last_user_text(state).upper():
            goal = ("사용자가 JQL 을 요구했다. **반드시 run_jql 로** 조건을 JQL 로 옮겨 실행하고 "
                    "결과를 보고하라. 다른 조회 도구로 대신하지 마라. "
                    "(실행된 JQL 한 줄은 시스템이 근거로 자동 첨부한다.)")
        ga = state.get("group_activity") or ""
        ga_block = ""
        if ga:
            ga_block = ("\n\n### 그룹 활동 자료 (코드가 로스터 전원을 조회함 — 이것이 사실의 전부)\n"
                        "★ 추가 조회 없이 이 자료만으로 3층으로 정리하라: ① 누가 있는지(로스터) "
                        "② 모듈 전체가 이 기간에 한 기여(2~3문장 서술) ③ 사람별 한 블록"
                        "(주로 한 일 — 근거 티켓 키, 코멘트·문서 활동 포함). 전원을 다뤄라.\n" + ga)
        return f"""\
# 명령서
{goal}

## 입력
사용자 요청: {last_user_text(state)}
짐작 모듈: {state.get('module') or '미상'}
언급된 티켓: {', '.join(state.get('mentioned_keys') or []) or '없음'}{ga_block}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
        finds = [f for f in (out.get("findings") or []) if isinstance(f, dict)][:10]
        # Responder 가 근거 카드로 그릴 수 있게 evidence 모양으로도 옮겨 준다.
        ev = [{"key": f.get("key") or "", "title": f.get("point") or "",
               "why": f.get("action") or ""} for f in finds if f.get("key")]
        return {"situation": out.get("headline") or "",
                "evidence": ev,
                "pmo_findings": finds,
                "pmo_caution": out.get("caution") or "",
                "trace": note(state, self.name, f"발견 {len(finds)}건")}
