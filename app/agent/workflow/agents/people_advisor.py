"""People Advisor — 담당자를 **근거와 함께** 제안한다. 이름만 던지면 PM 이 검증할 수 없다.

담당자 추천이 쓸모없어지는 방식은 정해져 있다: "적합해 보입니다"로 끝나는 것. 리더는 그걸
검증할 수 없으니 결국 자기가 다시 판단한다 — 그러면 에이전트가 한 일이 없다.

그래서 네 신호를 **각각 확인하고 숫자와 티켓 키로** 말하게 한다.
  ① 지금 얼마나 물려 있나   `get_team_workload`
  ② 비슷한 일을 해 봤나     ResearchAnalyst 의 근거 티켓 + `get_ticket_participants`
  ③ 그 논의에 실제로 꼈나   `get_ticket_participants` — 담당자 필드엔 없지만 코멘트엔 있는 사람
  ④ 그 모듈 사람인가        `get_module_people` / `get_person_profile`

**순위를 코드에 박지 않는다.** "진행중 건수가 가장 적은 사람"으로 정하면 난이도를 모르는
추천이 되고, "P1 이 밀려 있으면 예외" 같은 현실의 결을 담을 수 없다. 도구는 신호만 모으고
판단과 문장은 모델이 한다. 대신 **근거 없는 추천을 스키마가 막는다**(reasons 가 필수다).
"""

from __future__ import annotations

from app.agent.workflow.agents.base import StructuredAgent
from app.agent.workflow.agents.work_architect import draft_text
from app.agent.prompts.roles import SYSTEM_PEOPLE_ADVISOR
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
                    # 자식 담당도 **여기서** 정한다 — 사람을 고르는 일은 한 역할의 것이다.
                    "children": {
                        "type": "array",
                        "items": {"type": "object", "properties": {
                            "index": {"type": "integer", "description": "이 항목의 하위 번호(0부터)"},
                            "user": {"type": "string", "description": "Jira user id"},
                            "why": {"type": "string",
                                    "description": "왜 이 사람인가 — 숫자나 티켓 키를 넣어라"}}},
                        "description": ("하위(Sub-Task)가 있으면 **하위별 담당**도 정한다. "
                                        "부하가 높다고 스스로 판단해 뺀 사람을 하위에 넣지 "
                                        "마라 — 앞뒤가 맞지 않는다. 하위가 없으면 빈 배열"),
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
    from app.agent.workflow.relevance import matches_focus
    by_user: dict = {}
    for it in (r or {}).get("jira") or []:
        # 검색기는 recall을 위해 module 하나만 겹쳐도 결과를 준다. 담당 경험은 현재 요청의
        # 고유 keyword가 제목/요약에 실제로 겹칠 때만 '유사'라고 부른다.
        if not matches_focus(" ".join(str(it.get(k) or "")
                                      for k in ("title", "summary", "snippet")), kws):
            continue
        u = (it.get("assignee") or "").strip()
        if u:
            by_user.setdefault(u, []).append(it)
    rows = []
    for u, tickets in sorted(by_user.items(), key=lambda kv: -len(kv[1]))[:6]:
        refs = " · ".join(f"{t.get('key')} \"{t.get('title','')}\"({t.get('status','')})"
                          for t in tickets[:3])
        rows.append(f"- {u} — 유사 {len(tickets)}건: {refs}")
    return "\n".join(rows)


def _roster_load(state) -> str:
    """후보 로스터와 **현재 부하**를 코드가 조회한다.

    예전엔 도구(get_team_workload·get_module_people)로 두고 모델이 부르게 했다. 그런데
    부르는 대상이 늘 같았고(초안의 모듈), 도구 호출 한 번이 곧 LLM 왕복 한 번이라
    배정 하나에 4~5회를 태웠다(실측). 누구를 조회할지는 판단이 아니라 초안이 정한다.
    """
    mods = []
    for it in ((state.get("draft") or {}).get("items") or []):
        for c in (it.get("components") or []):
            if str(c).strip() and str(c) not in mods:
                mods.append(str(c))
    if not mods and state.get("module"):
        mods = [str(state["module"])]
    if not mods:
        return ""
    from app.agent.tools.people_tools import get_team_workload
    rows = []
    for m in mods[:3]:
        res = get_team_workload.invoke({"module": m}) or {}
        ppl = res.get("people") or []
        if not ppl:
            continue
        # 이름표는 **도구가 판정한 것**을 쓴다. 컴포넌트가 로스터 키와 안 맞으면 도구가
        # 전원으로 넓혀 오는데, 그걸 "[<컴포넌트> 로스터·부하]" 라고 적으면 PeopleAdvisor 가
        # 남의 모듈 사람을 그 모듈 소속으로 읽고 근거 문장에 그렇게 쓴다(실측 갭).
        rows.append(f"[{res.get('module') or m} 로스터·부하]")
        rows += [f"- {p.get('id')} {p.get('name', '')} — 진행중 {p.get('inProgress', 0)}건 · "
                 f"열림 {p.get('open', 0)}건 · 최근 완료 {p.get('done28d', 0)}건" for p in ppl]
    return "\n".join(rows)


class PeopleAdvisor(StructuredAgent):
    """★ 도구를 쓰지 않는다 — 후보 재료(유사 이력·로스터·부하)를 코드가 미리 조회한다.

    예전엔 ToolAgent 였는데, 부르는 대상이 늘 같았다(초안이 정한 모듈의 사람들).
    누구를 조회할지가 판단이 아니면 순회할 이유가 없다 — 도구 호출 한 번이 곧 LLM
    왕복 한 번이라 배정 하나에 4~5회를 태웠다(실측 기준선).
    """

    name = Node.PEOPLE_ADVISOR
    temperature = 0.2

    def node(self):
        base = super().node()

        def run(state):
            # 두 조회는 독립 — 병렬로. prod 는 호출당 수백 ms~수 초다.
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_hist = ex.submit(_similar_history, state)
                f_load = ex.submit(_roster_load, state)
            try:
                hist = f_hist.result()
            except Exception:
                hist = ""            # 검색 실패가 배정 자체를 막으면 안 된다
            try:
                load = f_load.result()
            except Exception:
                load = ""
            if hist:
                state = {**state, "similar_history": hist}
            if load:
                state = {**state, "roster_load": load}
            return base(state)

        return run

    def system(self, state):
        return persona(state, SYSTEM_PEOPLE_ADVISOR)

    def task(self, state):
        from app.agent.workflow.relevance import evidence_is_relevant
        ev = "\n".join(f"- {e.get('key','')} {e.get('title','')} — {e.get('why','')}"
                       for e in (state.get("evidence") or []) if evidence_is_relevant(e))
        data = wrap_data(
            data_block("현재 상황", state.get("situation")),
            data_block("유사 티켓(여기 등장한 사람들을 확인하라)", ev),
            data_block("유사 업무 담당 이력 (코드가 검색·집계함 — 근거로 활용하라)",
                       state.get("similar_history")),
            data_block("후보 로스터와 현재 부하 (코드가 조회함 — 이 안에서 고른다)",
                       state.get("roster_load")))
        return f"""\
# 명령서
아래 티켓 초안의 **각 항목마다** 담당자를 근거와 함께 제안하라.

## 제약조건
- 초안 항목 번호(index)를 그대로 쓴다.
- 사용자 id 는 `skcc.x1042` 형식이어야 한다. 이름을 적지 마라.
- 근거는 **위 자료에 있는 것만**. 자료에 없는 것을 근거처럼 적지 마라.
- 근거에는 워크로드 숫자만이 아니라 **유사 업무 이력(티켓 키·건수)** 을 반드시 확인해
  반영하라 — 위 자료의 담당 이력 표가 출발점이다. 이력이 없으면 없다고 적는다.
- **후보는 한 명이 아니다** — alternates 에 대안 후보를 1명 이상, 왜 1순위가 아닌지와
  함께 적는다. 사용자가 화면에서 후보 중 고른다.
- 같은 사람을 모든 항목에 몰지 마라 — 그건 배분이 아니다.
- **하위(Sub-Task)가 있으면 하위 담당도 네가 정한다**(children). 아래 초안에 붙은 현재
  하위 담당은 코드가 모듈 명단을 순번으로 돌린 임시값이니 부하를 보고 고쳐라.
  대안에서 "부하가 높아 부적합"이라 적은 사람을 하위에 넣지 마라 — 앞뒤가 안 맞는다.

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
            from app.agent.workflow.relevance import evidence_is_relevant
            blocked = {str(e.get("key") or "") for e in (state.get("evidence") or [])
                       if e.get("key") and not evidence_is_relevant(e)}
            has_similar = bool(str(state.get("similar_history") or "").strip())
            reasons = [str(r).strip() for r in (a.get("reasons") or []) if str(r).strip()]
            reasons = [r for r in reasons if not any(k in r for k in blocked)
                       and (has_similar or "유사" not in r)]
            # "유사 티켓 X 담당"은 담당 이력 표에서 **이 추천 사용자 행**에 X가 있을 때만
            # 사실이다. BUG2 실측에서 x1210을 추천하면서 실제 x1402 담당 DL-9090을
            # x1210의 경험처럼 썼다. workload 근거는 남기되 거짓 이력 근거는 제거한다.
            user = str(a.get("user") or "").strip()
            own_hist = next((line for line in str(state.get("similar_history") or "").splitlines()
                             if line.lstrip().startswith(f"- {user} ")), "")
            clean_reasons = []
            for reason in reasons:
                if "유사" in reason:
                    refs = _ticket_keys(reason)
                    if not own_hist or (refs and not any(k in own_hist for k in refs)):
                        continue
                cleaned = _ground_assignment_reason(reason, own_hist)
                if cleaned:
                    clean_reasons.append(cleaned)
            reasons = clean_reasons
            if not any(any(ch.isdigit() for ch in r) for r in reasons):
                measured = _workload_reason(state.get("roster_load"), user)
                if any(ch.isdigit() for ch in measured):
                    reasons = [measured]
            kids = []
            for c in (a.get("children") or []):
                if not isinstance(c, dict) or not str(c.get("user") or "").strip():
                    continue
                child_user = str(c.get("user") or "").strip()
                child_hist = next((line for line in
                                   str(state.get("similar_history") or "").splitlines()
                                   if line.lstrip().startswith(f"- {child_user} ")), "")
                why = _ground_assignment_reason(str(c.get("why") or "").strip(), child_hist)
                if not why:
                    why = _workload_reason(state.get("roster_load"), child_user)
                    if not any(ch.isdigit() for ch in why):
                        continue
                elif not any(ch.isdigit() for ch in why):
                    measured = _workload_reason(state.get("roster_load"), child_user)
                    if any(ch.isdigit() for ch in measured):
                        why = measured
                kids.append({"index": int(c.get("index") or 0),
                             "user": child_user, "why": why})
            alternates = []
            for x in (a.get("alternates") or []):
                if not isinstance(x, dict) or not x.get("user"):
                    continue
                alt_user = str(x.get("user") or "").strip()
                alt_hist = next((line for line in
                                 str(state.get("similar_history") or "").splitlines()
                                 if line.lstrip().startswith(f"- {alt_user} ")), "")
                why = _ground_assignment_reason(str(x.get("why") or "").strip(), alt_hist)
                if not why:
                    why = _workload_reason(state.get("roster_load"), alt_user)
                    if not any(ch.isdigit() for ch in why):
                        continue
                elif not any(ch.isdigit() for ch in why):
                    measured = _workload_reason(state.get("roster_load"), alt_user)
                    if any(ch.isdigit() for ch in measured):
                        why = measured
                alternates.append({"user": alt_user, "why": why})
            row = {"index": idx, "user": user,
                   "reasons": reasons, "children": kids,
                   "alternates": alternates[:2]}
            rows.append(_normalize_workload_choice(row))
        named = sum(1 for r in rows if r["user"])
        return {"assignments": rows,
                "trace": note(state, self.name,
                              f"{named}/{len(rows)}건 담당자 제안" + (
                                  f" · {out['caution'][:60]}" if out.get("caution") else ""))}


def _ticket_keys(text: str) -> list[str]:
    import re
    return re.findall(r"\b[A-Z][A-Z0-9]*-\d+\b", str(text or ""))


def _ground_assignment_reason(reason: str, own_history: str) -> str:
    """담당 이력 표에 없는 티켓·경험 주장을 지우고 측정된 workload 절은 보존한다."""
    import re
    text = str(reason or "").strip()
    refs = _ticket_keys(text)
    if refs and (not own_history or any(key not in own_history for key in refs)):
        return ""
    if not own_history and re.search(r"경험|유사\s*(?:업무|티켓)|코멘트\s*\d+건|"
                                     r"(?:티켓|작업)\s*담당|"
                                     r"업무에\s*적합|적합(?:함|하다|합니다)?", text):
        # "진행중 8건이며 ETL 경험"처럼 한 문장에 섞였으면 검증 가능한 부하만 살린다.
        m = re.search(r"진행\s*중(?:인)?\s*(?:티켓|작업)?\s*(\d+)\s*건|진행중\s*(\d+)\s*건",
                      text)
        if m:
            return f"진행중 {next(x for x in m.groups() if x is not None)}건"
        return ""
    return text


def _workload_reason(roster_load, user: str) -> str:
    import re
    for line in str(roster_load or "").splitlines():
        if line.lstrip().startswith(f"- {user} "):
            m = re.search(r"진행중\s*(\d+)\s*건", line)
            if m:
                return f"진행중 {m.group(1)}건"
    return "승인 화면에서 현재 부하 확인 필요"


def _normalize_workload_choice(row: dict) -> dict:
    """경력 근거 없이 workload만 비교했으면 실제 최소 진행 건수 후보를 1순위로 둔다."""
    import re

    def load(parts) -> int | None:
        text = " ".join(str(x) for x in parts)
        m = re.search(r"진행\s*중(?:인)?\s*(?:티켓|작업)?\s*(\d+)\s*건|진행중\s*(\d+)\s*건", text)
        if not m:
            return None
        return int(next(x for x in m.groups() if x is not None))

    reasons = list(row.get("reasons") or [])
    # 유사 이력·직접 경험이 있는 선택은 부하만으로 뒤집지 않되, 대안의 더 낮은 부하를
    # "높다"고 거꾸로 설명하게 두지는 않는다.
    has_experience = any(re.search(r"유사|(?:티켓|작업)\s*담당|DL-\d+", str(r))
                         for r in reasons)
    primary_load = load(reasons)
    alts = list(row.get("alternates") or [])
    ranked = [(load([a.get("why")]), i, a) for i, a in enumerate(alts)]
    ranked = [x for x in ranked if x[0] is not None]
    if primary_load is None or not ranked:
        return row
    alt_load, idx, alt = min(ranked, key=lambda x: x[0])
    if alt_load >= primary_load:
        return row
    if has_experience:
        new = dict(row)
        alts[idx] = dict(alt, why=(f"진행중 {alt_load}건으로 현재 부하는 더 낮지만, "
                                   "1순위의 관련·완료 경험을 우선함"))
        new["alternates"] = alts
        return new
    old_user = str(row.get("user") or "")
    new = dict(row)
    new["user"] = str(alt.get("user") or "")
    new["reasons"] = [f"진행중 {alt_load}건으로 후보 중 현재 부하가 가장 낮음"]
    alts[idx] = {"user": old_user, "why": f"진행중 {primary_load}건으로 1순위보다 부하가 높음"}
    new["alternates"] = alts
    return new


def merge_assignments(draft: dict, assignments: list) -> dict:
    """제안된 담당자를 초안에 실제로 꽂는다. **근거가 없는 제안은 반영하지 않는다** —
    근거 없이 배정된 담당자는 승인 화면에서 사용자가 검증할 방법이 없다.

    자식(Sub-Task) 담당도 여기서 덮는다. WorkArchitect 의 `_fill_owners` 는 모듈 명단을
    **순번으로** 돌릴 뿐 부하를 보지 않는다 — 실측: PeopleAdvisor 가 "x1450 은 진행중 15건이라
    부적합"이라 써 놓고 자식 2건이 그 사람에게 갔다. 사람을 고르는 일은 한 역할의 것이다.
    """
    items = list((draft or {}).get("items") or [])
    for a in assignments or []:
        i = a.get("index")
        if not (isinstance(i, int) and 0 <= i < len(items)):
            continue
        if a.get("user") and a.get("reasons"):
            # 사용자가 입으로 지정한 담당("성능 측정은 x1402")은 추천이 못 덮는다 —
            # 지정은 결정이고 추천은 제안이다(실측: 추천이 지정 3건을 전부 한 사람으로 뭉갬).
            if items[i].get("assignee_source") != "user":
                items[i] = dict(items[i], assignee=a["user"])
        kids = [dict(c) for c in (items[i].get("children") or []) if isinstance(c, dict)]
        if not kids:
            continue
        touched = False
        for c in a.get("children") or []:
            j, who = c.get("index"), str(c.get("user") or "").strip()
            if isinstance(j, int) and 0 <= j < len(kids) and who \
                    and kids[j].get("assignee_source") != "user":
                kids[j]["assignee"] = who
                touched = True
        if touched:
            items[i] = dict(items[i], children=kids)
    return dict(draft or {}, items=items)
