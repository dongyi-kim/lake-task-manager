"""agent/tools/people_tools.py — Assigner 의 **근거 수집** 도구.

담당자 추천에서 중요한 건 이름이 아니라 **왜 그 사람인가**다. "한가해 보여서"는 근거가 아니다.
그래서 도구는 순위를 매겨 주지 않고 **신호를 모아만 준다** — 판단과 문장은 모델이 한다.
로직을 코드에 박아 두면 "P1 이 밀려 있으면 예외" 같은 현실의 결을 담을 수 없다.

네 가지 신호를 각각 다른 도구로 나눠 둔 이유는, 셋만 필요한 경우가 대부분이기 때문이다.

  ① 지금 얼마나 물려 있나   → get_team_workload
  ② 비슷한 일을 해 봤나     → search_work_history 결과의 assignee (Historian 이 이미 갖고 있다)
  ③ 그 논의에 실제로 꼈나   → get_ticket_participants   (코멘트·멘션까지 본다)
  ④ 그 모듈 사람인가        → get_person_profile        (+ 최근 활동)
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.agent.tools._ctx import client, compact, settings, trim


# UI 회귀 픽스처 전용 모듈 — 개발 world 한정. 담당 후보 풀에 들어가면 안 된다.
_FIXTURE_MODULES = {"TEST"}


# ── 이름 해석의 문맥 ────────────────────────────────────────────────
# 사용자 지시(2026-08-10): 사람을 **단순 이름**으로 부르면 동명이인 문제가 생긴다. 우선순위로
# 추리되, 그래도 갈리면 물어보고 **그 대화 안에서는 계속 기억**할 것.
#
#   ① config 유저 목록(people.yaml)에 있는 사람
#   ② 최근 확인한 Task 에 관련된 사람(담당·보고·코멘트)
#   ③ 현재 Jira 프로젝트에 참가하고 있는 사람
#
# 왜 이 순서인가 — **가까운 맥락일수록 그 사람일 확률이 높다.** "이다은"이라고만 했을 때
# 우리 팀 이다은일 확률이, 방금 보던 티켓의 이다은일 확률이, 그 다음이 전사의 이다은이다.
# 순위를 코드가 매기고 **고르는 것은 근거가 하나로 좁혀졌을 때만** 한다(짐작 금지).
#
# 스레드별로 들고 있는 이유: 같은 프로세스에서 여러 대화가 돈다. 남의 대화에서 확인한
# 사람을 내 대화에 끌어오면 그게 바로 새 오답이다.
_pctx = {"id": "", "keys": [], "known": {}}

# 멘션·사번 표기 — `[~skcc.x1042]` / `@skcc.x1042` / `skcc.x1042` 를 한 벌로 본다.
# 사내 id 포맷은 "{회사코드}.{사번}" 이다(config/people.yaml 머리말).
import re as _re_mod                                            # noqa: E402
_re_uid = _re_mod.compile(r"[\[@~\s]*~?\s*([a-zA-Z][\w-]*\.[\w-]+)\s*\]?")


def set_person_context(thread_id: str, keys=None):
    """턴마다 세션이 불러 준다 — 이 대화가 **방금 보던 티켓**이 무엇인지 알려 준다.

    thread 가 바뀌면 기억을 버린다(대화가 다르면 '그 사람'도 다르다).
    """
    tid = str(thread_id or "")
    if _pctx["id"] != tid:
        _pctx.update(id=tid, keys=[], known={})
    for k in (keys or []):
        k = str(k or "").strip().upper()
        if k and k not in _pctx["keys"]:
            _pctx["keys"].append(k)
    del _pctx["keys"][8:]              # 최근 것 위주 — 오래 끌면 '가까운 맥락'이 아니게 된다


def remember_person(name: str, user_id: str) -> None:
    """이 대화 안에서 '그 이름 = 이 사람'으로 굳힌다(사용자 확인 또는 단독 해석)."""
    n = strip_title(name)
    if n and user_id:
        _pctx["known"][n] = str(user_id)


def _related_people(keys) -> set:
    """② 최근 확인한 Task 에 얽힌 사람들 — 담당·보고·코멘트 작성자."""
    out = set()
    if not keys:
        return out
    c = client()
    for k in list(keys)[:5]:
        try:
            raw = c.get_issue(k) or {}
        except Exception:
            continue
        f = raw.get("fields") or {}
        for who in (f.get("assignee"), f.get("reporter")):
            uid = (who or {}).get("name") or ""
            if uid:
                out.add(uid)
        for cm in ((f.get("comment") or {}).get("comments") or [])[:20]:
            uid = ((cm.get("author") or {}).get("name")) or ""
            if uid:
                out.add(uid)
    return out


def _in_project(uid: str) -> bool:
    """③ 이 사람이 지금 프로젝트에 발을 담그고 있나 — 담당이든 보고든 한 건이라도."""
    try:
        pk = settings().project_key
        safe = str(uid).replace('"', " ")
        r = client().search_issues(
            f'project = {pk} AND (assignee = "{safe}" OR reporter = "{safe}")', max_results=1)
        return bool(r)
    except Exception:
        return False


def _count(bucket) -> int:
    """워크로드 번들의 한 버킷 → 건수. 화면은 타입별로 쪼개 보여주지만 추천엔 총량이면 된다."""
    if not isinstance(bucket, dict):
        return 0
    c = bucket.get("count")
    if isinstance(c, dict):
        return sum(v for v in c.values() if isinstance(v, (int, float)))
    return int(c or 0)


@tool
def get_team_workload(module: str = "") -> dict:
    """지금 **누가 얼마나 물려 있는지** — 인력별 열림/진행중/최근완료 건수.

    module 을 주면 그 모듈만(예: "ETL"), 비우면 전원. 담당자를 제안하기 전에 반드시 한 번 본다.
    다만 **건수만으로 정하지 않는다** — 일이 적은 사람이 그 일을 할 줄 안다는 뜻은 아니다.
    ②③④ 신호와 함께 읽는다.

    준 module 이 인력 명단(people.yaml)에 없으면 **전원**으로 넓혀서 돌려주되, "module" 에
    그 사실을 적어 돌려준다 — 그 목록은 물어본 모듈의 로스터가 아니다.

    돌려주는 것: {"module": …, "resolved": true|false|null,
                 "people": [{id,name,module,open,inProgress,done28d}...]}
    """
    from app.infra.settings import load_people, resolve_module
    from app.domain.workload import build_workload_person
    c = client()
    roster = load_people() or {}
    asked = (module or "").strip()
    # 컴포넌트 이름과 로스터 키는 사람이 각각 적는 두 벌이라 표기에서 갈린다 — 정규화로 한 번 더 본다.
    key = asked if asked in roster else resolve_module(asked)
    # ★ 못 찾았을 때 **조용히 전원으로 넓히지 않는다.** 지금까지는 넓힌 결과가
    #   "[ETL 로스터·부하]" 라는 이름표를 달고 Assigner 재료로 들어갔다 — 컴포넌트 이름
    #   하나가 안 맞으면 전사 명단이 그 모듈인 척한다(실측 갭: 로스터 키 불일치).
    #   넓히는 것 자체는 유지한다(후보 0명이 배정을 통째로 막는 것이 더 나쁘다). 다만
    #   **이름표는 사실대로** 달아, 읽는 쪽이 그 목록을 그 모듈이라고 믿지 않게 한다.
    # ★ 전원으로 넓힐 때 **UI 회귀 픽스처 모듈은 뺀다** — 개발 world 에만 있는 계정이
    #   담당 후보로 올라온다(실사용 사고: 담당 제안이 test.ui02 였다). 그 모듈을 콕 집어
    #   물었을 때만 보여 준다(픽스처를 점검할 일도 있다).
    mods = [key] if key else [m for m in roster if m not in _FIXTURE_MODULES]
    rows, seen = [], set()
    for m in mods:
        for uid in roster.get(m) or []:
            if uid in seen:
                continue
            seen.add(uid)
            try:
                b = build_workload_person(c, uid, 28)
            except Exception:
                continue
            rows.append(compact({"id": uid, "name": b.get("name"), "module": m,
                                 "open": _count(b.get("open")),
                                 "inProgress": _count(b.get("inProgress")),
                                 "done28d": _count(b.get("done7d"))}))
    rows.sort(key=lambda r: (r.get("inProgress", 0), r.get("open", 0)))
    label = key or (f"전체(요청한 '{asked}' 는 인력 명단에 없다)" if asked else "전체")
    return {"module": label, "resolved": (bool(key) if asked else None),
            "doneWindowDays": 28, "people": rows}


@tool
def get_ticket_participants(key: str) -> dict:
    """그 티켓에 **실제로 관여한 사람들** — 리포터·담당자·코멘트 작성자·멘션된 사람.

    "예전에 이 문제를 다뤄 본 사람"을 찾는 가장 강한 신호다. 담당자 필드만 보면 놓친다 —
    정작 그 논의를 끌고 간 사람은 코멘트에만 있는 경우가 많다.
    Historian 이 찾은 유사 티켓 2~3건에 대해 부른다.
    """
    from app.domain.search import _ticket_people
    try:
        uids = _ticket_people(client(), key) or []
    except Exception as e:
        return {"key": key, "people": [], "error": str(e)[:200]}
    return {"key": key, "people": list(uids)[:20],
            "note": "리포터·담당자·코멘트작성자·멘션이 등장 순서대로 섞여 있다(중복 제거됨)."}


@tool
def get_person_profile(user_id: str) -> dict:
    """한 사람을 깊게 본다 — 소속 모듈, 현재 워크로드, **최근 활동**(무슨 티켓·문서를 만졌나).

    후보를 2~3명으로 좁힌 뒤 부른다. 최근 활동은 "지금 무슨 맥락에 들어가 있는지"를 알려 주므로,
    비슷한 일이 이미 손에 있으면 그것 자체가 추천 근거가 된다(문맥 전환 비용이 없다).
    """
    from app.infra.settings import load_people
    from app.domain.workload import build_workload_person
    c = client()
    out = {"id": user_id}
    for mod, ids in (load_people() or {}).items():
        if user_id in (ids or []):
            out["module"] = mod
            break
    try:
        b = build_workload_person(c, user_id, 28)
        out["name"] = b.get("name")
        out["workload"] = {"open": _count(b.get("open")), "inProgress": _count(b.get("inProgress")),
                           "done28d": _count(b.get("done7d"))}
    except Exception as e:
        out["workload_error"] = str(e)[:150]
    try:
        act = c.activity(user_id) or {}
        out["recentJira"] = [compact({"key": a.get("key"), "what": trim(a.get("summary"), 90),
                                      "when": (a.get("updated") or "")[:10]})
                             for a in (act.get("jira") or [])[:8]]
        out["recentDocs"] = [compact({"title": trim(a.get("title"), 90),
                                      "when": (a.get("updated") or "")[:10]})
                             for a in (act.get("confluence") or [])[:5]]
    except Exception as e:
        out["activity_error"] = str(e)[:150]
    return compact(out)


@tool
def get_module_people(key_or_component: str) -> dict:
    """어떤 티켓/컴포넌트가 속한 **모듈의 사람들**. 후보 풀을 만들 때 첫 단계로 쓴다.

    티켓 키(DL-123)를 주면 그 티켓의 컴포넌트·WBS 로 모듈을 역추적하고, 모듈명이나 컴포넌트명을
    바로 줘도 된다. 모듈을 못 찾으면 빈 목록 — 그때는 get_team_workload 로 전원을 본다.
    """
    from app.infra.settings import load_people
    from app.domain.search import _module_people
    roster = load_people() or {}
    tok = (key_or_component or "").strip()
    if tok in roster:
        return {"module": tok, "people": list(roster[tok])}
    try:
        uids = _module_people(client(), settings(), tok) or []
    except Exception as e:
        return {"module": None, "people": [], "error": str(e)[:200]}
    mod = next((m for m, ids in roster.items() if uids and set(uids) & set(ids or [])), None)
    return {"module": mod, "people": list(uids)[:30]}


@tool
def find_person(name: str) -> dict:
    """**이름으로 사람을 찾는다** — 모듈 로스터가 아니라 Jira 사용자 전체에서.

    왜 이 도구가 따로 있나(실사용 사고): "지금 이다은이 담당한 테스크들"에 에이전트가
    ①최근 3일 활동만 보고 "기록 없음" ②"'TEST' 모듈 로스터에 없다"로 답했다. 둘 다 틀렸다 —
    이다은은 ETL 모듈 사람이고 **진행 중 티켓이 8건** 있었다. 원인은 사람을 **이름으로**
    찾을 방법이 도구에 없어서, 모델이 모듈 로스터와 활동 창으로 밀려난 것이다.

    규칙(사용자 지시):
      · 대화에서 모듈을 **한정하지 않았으면 모듈로 좁히지 않는다** — 다른 모듈 사람도 조사한다.
      · Jira 사용자 디렉토리에 없으면 **존재하지 않는 사람**으로 본다(추측하지 않는다).
      · **동명이인**이면 고르지 말고 `candidates` 를 그대로 사용자에게 보여 확인받는다 —
        표시 이름(소속 포함)과 이메일을 함께 내야 사용자가 고를 수 있다.

    반환: {query, candidates:[{id, display, name, email, module}], resolved, ambiguous, assigned}
      resolved 가 있으면 그 사람이 **지금 들고 있는 일**(assigned)까지 함께 담는다 —
      한 번 찾았으면 다시 순회할 이유가 없다(이 저장소의 사전취합 규율).
    """
    from app.infra.settings import load_people
    q = strip_title(name)
    if not q:
        return {"query": "", "candidates": [], "resolved": "", "ambiguous": False}
    # ★ **사용자가 사번으로 지목했으면 헷갈릴 일이 없다**(사용자 지시). 멘션 표기
    #   `[~skcc.x1042]` 든 사번을 그대로 적었든, 그건 이름이 아니라 **식별자**다 —
    #   동명이인 판정에 넣을 이유가 없고, 넣으면 지목한 사람을 두고 되묻게 된다.
    _m = _re_uid.fullmatch(str(name or "").strip())
    if _m:
        uid = _m.group(1)
        return {"query": uid, "candidates": [], "resolved": uid, "ambiguous": False,
                "why": "사용자가 사번으로 지목", "assigned": _assigned_now(uid)}

    # ★ 이 대화에서 **이미 확인한 이름**이면 다시 묻지 않는다(사용자 지시).
    if q in _pctx["known"]:
        uid = _pctx["known"][q]
        return {"query": q, "candidates": [], "resolved": uid, "ambiguous": False,
                "why": "이 대화에서 이미 확인한 사람", "assigned": _assigned_now(uid)}
    c = client()
    try:
        raw = c.provider.get_json("/rest/api/2/user/search",
                                  params={"username": q, "maxResults": 10}) or []
    except Exception as e:
        return {"query": q, "candidates": [], "error": str(e)[:150]}
    roster = load_people() or {}
    mod_of = {uid: mod for mod, ids in roster.items() for uid in (ids or [])}
    cands = []
    for u in raw:
        uid = u.get("name") or u.get("key") or ""
        if not uid:
            continue
        disp = u.get("displayName") or uid
        cands.append(compact({"id": uid, "display": disp, "name": _real(disp),
                              "email": u.get("emailAddress") or "",
                              "module": mod_of.get(uid, "")}))
    out = {"query": q, "candidates": cands, "resolved": "", "ambiguous": len(cands) > 1}
    if not cands:
        out["note"] = (f"'{q}' 은(는) 우리 Jira 사용자 디렉토리에 없다 — **존재하지 않는 "
                       "사람으로 본다.** 비슷한 이름을 지어내거나 다른 사람으로 바꿔 답하지 마라.")
        return out

    # ── 우선순위로 추린다(사용자 지시) ─────────────────────────────
    # ① config 유저 목록 → ② 최근 확인한 Task 관련자 → ③ 프로젝트 참가자.
    # **가까운 맥락일수록 그 사람일 확률이 높다.** 다만 순위는 '고르는 근거'이지 '고르는
    # 권한'이 아니다 — 같은 층에 둘이 남으면 코드는 손을 떼고 사용자에게 묻는다.
    related = _related_people(_pctx["keys"]) if len(cands) > 1 else set()
    for cd in cands:
        uid = cd["id"]
        if uid in mod_of and mod_of[uid] not in _FIXTURE_MODULES:
            cd["tier"], cd["why"] = 1, f"config 인력({mod_of[uid]})"
        elif uid in related:
            cd["tier"], cd["why"] = 2, "최근 확인한 Task 관련자"
        elif len(cands) > 1 and _in_project(uid):
            cd["tier"], cd["why"] = 3, "이 프로젝트 참가자"
        else:
            cd["tier"], cd["why"] = 9, "우리 프로젝트에서 확인되지 않음"
    cands.sort(key=lambda c: c["tier"])
    top = [c for c in cands if c["tier"] == cands[0]["tier"] and c["tier"] < 9]

    if len(top) == 1:
        # 한 층에 한 명뿐 — **근거가 하나로 좁혀졌다.** 고르고, 이 대화에서 기억한다.
        pick = top[0]
        out.update(resolved=pick["id"], ambiguous=False,
                   why=pick["why"], assigned=_assigned_now(pick["id"]))
        remember_person(q, pick["id"])
        if len(cands) > 1:
            out["note"] = (f"동명이인이 {len(cands)}명이지만 **{pick['why']}** 근거로 "
                           f"{pick['display']} 로 봤다. 답변에 그 근거를 한 줄 밝혀라.")
        return out

    # 같은 층에 둘 이상이거나(진짜 동명이인) 아무도 우리 쪽이 아니다 — **묻는다.**
    out["ambiguous"] = True
    out["note"] = ("동명이인이다 — **고르지 말고 사용자에게 확인받아라.** 보기에는 표시 "
                   "이름(소속 포함)·이메일과 함께 `why`(어느 근거로 후보인지)를 적어라. "
                   "사용자가 고르면 `confirm_person` 으로 굳혀서 이 대화에서 다시 묻지 마라.")
    return out


@tool
def confirm_person(name: str, user_id: str) -> dict:
    """사용자가 고른 사람을 **이 대화에서 굳힌다** — 같은 이름을 두 번 묻지 않기 위해.

    동명이인 확인을 받은 **직후에 부른다.** 이후 이 대화에서 그 이름은 이 사람이다.
    (대화가 바뀌면 잊는다 — 남의 대화에서 확인한 사람을 끌어오면 그게 새 오답이다.)
    """
    n = strip_title(name)
    if not (n and user_id):
        return {"ok": False, "error": "이름과 사번이 모두 필요하다"}
    remember_person(n, user_id)
    return {"ok": True, "name": n, "user_id": user_id,
            "assigned": _assigned_now(user_id),
            "note": f"이 대화에서 '{n}' 은(는) {user_id} 다 — 다시 묻지 마라."}


# 사람 이름 뒤에 붙는 **호칭·직함** — 실사용에서 이대로 들어온다(사용자 지적):
#   "김동이 M", "윤산성매니저", "박지영차장", "홍길동 TL", "이재민파트장님"
# 붙여 쓰기도 하고 띄어 쓰기도 하며, 영문 약칭(M/TL/PL/PM/PO)도 섞인다. 이걸 그대로 검색에
# 넣으면 Jira 사용자 디렉토리에서 **못 찾고**, 그러면 "존재하지 않는 사람"으로 답하게 된다 —
# 실제로 있는 동료를 없다고 하는 것이 이 부류에서 가장 나쁜 실패다.
_TITLES = ("파트장", "그룹장", "본부장", "팀장", "실장", "부장", "차장", "과장", "대리",
           "사원", "선임", "책임", "수석", "매니저", "리더", "님", "씨")
_TITLES_EN = ("TL", "PL", "PM", "PO", "EM", "M", "L")


def strip_title(name: str) -> str:
    """이름에서 호칭·직함을 떼어 낸다. 뗄 것이 없으면 원문 그대로.

    떼고 나서 **두 글자 미만이면 원문을 쓴다** — "이 M" 같은 입력에서 이름까지 깎아
    아무나 걸리게 하는 것보다, 못 찾는 편이 낫다(찾는 척하는 것이 더 나쁘다).
    """
    import re as _r
    s = str(name or "").strip()
    if not s:
        return ""
    base = s
    for _ in range(3):                      # "이재민파트장님" 처럼 두 겹으로 붙는다
        for t in _TITLES:
            if base.endswith(t) and len(base) - len(t) >= 2:
                base = base[: -len(t)].strip()
                break
        else:
            break
    # 영문 약칭은 **낱말로 떨어져 있을 때만** 뗀다 — "김동이 M" 의 M 은 호칭이지만
    # 이름 안의 글자를 지우면 안 된다.
    m = _r.match(r"^(.+?)[\s./,-]+([A-Za-z]{1,2})$", base)
    if m and m.group(2).upper() in _TITLES_EN and len(m.group(1).strip()) >= 2:
        base = m.group(1).strip()
    base = base.strip(" ·,./-")
    return base if len(base) >= 2 else s


def _real(display: str) -> str:
    """'{본명} {회사}' → 본명. 표시용 짧은 이름."""
    try:
        from app.jira.jira_client import real_name
        return real_name(display) or display
    except Exception:
        return display


def _assigned_now(uid: str) -> dict:
    """그 사람이 **지금 들고 있는 일** — 진행 중과 열린 것. 모듈로 좁히지 않는다.

    "담당한 테스크"는 활동 로그(최근 며칠 무엇을 만졌나)가 아니라 **할당된 티켓**이다.
    이 둘을 섞어서 "최근 3일 활동 기록이 없습니다"로 답한 것이 실사용 사고였다.
    """
    from app.agent.tools.search_tools import run_jql
    try:
        # ★ 반환 키는 `tickets` 다(`issues` 가 아니다) — 처음 `issues` 로 읽어 **8건 있는
        #   사람을 0건으로** 돌려줬다. 도구 계약을 눈으로 확인하지 않고 이름을 짐작한 탓이다.
        r = run_jql.invoke({
            "jql": f'assignee = "{uid}" AND statusCategory in (new, indeterminate) '
                   "ORDER BY updated DESC", "limit": 40}) or {}
        rows = r.get("tickets") or []
        return {"jql": r.get("jql"), "count": len(rows), "tickets": rows[:40],
                "note": "할당된 **미완료** 티켓 전부다(모듈로 좁히지 않았다). "
                        "'최근 활동'과 다른 것이다 — 활동은 며칠간 무엇을 만졌나이고, "
                        "이건 지금 손에 들고 있는 일이다."}
    except Exception as e:
        return {"error": str(e)[:150]}
