"""agent/tools/pmo_tools.py — LTM 이 원래 하던 일을 에이전트가 대신 할 때 쓰는 도구.

앞선 도구들이 "새 일을 시작할 때" 쓰는 것이라면, 여기는 **이미 돌아가는 일을 들여다보는**
쪽이다. LTM 의 정체성이 원래 여기 있다 — WBS 진척, 내 Task, 인력 워크로드, 현안 트래킹.

## 권한을 프롬프트로 걸지 않는다

"매니저가 아니면 남의 활동을 보여 주지 마라"를 시스템 프롬프트에 적는 것은 **접근 제어가
아니다.** 모델이 헷갈릴 수도 있고, 티켓 본문에 섞여 들어온 문장이 지시처럼 읽힐 수도 있다.
그래서 남의 정보를 여는 도구는 **자기가 직접** 세션 사용자를 확인하고 거부한다.
LTM 이 화면에서 `_require_manager()` 로 막는 것과 같은 자리다 — 숨김은 접근 제어가 아니다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from langchain_core.tools import tool

from app.agent.tools._ctx import (client, compact, jira_key_allowed, jira_scope,
                                  search_projects, search_spaces, settings, trim)


def _today() -> date:
    return date.today()


def _d(v) -> date | None:
    s = str(v or "")[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _me() -> str:
    try:
        return ((client().current_user() or {}).get("id") or "").strip()
    except Exception:
        return ""


def _is_manager() -> bool:
    from app.infra.settings import is_manager
    try:
        return bool(is_manager(settings(), client().current_user() or {}))
    except Exception:
        return False


# ── 내 일 ──────────────────────────────────────────────────────────
@tool
def get_my_workload(user_id: str = "", include_done: bool = False) -> dict:
    """**내가 맡았거나 보고한 티켓 전부** — 상태·마감·우선순위·소속 Epic 과 함께.

    "나 오늘 뭐 해야 할까" 같은 질문의 출발점이다. 여기서 받은 목록을 마감·우선순위·정체
    여부로 판단해 오늘 집중할 것을 골라 준다. **골라 주는 건 네 일이고, 이 도구는 재료만 준다.**

    user_id 를 비우면 세션 사용자. 다른 사람 것을 보려면 매니저여야 한다.
    include_done=false 면 완료된 것은 뺀다(오늘 할 일에는 필요 없다).
    """
    c = client()
    me = _me()
    target = (user_id or "").strip() or me
    if target != me and not _is_manager():
        return {"error": "다른 사람의 업무 목록은 매니저만 볼 수 있습니다.", "denied": True}

    try:
        ctx = c.my_task_context() if target == me else None
        if ctx is None:
            safe = target.replace('"', " ")
            raws = c.search_issues(
                jira_scope(f'(assignee = "{safe}" OR reporter = "{safe}")') + " "
                "ORDER BY updated DESC", max_results=200)
        else:
            raws = c.issues_by_keys([k for k in (ctx.get("taskKeys") or []) if jira_key_allowed(k)])
    except Exception as e:
        return {"error": str(e)[:250]}

    today, rows = _today(), []
    for it in raws or []:
        f = (it.get("fields") or {})
        st = f.get("status") or {}
        done = ((st.get("statusCategory") or {}).get("key") or "").lower() == "done"
        if done and not include_done:
            continue
        due = _d(f.get("duedate"))
        upd = _d(f.get("updated"))
        rows.append(compact({
            "key": it.get("key"),
            "type": (f.get("issuetype") or {}).get("name"),
            "summary": f.get("summary"),
            "status": st.get("name"),
            "done": done,
            "priority": (f.get("priority") or {}).get("name"),
            "assignee": (f.get("assignee") or {}).get("name"),
            "duedate": f.get("duedate"),
            # 판단 재료를 **숫자로** 준다 — 모델이 날짜를 빼는 것보다 훨씬 덜 틀린다.
            "dueInDays": (due - today).days if due else None,
            "overdue": bool(due and due < today and not done),
            "staleDays": (today - upd).days if upd else None,
        }))
    rows.sort(key=lambda r: (r.get("dueInDays") is None, r.get("dueInDays", 9999)))
    return {"user": target, "today": today.isoformat(), "count": len(rows), "tickets": rows[:60]}


@tool
def find_stale_tickets(module: str = "", days: int = 14, limit: int = 15) -> dict:
    """**오래 아무도 손대지 않은 진행중 티켓** — 매니저가 챙겨야 할 것들.

    진행중인데 2주 넘게 업데이트가 없으면 대개 셋 중 하나다: 막혀 있거나, 잊혔거나, 이미
    끝났는데 상태를 안 옮겼거나. 어느 쪽이든 **누군가 물어봐야** 한다.

    module 을 주면 그 모듈만(예: "ETL"). days 는 '며칠째 조용한가'의 기준.
    """
    c = client()
    try:
        jql = jira_scope("statusCategory = indeterminate")
        if (module or "").strip():
            jql += f' AND component = "{module.strip()}"'
        jql += f" AND updated <= -{max(1, int(days or 14))}d ORDER BY updated ASC"
        from app.agent.tools.search_tools import _last_jql
        _last_jql.set(jql)            # "JQL로 보여줘" 요청이면 코드가 이 쿼리를 근거로 첨부한다
        raws = c.search_issues(jql, max_results=100)
    except Exception as e:
        return {"error": str(e)[:250]}

    today, rows = _today(), []
    for it in raws or []:
        f = it.get("fields") or {}
        upd = _d(f.get("updated"))
        rows.append(compact({
            "key": it.get("key"), "summary": f.get("summary"),
            "assignee": (f.get("assignee") or {}).get("name") or "(담당자 없음)",
            "status": (f.get("status") or {}).get("name"),
            "staleDays": (today - upd).days if upd else None,
            "duedate": f.get("duedate"),
        }))
    return {"module": module or "전체", "thresholdDays": days,
            "count": len(rows), "tickets": rows[:max(1, min(int(limit or 15), 40))]}


# ── 진척도 ─────────────────────────────────────────────────────────
@tool
def get_progress(target: str = "") -> dict:
    """**진척률** — Epic 키를 주면 그 Epic, 모듈명을 주면 그 모듈, 비우면 전체(WBS 롤업).

    숫자만 주지 않는다. **분모에서 빠진 것**(Bug·Ops·VoC·Epic Link 없는 티켓)과 SP 누락
    건수를 함께 준다 — "진척률이 왜 이런가"는 대개 그쪽에 답이 있다.

    돌려주는 것(Epic): {key, name, donePct, doneSp, totalSp, children:{total,done}}
    돌려주는 것(전체/모듈): 모듈별 진척률 + WBS Task 별 진척률
    """
    from app.infra.settings import load_plan
    c = client()
    tgt = (target or "").strip()

    try:
        plan = load_plan()
        if not search_projects():
            return {"error": "검색 범위 미설정 — search.jira.projects를 지정하세요"}
        # WBS는 화면의 write/destination 설정을 포함할 수 있다. Agent read에서는 허용된
        # project의 Epic만 남겨, epic_progress_map이 scope 밖 key를 prefetch하지 못하게 한다.
        plan = dict(plan)
        plan["wbs"] = [dict(w, epics=[dict(e) for e in (w.get("epics") or [])
                                      if jira_key_allowed(e.get("key"))])
                       for w in (plan.get("wbs") or [])]
        prog = c.epic_progress_map(plan)
    except Exception as e:
        return {"error": f"진척률을 계산하지 못했습니다: {str(e)[:200]}"}

    # Epic 하나
    if tgt and "-" in tgt:
        if not jira_key_allowed(tgt):
            return {"error": f"{tgt} 는 search.jira.projects 범위 밖입니다."}
        p = (prog or {}).get(tgt)
        if not p:
            return {"error": f"{tgt} 는 WBS 에 연결된 Epic 이 아닙니다. "
                             "wbs_config.yaml 에 없으면 집계에 잡히지 않습니다."}
        kids = c.epic_issues(tgt) or []
        done = sum(1 for k in kids if (k.get("statusCategory") or k.get("statusCat")) == "done")
        return compact({"epic": tgt, "name": p.get("name"),
                        "donePct": p.get("progressPct"), "doneSp": p.get("doneSp"),
                        "totalSp": p.get("totalSp"), "mockSp": p.get("mockSp"),
                        "children": {"total": len(kids), "done": done},
                        "note": "SP 가 없는 티켓은 1로, Bug 는 0으로 센다. "
                                "Bug·Ops·사용자 VoC 는 분모에서 빠진다."})

    # 모듈 또는 전체 — 화면(WBS Gantt)이 쓰는 rollup.build 를 그대로 쓴다.
    # 산식이 두 벌이 되면 에이전트의 숫자와 대시보드의 숫자가 갈라진다.
    try:
        from app.domain import rollup
        built = rollup.build(plan, prog)
    except Exception as e:
        return {"error": f"롤업에 실패했습니다: {str(e)[:200]}"}

    roll = built.get("rollup") or {}
    wbs_by_mod: dict[str, list] = {}
    for w in built.get("wbs") or []:
        wbs_by_mod.setdefault(w.get("moduleId") or "", []).append(w)

    mods = []
    for m in (roll.get("modules") or []):
        name = m.get("name") or m.get("id") or ""
        if tgt and name.lower() != tgt.lower():
            continue
        mods.append(compact({
            "module": name, "donePct": m.get("progressPct"),
            "start": m.get("start"), "end": m.get("end"),
            "tasks": [compact({"task": w.get("name"), "donePct": w.get("progressPct"),
                               "start": w.get("start"), "end": w.get("end"),
                               "epics": [compact({"key": e.get("epicKey"),
                                                  "pct": e.get("epicPct")})
                                         for e in (w.get("epics") or [])]})
                      for w in wbs_by_mod.get(m.get("id") or name, [])]}))
    if tgt and not mods:
        return {"error": f"'{tgt}' 모듈을 찾지 못했습니다. "
                         f"모듈: {', '.join((x.get('name') or '') for x in roll.get('modules') or [])}"}
    return compact({"scope": tgt or "전체",
                    "overallPct": (roll.get("pmo") or {}).get("progressPct"),
                    "modules": mods,
                    "note": "일정(start/end)은 Jira 가 아니라 wbs_config.yaml 이 갖는다 — "
                            "티켓 마감과 어긋나면 보고할 사실이다."})


# ── 타인 활동 (매니저 전용) ─────────────────────────────────────────
@tool
def get_user_activity(user_id: str, days: int = 3) -> dict:
    """**그 사람이 최근 며칠간 무엇을 했나** — 담당 티켓 변경·코멘트·문서 활동.

    ★ **매니저만 쓸 수 있다.** 매니저가 아니면 거부된다(프롬프트가 아니라 이 도구가 막는다).

    "A 작업자가 최근 3일간 뭐 했어?" 같은 질문에 쓴다. 활동이 적다고 곧바로 '일을 안 했다'로
    읽지 마라 — 긴 티켓 하나를 붙들고 있으면 기록이 적다. 무엇을 만졌는지를 보고 말하라.
    """
    uid = (user_id or "").strip()
    if not uid:
        return {"error": "누구를 볼지 알려 주세요(예: skcc.x1042)."}
    if not _is_manager():
        return {"error": "다른 사람의 활동 내역은 매니저만 볼 수 있습니다.", "denied": True}

    c = client()
    n = max(1, min(int(days or 3), 30))
    since = _today() - timedelta(days=n)
    out = {"user": uid, "days": n, "since": since.isoformat()}

    # ① 그 기간에 실제로 손댄 티켓 — 활동 스트림보다 이쪽이 확실하다.
    try:
        safe = uid.replace('"', " ")
        raws = c.search_issues(
            jira_scope(f'(assignee = "{safe}" OR reporter = "{safe}")') + " "
            f"AND updated >= -{n}d ORDER BY updated DESC", max_results=60)
        out["touched"] = [compact({
            "key": it.get("key"),
            "summary": (it.get("fields") or {}).get("summary"),
            "status": ((it.get("fields") or {}).get("status") or {}).get("name"),
            "updated": ((it.get("fields") or {}).get("updated") or "")[:10],
        }) for it in raws or []]
    except Exception as e:
        out["touched"], out["touched_error"] = [], str(e)[:150]

    # ② Jira/Confluence 활동 스트림 — 남의 티켓에 남긴 코멘트처럼 ①이 못 잡는 것이 여기 있다.
    try:
        act = c.activity(uid) or {}
        out["jiraActivity"] = [compact({"key": a.get("key"), "what": trim(a.get("summary"), 90),
                                        "when": (a.get("updated") or "")[:10]})
                               for a in (act.get("jira") or [])
                               if jira_key_allowed(a.get("key"))][:15]
        allowed_spaces = {x.upper() for x in search_spaces()}
        out["docActivity"] = [compact({"title": trim(a.get("title"), 90),
                                       "when": (a.get("updated") or "")[:10],
                                       "space": a.get("space") or a.get("spaceKey")})
                              for a in (act.get("confluence") or [])
                              if str(a.get("space") or a.get("spaceKey") or "").upper()
                              in allowed_spaces][:10]
    except Exception as e:
        out["activity_error"] = str(e)[:150]
    return out


@tool
def find_unassigned_tickets(module: str = "", limit: int = 15) -> dict:
    """**담당자가 비어 있는 미완료 티켓** — "담당자 없는 업무 있어?", "하나 집어 갈 일 없나".

    module 을 주면 그 모듈만(예: "ETL"). "내 모듈"이라고 했으면 먼저 whoami 로 모듈을
    알아낸 뒤 그 이름을 넣어라. 없으면 없다고 단정해서 답하면 된다 — 이 도구가 곧 근거다.
    """
    c = client()
    try:
        # `assignee is EMPTY` 를 JQL 에 넣지 않는다 — mock 이 이 절을 **조용히 무시**해서
        # 담당자 있는 티켓 전부가 "미배정"으로 둔갑했다(실측: 오답의 근원). 판정은 코드가 한다.
        jql = jira_scope("statusCategory != done")
        if (module or "").strip():
            jql += f' AND component = "{module.strip()}"'
        jql += " ORDER BY updated DESC"
        from app.agent.tools.search_tools import _last_jql
        _last_jql.set(jql + " AND assignee is EMPTY(코드 판정)")
        raws = c.search_issues(jql, max_results=300)
    except Exception as e:
        return {"error": str(e)[:250]}
    rows = []
    for it in raws or []:
        f = it.get("fields") or {}
        if f.get("assignee"):
            continue
        rows.append(compact({
            "key": it.get("key"), "summary": f.get("summary"),
            "type": (f.get("issuetype") or {}).get("name"),
            "status": (f.get("status") or {}).get("name"),
            "priority": (f.get("priority") or {}).get("name"),
            "duedate": f.get("duedate"),
            "component": ", ".join(x.get("name", "") for x in (f.get("components") or [])),
        }))
    return {"module": module or "전체", "count": len(rows),
            "tickets": rows[:max(1, min(int(limit or 15), 40))]}


@tool
def whoami() -> dict:
    """지금 이 대화의 사용자가 **누구이고 매니저인지**. 권한이 걸린 요청 전에 확인한다.

    "내 업무", "우리 모듈" 같은 말을 해석하려면 먼저 이걸 알아야 한다.
    """
    from app.infra.settings import load_people
    me = _me()
    mods = [m for m, ids in (load_people() or {}).items() if me and me in (ids or [])]
    return compact({"id": me or "(세션 사용자를 확인하지 못함)",
                    "manager": _is_manager(), "modules": mods,
                    "today": _today().isoformat()})
