"""
mock 모드 어댑터 — app/world.py 의 단일 world 를 in-process 로 소비한다.
(fake 서버는 같은 world 를 HTTP 로 서빙 → mock 출력 == local 출력)

공개 함수 시그니처는 유지: epic_issues / vit_issues / workload_people / activity.
"""

from . import world as worldmod


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
                    "statusCategory": it["statusCategory"], "labels": it["labels"]})
    return out


# ── 기능2: PMO_VIT 현안 (tree/ancestors/comments — build_vit 가 계산) ──
def _node(w, key):
    it = w.issues[key]
    node = {
        "key": key, "summary": it["summary"], "type": it["type"],
        "statusCategory": it["statusCategory"], "status": it["statusName"],
        "created": it["created"].isoformat(),
        "resolved": it["resolved"].isoformat() if it["resolved"] else None,
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
        out.append({
            "key": key, "summary": it["summary"], "type": it["type"], "module": it["module"],
            "assignee": _dispname(w, it["assignee"]),
            "start": it["created"].isoformat()[:10],
            "due": it["due"].isoformat() if it["due"] else None,
            "statusCategory": it["statusCategory"], "status": it["statusName"],
            "ancestors": [a for a in ancestors if a],
            "tree": _tree(w, key),
            "comments": [{"date": c["created"].isoformat(), "author": _dispname(w, c["author"]),
                          "text": f"({c['kind']}) {c['text']}"} for c in it["comments"]],
        })
    return out


# ── 기능3: 워크로드 (Task성 / VoC성 × 진행중 / 최근7일 완료) ──
# Epic 은 카운트 무의미 → 제외. VoC = Component 가 "VoC" 인 티켓(고객의 소리성).
def wl_category(component, itype):
    if component == "VoC":
        return "voc"
    if itype in ("Task", "Sub-Task"):
        return "task"
    return None                       # Epic·기타(Story/Bug/…)는 워크로드 카운트 제외


def workload_people(plan, people):
    w = _w()
    today = w.today
    out = {}
    for module in plan["modules"]:
        rows = []
        for pid in people.get(module, []):
            ip = {"task": 0, "voc": 0}
            dn = {"task": 0, "voc": 0}
            for k in w.by_assignee.get(pid, []):
                it = w.issues[k]
                c = wl_category(it["component"], it["type"])
                if not c:
                    continue
                if it["statusCategory"] == "inprogress":
                    ip[c] += 1
                elif it["statusCategory"] == "done" and it["resolved"] and (today - it["resolved"]).days <= 7:
                    dn[c] += 1
            rows.append({"id": pid, "inProgress": ip, "done7d": dn})
        out[module] = rows
    return out


# ── 기능3: 인력 활동 (Jira 이벤트 + Confluence) ──
def activity(user):
    w = _w()
    jira = [{"date": e["date"].isoformat(), "kind": e["kind"], "key": e["key"], "summary": e["summary"]}
            for e in w.activity.get(user, [])[:12]]
    conf = [{"date": p["date"].isoformat(), "title": p["title"], "space": p["space"]}
            for p in w.confluence.get(user, [])]
    return {"user": user, "jira": jira, "confluence": conf}
