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
    mods = [key] if key else list(roster)
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
