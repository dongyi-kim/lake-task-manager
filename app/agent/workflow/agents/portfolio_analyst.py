"""Portfolio Analyst — 현황을 조회해서 바로 답하는 길(my_day / progress / activity).

이 셋은 "과거 이력을 발굴"하는 요청이 아니라 **지금 상태를 집계**하는 요청이다. ResearchAnalyst 의
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
from app.agent.prompts.roles import SYSTEM_PORTFOLIO_ANALYST
from app.agent.workflow.prompts import persona
from app.agent.workflow.state import AgentState, Intent, Node, last_user_text, note

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string",
                     "description": "A specific one-line Korean conclusion, not a generic status label."},
        "findings": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "key": {"type": "string", "description": "Verified related ticket key, or empty when none."},
                "point": {"type": "string",
                          "description": "One Korean factual sentence. Preserve the exact tool-returned title "
                                         "and include relevant metrics or dates; never return a bare key."},
                "action": {"type": "string", "description": "Recommended Korean action, or empty."}}},
            "description": "At most ten findings directly verified by tool output.",
        },
        "caution": {"type": "string",
                    "description": "Korean interpretation caution, or empty; sparse activity is not proof of inactivity."},
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
        # ★ **모듈은 여럿일 수 있다** — "ETL 이랑 Catalog 최근 7일" 처럼 묻는다(사용자 지적).
        #   하나만 집으면 나머지가 조용히 빠진 채 답이 나간다.
        named = [mm for mm in _MODULES if mm.lower() in asked.lower()]
        module = state.get("module") or (named[0] if named else "")
        # ★ **"우리 모듈"은 이름이 아니라 지시어다** — 로그인 사용자가 속한 모듈로 푼다.
        #   실측(추천 칩 CHIP5 "우리 모듈의 최근 7일 업무 내역"): 모듈 이름이 없어 사전취합이
        #   통째로 꺼졌고, 그 자리를 ReAct 가 메우며 **UI 픽스처 티켓 다섯 건**을 답으로 냈다.
        #   첫 화면 추천 칩이라 빈도가 높은데 답이 남의 데이터였다.
        if not module and any(w in asked for w in ("우리 모듈", "our module", "내 모듈",
                                                   "우리팀", "우리 팀", "우리 모듈의")):
            try:
                from app.agent.tools.people_tools import _FIXTURE_MODULES
                from app.infra.settings import modules_of
                me = (T.BY_NAME["whoami"].invoke({}) or {}).get("id") or ""
                # ★ **픽스처 모듈은 답이 될 수 없다.** 개발 world 의 세션 사용자가 UI 픽스처
                #   계정이라 "우리 모듈"이 그쪽으로 풀렸고, 답이 통째로 [UI] 픽스처 티켓이
                #   됐다(실측 CHIP5). 못 고르면 비워 두는 편이 낫다 — 그러면 되묻는다.
                mine = [m for m in (modules_of(me) if me else [])
                        if m not in _FIXTURE_MODULES]
                module = mine[0] if mine else ""
            except Exception:
                module = ""
        if not module:
            return ""
        mods = named or [module]
        who = " · ".join(mods)
        roster = []
        for mm in mods:                    # 여러 모듈이면 로스터를 합친다(중복 제거)
            for uid in ((T.BY_NAME["get_module_people"].invoke({"key_or_component": mm})
                         or {}).get("people") or []):
                if uid not in roster:
                    roster.append(uid)
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



def _needs_module(state) -> bool:
    """모듈 이야기인데 **어느 모듈인지 알 수 없는가** — 그러면 물어야 한다.

    소속이 config 에 없는 사람, 픽스처 계정, "우리 팀" 같은 지시어가 여기 걸린다.
    이미 이름을 댔거나(ETL…), 티켓 유관자 질의이거나, 개인 질문이면 물을 것이 없다.
    """
    asked = last_user_text(state)
    if not any(w in asked for w in ("모듈", "인력", "구성원", "팀")):
        return False
    if any(w in asked for w in ("관련자", "유관자")) and (state.get("mentioned_keys") or []):
        return False
    if state.get("module") or any(mm.lower() in asked.lower() for mm in _MODULES):
        return False
    try:
        from app.agent import tools as T
        from app.agent.tools.people_tools import _FIXTURE_MODULES
        from app.infra.settings import modules_of
        me = (T.BY_NAME["whoami"].invoke({}) or {}).get("id") or ""
        mine = [m for m in (modules_of(me) if me else []) if m not in _FIXTURE_MODULES]
        return not mine
    except Exception:
        return True


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
    asked = last_user_text(state)
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_me = ex.submit(lambda: T.BY_NAME["whoami"].invoke({}) or {})
        f_load = ex.submit(lambda: T.BY_NAME["get_my_workload"].invoke({}) or {})
        f_stale = ex.submit(lambda: T.BY_NAME["find_stale_tickets"].invoke({"days": 14}) or {})
    rows, me = [], {}
    try:
        me = f_me.result() or {}
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
    # ── "담당자 없는 일 하나 집고 싶다" 는 **다른 기준**이다 ─────────────────
    # 내 일감(위)으로 답하면 물은 것과 다른 답이 나간다. 사전취합이 자료를 실어 주면
    # 모델은 순회를 건너뛰는데, 그때 이 기준만 조회에서 빠지면 조용히 틀린다 —
    # 재료에 넣어야 할 것은 "모델이 부를 법한 것"이 아니라 **질문이 요구하는 것**이다.
    if _re0.search(r"미배정|담당자\s*(?:가\s*)?없|주인\s*없|안\s*맡|집을|집고|맡을\s*(?:만한|일)", asked):
        try:
            mod = str((me or {}).get("module") or "")
            un = T.BY_NAME["find_unassigned_tickets"].invoke(
                {"module": mod} if mod else {}) or {}
            got = (un.get("tickets") or un.get("results") or [])[:8]
            rows.append(f"[미배정 — 사용자가 물은 기준. 이 목록이 곧 답이다 "
                        f"(모듈 {mod or '전체'}, {len(got)}건)]")
            for t in got:
                rows.append(f"- {t.get('key')} \"{t.get('summary', '')}\" "
                            f"({t.get('status', '')}, 우선 {t.get('priority') or '-'}, "
                            f"마감 {t.get('duedate') or '없음'})")
            if not got:
                rows.append("- 없음 — '없습니다'라고 단정적으로 답하고 확인한 기준을 밝혀라")
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
        # 모듈 3개 × 지표 3종 = 최대 9회. 전부 독립이라 직렬로 기다릴 이유가 없다.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = [(n,
                     ex.submit(lambda x=n: T.BY_NAME["get_progress"].invoke({"target": x}) or {}),
                     ex.submit(lambda x=n: T.BY_NAME["find_stale_tickets"].invoke(
                         {"module": x, "days": 14}) or {}),
                     ex.submit(lambda x=n: T.BY_NAME["get_team_workload"].invoke(
                         {"module": x}) or {}))
                    for n in picked]
            for n, f_pr, f_st, f_wl in futs:
                pr = f_pr.result()
                mod = ((pr.get("modules") or [{}])[0]) if not pr.get("error") else {}
                st = f_st.result()
                stale = st.get("tickets") or st.get("results") or []
                people = (f_wl.result()).get("people") or []
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
            # ★ **'지금 무엇을 하고 있나'를 따로 짚어 준다.** 위 목록에 진행중 표시가 있는데도
            #   모델은 **끝난 것만** 옮겨 적었다(실측 PROG1: 완료된 DL-9093·9094 만 쓰고,
            #   정작 열려 있는 DL-9095 를 한 번도 언급하지 않았다). 게다가 그 티켓이 하는 일을
            #   "완료되었음을 확인했습니다"라고 **거꾸로** 말했다 — 결과 문서가 그 대목을
            #   설명하고 있으면 문서의 서술을 완료로 오독한다.
            #   진척 질문의 답에서 가장 중요한 한 줄이 이것이라 목록에 섞어 두면 안 된다.
            open_kids = [c for c in r["children"] if not c.get("done")]
            if open_kids:
                rows.append("★ **지금 진행 중인 하위 작업 — 답에 반드시 키와 제목으로 넣는다**:")
                rows += [f'  - {c["key"]} "{c.get("title", "")}"'
                         f' (담당 {c.get("assignee") or "없음"})' for c in open_kids]
                rows.append("★ 위 티켓이 맡은 일은 **아직 안 끝났다**. 결과 문서나 연결 티켓에 "
                            "그 주제가 나온다고 해서 '완료'라고 쓰지 마라 — 티켓이 열려 있는 "
                            "것이 사실이고, 문서는 설계·계획일 수 있다.")
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


class PortfolioAnalyst(ToolAgent):
    name = Node.PORTFOLIO_ANALYST
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
            # ── ★ 모듈을 못 풀면 **되묻는다** — 짐작해서 남의 모듈을 답하지 않는다.
            #    config 에 소속이 안 적힌 사람이 있고(사용자 지적), "우리 모듈"이 그런
            #    사용자에게서 오면 풀 길이 없다. 그때 조용히 넘어가면 ReAct 가 아무 데이터나
            #    긁어 답한다(실측 CHIP5: 답이 통째로 UI 픽스처 티켓이었다).
            #    **복수 선택을 허용한다** — 대화가 두 모듈을 가리킬 수 있다(사용자 지적).
            if (state.get("intent") or "") == Intent.ACTIVITY and _needs_module(state):
                opts = list(_MODULES)
                return {"questions": [{
                    "question": "어느 모듈의 활동을 볼까요? (여러 개면 함께 말씀해 주세요)",
                    "kind": "choice", "field": "module",
                    "options": opts + ["잘 모르겠다 — 내가 속한 곳으로"]}],
                    "trace": note(state, self.name, "모듈 미해결 — 컴포넌트 확인 질문")}
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
                    out["trace"] = (out.get("trace") or []) + [
                        {"node": self.name, "label": "현황 조회",
                         "note": "사전 취합 자료로 바로 정리(조회 생략)"}]
                    return out
                except Exception:
                    pass
            out = react(state)
            if pre:
                out["group_activity"] = pre
            if prog:
                out["ticket_progress"] = prog    # ResultIntegrator 도 이 자료로 3층을 쓴다 — State 에 싣는다
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
        return persona(state, SYSTEM_PORTFOLIO_ANALYST)

    def task(self, state):
        intent = state.get("intent") or ""
        goal = {
            Intent.MY_DAY: "Select what this user should focus on today. Support overdue, due-soon, and "
                           "stale priorities with metrics. For a manager, include relevant stalled team work. "
                           "If the user wants to take an unassigned item, call `whoami` to resolve the user's "
                           "module and then `find_unassigned_tickets(module=...)`. Never substitute another "
                           "criterion such as missing Epic placement.",
            Intent.PROGRESS: "Explain the requested target's progress and why. If the number looks unusual, "
                             "identify denominator exclusions such as Bug, VoC, or missing Epic Link. For stale "
                             "items, call `find_stale_tickets` with the user's exact day threshold; for example, "
                             "`2일 이상` means `days=2`. Answer existence questions definitively: include every "
                             "verified result, or use the Korean word `없음` in `headline` when zero.",
            Intent.ACTIVITY: "Retrieve and summarize the requested person's recent verified activity. For a "
                             "group such as `ETL 인력들` or `우리 모듈 사람들`, do not select one person: use "
                             "`get_module_people` and `get_team_workload` to cover the full roster. Never invent "
                             "a user ID outside the returned roster.",
        }.get(intent, "Retrieve and summarize the current state that directly answers the request.")
        # 'JQL' 을 입에 올린 요청은 run_jql 이 **의무**다 — 결과만이 아니라 쿼리 자체를 원한다.
        if "JQL" in last_user_text(state).upper():
            goal = ("The user explicitly requested JQL. Translate every condition into JQL, execute it through "
                    "`run_jql`, and report the results. Do not substitute another retrieval tool. The runtime "
                    "automatically attaches the executed JQL as evidence.")
        ga = state.get("group_activity") or ""
        ga_block = ""
        if ga:
            ga_block = ("\n\n### Complete Roster Activity Data\n\n"
                        "No additional retrieval is needed. Organize this data into three Korean layers: "
                        "roster coverage; two or three sentences about the module's combined contribution; "
                        "and one block per person describing verified work with ticket, comment, and document "
                        "evidence. Cover every roster member.\n" + ga)
        tp = state.get("ticket_progress") or ""
        if tp:
            ga_block += (
                "\n\n### Prefetched Ticket Progress Data\n\n"
                "Answer from this data without another query. Progress is not a status word. In Korean, cover "
                "in order: current completion including completed children; the events supporting that judgment "
                "such as comments, ticket changes, resolved blockers, or recently updated result documents; "
                "and remaining work and deadline risk. Preserve a document's stated remaining work. Attach an "
                "exact ticket key and title or document title and update date to every material fact.\n" + tp)
        return f"""\
# Task

{goal}

Write `headline`, `point`, `action`, and `caution` in Korean while preserving identifiers exactly.

## Input Data

User request: {last_user_text(state)}

Inferred module: {state.get('module') or 'unknown'}

Explicit ticket keys: {', '.join(state.get('mentioned_keys') or []) or 'none'}{ga_block}"""

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
            # 담당자 확인은 항목마다 독립이다 — 직렬로 돌면 10건에 조회 10번을 그대로
            # 기다린다(mock 은 ms 지만 prod Jira 는 호출당 수백 ms). 병렬로 한 번에.
            from concurrent.futures import ThreadPoolExecutor

            from app.agent import tools as T

            def _assignee(k: str) -> str:
                try:
                    return str((T.BY_NAME["get_ticket"].invoke({"key": k}) or {})
                               .get("assignee") or "").strip()
                except Exception:
                    return ""

            keyed = [str(f.get("key") or "").strip() for f in finds]
            with ThreadPoolExecutor(max_workers=4) as ex:
                asgs = list(ex.map(lambda k: _assignee(k) if k else "", keyed))
            kept, dropped = [], []
            for f, k, asg in zip(finds, keyed, asgs):
                if not k:
                    kept.append(f)
                    continue
                (kept if (not asg or asg == who) else dropped).append(f)
            if dropped:
                finds = kept
                out["caution"] = ((out.get("caution") or "")
                                  + f" ({who} 담당이 아닌 "
                                    f"{', '.join(str(d.get('key')) for d in dropped)} 은 "
                                    "제외했다)").strip()
        # ResultIntegrator 가 근거 카드로 그릴 수 있게 evidence 모양으로도 옮겨 준다.
        ev = [{"key": f.get("key") or "", "title": f.get("point") or "",
               "why": f.get("action") or ""} for f in finds if f.get("key")]
        return {"situation": out.get("headline") or "",
                "evidence": ev,
                "pmo_findings": finds,
                "pmo_caution": out.get("caution") or "",
                "trace": note(state, self.name, f"발견 {len(finds)}건")}
