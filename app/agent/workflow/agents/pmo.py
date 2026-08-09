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

import re as _re0

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
    # ── "얼마나 바쁜지"를 물었으면 **부하 수치**가 답이다 ───────────────────
    # 실측: "ETL 사람들 요즘 얼마나 바쁜지"에 무슨 일을 했는지만 나열하고 정작
    # 바쁨의 정도(진행중 건수·지연·최근 처리량)는 한 줄도 없었다. 활동 회고와
    # 부하 판단은 다른 질문이다 — 코드가 워크로드를 함께 실어 준다.
    if _re.search(r"바쁘|바쁨|부하|여유|한가|워크로드|일이 많|얼마나 (?:많|바)", asked):
        try:
            wl = T.BY_NAME["get_team_workload"].invoke({"module": who}) or {}
            people = wl.get("people") or []
            if people:
                rows.append("[부하 — 이 수치로 '얼마나 바쁜지'를 판단해 답하라. "
                            "진행중이 팀 평균을 크게 넘으면 과부하, 훨씬 적으면 여유다]")
                avg = sum(int(x.get("inProgress") or 0) for x in people) / max(1, len(people))
                for x in people:
                    rows.append(
                        f"- {x.get('id')} {x.get('name', '')}: 진행중 {x.get('inProgress')}건 · "
                        f"열림 {x.get('open')}건 · 최근 {wl.get('doneWindowDays', 28)}일 완료 "
                        f"{x.get('done28d')}건")
                rows.append(f"- 팀 진행중 평균 {avg:.1f}건")
        except Exception:
            pass
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


def _my_day(state) -> str:
    """"나 오늘 뭐 해야 할까" — 재료를 **코드가 병렬로** 조회한다.

    my_day 가 쓰는 도구는 늘 같다(내가 누구인가 → 내 일감 → 정체된 것). 무엇을 부를지가
    판단이 아니면 순회할 이유가 없다 — 도구 호출 한 번이 곧 LLM 왕복 한 번이라
    조회 하나에 4회를 태웠다(실측 기준선: 21k 토큰).
    """
    from app.agent.workflow.state import Intent as _I
    if (state.get("intent") or "") != _I.MY_DAY:
        return ""
    from concurrent.futures import ThreadPoolExecutor

    from app.agent import tools as T
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_me = ex.submit(lambda: T.BY_NAME["whoami"].invoke({}) or {})
        f_load = ex.submit(lambda: T.BY_NAME["get_my_workload"].invoke({}) or {})
        f_stale = ex.submit(lambda: T.BY_NAME["find_stale_tickets"].invoke({"days": 14}) or {})
    rows = []
    try:
        me = f_me.result()
        rows.append(f"[나] {me.get('id')} {me.get('name', '')} · 모듈 {me.get('module', '-')}"
                    f" · 매니저 {'예' if me.get('manager') else '아니오'}")
    except Exception:
        pass
    try:
        wl = f_load.result()
        tickets = [t for t in (wl.get("tickets") or []) if not t.get("done")]
        today = str(wl.get("today") or "")
        # 마감 지난 것 → 임박 → 나머지. 판단(무엇부터)은 모델이 하되 **순서의 근거**는
        # 코드가 붙여 준다("마감 3일 지남"이 있어야 우선순위를 말로 설명할 수 있다).
        def _key(t):
            d = str(t.get("duedate") or "")
            return (0, d) if d and today and d < today else ((1, d) if d else (2, ""))
        rows.append(f"[내 일감] 열린 것 {len(tickets)}건 (전체 {wl.get('count')}건)")
        for t in sorted(tickets, key=_key)[:12]:
            d = str(t.get("duedate") or "")
            late = " · 마감 지남" if d and today and d < today else ""
            rows.append(f"- {t.get('key')} \"{t.get('summary', '')}\" ({t.get('status', '')}"
                        f", 우선 {t.get('priority') or '-'}, 마감 {d or '없음'}{late})")
    except Exception:
        pass
    try:
        st = f_stale.result()
        for t in (st.get("tickets") or [])[:5]:
            rows.append(f"[정체] {t.get('key')} \"{t.get('summary', '')}\" "
                        f"{t.get('staleDays')}일째 · 담당 {t.get('assignee') or '없음'}")
    except Exception:
        pass
    return ("[오늘 할 일 재료 — 코드가 조회함. 이 안에서 우선순위를 판단해 답하라]\n"
            + "\n".join(rows)) if rows else ""


def _self_report(state) -> str:
    """주간보고류("내가 이번 주 한 일 정리") 사전 취합 — **본인 활동**을 코드가 조회한다.

    my_day 경로는 '앞으로 할 일'(워크로드)을 주는데, 주간보고는 '한 일'(활동 회고)이다 —
    실측: 주간보고 요청에 지연 티켓 목록만 나왔다."""
    import re as _re
    asked = last_user_text(state)
    reporty = ("주간보고" in asked or "주간 보고" in asked
               or (("내가" in asked or "제가" in asked) and "한 일" in asked))
    if not reporty:
        return ""
    m = _re.search(r"(\d+)\s*일", asked)
    days = max(1, min(int(m.group(1)) if m else 7, 30))
    try:
        from app.agent import tools as T
        a = T.BY_NAME["get_user_activity"].invoke({"user_id": "", "days": days}) or {}
    except Exception:
        return ""
    if a.get("denied") or a.get("error"):
        return ""
    rows = [f"[본인 활동 — 최근 {days}일] 주간보고 형식(완료한 일 / 진행 중 / 이슈·다음 계획)으로 쓴다"]
    for t in (a.get("touched") or [])[:10]:
        rows.append(f"- 티켓 {t.get('key')} \"{t.get('summary', '')}\" ({t.get('status', '')})")
    for j in (a.get("jiraActivity") or [])[:8]:
        rows.append(f"- 활동 {j.get('key')} {j.get('what', '')} ({str(j.get('when') or '')[:10]})")
    for d in (a.get("docActivity") or [])[:5]:
        rows.append(f"- 문서 「{d.get('title', '')}」 수정")
    return "\n".join(rows) if len(rows) > 1 else ""


def _module_compare(state) -> str:
    """모듈 비교 질의("Catalog 랑 ETL 중에 어디가 더 밀렸어?") — 지표를 **코드가** 조회한다.

    실측(Round Q): 모델이 target 없이 get_progress 를 불러 전사 진척률(overallPct)을
    받고는 "두 모듈이 48.6%로 동일"이라 했고, 이어 "완료율이 **높은** ETL 이 더 밀렸다"는
    거꾸로 된 결론을 냈다. 비교는 같은 지표를 나란히 놓고 정의를 못 박아야 한다.
    """
    asked = last_user_text(state)
    if not _re0.search(r"중에|보다|어느|어디가|비교|대비", asked):
        return ""
    try:
        from app.agent import tools as T
        opts = T.BY_NAME["list_ticket_options"].invoke({"kind": "components"}) or {}
        names = [str(x) for x in (opts.get("components") or opts.get("values") or [])]
        picked = [n for n in names if n and n.lower() in asked.lower()][:3]
        if len(picked) < 2:
            return ""
        rows = ["(코드가 조회한 모듈 지표 — 비교는 이 표로만 한다. "
                "'밀렸다'는 **완료율이 낮다**는 뜻이고, 마감이 지난 건수로 뒷받침한다. "
                "전사 진척률(overallPct)은 모듈 비교에 쓰지 마라)"]
        for n in picked:
            pr = T.BY_NAME["get_progress"].invoke({"target": n}) or {}
            mod = ((pr.get("modules") or [{}])[0]) if not pr.get("error") else {}
            st = T.BY_NAME["find_stale_tickets"].invoke({"module": n, "days": 14}) or {}
            stale = st.get("tickets") or st.get("results") or []
            wl = T.BY_NAME["get_team_workload"].invoke({"module": n}) or {}
            people = wl.get("people") or []
            rows.append(
                f"- {n}: 완료율 {mod.get('donePct', '?')}% · 14일+ 정체 {len(stale)}건 · "
                f"인원 {len(people)}명 · 진행중 "
                f"{sum(int(p.get('inProgress') or 0) for p in people)}건 · "
                f"최근 28일 완료 {sum(int(p.get('done28d') or 0) for p in people)}건")
        return "\n".join(rows)
    except Exception:
        return ""


def _ticket_progress(state) -> str:
    """티켓 한 건의 진척 질의 — 근거 네 갈래를 **코드가** 모아 자료로 준다.

    상태 필드는 'In Progress' 한 단어라 답이 못 된다. 모델의 도구 순회에 맡기면 코멘트만
    보거나 하위 티켓만 세고 끝낸다 — 결과를 적는 문서의 최근 수정처럼 **찾아가야 보이는**
    근거가 특히 잘 누락된다. 반복문으로 되는 일은 코드가 한다.
    """
    from app.agent.workflow.state import Intent as _I
    keys = [k for k in (state.get("mentioned_keys") or []) if k][:2]
    if not keys:
        return ""
    asked = last_user_text(state)
    progressy = any(w in asked for w in ("진척", "진행", "어디까지", "얼마나 됐", "상황",
                                         "현황", "잘 되고", "근황"))
    if not (progressy or (state.get("intent") or "") == _I.PROGRESS):
        return ""

    from concurrent.futures import ThreadPoolExecutor

    from app.agent.tools.survey_tools import progress_report
    blocks = []
    # Epic 키의 진척은 직계 children 이 아니라 **트리**다 — progress_report 만 보면
    # "하위 5개 전부 완료"로 오답한다(실측: Epic 아래 열린 Task 다수를 못 봄).
    from app.agent import tools as T

    def _epic_block(k):
        try:
            tr = T.BY_NAME["get_epic_tree"].invoke({"epic_key": k}) or {}
            rows = tr.get("children") or []
            if not rows or tr.get("error"):
                return ""
            done = sum(1 for t in rows if t.get("done"))
            lines = [f"[{k} Epic 트리 — 전체 {len(rows)}건 중 완료 {done}건. "
                     "이 목록이 곧 '이 Epic 아래 티켓 전부'다]"]
            for t in rows[:30]:
                lines.append(f"- {t.get('key')} \"{t.get('summary', '')}\" "
                             f"{t.get('status', '')}{' ✅' if t.get('done') else ''}")
            return "\n".join(lines)
        except Exception:
            return ""
    # 키 2건이면 두 티켓을 병렬로 — prod 에선 티켓당 5갈래 조회가 통째로 대기가 된다.
    with ThreadPoolExecutor(max_workers=2) as ex:
        reports = list(ex.map(lambda k: progress_report(k), keys))
    for k, r in zip(keys, reports):
        if r.get("error"):
            # 미존재 키는 **사실**이다 — 자료로 밝혀야 모델이 '권한 없음'으로 지어내지
            # 않는다(실측: DL-90933 을 권한 문제라고 답했다). 오탈자 후보도 코드가 찾는다.
            hint = ""
            m = _re0.match(r"([A-Z]+-)(\d+)$", k or "")
            if m and len(m.group(2)) >= 2:
                # 오탈자 후보: 마지막 자리 삭제(90933→9093) / 앞자리 유지 축약
                cands = [m.group(1) + m.group(2)[:-1], m.group(1) + m.group(2)[1:]]
                for cand in cands:
                    try:
                        if not progress_report(cand).get("error"):
                            hint = f" 비슷한 키로 {cand} 가 실재한다 — 오탈자인지 확인하라."
                            break
                    except Exception:
                        pass
            blocks.append(f"[{k}] 존재하지 않는 티켓이다(권한 문제가 아니라 미존재).{hint}")
            continue
        eb = _epic_block(k)
        if eb:
            blocks.append(eb)
        rows = [f'[{r["key"]}] "{r.get("title", "")}" — 상태 {r.get("status")}'
                f' · 담당 {r.get("assignee") or "없음"} · 마감 {r.get("due") or "없음"}'
                f' · 최근 갱신 {r.get("updated")}']
        if r.get("children"):
            rows.append(f'하위 Sub-Task {r.get("children_done")} 완료:')
            rows += [f'  - {c["key"]} "{c.get("title", "")}" '
                     f'{"완료" if c.get("done") else "진행중"}'
                     f' (담당 {c.get("assignee") or "없음"})' for c in r["children"]]
        if r.get("changes"):
            rows.append("티켓 변동:")
            rows += [f'  - {ch["date"]} {ch.get("field")} '
                     f'{ch.get("from") or "(없음)"} → {ch.get("to") or "(없음)"}'
                     for ch in r["changes"]]
        if r.get("comments"):
            rows.append("진행 보고(코멘트, 오래된 것부터):")
            rows += [f'  - {m["date"]} {m.get("who")}: {m.get("text", "")}'
                     for m in r["comments"]]
        if r.get("links"):
            rows.append("연결 티켓:")
            rows += [f'  - {x["key"]} ({x.get("rel")}) "{x.get("title", "")}" '
                     f'{"해결됨" if x.get("done") else x.get("status") or ""}'
                     f' (갱신 {x.get("updated")})' for x in r["links"]]
        for dc in r.get("documents") or []:
            rows.append(f'결과 기록 문서 「{dc.get("title")}」 (최종 수정 {dc.get("updated")}):')
            rows.append(f'  {dc.get("excerpt", "")}')
        blocks.append(chr(10).join(rows))
    return (chr(10) + chr(10)).join(blocks)[:4000]


class PMO(ToolAgent):
    name = "pmo"
    temperature = 0.1
    # 그룹 질의(whoami+로스터+워크로드+인원별 활동)는 6걸음으로 부족했다. 다만 전원
    # 조회는 이제 _group_activity 가 코드로 하므로 12 까지 열어 둘 이유가 없다.
    max_steps = 8

    def node(self):
        react = super().node()

        def run(state):
            # "아까 그 티켓 어떻게 됐어?" — **이 대화에 그 티켓이 없다.** 지시어의 대상이
            # 없으면 워크로드를 덤프하지 말고 되묻는다(실측: 전체 목록으로 답했다).
            asked0 = last_user_text(state)
            if (any(w in asked0 for w in ("아까", "그 티켓", "방금", "그거"))
                    and not (state.get("mentioned_keys") or [])
                    and len(state.get("messages") or []) <= 1):
                return {"questions": [{"question": "이 대화에서 이전에 다룬 티켓이 없습니다. "
                                                   "어느 티켓을 말씀하시는 건가요? (키 또는 제목)",
                                       "kind": "text", "options": [], "field": ""}],
                        "trace": note(state, self.name, "지시어 대상 없음 — 확인 질문")}
            try:
                pre = _group_activity(state)
            except Exception:
                pre = ""
            if pre:
                state = {**state, "group_activity": pre}
            if not pre:
                try:
                    pre = _self_report(state)
                except Exception:
                    pre = ""
                if pre:
                    state = {**state, "group_activity": pre}
            # PMO_VIT 현안 질의 — 라벨 필터 조회다. 전체 진척률 덤프로 답하던 것(실측)을
            # 코드가 현안 목록(키·제목·상태·담당)으로 바꾼다.
            asked_v = last_user_text(state)
            if "PMO_VIT" in asked_v.upper() or "현안" in asked_v:
                try:
                    from app.agent import tools as T
                    rj = T.BY_NAME["run_jql"].invoke(
                        {"jql": 'labels = "PMO_VIT" ORDER BY duedate ASC', "limit": 20}) or {}
                    rows = rj.get("items") or rj.get("tickets") or []
                    if rows:
                        blk = ("[PMO_VIT 현안 " + str(len(rows)) + "건 — 이 목록이 곧 답이다. "
                               "건별 상태·담당·마감으로 답하라]\n"
                               + "\n".join(f"- {t.get('key')} \"{t.get('summary', '')}\" "
                                           f"{t.get('status', '')} (담당 {t.get('assignee') or '없음'}"
                                           f"{', 마감 ' + str(t.get('duedate')) if t.get('duedate') else ''})"
                                           for t in rows[:20]))
                        state = {**state, "ticket_progress":
                                 ((state.get("ticket_progress") or "") + "\n\n" + blk).strip()}
                except Exception:
                    pass
            # "오늘 뭐 해야 할까" — 부를 도구가 정해져 있다. 코드가 병렬로 조회한다.
            try:
                day_blk = _my_day(state)
            except Exception:
                day_blk = ""
            if day_blk:
                state = {**state, "ticket_progress":
                         ((state.get("ticket_progress") or "") + "\n\n" + day_blk).strip()}
            try:
                cmp_blk = _module_compare(state)
            except Exception:
                cmp_blk = ""
            if cmp_blk:
                state = {**state, "ticket_progress":
                         ((state.get("ticket_progress") or "") + "\n\n" + cmp_blk).strip()}
            try:
                prog = _ticket_progress(state)
            except Exception:
                prog = ""
            if prog:
                # VIT 현안 블록 등 앞선 사전취합을 덮지 않는다 — 병합.
                merged = ((state.get("ticket_progress") or "") + "\n\n" + prog).strip()
                state = {**state, "ticket_progress": merged}
            vit_blk = state.get("ticket_progress") or ""
            from app.agent.tools.search_tools import take_last_jql
            take_last_jql()                    # 이전 턴 잔여 비우기
            # ── L3a 직결: 진척/그룹활동 재료를 코드가 전부 취합했으면 걷지 않는다.
            # (JQL 요구는 예외 — run_jql 실행 자체가 요청의 일부다.)
            if (prog or pre or vit_blk) and "JQL" not in last_user_text(state).upper():
                try:
                    out = self.apply(state, self._conclude(state, []))
                    if pre:
                        out["group_activity"] = pre
                    if prog:
                        out["ticket_progress"] = prog
                    out["trace"] = (out.get("trace") or [])                         + [{"node": self.name, "label": "현황 조회",
                            "note": "사전 취합 자료로 바로 정리(조회 생략)"}]
                    return out
                except Exception:
                    pass
            out = react(state)
            if pre:
                out["group_activity"] = pre
            if prog:
                out["ticket_progress"] = prog    # Responder 도 이 자료로 3층을 쓴다 — State 에 싣는다
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
        tp = state.get("ticket_progress") or ""
        if tp:
            ga_block += (
                "\n\n### 티켓 진척 자료 (코드가 네 갈래를 이미 취합함 — 추가 조회 불필요)\n"
                "★ 이 자료만으로 답하라. **진척은 상태 한 단어가 아니다** — 순서대로 쓴다: "
                "① 지금 어디까지 왔나(하위 완료 개수·무엇이 끝났나) ② 그 근거가 된 사건"
                "(코멘트 보고·티켓 변동·막던 티켓 해소·결과 문서의 최근 수정) ③ 남은 일과 "
                "리스크(마감 대비). 문서의 '남은 일'은 그대로 옮긴다. 근거마다 티켓 키+제목 "
                "또는 문서 제목·수정일을 붙여라.\n" + tp)
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
        # ── "누구는 뭐부터 해야 하나"에 **남의 티켓**을 섞지 않는다 ─────────────
        # 실측(Round P): x1210 의 할 일 1번이 i2044 담당 티켓이었다. 사람을 지목한
        # my_day 질문에서는 코드가 담당자를 조회해 다른 사람 것을 걷어낸다
        # (담당 없는 티켓은 "집을 수 있는 일"이라 남긴다).
        m_who = _re0.search(r"(?:skcc\.)?([a-z]{1,2}\d{2,6})", last_user_text(state))
        who = f"skcc.{m_who.group(1)}" if m_who else ""
        if who and (state.get("intent") or "") == Intent.MY_DAY:
            kept, dropped = [], []
            for f in finds:
                k = str(f.get("key") or "").strip()
                if not k:
                    kept.append(f)
                    continue
                try:
                    from app.agent import tools as T
                    asg = str((T.BY_NAME["get_ticket"].invoke({"key": k}) or {})
                              .get("assignee") or "").strip()
                except Exception:
                    asg = ""
                (kept if (not asg or asg == who) else dropped).append(f)
            if dropped:
                finds = kept
                out["caution"] = ((out.get("caution") or "")
                                  + f" ({who} 담당이 아닌 "
                                    f"{', '.join(str(d.get('key')) for d in dropped)} 은 "
                                    "제외했다)").strip()
        # Responder 가 근거 카드로 그릴 수 있게 evidence 모양으로도 옮겨 준다.
        ev = [{"key": f.get("key") or "", "title": f.get("point") or "",
               "why": f.get("action") or ""} for f in finds if f.get("key")]
        return {"situation": out.get("headline") or "",
                "evidence": ev,
                "pmo_findings": finds,
                "pmo_caution": out.get("caution") or "",
                "trace": note(state, self.name, f"발견 {len(finds)}건")}
