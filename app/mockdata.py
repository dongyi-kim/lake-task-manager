"""
mock 모드 어댑터 — app/world.py 의 단일 world 를 in-process 로 소비한다.
(fake 서버는 같은 world 를 HTTP 로 서빙 → mock 출력 == local 출력)

공개 함수 시그니처는 유지: epic_issues / vit_issues / workload_people / activity.
"""

from datetime import timedelta

from . import world as worldmod
from .names import real_name


def _w():
    return worldmod.get_world()


def _dispname(w, uid):
    return (w.users.get(uid) or {}).get("displayName") or uid


# ── 기능1: Epic 이름 / 자식 ──
def epic_name(epic_key):
    it = _w().issues.get(epic_key)
    return it["summary"] if it else epic_key


def epic_issues(epic_key):
    w = _w()
    out = []
    for ck in w.epic_children.get(epic_key, []):
        it = w.issues[ck]
        out.append({"key": ck, "type": it["type"], "sp": it["sp"],
                    "statusCategory": it["statusCategory"], "labels": it["labels"],
                    "component": it.get("component")})
    return out


def epic_raw_children(epic_key):
    """Epic 직속 자식(Story/Task/Bug 등)을 raw Jira 이슈 형태로 — subtasks(상태 포함) 그대로.
    local/prod 의 search 결과와 동일 형태 → 같은 _display_node 로 트리를 만든다(파리티)."""
    w = _w()
    return [w.jira_issue(ck) for ck in w.epic_children.get(epic_key, [])]


def raw_issue(key):
    """단일 티켓 raw Jira 형태 (없으면 None) — 범용 /api/issue/{key} 용."""
    return _w().jira_issue(key)


def issue_comments(key, limit=5):
    """단일 티켓 코멘트 — _issue_comments(비-mock) 와 동일 정규화 형태."""
    cs = _w().jira_comments(key) or []
    return [{
        "date": (c.get("created") or "")[:10],
        "author": ((c.get("author") or {}).get("displayName") or (c.get("author") or {}).get("name")),
        "text": (c.get("body") or "")[:200],
    } for c in cs[:limit]]


# ── 기능2: PMO_VIT 현안 (tree/ancestors/comments — build_vit 가 계산) ──
def _node(w, key):
    it = w.issues[key]
    node = {
        "key": key, "summary": it["summary"], "type": it["type"],
        "assignee": real_name(_dispname(w, it["assignee"])),
        "statusCategory": it["statusCategory"], "status": it["statusName"],
        "created": w._dt(it["created"], it.get("tcreated")),
        "resolved": w._dt(it["resolved"], it.get("tresolved")) if it["resolved"] else None,
        "children": [_node(w, sk) for sk in it["subtasks"] if sk in w.issues],
    }
    return node


def _tree(w, root_key):
    it = w.issues[root_key]
    if it["type"] == "Epic":
        return [_node(w, ck) for ck in w.epic_children.get(root_key, [])]
    return [_node(w, sk) for sk in it["subtasks"] if sk in w.issues]


def vit_issues(plan, people, epic_prog=None):
    w = _w()
    out = []
    for key in w.by_label.get("PMO_VIT", []):
        it = w.issues[key]
        ancestors = []
        if it["parentKey"]:
            ancestors.append(it["parentKey"])
        if it["epicKey"]:
            ancestors.append(it["epicKey"])
        cat = it["statusCategory"]
        started = None
        if cat != "todo":
            started = it["created"] + timedelta(days=max((it["updated"] - it["created"]).days, 0) // 3)
        out.append({
            "key": key, "summary": it["summary"], "type": it["type"], "module": it["module"],
            "assignee": _dispname(w, it["assignee"]),
            "start": it["created"].isoformat()[:10],
            "created": it["created"].isoformat()[:10],
            "started": started.isoformat()[:10] if started else None,
            "updated": w._dt(it["updated"], it.get("tupdated")),   # 시간까지(Updated At)
            "due": it["due"].isoformat() if it["due"] else None,
            "statusCategory": it["statusCategory"], "status": it["statusName"],
            "ancestors": [a for a in ancestors if a],
            "tree": _tree(w, key),
            "comments": [{"date": w._dt(c["created"], c.get("tcreated")), "author": _dispname(w, c["author"]),
                          "text": f"({c['kind']}) {c['text']}"} for c in it["comments"]],
        })
    return out


# ── 기능3: 워크로드 (Task성 / VoC성 × 진행중 / 최근7일 완료) ──
# Epic 은 카운트 무의미 → 제외. VoC성 = Component 가 "사용자 VoC" 인 티켓(고객의 소리성).
def wl_category(component, itype):
    if component == "사용자 VoC":
        return "voc"
    if itype == "Sub-Task":
        return "subtask"
    if itype == "Task":
        return "task"
    return None                       # Epic·기타(Story/Bug/…)는 워크로드 카운트 제외


def _zero_metrics():
    # count(티켓수) · hr(소요시간, 시). 카테고리 3분할: task / subtask / voc.
    return {"count": {"task": 0, "subtask": 0, "voc": 0}, "hr": {"task": 0, "subtask": 0, "voc": 0}}


def _accum(m, cat, it):
    m["count"][cat] += 1
    secs = sum(wl.get("seconds", 0) for wl in it.get("worklog", []))   # jira_client 의 timespent 파리티
    m["hr"][cat] += round(secs / 3600.0, 1)


def workload_people(plan, people):
    w = _w()
    today = w.today
    out = {}
    for module in plan["modules"]:
        rows = []
        for pid in people.get(module, []):
            ip = _zero_metrics()
            dn = _zero_metrics()
            for k in w.by_assignee.get(pid, []):
                it = w.issues[k]
                c = wl_category(it["component"], it["type"])
                if not c:
                    continue
                if it["statusCategory"] == "inprogress":
                    _accum(ip, c, it)
                elif it["statusCategory"] == "done" and it["resolved"] and (today - it["resolved"]).days <= 7:
                    _accum(dn, c, it)
            rows.append({"id": pid, "displayName": _dispname(w, pid), "inProgress": ip, "done7d": dn})
        out[module] = rows
    return out


def workload_tickets(user):
    """인력 상세용: 진행중 / 최근7일 완료 티켓 리스트 (카운트와 동일 필터·기준)."""
    w = _w()
    today = w.today
    ip, dn = [], []
    for k in w.by_assignee.get(user, []):
        it = w.issues[k]
        if wl_category(it["component"], it["type"]) is None:
            continue
        row = {"key": k, "summary": it["summary"], "type": it["type"],
               "status": it["statusName"], "statusCategory": it["statusCategory"],
               "due": it["due"].isoformat() if it.get("due") else None, "resolved": None}
        if it["statusCategory"] == "inprogress":
            ip.append(row)
        elif it["statusCategory"] == "done" and it["resolved"] and (today - it["resolved"]).days <= 7:
            row["resolved"] = w._dt(it["resolved"], it.get("tresolved"))
            dn.append(row)
    return {"user": user, "inProgress": ip, "done7d": dn}


# ── 기능3: 인력 활동 (Jira 이벤트 + Confluence) ──
def activity(user):
    w = _w()
    # 캡 20 = 클라이언트 _parse_activity limit (local 파리티). 프론트가 다시 [:10] 로 자른다.
    jira = [{"date": w._dt(e["date"], e.get("time")), "kind": e["kind"], "key": e["key"], "summary": e["summary"]}
            for e in w.activity.get(user, [])[:20]]
    conf = [{"date": w._dt(p["date"], p.get("time")), "title": p["title"], "space": p["space"]}
            for p in w.confluence.get(user, [])]
    return {"user": user, "jira": jira, "confluence": conf}
