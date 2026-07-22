"""'내 Task' 모델 — 세션 사용자가 담당한 일감을 **부모 Task 단위 그룹**으로 묶어 돌려준다.

왜 그룹인가: 화면이 세 가지 뷰(시간 우선 / 부모 클러스터 / 계층 우선)를 제공하는데, 셋 다
같은 사실에서 파생된다 — "내 실행 원자(atom)가 무엇이고, 그게 어느 부모·어느 Epic 아래 있으며,
그 부모는 얼마나 진행됐는가". 그래서 서버는 뷰가 아니라 **그 사실 하나**를 준다.

용어:
- atom  : 내가 실제로 실행하는 단위. 내가 담당한 Sub-Task, 또는 하위가 없는 내 Task.
          내가 Task 담당이면서 하위가 있으면 그 Task 자체는 원자가 아니다(하위가 실행 단위).
- group : 원자를 담는 부모 Task. 내가 하위만 담당하는 남의 Task 도 그룹이 된다(맥락으로 필요).
- others: 같은 그룹 안의 **동료 하위** — 처음부터 펼치지 않고 집계만 준다(공간 절약).

정렬 키는 **여기서 계산해 내려보낸다**(프론트에서 매번 재계산하지 않게):
- atom.dueDays / atom.priRank
- group.urgency(= 내 원자 중 가장 급한 마감) / group.priRank(= 가장 높은 우선순위)
- group.pct(= SP 가중 롤업, 부분점수 포함)
"""

from datetime import date, datetime

from .names import real_name

# 우선순위 정규화 — 인스턴스마다 이름이 다를 수 있어 이름을 소문자로 맞춰 본다.
# 못 알아보면 중간(2)으로 둔다: 모르는 값이 맨 위나 맨 아래로 튀는 게 제일 나쁘다.
_PRI_RANK = {"highest": 0, "high": 1, "medium": 2, "normal": 2, "low": 3, "lowest": 4}
_PRI_BAND = {0: "high", 1: "high", 2: "mid", 3: "low", 4: "low"}

# 상태 부분점수 — 완료만 100% 로 치면 진행 중인 일이 통째로 0 이라 롤업이 실제보다 어둡다.
# (WBS/Epic 진척률은 '완료/전체' 이진이 원칙이지만, 이 화면은 개인 트래킹용 체감 지표라 다르다.
#  두 숫자를 섞어 쓰지 말 것 — 목적이 다르다.)
_PARTIAL = {"done": 1.0, "inprogress": 0.4, "todo": 0.0}

_NO_DUE = 10 ** 6           # 마감 없음 = 맨 뒤. None 정렬 분기를 안 만들려고 큰 수를 쓴다


def _days_until(due, today):
    if not due:
        return None
    try:
        d = datetime.strptime(str(due)[:10], "%Y-%m-%d").date()
    except Exception:
        return None
    return (d - today).days


def _pri(f):
    name = ((f.get("priority") or {}).get("name") or "").strip()
    rank = _PRI_RANK.get(name.lower(), 2)
    return name, rank


def _cat(f):
    st = f.get("status") or {}
    key = ((st.get("statusCategory") or {}).get("key") or "").lower()
    return {"new": "todo", "indeterminate": "inprogress", "done": "done"}.get(key, "todo")


def _node(raw, today, epic_field):
    """이슈 원본 → 화면이 쓰는 얇은 노드."""
    f = raw.get("fields") or {}
    a = f.get("assignee") or {}
    pri_name, pri_rank = _pri(f)
    dd = _days_until(f.get("duedate"), today)
    itype = (f.get("issuetype") or {}).get("name") or ""
    return {
        "key": raw.get("key") or "",
        "title": f.get("summary") or "",
        "type": itype,
        "isSub": bool((f.get("issuetype") or {}).get("subtask")),
        "status": (f.get("status") or {}).get("name") or "",
        "statusCategory": _cat(f),
        "assignee": real_name(a.get("displayName") or a.get("name")) or None,
        "assigneeId": a.get("name"),
        "pri": pri_name, "priRank": pri_rank, "priBand": _PRI_BAND.get(pri_rank, "mid"),
        "due": f.get("duedate") or None,
        "dueDays": dd,
        "sp": f.get(epic_field["sp"]),
        "epic": f.get(epic_field["epic"]) or None,
        "parentKey": ((f.get("parent") or {}).get("key")) or None,
    }


def _sp(n):
    """SP 누락 시 가중치 — 롤업이 0 으로 무너지지 않게 최소 1 (Bug 는 0 이 정상값이라 그대로)."""
    v = n.get("sp")
    if v is None:
        return 0 if n.get("type") == "Bug" else 1
    try:
        return float(v)
    except Exception:
        return 1


def _rollup(nodes):
    tot = sum(_sp(n) for n in nodes)
    if not tot:
        return 0
    got = sum(_sp(n) * _PARTIAL.get(n["statusCategory"], 0) for n in nodes)
    return int(round(got / tot * 100))


def build_my_tasks(client, user=None, include_done=False, limit=200):
    """세션 사용자(또는 user 지정)의 '내 Task' 모델.

    담당 이슈를 한 번에 긁고(=JQL 1회), 부모/형제는 **필요한 것만** 배치로 채운다.
    prod SSO 는 직렬이라 왕복 횟수가 곧 체감 속도다.
    """
    me = user or (client.current_user() or {}).get("id")
    if not me:
        return {"user": None, "groups": [], "epics": [], "error": "세션 사용자를 확인할 수 없습니다."}

    today = client.s_today() if hasattr(client, "s_today") else date.today()
    ef = {"sp": client.s.sp_field_id, "epic": client.s.epic_link_field_id}

    # 1) 내가 담당한 이슈 — 기본은 미완료만(완료까지 넣으면 화면이 과거로 가득 찬다)
    jql = 'assignee = "%s"' % me
    if not include_done:
        jql += " AND statusCategory != Done"
    jql += " ORDER BY duedate ASC"
    mine_raw = [r for r in client.search_issues(jql, max_results=limit)
                if ((r.get("fields") or {}).get("issuetype") or {}).get("name") != "Epic"]
    # Epic 은 담당자가 있어도 '실행 단위'가 아니라 묶음이다 — 목록에 섞으면 노이즈가 된다
    # (Epic 자체는 아래에서 그룹의 소속 표시로만 쓴다).
    mine = [_node(r, today, ef) for r in mine_raw]
    if not mine:
        return {"user": {"id": me}, "groups": [], "epics": [], "counts": _counts([])}

    # 2) 맥락 채우기 — 부모(내가 Sub 담당인 경우)와 하위(내 Task 의 동료 Sub 포함).
    #    하위는 JQL('parent in ...')이 아니라 이슈의 subtasks 필드로 모은다.
    #    구버전 Jira 는 parent JQL 지원이 들쭉날쭉이고, subtasks 는 이미 받아온 필드라 공짜다.
    def sub_keys(raw):
        return [x.get("key") for x in ((raw.get("fields") or {}).get("subtasks") or []) if x.get("key")]

    need_parents = sorted({n["parentKey"] for n in mine if n["isSub"] and n["parentKey"]})
    parent_raw = {r["key"]: r for r in client.issues_by_keys(need_parents)} if need_parents else {}
    parents = {k: _node(r, today, ef) for k, r in parent_raw.items()}

    # 하위를 알아야 하는 이슈 = 내 Task 들 + 위에서 가져온 부모들
    kid_keys = []
    for r in mine_raw:
        kid_keys += sub_keys(r)
    for r in parent_raw.values():
        kid_keys += sub_keys(r)
    kid_of = {}                                   # 부모키 -> [자식 노드]
    if kid_keys:
        for r in client.issues_by_keys(sorted(set(kid_keys))):
            c = _node(r, today, ef)
            if c["parentKey"]:
                kid_of.setdefault(c["parentKey"], []).append(c)

    # 3) 그룹 구성 — 부모 Task 단위. 하위 없는 내 Task 는 자기 자신이 그룹이자 원자.
    groups = {}

    def group_of(node, has_kids):
        g = groups.get(node["key"])
        if not g:
            g = groups[node["key"]] = {
                "key": node["key"], "title": node["title"], "type": node["type"],
                "mine": node["assigneeId"] == me,
                "assignee": node["assignee"], "assigneeId": node["assigneeId"],
                "status": node["status"], "statusCategory": node["statusCategory"],
                "epic": node["epic"], "pri": node["pri"], "priRank": node["priRank"],
                "priBand": node["priBand"], "due": node["due"], "dueDays": node["dueDays"],
                "atoms": [], "others": [], "hasSubs": has_kids,
                # 단독 = 하위가 없어 부모 헤더를 따로 그릴 필요가 없는 내 Task
                "standalone": not has_kids,
            }
        return g

    seen_atoms = set()

    def add_atom(g, node):
        if node["key"] in seen_atoms:
            return
        seen_atoms.add(node["key"])
        g["atoms"].append(node)

    for n in mine:
        if n["isSub"] and n["parentKey"]:
            p = parents.get(n["parentKey"])
            g = group_of(p, True) if p else group_of(n, False)
            add_atom(g, n)
        else:
            kids = kid_of.get(n["key"]) or []
            g = group_of(n, bool(kids))
            my_kids = [c for c in kids if c["assigneeId"] == me]
            if my_kids:
                for c in my_kids:
                    add_atom(g, c)      # 하위가 있으면 하위가 실행 단위 — Task 자체는 원자가 아니다
            else:
                add_atom(g, n)          # 하위가 없거나 전부 남의 것 → Task 자체가 원자

    # 4) 동료 하위 + 롤업 + 정렬 키
    for g in groups.values():
        kids = kid_of.get(g["key"]) or []
        mine_keys = {a["key"] for a in g["atoms"]}
        g["others"] = [c for c in kids if c["key"] not in mine_keys]
        g["hasSubs"] = bool(kids)
        # 롤업은 하위 전체 기준(동료 몫 포함) — 부모의 실제 진척이다.
        # 하위가 없으면 그 Task 하나의 상태가 곧 진척이라 별도 바를 그리지 않는다(pct=None).
        g["pct"] = _rollup(kids) if kids else None
        g["othersDone"] = sum(1 for c in g["others"] if c["statusCategory"] == "done")
        g["atoms"].sort(key=lambda a: (a["dueDays"] if a["dueDays"] is not None else _NO_DUE,
                                       a["priRank"], a["key"]))
        g["urgency"] = min((a["dueDays"] for a in g["atoms"] if a["dueDays"] is not None),
                           default=None)
        g["priRank"] = min((a["priRank"] for a in g["atoms"]), default=2)

    out = sorted(groups.values(),
                 key=lambda g: (g["urgency"] if g["urgency"] is not None else _NO_DUE,
                                g["priRank"], g["key"]))

    # 5) Epic 메타 — 그룹이 참조하는 것만(이름을 보여주려면 제목이 필요하다)
    epics = []
    for ek in sorted({g["epic"] for g in out if g["epic"]}):
        b = client.ticket_badge(ek)
        epics.append({"key": ek, "title": (b or {}).get("summary") or ek,
                      "statusCategory": (b or {}).get("statusCategory") or "todo"})

    atoms = [a for g in out for a in g["atoms"]]
    return {"user": {"id": me, "name": (client.current_user() or {}).get("name") or me},
            "today": today.isoformat(), "groups": out, "epics": epics,
            "counts": _counts(atoms)}


def _counts(atoms):
    """상단 요약 — '오늘 뭘 봐야 하나'의 첫 신호. 지남/오늘/이번주/전체·완료."""
    over = sum(1 for a in atoms if (a["dueDays"] is not None and a["dueDays"] < 0)
               and a["statusCategory"] != "done")
    today_n = sum(1 for a in atoms if a["dueDays"] == 0 and a["statusCategory"] != "done")
    week = sum(1 for a in atoms if a["dueDays"] is not None and 0 < a["dueDays"] <= 7
               and a["statusCategory"] != "done")
    return {"total": len(atoms), "overdue": over, "today": today_n, "week": week,
            "done": sum(1 for a in atoms if a["statusCategory"] == "done"),
            "noDue": sum(1 for a in atoms if a["dueDays"] is None)}
