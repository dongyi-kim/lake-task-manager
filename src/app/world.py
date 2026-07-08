"""
Fake world — 단일 결정적 Jira/Confluence 데이터 세계.
fake 서버(HTTP)와 mock 모드(in-process)가 이 world 를 공유한다.

- config/wbs_config.yaml(module→WBS→epic) + people.yaml 로부터 결정적으로 생성.
- 모든 이슈(Epic/Story/Task/Bug/Sub-task)는 world.issues 에 canonical 형태로 존재.
- 인덱스: by_label, by_assignee, epic_children.
- Jira REST 직렬화 + Confluence/activity 소스 제공.
"""

import hashlib
import random
from datetime import date, timedelta
from functools import lru_cache

from . import worldcontent as wc
from .settings import get_settings, load_people, load_plan

# 사내 워크플로 상태 (Open/In Progress/Resolved/Closed/Reopened) → 내부 cat
_STATUS_NAMES = {"todo": ["Open", "Open", "Reopened"],
                 "inprogress": ["In Progress"],
                 "done": ["Resolved", "Resolved", "Closed"]}
_STATUS_ID = {"Open": "1", "In Progress": "3", "Reopened": "4", "Resolved": "5", "Closed": "6"}
# 내부 cat → 실 Jira DC statusCategory (key=new/indeterminate/done). prod 와 동일해야 함.
_JIRA_CAT = {
    "todo": {"id": 2, "key": "new", "colorName": "blue-gray", "name": "To Do"},
    "inprogress": {"id": 4, "key": "indeterminate", "colorName": "yellow", "name": "In Progress"},
    "done": {"id": 3, "key": "done", "colorName": "green", "name": "Done"},
}
# 사내 이슈타입: Bug, Epic, Improvement, New Feature, Story, Task, Sub-Task
SUBTASK_TYPE = "Sub-Task"
_STORYLIKE = {"Story", "Task", "Improvement", "New Feature"}   # SP 보유 + subtask 가능
_CHILD_TYPES = ["Story", "Task", "Bug", "Improvement", "New Feature"]
_VIT_ROOT_TYPES = ["Epic", "Task", "Story", "Improvement"]
_VIT_SIZES = [5, 2, 4, 0, 6, 3, 1]          # 모듈별 PMO_VIT 현안 개수(다양성)
_CHILD_TYPES_W = [6, 4, 2, 2, 1]            # 가중치


def _rng(*parts):
    seed = int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def _cat(rng, maturity):
    return "done" if rng.random() < maturity else ("inprogress" if rng.random() < 0.5 else "todo")


# 사용자 표시이름 합성 풀 — displayName "{본명} {소속회사명}". 회사는 SKCC 다수 + 협력사 소수
# (공백 포함 'SK주식회사 C&C' 로 본명 파싱[names.real_name=첫어절]의 다어절 회사 견고성도 커버).
_SURNAMES = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임",
             "한", "오", "서", "신", "권", "황", "안", "송", "류", "홍"]
_GIVEN = ["도윤", "서준", "하준", "지호", "예준", "민재", "수아", "지우", "서연", "하은",
          "은우", "지훈", "현우", "유진", "다은", "준서", "시우", "민서", "채원", "지안"]
_COMPANIES = ["SKCC", "SKCC", "SKCC", "SKCC", "코어씨앤아이", "데이터메이커", "SK주식회사 C&C"]


def _shash(s):
    """id → 결정적 정수(PYTHONHASHSEED 무관). 본명/회사 배정용."""
    h = 0
    for ch in s:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return h


class World:
    def __init__(self, plan, people, today):
        self.today = today
        self.plan = plan
        self.people = people
        self.project = plan.get("project_key", "LAKE")
        s = get_settings()
        self.sp_field = s.sp_field_id
        self.epic_link_field = s.epic_link_field_id

        self.modules = list(plan["modules"])
        self.users = self._make_users()
        self.issues = {}                 # key -> canonical issue
        self._counter = 5000             # 생성 키 DL-5001+ (config epic id DL-1xx 와 충돌 회피)

        self._build_wbs_epics()
        self._build_extra_epics()     # WBS 밖 일반 epic
        self._build_standalone()      # epic 소속 아닌 독립 task/story
        self._build_history()         # 1~6월 생성·종료된 과거 완료 이슈 (대량)
        self._build_vit()
        self._build_voc()
        self._index()
        self._build_activity()
        self._build_confluence()

    # ── 사용자 ──
    def _make_users(self):
        # 사내 관례: displayName = "{본명} {소속회사명}", id = "{회사코드}.{사번}"(x*=개발/i*=운영).
        # 본명·회사는 id 로 결정적 배정(같은 사람=항상 같은 이름). 회사명은 대부분 SKCC + 협력사 소수.
        users = {"pmo": {"name": "pmo", "displayName": "PMO Office"},
                 "lead": {"name": "lead", "displayName": "정한울 SKCC"}}
        taken = {"정한울"}                       # 본명 중복 방지(데모 가독성) — id 정렬로 결정적
        for uid in sorted({u for ids in self.people.values() for u in ids}):
            h = _shash(uid)
            g = h // 7
            nm = _SURNAMES[h % len(_SURNAMES)] + _GIVEN[g % len(_GIVEN)]
            while nm in taken:                    # 충돌 시 given 을 결정적으로 다음 후보로
                g += 1
                nm = _SURNAMES[h % len(_SURNAMES)] + _GIVEN[g % len(_GIVEN)]
            taken.add(nm)
            co = _COMPANIES[(h // 3) % len(_COMPANIES)]
            users[uid] = {"name": uid, "realName": nm, "company": co,
                          "displayName": f"{nm} {co}"}
        return users

    def _pool(self, module):
        return self.people.get(module, []) or ["pmo"]

    def _newkey(self):
        self._counter += 1
        return f"{self.project}-{self._counter}"

    def _dt(self, d, hm=None):
        return d.isoformat() + "T" + (hm or "09:00") + ":00.000+0000"

    # ── 이슈 생성 헬퍼 ──
    def _make_issue(self, rng, itype, module, epic_key=None, parent_key=None,
                    label_pmo=False, summary=None, component=None, assignee=None,
                    created=None, resolved=None, force_cat=None):
        pool = self._pool(module)
        assignee = assignee or pool[rng.randrange(len(pool))]
        reporter = rng.choice(["pmo", "lead"] + pool)
        maturity = rng.uniform(0.15, 0.85)
        created = created or (self.today - timedelta(days=rng.randint(5, 175)))
        if resolved is not None:                       # 과거 종료 override
            cat = "done"
        else:
            cat = force_cat or _cat(rng, maturity)
            if cat == "done":                          # 완료일을 created~today 전체에 분산
                span = max((self.today - created).days, 1)
                resolved = created + timedelta(days=rng.randint(1, span))
        updated = resolved or (created + timedelta(days=rng.randint(0, max((self.today - created).days, 1))))
        if updated > self.today:
            updated = self.today
        due = None if rng.random() < 0.25 else (created + timedelta(days=rng.randint(40, 160)))
        def _tm():   # 결정적 업무시간 hh:MM (뉴스/활동 시간표시용)
            return "%02d:%02d" % (rng.randint(8, 20), rng.choice([0, 10, 15, 20, 30, 40, 45, 50]))
        status_name = rng.choice(_STATUS_NAMES[cat])
        # SP: story-like 는 값 or 누락(None), Bug 는 0, Epic/Sub-Task 는 None
        if itype in _STORYLIKE:
            sp = None if rng.random() < 0.12 else rng.choice([1, 2, 3, 5, 8])
        elif itype == "Bug":
            sp = 0
        else:
            sp = None
        labels = []
        if itype in _STORYLIKE and rng.random() < 0.15:
            labels.append("mock")
        if label_pmo:
            labels.append("PMO_VIT")

        key = epic_key if itype == "Epic" and epic_key else self._newkey()
        ncom = rng.randint(0, 4) if itype != SUBTASK_TYPE else rng.randint(0, 1)
        comments = [{"author": rng.choice(pool + ["pmo", "lead"]),
                     "kind": k, "text": t,
                     "created": self.today - timedelta(days=rng.randint(0, 13))}
                    for (k, t) in wc.comments(rng, pool, ncom)]
        worklog = []
        if cat != "todo":
            for _ in range(rng.randint(0, 3)):
                worklog.append({"author": assignee,
                                "date": self.today - timedelta(days=rng.randint(0, 7)),
                                "seconds": 3600 * rng.randint(1, 6)})

        self.issues[key] = {
            "key": key, "project": self.project, "type": itype,
            "summary": summary or self._summary(rng, itype, module),
            "description": wc.description(rng, itype),
            "module": module, "component": component or module,
            "assignee": assignee, "reporter": reporter,
            "statusCategory": cat, "statusName": status_name,
            "labels": labels, "sp": sp,
            "epicKey": epic_key if itype != "Epic" else None,
            "parentKey": parent_key,
            "created": created, "updated": updated, "resolved": resolved, "due": due,
            "tcreated": _tm(), "tresolved": _tm(), "tupdated": _tm(),
            "comments": comments, "worklog": worklog, "subtasks": [],
        }
        return key

    _SUMMARY = {
        "Epic": ["실시간 처리 안정화", "메타데이터 표준화", "대용량 적재 최적화", "쿼리 성능 개선"],
        "Story": ["신규 커넥터 추가", "대시보드 위젯", "API 스펙 확정", "캐시 전략 적용"],
        "Task": ["환경 구성", "배포 파이프라인 개선", "데이터 품질 룰 추가", "스키마 마이그레이션"],
        "Bug": ["NPE 수정", "경계값 오류 수정", "동시성 이슈 해결", "타임아웃 조정"],
        "Improvement": ["로그 포맷 개선", "쿼리 튜닝", "리트라이 정책 보강", "에러 메시지 개선"],
        "New Feature": ["증분 CDC 지원", "롤백 API", "실시간 알림", "셀프서비스 조회"],
        "Sub-Task": ["단위 테스트", "코드 리뷰 반영", "QA 확인", "릴리스 노트"],
    }

    def _summary(self, rng, itype, module):
        return f"[{module}] " + rng.choice(self._SUMMARY.get(itype, ["작업"]))

    def _add_subtasks(self, rng, parent_key, module, n):
        for _ in range(n):
            sk = self._make_issue(rng, SUBTASK_TYPE, module, parent_key=parent_key)
            self.issues[parent_key]["subtasks"].append(sk)

    # ── WBS epics + 자식 ──
    def _build_wbs_epics(self):
        epic_module = {}
        for w in self.plan["wbs"]:
            for e in w["epics"]:
                epic_module.setdefault(e["key"], w["module"])
        for ekey, module in epic_module.items():
            rng = _rng("epic", ekey)
            self._make_issue(rng, "Epic", module, epic_key=ekey)   # Epic 이름은 생성 풀에서(=Jira)
            for _ in range(rng.randint(4, 9)):
                ct = rng.choices(_CHILD_TYPES, weights=_CHILD_TYPES_W)[0]
                ck = self._make_issue(rng, ct, module, epic_key=ekey)
                if ct in _STORYLIKE and rng.random() < 0.4:
                    self._add_subtasks(rng, ck, module, rng.randint(1, 2))

    # ── WBS 밖 일반 Epic (현안 아님) + 자식 ──
    def _build_extra_epics(self):
        for module in self.modules:
            rng = _rng("xepic", module)
            for _ in range(rng.randint(2, 5)):
                ek = self._newkey()
                self._make_issue(rng, "Epic", module, epic_key=ek)
                for _ in range(rng.randint(5, 12)):
                    ct = rng.choices(_CHILD_TYPES, weights=_CHILD_TYPES_W)[0]
                    ck = self._make_issue(rng, ct, module, epic_key=ek)
                    if ct in _STORYLIKE and rng.random() < 0.4:
                        self._add_subtasks(rng, ck, module, rng.randint(1, 2))

    # ── Epic 소속 아닌 독립 Task/Story (진행중·완료 혼합) ──
    def _build_standalone(self):
        for module in self.modules:
            pool = self._pool(module)
            rng = _rng("solo", module)
            for i in range(rng.randint(8, 16)):
                t = rng.choices(_CHILD_TYPES, weights=_CHILD_TYPES_W)[0]
                k = self._make_issue(rng, t, module, assignee=pool[i % len(pool)])
                if t in _STORYLIKE and rng.random() < 0.35:
                    self._add_subtasks(rng, k, module, rng.randint(1, 2))

    # ── 1~6월에 생성·종료된 과거 완료 이슈 (대량, Closed/Resolved) ──
    def _build_history(self):
        jan1 = date(2026, 1, 1)
        for module in self.modules:
            pool = self._pool(module)
            rng = _rng("hist", module)
            for i in range(rng.randint(12, 24)):
                created = jan1 + timedelta(days=rng.randint(0, 120))       # Jan~중순 May
                resolved = created + timedelta(days=rng.randint(5, 110))   # 종료
                if resolved >= self.today:
                    resolved = self.today - timedelta(days=rng.randint(15, 150))
                if resolved <= created:
                    resolved = created + timedelta(days=7)
                t = rng.choices(["Task", "Story", "Bug", "Improvement"], weights=[5, 4, 3, 2])[0]
                k = self._make_issue(rng, t, module, assignee=pool[i % len(pool)],
                                     created=created, resolved=resolved)
                if t in _STORYLIKE and rng.random() < 0.45:
                    for _ in range(rng.randint(1, 3)):
                        scr = created + timedelta(days=rng.randint(0, 15))
                        srv = min(scr + timedelta(days=rng.randint(3, 60)), resolved)
                        sk = self._make_issue(rng, SUBTASK_TYPE, module, parent_key=k,
                                              assignee=pool[i % len(pool)], created=scr, resolved=srv)
                        self.issues[k]["subtasks"].append(sk)

    # ── PMO_VIT 현안 (모듈별 다양 개수, 조상/자손 dedup 케이스 포함) ──
    def _build_vit(self):
        dedup_done = False
        for mi, module in enumerate(self.modules):
            size = _VIT_SIZES[mi % len(_VIT_SIZES)]
            for i in range(size):
                rng = _rng("vit", module, i)
                rtype = rng.choices(_VIT_ROOT_TYPES, weights=[5, 3, 2, 2])[0]
                if rtype == "Epic":
                    ekey = self._newkey()               # Epic 루트 key = LAKE-#
                    root = self._make_issue(rng, "Epic", module, epic_key=ekey, label_pmo=True)
                    # 자식들 (epicKey=root)
                    child_keys = []
                    for _ in range(rng.randint(3, 8)):
                        ct = rng.choices(_CHILD_TYPES, weights=_CHILD_TYPES_W)[0]
                        ck = self._make_issue(rng, ct, module, epic_key=root)
                        child_keys.append(ck)
                        if ct in _STORYLIKE and rng.random() < 0.5:
                            self._add_subtasks(rng, ck, module, rng.randint(1, 2))
                    # dedup 케이스: 자식 하나도 PMO_VIT (조상이 이미 VIT → 스킵되어야 함)
                    if not dedup_done and child_keys:
                        self.issues[child_keys[0]]["labels"].append("PMO_VIT")
                        dedup_done = True
                else:
                    root = self._make_issue(rng, rtype, module, label_pmo=True)
                    for _ in range(rng.randint(2, 5)):
                        self._add_subtasks(rng, root, module, 1)

    # ── VoC 티켓 (Component=VoC, 고객의 소리성 업무) ──
    def _build_voc(self):
        for module in self.modules:
            for pid in self._pool(module):
                rng = _rng("voc", pid)
                for _ in range(rng.randint(0, 5)):
                    self._make_issue(rng, rng.choice(["Task", "Bug", "Story"]), module,
                                     component="사용자 VoC", assignee=pid)

    # ── 인덱스 ──
    def _index(self):
        self.by_label = {}
        self.by_assignee = {}
        self.epic_children = {}
        for k, it in self.issues.items():
            for lb in it["labels"]:
                self.by_label.setdefault(lb, []).append(k)
            self.by_assignee.setdefault(it["assignee"], []).append(k)
            if it["epicKey"]:
                self.epic_children.setdefault(it["epicKey"], []).append(k)

    # ── 활동(activity) : 이슈 이벤트를 인력별로 집계 ──
    def _build_activity(self):
        ev = {}
        def add(user, date_, time_, kind, key, summary):
            ev.setdefault(user, []).append({"date": date_, "time": time_, "kind": kind, "key": key, "summary": summary})
        for it in self.issues.values():
            add(it["reporter"], it["created"], it.get("tcreated"), "created", it["key"], it["summary"])
            for c in it["comments"]:
                add(c["author"], c["created"], it.get("tupdated"), "commented", it["key"], it["summary"])
            for w in it["worklog"]:
                add(w["author"], w["date"], it.get("tupdated"), "logged work", it["key"], it["summary"])
            if it["resolved"]:
                add(it["assignee"], it["resolved"], it.get("tresolved"), "resolved", it["key"], it["summary"])
                add(it["assignee"], it["resolved"], it.get("tresolved"), "transitioned", it["key"], it["summary"])
        for u in ev:
            ev[u].sort(key=lambda e: (e["date"].isoformat(), e.get("time") or ""), reverse=True)
        self.activity = ev

    def _build_confluence(self):
        conf = {}
        for module, ids in self.people.items():
            for uid in ids:
                rng = _rng("conf", uid)
                pages = []
                for _ in range(rng.randint(0, 4)):
                    pages.append({"title": wc.conf_title(rng), "space": wc.conf_space(rng),
                                  "action": wc.conf_action(rng),
                                  "date": self.today - timedelta(days=rng.randint(0, 13)),
                                  "time": "%02d:%02d" % (rng.randint(8, 19), rng.choice([0, 15, 30, 45]))})
                pages.sort(key=lambda p: p["date"], reverse=True)
                conf[uid] = pages
        self.confluence = conf

    # ── Jira REST 직렬화 (실 Jira DC 8.20.8 형태) ──
    def _status_obj(self, cat, name):
        jc = _JIRA_CAT[cat]
        return {"id": _STATUS_ID.get(name, "1"), "name": name,
                "statusCategory": {"id": jc["id"], "key": jc["key"],
                                   "colorName": jc["colorName"], "name": jc["name"]}}

    def _user_obj(self, uid):
        u = self.users.get(uid, {"name": uid, "displayName": uid})
        return {"name": u["name"], "key": u["name"], "displayName": u["displayName"],
                "emailAddress": f"{u['name']}@example.com", "active": True}

    def jira_fields(self, it):
        f = {
            "summary": it["summary"], "description": it["description"],
            "issuetype": {"name": it["type"], "subtask": it["type"] == SUBTASK_TYPE},
            "status": self._status_obj(it["statusCategory"], it["statusName"]),
            "assignee": self._user_obj(it["assignee"]),
            "reporter": self._user_obj(it["reporter"]),
            "components": [{"name": it["component"]}],
            "labels": it["labels"],
            "created": self._dt(it["created"], it.get("tcreated")),
            "updated": self._dt(it["updated"], it.get("tupdated")),
            "resolutiondate": self._dt(it["resolved"], it.get("tresolved")) if it["resolved"] else None,
            "duedate": it["due"].isoformat() if it["due"] else None,
            self.sp_field: it["sp"],
            self.epic_link_field: it["epicKey"],
        }
        if it["parentKey"]:
            p = self.issues.get(it["parentKey"])
            if p:
                f["parent"] = {"key": p["key"], "fields": {
                    "summary": p["summary"],
                    "status": self._status_obj(p["statusCategory"], p["statusName"]),
                    "issuetype": {"name": p["type"]}}}
        f["subtasks"] = [{"key": sk, "fields": {
            "summary": self.issues[sk]["summary"],
            "status": self._status_obj(self.issues[sk]["statusCategory"], self.issues[sk]["statusName"]),
            "issuetype": {"name": SUBTASK_TYPE, "subtask": True}}}
            for sk in it["subtasks"] if sk in self.issues]
        return f

    def jira_issue(self, key):
        it = self.issues.get(key)
        return {"key": key, "fields": self.jira_fields(it)} if it else None

    def jira_comments(self, key):
        it = self.issues.get(key)
        if not it:
            return []
        out = []
        for i, c in enumerate(it["comments"]):
            au = self.users.get(c["author"], {"name": c["author"], "displayName": c["author"]})
            out.append({"id": f"{key}-c{i}",
                        "author": {"name": au["name"], "displayName": au["displayName"]},
                        "body": f"({c['kind']}) {c['text']}",
                        "created": self._dt(c["created"]), "updated": self._dt(c["created"])})
        out.sort(key=lambda c: c["created"], reverse=True)
        return out


@lru_cache(maxsize=1)
def get_world():
    return World(load_plan(), load_people(), date.today())
