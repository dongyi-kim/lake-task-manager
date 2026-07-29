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

import re
from datetime import date, datetime

from app.domain.names import real_name
from app.domain.progress import norm_cat

# 우선순위 정규화 — 인스턴스마다 이름이 다를 수 있어 이름을 소문자로 맞춰 본다.
# 못 알아보면 중간(2)으로 둔다: 모르는 값이 맨 위나 맨 아래로 튀는 게 제일 나쁘다.
# 우선순위 이름 → 등급(0=가장 급함). Jira 는 인스턴스마다 우선순위 **이름 체계가 다르다** —
# 기본 스킴(Highest/High/…)과 Blocker/Critical/Major/Minor/Trivial 스킴이 둘 다 흔하다.
# 모르는 이름은 2(보통)로 떨어지므로, 안 적어 두면 Blocker 가 '보통' 으로 표시된다(실제로 그랬다).
_PRI_RANK = {
    "highest": 0, "high": 1, "medium": 2, "normal": 2, "low": 3, "lowest": 4,
    "blocker": 0, "critical": 1, "major": 2, "minor": 3, "trivial": 4,
    "urgent": 0, "p1": 0, "p2": 1, "p3": 2, "p4": 3, "p5": 4,
}
_PRI_BAND = {0: "high", 1: "high", 2: "mid", 3: "low", 4: "low"}
# 'P0-Blocker' 처럼 **접두사 숫자로 등급을 말하는** 체계(사내 표준). 이름보다 이게 우선한다.
_P_PREFIX = re.compile(r"^\s*P\s*(\d+)", re.I)

# 상태 부분점수 — 완료만 100% 로 치면 진행 중인 일이 통째로 0 이라 롤업이 실제보다 어둡다.
# (WBS/Epic 진척률은 '완료/전체' 이진이 원칙이지만, 이 화면은 개인 트래킹용 체감 지표라 다르다.
#  두 숫자를 섞어 쓰지 말 것 — 목적이 다르다.)
_PARTIAL = {"done": 1.0, "inprogress": 0.4, "todo": 0.0}

_NO_DUE = 10 ** 6           # 마감 없음 = 맨 뒤. None 정렬 분기를 안 만들려고 큰 수를 쓴다
# '최근 완료' 로 볼 기간 선택지. 1주는 워크로드의 done7d 와 같은 창이다.
DONE_WINDOWS = {"1w": 7, "1m": 30}


def _days_until(due, today):
    if not due:
        return None
    try:
        d = datetime.strptime(str(due)[:10], "%Y-%m-%d").date()
    except Exception:
        return None
    return (d - today).days


def pri_rank(name):
    """우선순위 이름 → 등급(0=가장 급함). **이 프로젝트의 유일한 판정처**다.

    ★ 사내 체계는 'P0-Blocker … P4-Trivial' 이라 **접두사 숫자가 곧 등급**이다. 이름을 통째로
      표에서 찾는 방식은 사내 이름이 바뀌는 순간(P1-Critical → P1-치명) 전부 '보통' 으로
      떨어진다. 그래서 숫자를 먼저 읽고, 그게 없을 때만 이름 표로 간다
      (Highest/High/… · Blocker/Critical/… 같은 표준 스킴).
    """
    n = (name or "").strip()
    m = _P_PREFIX.match(n)
    if m:
        return min(int(m.group(1)), 4)
    return _PRI_RANK.get(n.lower(), 2)


def _pri(f):
    name = ((f.get("priority") or {}).get("name") or "").strip()
    return name, pri_rank(name)


def _cat(f):
    st = f.get("status") or {}
    return norm_cat((st.get("statusCategory") or {}).get("key"))


def _node(raw, today, epic_field):
    """이슈 원본 → 화면이 쓰는 얇은 노드."""
    f = raw.get("fields") or {}
    a = f.get("assignee") or {}
    rp = f.get("reporter") or {}
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
        "reporter": real_name(rp.get("displayName") or rp.get("name")) or None,
        "reporterId": rp.get("name"),
        "pri": pri_name, "priRank": pri_rank, "priBand": _PRI_BAND.get(pri_rank, "mid"),
        "due": f.get("duedate") or None,
        "dueDays": dd,
        "resolved": f.get("resolutiondate") or None,   # 완료 카드는 마감 대신 완료일을 보인다
        "sp": f.get(epic_field["sp"]),
        "epic": f.get(epic_field["epic"]) or None,
        # 사용자 VoC 는 Epic 이 없어도 **전용 Epic 처럼** 취급한다(색·묶음 모두).
        # 단 Epic 이 배정돼 있으면 그 Epic 이 우선이다 — 워크로드 Epic 분포와 같은 규칙.
        "voc": epic_field["voc"] in [c.get("name") for c in (f.get("components") or [])],
        "components": [c.get("name") for c in (f.get("components") or []) if c.get("name")],
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


def build_my_tasks(client, user=None, include_done=False, limit=200, scope="assignee",
                   open_filter="all", done_filter="1w"):
    """세션 사용자(또는 user 지정)의 '내 Task' 모델.

    scope: assignee(담당) | reporter(내가 등록) | both. '내 일'의 정의는 사람마다 다르다 —
    남에게 넘긴 뒤 결과를 봐야 하는 사람에게는 reporter 도 자기 일이다. JQL 조건이라 서버 몫.
    open_filter: all | 2w      — '할당됨' 축에 무엇을 담을지
    done_filter: 1w | 1m       — '최근 완료' 축의 기간

    담당 이슈를 한 번에 긁고(=JQL 1회), 부모/형제는 **필요한 것만** 배치로 채운다.
    prod SSO 는 직렬이라 왕복 횟수가 곧 체감 속도다.
    """
    me = user or (client.current_user() or {}).get("id")
    if not me:
        return {"user": None, "groups": [], "epics": [], "error": "세션 사용자를 확인할 수 없습니다."}

    today = client.s_today() if hasattr(client, "s_today") else date.today()
    ef = {"sp": client.s.sp_field_id, "epic": client.s.epic_link_field_id,
          "voc": client.s.voc_component}

    # 1) 내가 담당(또는 등록)한 이슈.
    #    화면이 '할당됨 / 진행 중 / 최근 완료' 3상태를 축으로 쓰므로 상태별 조건을 **한 질의**에 담는다
    #    (버킷마다 따로 부르면 prod SSO 는 직렬이라 왕복이 그대로 지연이 된다).
    #      · 할당됨: 오래 방치된 것까지 다 볼지(all), 최근 2주 안에 손댄 것만 볼지(2w)
    #      · 진행 중: 항상 전부 — 지금 하는 일을 숨기면 안 된다
    #      · 완료   : 최근 N일 안에 끝낸 것만(완료 전체를 넣으면 화면이 과거로 가득 찬다)
    # 스코프 해석 — assignee(기본) | reporter | both | module:<모듈명>.
    #   module 스코프: 그 모듈의 일감 = 모듈 인력(people.yaml) 중 하나가 담당/보고 **또는**
    #   티켓 component 가 그 모듈. (매니저 구분 없이 누구나 다른 모듈도 볼 수 있다.)
    #   assignee(기본) | reporter | both | mymodules | module:<모듈> |
    #   assignee:<사번> | reporter:<사번> | epic:<키>  — 뒤 셋은 '특정 사람/Epic' 필터(퀵필터 picker).
    mod_ids, mod_comps = set(), set()
    spec_user, epic_scope = None, None

    def _lit(s):
        """따옴표로 감싸는 JQL 리터럴 안전화 — 따옴표·역슬래시 제거(스코프 문자열은 사용자 입력)."""
        return "".join(c for c in (s or "") if c not in '"\\').strip()

    if scope == "mymodules" or scope.startswith("module:"):
        from app.infra.settings import load_people, modules_of
        people = load_people() or {}
        if scope == "mymodules":
            targets = modules_of(me)                      # 내가 속한 모듈 전체(union)
        else:
            m = scope.split(":", 1)[1].strip()
            targets = [m] if m in people else []
        for mod in targets:
            for pid in (people.get(mod) or []):
                if pid:
                    mod_ids.add(pid)
            mod_comps.add(mod)
        scope_kind = "module" if targets else "assignee"  # 대상 없으면 안전 폴백
    elif scope.startswith("assignee:"):
        spec_user = _lit(scope.split(":", 1)[1])
        scope_kind = "uassignee" if spec_user else "assignee"
    elif scope.startswith("reporter:"):
        spec_user = _lit(scope.split(":", 1)[1])
        scope_kind = "ureporter" if spec_user else "assignee"
    elif scope.startswith("epic:"):
        epic_scope = "".join(c for c in scope.split(":", 1)[1] if c.isalnum() or c == "-")
        scope_kind = "epic" if epic_scope else "assignee"
    else:
        scope_kind = scope if scope in ("reporter", "both") else "assignee"

    def _in(field, ids):
        return "%s in (%s)" % (field, ", ".join('"%s"' % i for i in sorted(ids)))

    # 상태 축 조건(할당됨/진행중/최근완료 세 버킷을 한 질의에). 캐시 키에도 반영해 필터가 다르면
    # 다른 캐시가 되게 한다(2w/1w 결과가 섞이지 않게).
    open_cond = 'statusCategory = "To Do"'
    if open_filter == "2w":
        open_cond += " AND updated >= -14d"
    done_days = DONE_WINDOWS.get(done_filter, DONE_WINDOWS["1w"])
    parts = ['statusCategory = "In Progress"', "(%s)" % open_cond,
             "(statusCategory = Done AND resolved >= -%dd)" % done_days]
    cond = "(%s)" % " OR ".join(parts)
    filt = "all" if include_done else ("%s.%s" % (open_filter, done_filter))
    env = getattr(client, "env", "?")

    def _where(base):
        j = base if include_done else "%s AND %s" % (base, cond)   # include_done 이면 상태 무관 전체
        return j + " ORDER BY duedate ASC"

    # (cache_key, jql) 서브쿼리 — `IN`/`OR` 을 **사람 하나치**로 쪼갠다. 사람별 목록은 모듈이
    # 겹치면 그대로 재사용되므로 각자 캐시 대상이다. 'me' 도 사람이라 같은 규칙을 쓴다:
    # currentUser 로 온 사번이든 picker(사번)든 **같은 사번 → 같은 `asg:{id}`/`rep:{id}` 키**로
    # 모여 currentUser()/username 이 따로 캐시되거나 중복 조회되지 않는다.
    subqs = []

    def _add(kkey, base):
        subqs.append(("mt:%s:%s:%s" % (env, kkey, filt), _where(base)))

    if scope_kind == "module":
        for p in sorted(mod_ids):
            _add("asgrep:%s" % p, '(assignee = "%s" OR reporter = "%s")' % (p, p))
        if mod_comps:
            _add("cmp:%s" % ",".join(sorted(mod_comps)), _in("component", mod_comps))
        if not subqs:
            _add("asg:%s" % me, 'assignee = "%s"' % me)
    elif scope_kind == "uassignee":
        _add("asg:%s" % spec_user, 'assignee = "%s"' % spec_user)
    elif scope_kind == "ureporter":
        _add("rep:%s" % spec_user, 'reporter = "%s"' % spec_user)
    elif scope_kind == "epic":
        _add("epic:%s" % epic_scope, '"Epic Link" = %s' % epic_scope)
    elif scope_kind == "reporter":
        _add("rep:%s" % me, 'reporter = "%s"' % me)
    elif scope_kind == "both":                            # 담당·보고를 따로 쪼개 각자 캐시(합집합은 병합)
        _add("asg:%s" % me, 'assignee = "%s"' % me)
        _add("rep:%s" % me, 'reporter = "%s"' % me)
    else:
        _add("asg:%s" % me, 'assignee = "%s"' % me)

    # 실행 + **취합/중복제거** — 한 티켓이 여러 사람 질의에 걸릴 수 있으니(예: A가 담당, B가 보고)
    # 이슈 키로 dedup 한다. Epic 은 실행 단위가 아니라 묶음이라 목록에서 뺀다.
    seen, mine_raw = set(), []
    for ck, jq in subqs:
        for r in client.search_issues(jq, max_results=limit, cache_key=ck):
            k = r.get("key")
            if not k or k in seen:
                continue
            if (((r.get("fields") or {}).get("issuetype") or {}).get("name")) == "Epic":
                continue
            seen.add(k)
            mine_raw.append(r)
    # Epic 은 담당자가 있어도 '실행 단위'가 아니라 묶음이다 — 목록에 섞으면 노이즈가 된다
    # (Epic 자체는 아래에서 그룹의 소속 표시로만 쓴다).
    def role_of(n):
        """이 일감에서 내 역할 — 화면이 '담당/등록'을 구분해 보여줄 수 있게."""
        a, r = n["assigneeId"] == me, n["reporterId"] == me
        return "both" if (a and r) else ("assignee" if a else ("reporter" if r else None))

    def is_mine(n):
        """이 스코프의 '대상 일'인가 — 하위 중 무엇을 원자로 뽑을지의 기준이다.
        module 스코프에선 '그 모듈 소속'(인력 담당/보고 or 모듈 component)을 대상으로 본다."""
        if scope_kind == "module":
            if n["assigneeId"] in mod_ids or n["reporterId"] in mod_ids:
                return True
            return any(c in mod_comps for c in (n.get("components") or []))
        if scope_kind == "uassignee":
            return n["assigneeId"] == spec_user
        if scope_kind == "ureporter":
            return n["reporterId"] == spec_user
        if scope_kind == "epic":
            return n.get("epic") == epic_scope          # 그 Epic 직속(Sub-Task 는 맥락으로만)
        if scope_kind == "reporter":
            return n["reporterId"] == me
        if scope_kind == "both":
            return n["assigneeId"] == me or n["reporterId"] == me
        return n["assigneeId"] == me

    mine = [_node(r, today, ef) for r in mine_raw]
    for n in mine:
        n["role"] = role_of(n)
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
            c["role"] = role_of(c)
            if c["parentKey"]:
                kid_of.setdefault(c["parentKey"], []).append(c)

    # 3) 그룹 구성 — 부모 Task 단위. 하위 없는 내 Task 는 자기 자신이 그룹이자 원자.
    groups = {}

    def group_of(node, has_kids):
        g = groups.get(node["key"])
        if not g:
            g = groups[node["key"]] = {
                "key": node["key"], "title": node["title"], "type": node["type"],
                "mine": is_mine(node), "role": node.get("role"),
                "assignee": node["assignee"], "assigneeId": node["assigneeId"],
                "status": node["status"], "statusCategory": node["statusCategory"],
                "reporter": node.get("reporter"), "reporterId": node.get("reporterId"),
                "voc": node.get("voc"),          # Epic 이 없어도 VoC 는 전용 Epic 처럼 묶고 색을 준다
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
            my_kids = [c for c in kids if is_mine(c)]
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
        # 화면에는 퍼센트 대신 **몇 개 중 몇 개**를 적는다. 퍼센트는 SP 가중이 섞인 값이라
        # "3/7" 처럼 손으로 세어 확인할 수가 없다 — 눈으로 대조되는 숫자가 신뢰를 만든다.
        g["kidsDone"] = sum(1 for c in kids if c["statusCategory"] == "done")
        g["kidsTotal"] = len(kids)
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
        epics.append({"key": ek, "title": client.epic_label(b, ek),   # Epic Name→Summary→키
                      "statusCategory": (b or {}).get("statusCategory") or "todo"})

    atoms = [a for g in out for a in g["atoms"]]
    return {"user": {"id": me, "name": (client.current_user() or {}).get("name") or me},
            "scope": scope, "openFilter": open_filter, "doneFilter": done_filter,
            "doneWindowDays": DONE_WINDOWS.get(done_filter, 7),
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
